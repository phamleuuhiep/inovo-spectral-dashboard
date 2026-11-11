# -*- coding: utf-8 -*-
"""
Mô phỏng quy trình sàng lọc của Li et al. 2026 (Animals 16:2796, DSSF-Net):
  - phổ truyền qua tương đối T = (S - D) / (L - D)   (hoặc counts trừ dark)
  - đặc trưng dọc trên cùng 1 quả: ΔS = S_b - S_a,  R = ΔS / (S_a + ε)
  - sàng lọc từng batch và từng cặp batch bằng SVM (RBF) + AUC kiểm định chéo phân tầng
  - kiểm định hoán vị trên TOÀN BỘ quá trình sàng lọc (thống kê max): với N nhỏ, chọn cấu hình
    tốt nhất trong ~120 cấu hình luôn cho AUC cao kể cả khi nhãn vô nghĩa

Khác bài báo: bài báo có 1400 trứng và chọn siêu tham số bằng grid search; ở đây N nhỏ
nên dùng SVM cố định (C=10, gamma='scale') và kiểm định chéo lặp lại.
Chạy như một job nền (vài phút nếu có hoán vị), client hỏi tiến trình qua /api/job.
"""

import threading
import time
import uuid

import numpy as np

from analysis import _darksub, _grid

PAPER_REGIONS = [(594, 620), (660, 675)]
FEATURE_SETS = {
    "concat": "S_a + S_b (nối trực tiếp)",
    "dS": "ΔS",
    "R": "R",
    "all": "S_a + S_b + ΔS + R",
}
JOBS = {}


def _basis(store, b, rec, grid, basis, sel):
    y = _darksub(store, b, rec, grid)
    if y is None:
        return None
    if basis == "T":
        L = _darksub(store, b, b["lamp"], grid)
        if L is None:
            return None
        with np.errstate(divide="ignore", invalid="ignore"):
            y = np.where(L > 0.03 * np.nanmax(L), y / L, np.nan)
    return y[sel]


def _members(groups, g):
    if g == "fertile":
        return {l for l, x in groups.items() if x in ("male", "female", "fertile")}
    return {l for l, x in groups.items() if x == g}


def prepare(store, groups, end, g1, g2, basis, lo, hi, only=None):
    """Đọc dữ liệu (cần khoá store) -> dict thuần numpy để job chạy không đụng store. only = chỉ xét các trứng này."""
    batches = store.batches()
    grid = _grid(store, batches)
    sel = (grid >= lo) & (grid <= hi)
    A, B = _members(groups, g1), _members(groups, g2)
    A, B = A - B, B - A
    if only is not None:
        A, B = A & set(only), B & set(only)
    labels = sorted(A | B)
    spec = []
    for b in batches:
        row = {}
        for l in labels:
            recs = (b["eggs"].get(l) or {}).get(end) or []
            if recs:
                v = _basis(store, b, recs[-1], grid, basis, sel)
                if v is not None and np.isfinite(v).mean() > 0.5:
                    row[l] = np.nan_to_num(v, nan=0.0)
        spec.append(row)
    if basis == "T":
        eps = 0.02                      # như bài báo, trên thang T
    else:                               # counts: tỷ lệ theo mức tín hiệu
        allv = [v for row in spec for v in row.values()]
        eps = 0.02 * float(np.nanmedian([np.nanmean(v) for v in allv])) if allv else 1.0
    return {"spec": spec, "y": {l: int(l in A) for l in labels}, "eps": eps, "n1": len(A), "n2": len(B),
            "batches": [{"batch_no": b["batch_no"], "age_h": b["age_h"]} for b in batches],
            "meta": {"basis": basis, "end": end, "g1": g1, "g2": g2, "range": [lo, hi]}}


def _feats(spec, eps, a, b, kind, ls):
    Sa = np.array([spec[a][l] for l in ls]); Sb = np.array([spec[b][l] for l in ls])
    dS = Sb - Sa
    if kind == "concat":
        return np.hstack([Sa, Sb])
    if kind == "dS":
        return dS
    R = dS / (Sa + eps)
    return R if kind == "R" else np.hstack([Sa, Sb, dS, R])


