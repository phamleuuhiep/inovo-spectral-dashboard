# -*- coding: utf-8 -*-
"""
Tab "Trứng nở · Trống/Mái": các trứng đã nở có tự tách thành 2 nhóm (≈ Trống / Mái) theo phổ hay không?

Không dùng nhãn. Với mỗi cấu hình (đầu đo × đại lượng × dữ liệu ngày) trên tập trứng đã chọn:
  1. X = phổ hình dạng (counts / TB band) đã làm mượt, mỗi khối trừ TB và chia căn tổng phương sai của khối.
  2. PCA, giữ các PC tới 95% phương sai (tối thiểu 2).
  3. Chia đôi TỐT NHẤT: n ≤ 16 duyệt hết 2^(n−1) cách chia (mỗi nhóm ≥ 2 trứng), tiêu chí R² = 1 − SSW/SST
     (= k-means k = 2 tối ưu toàn cục); n lớn hơn dùng k-means.
  4. Kiểm định kiểu SigClust: dữ liệu giả Gaussian cùng phương sai từng PC (không có cụm), chạy y hệt bước 3;
     p = tỷ lệ dữ liệu giả có R² ≥ R² thật. p nhỏ = có cấu trúc 2 nhóm thật, không phải do chia đôi bất kỳ.
  5. Nhiều cấu hình -> q (Benjamini–Hochberg) và số cấu hình p < 0.05 so với kỳ vọng ngẫu nhiên.
Đồng thuận: tỷ lệ cấu hình xếp 2 trứng cùng nhóm; chia đôi đồng thuận = cách chia tối đa hoá đồng thuận trong nhóm.
Gây nhiễu: độ sáng vỏ (counts band D đầu tiên ≥ 2) và ngày nở được so giữa 2 nhóm đồng thuận.
Khi đã gán Trống/Mái cho ≥ 2 trứng mỗi giới: độ chính xác / ARI của từng cấu hình và p chính xác cho chia đồng thuận.
"""

import itertools

import numpy as np

PAPER_BANDS = ((594, 620), (660, 675))
QUANTS = ("shape", "deriv", "paper")
ENDS = ("blunt", "pointed", "both")
MAX_EXACT = 16


def _smooth(v, w):
    if w <= 1:
        return v
    k = np.ones(int(w)) / int(w)
    pad = int(w) // 2
    return np.convolve(np.pad(v, pad, mode="edge"), k, mode="valid")[:len(v)]


def _shape(v, wl, band):
    m = (wl >= band[0]) & (wl <= band[1])
    mu = np.nanmean(v[m])
    return v / mu if mu > 0 else v * np.nan


def _quant(v, wl, q):
    if q == "deriv":
        return np.gradient(v, wl)
    if q == "paper":
        return v[np.any([(wl >= a) & (wl <= b) for a, b in PAPER_BANDS], axis=0)]
    return v


# ----------------------------------------------------------------------------
# Chia đôi tối ưu + null
# ----------------------------------------------------------------------------
_PART = {}


def _partitions(n):
    """Ma trận thành viên (m × n) của mọi cách chia đôi với mỗi nhóm ≥ 2 (trứng 0 luôn ở nhóm 0)."""
    if n not in _PART:
        rows = []
        for mask in range(1, 2 ** (n - 1)):
            a = np.array([(mask >> i) & 1 for i in range(n - 1)] + [0], bool)[::-1]
            if 2 <= a.sum() <= n - 2:
                rows.append(a)
        _PART[n] = np.array(rows, bool)
    return _PART[n]


def _best_split(F):
    """-> (nhãn 0/1, R²). F đã trừ trung bình."""
    n = len(F)
    sst = float((F ** 2).sum())
    if sst <= 0:
        return np.zeros(n, int), 0.0
    if n <= MAX_EXACT:
        M = _partitions(n).astype(float)
        na = M.sum(1)
        sa = M @ F                                  # tổng của nhóm 1; nhóm 0 = −sa (F đã trừ TB)
        ssb = (sa ** 2).sum(1) * (1 / na + 1 / (n - na))
        i = int(np.argmax(ssb))
        return M[i].astype(int), float(ssb[i] / sst)
    from sklearn.cluster import KMeans
    lab = KMeans(2, n_init=20, random_state=0).fit_predict(F)
    ssw = sum(((F[lab == c] - F[lab == c].mean(0)) ** 2).sum() for c in (0, 1))
    return lab, float(1 - ssw / sst)


