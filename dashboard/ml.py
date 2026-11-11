# -*- coding: utf-8 -*-
"""
Tab "Học máy": tìm trứng không phôi khi mới có rất ít nhãn, và (về sau) phân loại Trống / Mái.

Dữ liệu: counts trừ dark, gộp theo NGÀY (ngày lịch của batch - ngày bắt đầu ấp; các batch cùng ngày lấy trung bình),
mỗi đầu đo (tù / nhọn) riêng, cắt theo cửa sổ λ.

Đặc trưng
  dev  : thay đổi phát triển của chính quả trứng so với ngày đầu — với mỗi ngày sau và mỗi đầu đo:
         đổi hình dạng phổ (|S_d/mean − S_0/mean| trung bình), ΔA band = log10(S_0/S_d), thay đổi R/NIR.
         Trứng không phôi không phát triển -> phổ gần như không đổi.
  spec : phổ hình dạng ngày A, ngày B và hiệu A − gốc, B − gốc (như tab Không giám sát).

Khi ít nhãn (task "infertile"):
  - Chỉ số phát triển (không dùng nhãn): TB z-score của các đặc trưng dev (dấu sao cho cao = phát triển nhiều).
  - Giống trứng đã biết: khoảng cách tới các trứng không phôi đã gán (trừ khoảng cách tới trứng có phôi đã gán).
  - PU learning (Positive–Unlabeled bagging): hồi quy logistic, dương = không phôi đã gán, âm = mẫu ngẫu nhiên
    từ trứng chưa gán (+ trứng có phôi đã gán); điểm out-of-bag.
  - Tổng hợp = trung bình phân vị hạng của các điểm có được.
Khi đủ nhãn (≥ 3 mỗi lớp): học có giám sát (LR / SVM / RF) + kiểm định chéo phân tầng lặp lại + kiểm định hoán vị.
"""

import numpy as np

from analysis import _darksub, _grid

FERTILE = ("male", "female", "fertile")
R_BAND, NIR_BAND = (620, 680), (780, 880)


# ----------------------------------------------------------------------------
# Dữ liệu theo ngày
# ----------------------------------------------------------------------------
def _day_of(b, start):
    from datetime import datetime
    try:
        if start and b.get("started"):
            d0 = datetime.fromisoformat(str(start)).date()
            return (datetime.fromisoformat(str(b["started"])).date() - d0).days
    except ValueError:
        pass
    return int(np.ceil(b["age_h"] / 24)) if b.get("age_h") is not None else None


def prepare(store, x0, x1):
    """Đọc store (cần khoá) -> phổ trung bình mỗi ngày cho mỗi trứng × đầu đo."""
    batches = store.batches()
    grid = _grid(store, batches)
    sel = (grid >= x0) & (grid <= x1)
    start = (store.info() or {}).get("incubation_start")
    days = {}
    for bi, b in enumerate(batches):
        d = _day_of(b, start)
        if d is not None:
            days.setdefault(d, []).append(bi)
    labels = sorted({l for b in batches for l in b["eggs"]})
    spec = {"blunt": {}, "pointed": {}}
    for end in spec:
        for l in labels:
            per_day = {}
            for d, bis in days.items():
                vs = []
                for bi in bis:
                    recs = (batches[bi]["eggs"].get(l) or {}).get(end) or []
                    if recs:
                        y = _darksub(store, batches[bi], recs[-1], grid)
                        if y is not None:
                            vs.append(y[sel])
                if vs:
                    per_day[d] = np.nanmean(vs, axis=0)
            spec[end][l] = per_day
    return {"wl": grid[sel], "days": sorted(days), "day_batches": {d: [batches[i]["batch_no"] for i in bis] for d, bis in days.items()},
            "labels": labels, "spec": spec}


# ----------------------------------------------------------------------------
# Đặc trưng
# ----------------------------------------------------------------------------
def _mean_in(v, wl, lo, hi):
    m = (wl >= lo) & (wl <= hi)
    return float(np.nanmean(v[m])) if m.any() else np.nan


def _shape(v, wl, band):
    m = _mean_in(v, wl, *band)
    return v / m if m and np.isfinite(m) and m > 0 else v * np.nan