def _cv_auc(X, y, seed, n_repeats):
    from sklearn.metrics import roc_auc_score
    from sklearn.model_selection import RepeatedStratifiedKFold
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.svm import SVC
    n1, n0 = int(y.sum()), int(len(y) - y.sum())
    k = min(5, n1, n0)
    if k < 2:
        return None
    aucs = []
    for tr, te in RepeatedStratifiedKFold(n_splits=k, n_repeats=n_repeats, random_state=seed).split(X, y):
        if len(set(y[te])) < 2:
            continue
        m = make_pipeline(StandardScaler(), SVC(C=10, gamma="scale")).fit(X[tr], y[tr])
        aucs.append(roc_auc_score(y[te], m.decision_function(X[te])))
    return float(np.mean(aucs)) if aucs else None


def _run_all(P, yfun, seed, n_repeats, tick=None):
    """Một lượt sàng lọc đầy đủ (từng batch + mọi cặp × mọi bộ đặc trưng). yfun(label)->0/1."""
    spec, eps = P["spec"], P["eps"]
    nb = len(spec)
    single, pairs = [None] * nb, {k: [[None] * nb for _ in range(nb)] for k in FEATURE_SETS}
    best = None
    for i in range(nb):
        ls = sorted(spec[i])
        if len(ls) >= 6:
            single[i] = _cv_auc(np.array([spec[i][l] for l in ls]), np.array([yfun(l) for l in ls]), seed, n_repeats)
            if single[i] is not None and (best is None or single[i] > best["auc"]):
                best = {"a": i, "b": None, "kind": "single", "auc": single[i], "n": len(ls)}
        if tick: tick()
    for i in range(nb):
        for j in range(i + 1, nb):
            ls = sorted(set(spec[i]) & set(spec[j]))
            if len(ls) >= 6:
                y = np.array([yfun(l) for l in ls])
                for kind in FEATURE_SETS:
                    auc = _cv_auc(_feats(spec, eps, i, j, kind, ls), y, seed, n_repeats)
                    pairs[kind][i][j] = pairs[kind][j][i] = auc
                    if auc is not None and (best is None or auc > best["auc"]):
                        best = {"a": i, "b": j, "kind": kind, "auc": auc, "n": len(ls)}
            if tick: tick()
    return single, pairs, best


def start_job(P, n_perm=20, n_repeats=3, seed=42):
    nb = len(P["spec"])
    per_run = nb + nb * (nb - 1) // 2
    job = {"id": uuid.uuid4().hex[:12], "status": "running", "done": 0, "total": per_run * (1 + n_perm),
           "phase": "Sàng lọc", "result": None, "error": None, "started": time.time(), "cancel": False}
    JOBS[job["id"]] = job
    for k in [k for k, j in JOBS.items() if j["status"] != "running" and time.time() - j["started"] > 3600]:
        JOBS.pop(k, None)

    def tick():
        job["done"] += 1
        if job["cancel"]:
            raise InterruptedError

    def run():
        try:
            yl = P["y"]
            single, pairs, best = _run_all(P, lambda l: yl[l], seed, n_repeats, tick)
            res = {**P["meta"], "n1": P["n1"], "n2": P["n2"], "eps": P["eps"], "batches": P["batches"],
                   "single": single, "pairs": pairs, "best": best, "feature_sets": FEATURE_SETS,
                   "regions": PAPER_REGIONS, "n_configs": per_run, "perm": None}
            job["result"] = res
            if n_perm and best:
                rng, labels, null = np.random.default_rng(seed), sorted(yl), []
                for r in range(n_perm):
                    job["phase"] = f"Hoán vị nhãn {r + 1}/{n_perm}"
                    shuf = dict(zip(labels, rng.permutation([yl[l] for l in labels])))
                    _, _, b = _run_all(P, lambda l: shuf[l], seed, n_repeats, tick)
                    if b:
                        null.append(b["auc"])
                    res["perm"] = {"null_max": null, "mean": float(np.mean(null)), "p95": float(np.percentile(null, 95)),
                                   "p": float((1 + sum(a >= best["auc"] for a in null)) / (1 + len(null))), "n": len(null)}
            job["status"] = "done"
        except InterruptedError:
            job["status"] = "cancelled"
        except Exception as ex:  # noqa
            job["status"], job["error"] = "error", repr(ex)

    threading.Thread(target=run, daemon=True).start()
    return job


def job_view(job):
    return {k: job[k] for k in ("id", "status", "done", "total", "phase", "result", "error")}
