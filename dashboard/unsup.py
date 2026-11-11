# -*- coding: utf-8 -*-
"""
Học không giám sát cho tab "Không giám sát". Client gửi ma trận X (trứng × biến) đã dựng sẵn
(phổ 1 batch / ΔS / quỹ đạo / đặc trưng); server chỉ chạy thuật toán scikit-learn và trả kết quả.

  Giảm chiều : PCA (kèm loadings, phương sai giải thích), t-SNE, MDS
  Phân cụm   : K-Means, Gaussian Mixture, phân cấp Ward (kèm dendrogram), DBSCAN
  Chọn k     : inertia (elbow), silhouette, BIC của GMM cho k = 2..kmax
  Bất thường : Isolation Forest, Local Outlier Factor
  Đối chiếu  : ARI / NMI và bảng chéo giữa cụm và nhóm đã gán (nếu có)
  Quét ngày  : scan() chạy cùng thuật toán cho từng ngày, từng cặp ngày và cặp ngày + Δ so với ngày tham chiếu
"""

import numpy as np


def _clean(X):
    X = np.asarray(X, dtype=float)
    # cột toàn NaN / hằng số bị bỏ; NaN còn lại thay bằng trung bình cột. Trả kèm chỉ số cột gốc được giữ.
    cols = np.where(np.isfinite(X).any(axis=0))[0]
    X = X[:, cols]
    mu = np.nanmean(X, axis=0)
    idx = np.where(~np.isfinite(X))
    X[idx] = np.take(mu, idx[1])
    ok = X.std(axis=0) > 1e-12
    return X[:, ok], cols[ok]


def _scale(X, scale):
    # "z" (hoặc True): z-score từng biến · "minmax": đưa từng biến về 0–1 · còn lại: chỉ trừ trung bình
    if scale is True or scale == "z":
        return (X - X.mean(axis=0)) / X.std(axis=0)
    if scale == "minmax":
        lo, hi = X.min(axis=0), X.max(axis=0)
        return (X - lo) / (hi - lo)
    return X - X.mean(axis=0)


def _reduce(Z, seed):
    from sklearn.decomposition import PCA
    n, p = Z.shape
    ncomp = min(10, n, p)
    pca = PCA(n_components=ncomp, random_state=seed).fit(Z)
    scores = pca.transform(Z)
    # phân cụm trên các PC giữ ≥ 95% phương sai (bớt nhiễu khi p >> n)
    cum = np.cumsum(pca.explained_variance_ratio_)
    nkeep = int(min(ncomp, np.searchsorted(cum, 0.95) + 1))
    F = scores[:, :max(2, nkeep)] if ncomp >= 2 else scores
    return pca, scores, F


def _cluster(F, method, k, eps, min_samples, seed):
    from sklearn.cluster import DBSCAN, AgglomerativeClustering, KMeans
    from sklearn.mixture import GaussianMixture
    from sklearn.neighbors import NearestNeighbors
    n = len(F)
    prob = None
    if method == "gmm":
        gm = GaussianMixture(n_components=k, covariance_type="diag", random_state=seed, n_init=5).fit(F)
        lab = gm.predict(F)
        prob = gm.predict_proba(F).max(axis=1)
    elif method == "ward":
        lab = AgglomerativeClustering(n_clusters=k, linkage="ward").fit_predict(F)
    elif method == "dbscan":
        if not eps:   # gợi ý eps = trung vị khoảng cách tới láng giềng thứ min_samples
            d, _ = NearestNeighbors(n_neighbors=min(min_samples, n - 1) + 1).fit(F).kneighbors(F)
            eps = float(np.median(d[:, -1]))
        lab = DBSCAN(eps=eps, min_samples=min_samples).fit_predict(F)
    else:
        lab = KMeans(n_clusters=k, n_init=20, random_state=seed).fit(F).labels_
    return lab, prob, eps


def _silhouette(F, lab):
    from sklearn.metrics import silhouette_samples, silhouette_score
    valid = lab >= 0
    nlab = len(set(lab[valid]))
    if nlab < 2 or valid.sum() <= nlab:
        return None, None
    sil_s = np.full(len(lab), np.nan)
    sil_s[valid] = silhouette_samples(F[valid], lab[valid])
    return float(silhouette_score(F[valid], lab[valid])), sil_s


def _agree(lab, groups):
    from sklearn.metrics import adjusted_rand_score, normalized_mutual_info_score
    if not groups:
        return None
    idx = [i for i, g in enumerate(groups) if g]
    if len(idx) < 4 or len(set(groups[i] for i in idx)) < 2:
        return None
    gi, li = [groups[i] for i in idx], [int(lab[i]) for i in idx]
    cats = sorted(set(gi)); cls = sorted(set(li))
    return {"ari": float(adjusted_rand_score(gi, li)), "nmi": float(normalized_mutual_info_score(gi, li)),
            "n": len(idx), "groups": cats, "clusters": cls,
            "table": [[sum(1 for g, c in zip(gi, li) if g == G and c == Cc) for Cc in cls] for G in cats]}