def dev_features(P, ends, band):
    """Ma trận đặc trưng phát triển + dấu (+1: cao = phát triển nhiều) + quỹ đạo để vẽ."""
    wl, days = P["wl"], P["days"]
    d0, later = days[0], days[1:]
    bm = (wl >= band[0]) & (wl <= band[1])
    names, signs, cols = [], [], []
    traj = {l: {"shape": [None] * len(days), "dA": [None] * len(days)} for l in P["labels"]}
    acc = {l: {"shape": [[] for _ in days], "dA": [[] for _ in days]} for l in P["labels"]}
    for end in ends:
        tag = "tù" if end == "blunt" else "nhọn"
        for d in later:
            c_sh, c_da, c_rn = [], [], []
            for l in P["labels"]:
                s = P["spec"][end][l]
                if d0 not in s or d not in s:
                    c_sh.append(np.nan); c_da.append(np.nan); c_rn.append(np.nan); continue
                a, b = s[d0], s[d]
                sh = float(np.nanmean(np.abs(_shape(b, wl, band) - _shape(a, wl, band))[bm]))
                ma, mb = _mean_in(a, wl, *band), _mean_in(b, wl, *band)
                da = float(np.log10(ma / mb)) if ma > 0 and mb > 0 else np.nan
                rn = lambda v: _mean_in(v, wl, *R_BAND) / _mean_in(v, wl, *NIR_BAND)
                c_sh.append(sh); c_da.append(da); c_rn.append(rn(b) - rn(a))
                k = days.index(d)
                acc[l]["shape"][k].append(sh); acc[l]["dA"][k].append(da)
            names += [f"Đổi hình dạng D{d} ({tag})", f"ΔA band D{d} ({tag})", f"Đổi R/NIR D{d} ({tag})"]
            signs += [1, 1, -1]          # phôi phát triển: hình dạng đổi, hấp thụ tăng, tỷ số đỏ/NIR giảm
            cols += [c_sh, c_da, c_rn]
    for l in P["labels"]:
        for key in ("shape", "dA"):
            traj[l][key] = [0.0 if k == 0 else (float(np.nanmean(v)) if v else None) for k, v in enumerate(acc[l][key])]
    X = np.array(cols, float).T if cols else np.zeros((len(P["labels"]), 0))
    return X, names, np.array(signs, float), traj


def spec_features(P, ends, band, da, db, dr):
    wl, names, cols = P["wl"], [], []
    blocks = [(da, None), (db, None), (da, dr), (db, dr)]
    rows = []
    for l in P["labels"]:
        parts = []
        for end in ends:
            s = P["spec"][end][l]
            for a, r in blocks:
                if a not in s or (r is not None and r not in s):
                    parts.append(np.full(len(wl), np.nan)); continue
                v = _shape(s[a], wl, band)
                parts.append(v - _shape(s[r], wl, band) if r is not None else v)
        rows.append(np.concatenate(parts))
    for end in ends:
        tag = "tù" if end == "blunt" else "nhọn"
        for a, r in blocks:
            nm = f"D{a}" if r is None else f"D{a}−D{r}"
            names += [f"{nm} {w:.0f} nm ({tag})" for w in wl]
    return np.array(rows, float), names


def _impute(X):
    X = X.copy()
    mu = np.nanmean(X, axis=0)
    mu = np.where(np.isfinite(mu), mu, 0.0)
    idx = np.where(~np.isfinite(X))
    X[idx] = np.take(mu, idx[1])
    return X


def _zscore(X):
    sd = X.std(axis=0)
    sd[sd < 1e-12] = 1.0
    return (X - X.mean(axis=0)) / sd


def _reduce(Z, n_max=10):
    """Nhiều biến hơn trứng -> chiếu lên các PC đầu (giữ ~95% phương sai) để khoảng cách / logistic ổn định."""
    if Z.shape[1] <= 15:
        return Z
    from sklearn.decomposition import PCA
    k = min(n_max, Z.shape[0] - 1, Z.shape[1])
    pca = PCA(n_components=k, random_state=0).fit(Z)
    keep = int(min(k, np.searchsorted(np.cumsum(pca.explained_variance_ratio_), 0.95) + 1))
    return pca.transform(Z)[:, :max(2, keep)]


def _pct_rank(v):
    """Phân vị hạng 0..1 (cao = giống không phôi hơn); None giữ nguyên."""
    v = np.asarray([np.nan if x is None else x for x in v], float)
    ok = np.isfinite(v)
    out = np.full(len(v), np.nan)
    if ok.sum() >= 2:
        r = v[ok].argsort().argsort()
        out[ok] = r / (ok.sum() - 1)
    return out


# ----------------------------------------------------------------------------
# Ít nhãn: xếp hạng ứng viên không phôi
# ----------------------------------------------------------------------------
def _similarity(R, pos, neg):
    D = np.linalg.norm(R[:, None, :] - R[None, :, :], axis=2)
    out = np.full(len(R), np.nan)
    for i in range(len(R)):
        P = [p for p in pos if p != i]
        if not P:
            continue
        s = -D[i, P].mean()
        N = [q for q in neg if q != i]
        if N:
            s += D[i, N].mean()
        out[i] = s
    return out