def _reduce(X):
    X = X - X.mean(0)
    U, s, _ = np.linalg.svd(X, full_matrices=False)
    var = s ** 2
    if var.sum() <= 0:
        return X[:, :2] * 0, np.zeros(2)
    cum = np.cumsum(var) / var.sum()
    k = int(max(2, min(len(s), np.searchsorted(cum, 0.95) + 1)))
    return U[:, :k] * s[:k], var / var.sum()


def _silhouette(F, lab):
    from sklearn.metrics import silhouette_score
    return float(silhouette_score(F, lab)) if len(set(lab)) == 2 else None


def _null(F, n_null, rng):
    sd = F.std(0)
    return np.array([_best_split(g - g.mean(0))[1] for g in (rng.normal(size=F.shape) * sd for _ in range(n_null))])


def _align(lab, ref):
    lab = np.asarray(lab)
    return lab if (lab == ref).sum() >= (lab != ref).sum() else 1 - lab


def _ari(a, b):
    from sklearn.metrics import adjusted_rand_score
    return float(adjusted_rand_score(a, b))


def _cohen(v, lab):
    """Cohen's d (nhóm 1 − nhóm 0) / SD gộp."""
    v, lab = np.asarray(v, float), np.asarray(lab)
    a, b = v[lab == 0], v[lab == 1]
    if len(a) < 2 or len(b) < 2:
        return None
    sp = np.sqrt(((len(a) - 1) * a.var(ddof=1) + (len(b) - 1) * b.var(ddof=1)) / (len(v) - 2))
    return float((b.mean() - a.mean()) / sp) if sp > 0 else None


def _bh(p):
    p = np.asarray(p, float)
    o = np.argsort(p)
    q = p[o] * len(p) / np.arange(1, len(p) + 1)
    q = np.minimum.accumulate(q[::-1])[::-1]
    out = np.empty_like(q)
    out[o] = np.minimum(q, 1)
    return out