def scan(days, configs, groups=None, k=3, method="kmeans", scale="z", eps=None, min_samples=3, seed=42):
    """days: {ngày: [vector của từng trứng hoặc None]} (cùng thứ tự trứng với groups).
    configs: [{name, blocks: [[a, r], ...]}] — mỗi khối = phổ ngày a, hoặc hiệu ngày a − ngày r nếu r khác None.
    Mỗi cấu hình chỉ dùng các trứng có đủ mọi ngày cần thiết. Trả silhouette, ARI/NMI, PC1–PC2 và nhãn cụm."""
    out = []
    n_all = len(next(iter(days.values())))
    for c in configs:
        try:
            need = {d for b in c["blocks"] for d in b if d is not None}
            rows = [i for i in range(n_all) if all(days[d][i] is not None for d in need)]
            X = [np.concatenate([np.asarray(days[a][i], float) - (np.asarray(days[r][i], float) if r is not None else 0)
                                 for a, r in c["blocks"]]) for i in rows]
            X, _ = _clean(X)
            n, p = X.shape
            if n < 4 or p < 1:
                raise ValueError("cần ít nhất 4 trứng có dữ liệu")
            _, scores, F = _reduce(_scale(X, scale), seed)
            lab, _, _ = _cluster(F, method, int(max(2, min(k, n - 1))), eps, min_samples, seed)
            sil, _ = _silhouette(F, lab)
            ag = _agree(lab, [groups[i] for i in rows] if groups else None)
            emb = scores[:, :2] if scores.shape[1] >= 2 else np.c_[scores[:, 0], np.zeros(n)]
            out.append({"name": c.get("name"), "n": n, "p": p, "rows": rows, "silhouette": sil,
                        "ari": ag["ari"] if ag else None, "nmi": ag["nmi"] if ag else None,
                        "embedding": emb.tolist(), "labels": [int(x) for x in lab]})
        except Exception as ex:
            out.append({"name": c.get("name"), "error": str(ex)})
    return out


def run(X, k=3, method="kmeans", embed="pca", scale="z", eps=None, min_samples=3, groups=None, seed=42):
    from scipy.cluster.hierarchy import dendrogram, linkage
    from sklearn.cluster import AgglomerativeClustering, KMeans
    from sklearn.ensemble import IsolationForest
    from sklearn.manifold import MDS, TSNE
    from sklearn.metrics import silhouette_score
    from sklearn.mixture import GaussianMixture
    from sklearn.neighbors import LocalOutlierFactor

    X, cols = _clean(X)
    n, p = X.shape
    if n < 4 or p < 1:
        raise ValueError("cần ít nhất 4 trứng có dữ liệu và 1 biến")
    Z = _scale(X, scale)
    k = int(max(2, min(k, n - 1)))

    # ---------- giảm chiều ----------
    pca, scores, F = _reduce(Z, seed)
    ncomp = scores.shape[1]
    if embed == "tsne":
        emb = TSNE(n_components=2, perplexity=max(2.0, min(10.0, (n - 1) / 3)), init="pca",
                   random_state=seed).fit_transform(Z)
    elif embed == "mds":
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            emb = MDS(n_components=2, random_state=seed, n_init=4).fit_transform(Z)
    else:
        emb = scores[:, :2] if ncomp >= 2 else np.c_[scores[:, 0], np.zeros(n)]

    # ---------- phân cụm ----------
    lab, prob, eps = _cluster(F, method, k, eps, min_samples, seed)
    sil, sil_s = _silhouette(F, lab)

    # ---------- chọn k ----------
    kscan = []
    for kk in range(2, min(8, n - 1) + 1):
        km = KMeans(n_clusters=kk, n_init=10, random_state=seed).fit(F)
        row = {"k": kk, "inertia": float(km.inertia_), "sil_kmeans": float(silhouette_score(F, km.labels_))}
        try:
            row["bic"] = float(GaussianMixture(n_components=kk, covariance_type="diag", random_state=seed).fit(F).bic(F))
        except Exception:
            row["bic"] = None
        wl = AgglomerativeClustering(n_clusters=kk, linkage="ward").fit_predict(F)
        row["sil_ward"] = float(silhouette_score(F, wl))
        kscan.append(row)

    # ---------- dendrogram (Ward) ----------
    L = linkage(F, method="ward")
    dg = dendrogram(L, no_plot=True)
    dendro = {"icoord": dg["icoord"], "dcoord": dg["dcoord"], "leaves": [int(i) for i in dg["leaves"]],
              "cut": float(L[-(k - 1), 2] + L[-k, 2]) / 2 if n > k else None}

    # ---------- bất thường ----------
    iso = -IsolationForest(n_estimators=300, random_state=seed).fit(F).score_samples(F)   # cao = bất thường
    lof = LocalOutlierFactor(n_neighbors=min(5, n - 1))
    lof.fit(F)
    lof_s = -lof.negative_outlier_factor_                                                   # > 1.5 ~ bất thường

    # ---------- đối chiếu với nhóm đã gán ----------
    agree = _agree(lab, groups)

    nz = lambda a: None if a is None else [None if not np.isfinite(v) else float(v) for v in a]
    return {
        "n": n, "p": p, "k": k, "method": method, "embed": embed, "eps": eps, "n_pc_used": int(F.shape[1]),
        "embedding": emb.tolist(), "labels": [int(x) for x in lab], "prob": nz(prob),
        "silhouette": sil, "silhouette_samples": nz(sil_s),
        "pca": {"var": pca.explained_variance_ratio_.tolist(), "loadings": pca.components_[:2].tolist(), "cols": cols.tolist()},
        "kscan": kscan, "dendro": dendro,
        "anomaly": {"iforest": iso.tolist(), "lof": lof_s.tolist()},
        "agree": agree,
    }