def _pu_bagging(R, pos, neg, n_bags=300, seed=0):
    from sklearn.linear_model import LogisticRegression
    rng = np.random.default_rng(seed)
    n = len(R)
    unl = np.array([i for i in range(n) if i not in pos and i not in neg])
    score, cnt = np.zeros(n), np.zeros(n)
    if not pos or len(unl) < 4:
        return np.full(n, np.nan)
    k = max(len(pos) + len(neg), len(unl) // 2)
    for _ in range(n_bags):
        samp = rng.choice(unl, size=min(k, len(unl) - 1), replace=False)
        tr = np.r_[pos, neg, samp].astype(int)
        y = np.r_[np.ones(len(pos)), np.zeros(len(neg) + len(samp))]
        m = LogisticRegression(C=1.0, class_weight="balanced", max_iter=2000).fit(R[tr], y)
        oob = np.setdiff1d(unl, samp)
        score[oob] += m.decision_function(R[oob]); cnt[oob] += 1
    out = np.where(cnt > 0, score / np.maximum(cnt, 1), np.nan)
    # trứng đã gán không phôi: điểm khi bỏ chính nó ra (chỉ khi còn ≥ 1 trứng dương khác)
    for p in pos:
        rest = [q for q in pos if q != p]
        if not rest:
            continue
        tr = np.r_[rest, neg, unl].astype(int)
        y = np.r_[np.ones(len(rest)), np.zeros(len(neg) + len(unl))]
        m = LogisticRegression(C=1.0, class_weight="balanced", max_iter=2000).fit(R[tr], y)
        out[p] = float(m.decision_function(R[[p]])[0])
    return out


# ----------------------------------------------------------------------------
# Đủ nhãn: học có giám sát
# ----------------------------------------------------------------------------
def _model(kind, p, n):
    from sklearn.decomposition import PCA
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.svm import SVC
    steps = [SimpleImputer(strategy="mean"), StandardScaler()]
    if p > 20:
        steps.append(PCA(n_components=max(2, min(10, n - 2)), random_state=0))
    if kind == "svm":
        clf = SVC(C=1.0, gamma="scale", class_weight="balanced")
    elif kind == "rf":
        clf = RandomForestClassifier(n_estimators=100, min_samples_leaf=2, class_weight="balanced", random_state=0, n_jobs=-1)
    else:
        clf = LogisticRegression(C=0.5, class_weight="balanced", max_iter=3000)
    return make_pipeline(*steps, clf)


def _score(m, X):
    if hasattr(m, "predict_proba"):
        try:
            return m.predict_proba(X)[:, 1]
        except AttributeError:
            pass
    return 1 / (1 + np.exp(-m.decision_function(X)))


def _cv(X, y, kind, repeats, seed):
    from sklearn.metrics import roc_auc_score
    from sklearn.model_selection import RepeatedStratifiedKFold
    k = int(min(5, y.sum(), len(y) - y.sum()))
    oof, cnt, aucs = np.zeros(len(y)), np.zeros(len(y)), []
    for tr, te in RepeatedStratifiedKFold(n_splits=k, n_repeats=repeats, random_state=seed).split(X, y):
        m = _model(kind, X.shape[1], len(tr)).fit(X[tr], y[tr])
        s = _score(m, X[te])
        oof[te] += s; cnt[te] += 1
        if len(set(y[te])) == 2:
            aucs.append(roc_auc_score(y[te], s))
    return oof / np.maximum(cnt, 1), float(np.mean(aucs)), float(np.std(aucs)), k


def supervised(X, y_all, kind="lr", n_perm=50, repeats=5, seed=0):
    """y_all: 1 / 0 / None. CV trên trứng có nhãn, hoán vị nhãn, rồi dự đoán trứng chưa gán."""
    lab = np.array([v is not None for v in y_all])
    Xl, y = X[lab], np.array([v for v in y_all if v is not None], int)
    oof, auc, sd, k = _cv(Xl, y, kind, repeats, seed)
    perm = None
    if kind == "rf":
        n_perm = min(n_perm, 20)        # RF chậm: giới hạn số lần hoán vị
    if n_perm:
        rng = np.random.default_rng(seed)
        null = [_cv(Xl, rng.permutation(y), kind, max(1, repeats // 2), seed + r + 1)[1] for r in range(n_perm)]
        perm = {"n": n_perm, "mean": float(np.mean(null)), "p95": float(np.percentile(null, 95)),
                "p": float((1 + sum(a >= auc for a in null)) / (1 + n_perm))}
    m = _model(kind, X.shape[1], len(y)).fit(Xl, y)
    pred = np.full(len(X), np.nan)
    pred[lab] = oof
    if (~lab).any():
        pred[~lab] = _score(m, X[~lab])
    return {"model": kind, "auc": auc, "auc_sd": sd, "k": k, "repeats": repeats, "perm": perm,
            "n_pos": int(y.sum()), "n_neg": int(len(y) - y.sum()), "score": pred}


# ----------------------------------------------------------------------------
# Điểm vào
# ----------------------------------------------------------------------------
def run(P, groups, task="infertile", feats="dev", ends=("blunt", "pointed"), band=(604, 922),
        da=None, db=None, dr=None, model="lr", n_perm=50):
    labels, days = P["labels"], P["days"]
    if len(days) < 2:
        raise ValueError("cần dữ liệu ít nhất 2 ngày")
    da = da if da in days else days[min(2, len(days) - 1)]
    db = db if db in days else days[-1]
    dr = dr if dr in days else days[min(1, len(days) - 1)]
    Xd, dnames, signs, traj = dev_features(P, ends, band)
    blocks, names = [], []
    if feats in ("dev", "both"):
        blocks.append(Xd); names += dnames
    if feats in ("spec", "both"):
        Xs, snames = spec_features(P, ends, band, da, db, dr)
        blocks.append(Xs); names += snames
    X = np.hstack(blocks)
    has = np.isfinite(X).mean(axis=1) > 0.5            # bỏ trứng thiếu quá nửa dữ liệu
    idx = np.where(has)[0]
    labels_used = [labels[i] for i in idx]
    X = X[idx]
    keep = np.isfinite(X).any(axis=0)
    X, names = X[:, keep], [n for n, k in zip(names, keep) if k]
    g = [groups.get(l) for l in labels_used]
    out = {"task": task, "feats": feats, "ends": list(ends), "days": days, "day_batches": P["day_batches"],
           "da": da, "db": db, "dr": dr, "n_features": len(names), "labels": labels_used,
           "groups": g, "traj": {l: traj[l] for l in labels_used}, "excluded": [l for i, l in enumerate(labels) if not has[i]],
           "rank": None, "sup": None, "messages": []}

    # chỉ số phát triển (không dùng nhãn)
    Zd = _zscore(_impute(Xd[idx])) if Xd.shape[1] else None
    dev = (Zd * signs).mean(axis=1) if Zd is not None else np.full(len(idx), np.nan)
    out["dev"] = [float(v) for v in dev]

    if task == "infertile":
        y_all = [1 if x == "infertile" else 0 if x in FERTILE else None for x in g]
    else:
        y_all = [1 if x == "male" else 0 if x == "female" else None for x in g]
    pos = [i for i, v in enumerate(y_all) if v == 1]
    neg = [i for i, v in enumerate(y_all) if v == 0]
    out["n_pos"], out["n_neg"] = len(pos), len(neg)

    if task == "infertile":
        R = _reduce(_zscore(_impute(X)))
        sim = _similarity(R, pos, neg) if pos else np.full(len(idx), np.nan)
        pu = _pu_bagging(R, pos, neg) if pos else np.full(len(idx), np.nan)
        s_dev = _pct_rank(-dev)                      # phát triển ít = giống không phôi
        s_sim, s_pu = _pct_rank(sim), _pct_rank(pu)
        comb = np.nanmean(np.vstack([s_dev, s_sim, s_pu]), axis=0)
        order = np.argsort(-np.nan_to_num(comb, nan=-1))
        rank = np.empty(len(idx), int); rank[order] = np.arange(1, len(idx) + 1)
        f = lambda a: [None if not np.isfinite(v) else float(v) for v in a]
        out["rank"] = {"sim": f(sim), "pu": f(pu), "p_dev": f(s_dev), "p_sim": f(s_sim), "p_pu": f(s_pu),
                       "comb": f(comb), "rank": [int(r) for r in rank]}
        # kiểm chứng: trứng không phôi đã biết đứng thứ mấy theo chỉ số phát triển (không dùng nhãn)
        dev_rank = (-dev).argsort()[::-1].argsort() + 1
        out["check"] = [{"label": labels_used[p], "dev_rank": int(dev_rank[p]), "n": len(idx),
                         "comb_rank": int(rank[p])} for p in pos]
        if not pos:
            out["messages"].append("Chưa có trứng không phôi nào được gán — chỉ dùng chỉ số phát triển.")

    if len(pos) >= 3 and len(neg) >= 3:
        sup = supervised(X, y_all, kind=model, n_perm=n_perm)
        sup["score"] = [None if not np.isfinite(v) else float(v) for v in sup["score"]]
        out["sup"] = sup
    else:
        need = "không phôi và có phôi" if task == "infertile" else "Trống và Mái"
        out["messages"].append(f"Học có giám sát cần ≥ 3 trứng mỗi lớp ({need}); hiện có {len(pos)} / {len(neg)}.")
    return out