# ----------------------------------------------------------------------------
# Chạy
# ----------------------------------------------------------------------------
def run(P, labels, band=(604, 922), smooth=11, base_day=2, n_null=200, groups=None, hatch=None, seed=0):
    """P = ml.prepare(...). labels = trứng cần xét. groups = {label: male/female/...}, hatch = {label: ngày nở}."""
    groups, hatch = groups or {}, hatch or {}
    wl = P["wl"]
    labels = [l for l in labels if l in P["labels"]]
    days = [d for d in P["days"]
            if all(d in P["spec"][e].get(l, {}) for l in labels for e in ("blunt", "pointed"))]
    missing = sorted(set(P["days"]) - set(days))
    if len(labels) < 4:
        raise ValueError("cần ít nhất 4 trứng có dữ liệu")
    if not days:
        raise ValueError("không có ngày nào đủ dữ liệu cho mọi trứng đã chọn")
    n = len(labels)
    # phổ hình dạng đã làm mượt: S[end][label][day]
    S = {e: {l: {d: _shape(_smooth(P["spec"][e][l][d], smooth), wl, band) for d in days} for l in labels}
         for e in ("blunt", "pointed")}

    inputs = [(f"D{d}", [(d, None)]) for d in days]
    if base_day in days:
        inputs += [(f"D{d}−D{base_day}", [(d, base_day)]) for d in days if d > base_day]
        late = 5 if 5 in days else max(days)
        if late > base_day:
            inputs.append((f"D{base_day}‖D{late}‖Δ", [(base_day, None), (late, None), (late, base_day)]))

    rng = np.random.default_rng(seed)
    configs, splits = [], []
    for end in ENDS:
        es = ("blunt", "pointed") if end == "both" else (end,)
        for q in QUANTS:
            for name, blocks in inputs:
                parts = []
                for e in es:
                    for a, r in blocks:
                        B = np.array([_quant(S[e][l][a] - (S[e][l][r] if r is not None else 0), wl, q) for l in labels])
                        B = B - B.mean(0)
                        tv = np.sqrt((B ** 2).sum() / n)
                        parts.append(B / tv if tv > 0 else B)
                F, var = _reduce(np.hstack(parts))
                lab, r2 = _best_split(F)
                null = _null(F, n_null, rng) if n_null else np.array([])
                p = float((1 + (null >= r2).sum()) / (1 + len(null))) if len(null) else None
                configs.append({"end": end, "q": q, "input": name, "n_pc": int(F.shape[1]), "r2": r2,
                                "null_r2": float(np.median(null)) if len(null) else None,
                                "null_p95": float(np.percentile(null, 95)) if len(null) else None, "p": p,
                                "silhouette": _silhouette(F, lab), "pc": F[:, :2].tolist(), "pc_var": var[:2].tolist()})
                splits.append(lab)

    splits = np.array(splits)
    if all(c["p"] is not None for c in configs):
        for c, qv in zip(configs, _bh([c["p"] for c in configs])):
            c["q_bh"] = float(qv)

    # ---------- đồng thuận ----------
    same = np.array([[np.mean(splits[:, i] == splits[:, j]) for j in range(n)] for i in range(n)])
    W = same - same[~np.eye(n, dtype=bool)].mean()
    np.fill_diagonal(W, 0)
    if n <= MAX_EXACT:
        M = _partitions(n)
        score = [W[np.ix_(m, m)].sum() + W[np.ix_(~m, ~m)].sum() for m in M]
        cons = M[int(np.argmax(score))].astype(int)
    else:
        from sklearn.cluster import AgglomerativeClustering
        cons = AgglomerativeClustering(2, metric="precomputed", linkage="average").fit_predict(1 - same)
    if cons[0] != 0:
        cons = 1 - cons
    for c, lab in zip(configs, splits):
        a = _align(lab, cons)
        c["labels"] = [int(x) for x in a]
        c["ari_cons"] = _ari(a, cons)
        c["sizes"] = [int((a == 0).sum()), int((a == 1).sum())]
    support = [float(np.mean([same[i, j] for j in range(n) if j != i and cons[j] == cons[i]])) for i in range(n)]

    # ---------- gây nhiễu: độ sáng vỏ, ngày nở ----------
    bm = (wl >= band[0]) & (wl <= band[1])
    d_b = days[0] if days[0] >= 2 or len(days) == 1 else days[1]
    bright = {e: [float(np.log10(max(np.nanmean(P["spec"][e][l][d_b][bm]), 1e-9))) for l in labels] for e in ("blunt", "pointed")}

    confound = {"bright_day": d_b, "bright": bright, "d_bright": {e: _cohen(v, cons) for e, v in bright.items()},
                "hatch": [hatch.get(l) for l in labels]}
    for c in configs:
        c["d_bright"] = max(abs(_cohen(bright[e], c["labels"]) or 0) for e in ("blunt", "pointed"))

    # ---------- nhãn Trống / Mái (nếu có) ----------
    sex = [groups.get(l) if groups.get(l) in ("male", "female") else None for l in labels]
    truth = None
    idx = [i for i, s in enumerate(sex) if s]
    if sum(s == "male" for s in sex) >= 2 and sum(s == "female" for s in sex) >= 2:
        y = np.array([sex[i] == "male" for i in idx], int)
        for c in configs:
            a = np.array(c["labels"])[idx]
            c["acc"] = float(max((a == y).mean(), (a != y).mean()))
            c["ari_sex"] = _ari(a, y)
        a = cons[idx]
        acc = float(max((a == y).mean(), (a != y).mean()))
        # p chính xác: mọi cách gán nhãn có cùng số Trống / Mái
        k1 = int(y.sum())
        accs = []
        for comb in itertools.combinations(range(len(idx)), k1):
            yy = np.zeros(len(idx), int); yy[list(comb)] = 1
            accs.append(max((a == yy).mean(), (a != yy).mean()))
        truth = {"n": len(idx), "n_male": k1, "n_female": len(idx) - k1, "acc": acc, "ari": _ari(a, y),
                 "p": float(np.mean(np.array(accs) >= acc - 1e-12))}

    ps = [c["p"] for c in configs if c["p"] is not None]
    spec = {e: {l: {d: [round(float(v), 5) if np.isfinite(v) else None for v in S[e][l][d]] for d in days} for l in labels}
            for e in ("blunt", "pointed")}
    return {
        "labels": labels, "days": days, "missing_days": missing, "base_day": base_day, "n_null": n_null,
        "wl": [round(float(w), 2) for w in wl], "paper_bands": PAPER_BANDS, "spec": spec,
        "configs": configs, "consensus": [int(x) for x in cons], "same": same.tolist(), "support": support,
        "summary": {"n_configs": len(configs), "n_sig": int(sum(p < 0.05 for p in ps)), "expected": 0.05 * len(ps),
                    "min_p": min(ps) if ps else None, "min_q": min((c.get("q_bh", 1) for c in configs), default=None),
                    "mean_ari_cons": float(np.mean([c["ari_cons"] for c in configs])),
                    "n_partitions": int(len(_partitions(n))) if n <= MAX_EXACT else None},
        "confound": confound, "sex": sex, "truth": truth,
    }

