"""
Hình dạng trứng cho tab "Hình dạng": đọc CSV kích thước (src/egg_size.py) trong <session>/outputs_shape/*.csv.

CSV chỉ có chiều dài L và chiều rộng B (area_mm2 = πLB/4 chỉ là diện tích ellipse), nên đường viền được dựng ở
client bằng mô hình Hügelschäffer (Narushin et al. 2021):
    y(x) = ± B/2 · sqrt((L² − 4x²) / (L² + 8wx + 4w²)),   w = (w/L) · L
w/L ước lượng từ đường viền thật của ảnh hiệu chuẩn (outputs_shape/egg_size/captures) nếu có OpenCV + scipy,
không thì dùng WL_DEFAULT. Server chỉ đọc số đo, bỏ lần đo lệch và lấy trung vị mỗi trứng.
"""

import csv
import glob
import json
import os
import re
import sys
from datetime import datetime

import numpy as np

SHAPE_DIR = "outputs_shape"
WL_DEFAULT = 0.046        # trung vị w/L của 5 trứng hiệu chuẩn 14/10/2026 (0.034–0.074)
TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2})__(\d{2})-(\d{2})-(\d{2})")
SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")


def shape_dir(store):
    """Thư mục outputs_shape của session (chỉ nguồn thư mục)."""
    d = getattr(store, "dir", None)
    p = os.path.join(d, SHAPE_DIR) if d else None
    return p if p and os.path.isdir(p) else None


def _num(v):
    try:
        x = float(v)
        return x if np.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _ts(path):
    m = TS_RE.search(os.path.basename(path or ""))
    return f"{m.group(1)}T{m.group(2)}:{m.group(3)}:{m.group(4)}" if m else None


def _hours(ts, start):
    try:
        return (datetime.fromisoformat(ts) - datetime.fromisoformat(start)).total_seconds() / 3600
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------------------
# w/L từ ảnh hiệu chuẩn
# ----------------------------------------------------------------------------
def _hug_y(x, L, B, w):
    return B / 2 * np.sqrt(np.clip(L ** 2 - 4 * x ** 2, 0, None) / (L ** 2 + 8 * w * x + 4 * w ** 2))


def _fit_one(es, path):
    from scipy.optimize import least_squares
    m = es.measure_px(es.read_image(path))
    if m is None:
        return None
    p = m["contour"].reshape(-1, 2).astype(float)
    (cx, cy), (d1, d2), ang = m["ellipse"]
    t = np.deg2rad(ang) + (np.pi / 2 if d2 > d1 else 0)
    q = (p - [cx, cy]) @ np.array([[np.cos(t), np.sin(t)], [-np.sin(t), np.cos(t)]]).T
    L0, B0 = max(d1, d2), min(d1, d2)

    def uv(par):
        x0, y0, th = par[:3]
        c, s = np.cos(th), np.sin(th)
        return (q[:, 0] - x0) * c + (q[:, 1] - y0) * s, -(q[:, 0] - x0) * s + (q[:, 1] - y0) * c

    def res(par):
        u, v = uv(par)
        return np.abs(v) - _hug_y(u, *par[3:])

    r = min((least_squares(res, [0, 0, 0, L0, B0, w0], loss="soft_l1", f_scale=3) for w0 in (-0.05 * L0, 0.05 * L0)),
            key=lambda r: r.cost)
    L, B, w = r.x[3:]
    u, v = uv(r.x)
    return {"file": os.path.basename(path), "wL": round(float(abs(w) / L), 4),
            "err_hug_px": round(float(np.median(np.abs(r.fun))), 2),
            "err_ellipse_px": round(float(np.median(np.abs(np.abs(v) - _hug_y(u, L, B, 0)))), 2)}


def fit_wl(sdir, cache_dir):
    """w/L = trung vị trên các ảnh hiệu chuẩn; kết quả lưu cache theo tên + mtime + kích thước ảnh."""
    caps = sorted(glob.glob(os.path.join(sdir, "egg_size", "captures", "*.png")))
    out = {"wL": WL_DEFAULT, "source": "default", "fits": []}
    if not caps:
        return out
    sig = [[os.path.basename(c), os.path.getmtime(c), os.path.getsize(c)] for c in caps]
    cpath = os.path.join(cache_dir, "shape_fit_" + re.sub(r"[^\w.-]+", "_", os.path.abspath(sdir))[-80:] + ".json")
    try:
        with open(cpath, encoding="utf-8") as f:
            hit = json.load(f)
        if hit.get("sig") == sig:
            return hit["fit"]
    except (OSError, ValueError):
        pass
    try:
        if SRC_DIR not in sys.path:
            sys.path.insert(0, SRC_DIR)
        import egg_size as es
        fits = [x for x in (_fit_one(es, c) for c in caps) if x]
    except Exception as ex:  # thiếu OpenCV / scipy -> dùng mặc định
        return {**out, "error": repr(ex)}
    if fits:
        out = {"wL": round(float(np.median([x["wL"] for x in fits])), 4), "source": "captures", "fits": fits}
    os.makedirs(cache_dir, exist_ok=True)
    with open(cpath, "w", encoding="utf-8") as f:
        json.dump({"sig": sig, "fit": out}, f, ensure_ascii=False)
    return out


# ----------------------------------------------------------------------------
# Số đo -> profile từng trứng
# ----------------------------------------------------------------------------
def summary(store, batches, cache_dir):
    sdir = shape_dir(store)
    if not sdir:
        return {"available": False}
    start = store.info().get("incubation_start")
    width = max((len(l) for b in batches for l in b["eggs"]), default=4)
    meas, sets = [], []
    for path in sorted(glob.glob(os.path.join(sdir, "*.csv"))):
        name = os.path.splitext(os.path.basename(path))[0]
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = [r for r in csv.DictReader(f) if "length_mm" in r and "egg_label" in r]
        if not rows:
            continue
        tss = []
        for r in rows:
            lab = (r.get("egg_label") or "").strip()
            if not lab.isdigit():
                continue
            ts = _ts(r.get("file"))
            tss.append(ts)
            meas.append({"set": name, "label": lab.zfill(width), "status": (r.get("status") or "").strip(),
                         "L": _num(r.get("length_mm")), "B": _num(r.get("width_mm")), "ts": ts, "age_h": _hours(ts, start)})
        ts0 = min((t for t in tss if t), default=None)
        # batch phổ đo gần nhất (≤ 3 h) để đối chiếu, vd b01 (08/10 11h) ↔ batch_02
        near = None
        if ts0:
            d = [(abs(_hours(ts0, b["started"]) or 1e9), b["batch_no"]) for b in batches if b.get("started")]
            near = min(d)[1] if d and min(d)[0] <= 3 else None
        sets.append({"name": name, "ts": ts0, "age_h": _hours(ts0, start), "n": len(rows), "batch_no": near})

    # bỏ lần đo lệch khỏi trung vị của chính trứng đó > max(3·MAD, 1 mm) (tách nền sai)
    ok = [m for m in meas if m["status"] == "ok" and m["L"] and m["B"]]
    eggs = {}
    for lab in sorted({m["label"] for m in meas}):
        mine = [m for m in ok if m["label"] == lab]
        use = mine
        for k in ("L", "B"):
            if len(use) >= 3:
                v = np.array([m[k] for m in mine])
                med = np.median(v)
                mad = 1.4826 * np.median(np.abs(v - med))
                bad = {id(m) for m, x in zip(mine, v) if abs(x - med) > max(3 * mad, 1.0)}
                use = [m for m in use if id(m) not in bad]
        for m in mine:
            m["used"] = any(m is u for u in use)
        if not use:
            continue
        L, B = (np.array([m[k] for m in use]) for k in ("L", "B"))
        eggs[lab] = {"L": round(float(np.median(L)), 3), "B": round(float(np.median(B)), 3),
                     "L_sd": round(float(L.std(ddof=1)), 3) if len(L) > 1 else None,
                     "B_sd": round(float(B.std(ddof=1)), 3) if len(B) > 1 else None,
                     "n_used": len(use), "n_total": sum(m["label"] == lab for m in meas)}
    for m in meas:
        m.setdefault("used", False)
    return {"available": True, "dir": sdir, "sets": sets, "meas": meas, "eggs": eggs,
            "fit": fit_wl(sdir, cache_dir), "wl_default": WL_DEFAULT}


# ----------------------------------------------------------------------------
# Từ pixel ảnh: vi phân -> mép trứng -> tích phân (không giả định mô hình hình dạng)
# ----------------------------------------------------------------------------
# 1) Mặt nạ GrabCut (egg_size.find_egg) cho tâm và bán kính tiên nghiệm r₀(θ).
# 2) Vi phân: theo NR tia từ tâm, lấy profile I(r) của ảnh R − B (làm mượt σ = 2 px) trong ±8% quanh r₀(θ),
#    tính dI/dr; mép = chỗ dI/dr âm nhất (trứng đỏ -> nền xanh), nội suy parabol để có độ chính xác dưới pixel.
# 3) Bỏ điểm mép lệch khỏi trung vị trượt, làm trơn r(θ) bằng chuỗi Fourier bậc NK (cho cả dr/dθ giải tích).
# 4) Đổi sang mm theo hệ trục trứng (PCA; hệ số hiệu chuẩn riêng cho trục dài / trục ngắn), đầu tù về bên trái.
# 5) Tích phân trên đường viền: chu vi ∮ds, diện tích ½∮(x dy − y dx), thể tích π∫y²dx và diện tích vỏ 2π∫y ds
#    với y(x) = nửa bề rộng tại x (giả định trứng tròn xoay quanh trục dài).
NR, NK, PX_VER = 720, 12, 2
PROF_DEG = (0, 90, 180, 270)         # các tia lưu lại profile để vẽ minh hoạ phần vi phân


def _calib(sdir):
    try:
        with open(os.path.join(sdir, "egg_size", "calibration.json"), encoding="utf-8") as f:
            c = json.load(f)
        return {"kL": c["mm_per_px_length"], "kW": c["mm_per_px_width"], "size": c.get("image_size")}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _fourier(th, r, K):
    A = np.column_stack([np.ones_like(th)] + [f(k * th) for k in range(1, K + 1) for f in (np.cos, np.sin)])
    return np.linalg.lstsq(A, r, rcond=None)[0]


def _feval(c, th, K, deriv=False):
    out = np.zeros_like(th) if deriv else np.full_like(th, c[0])
    for k in range(1, K + 1):
        a, b = c[2 * k - 1], c[2 * k]
        out += k * (b * np.cos(k * th) - a * np.sin(k * th)) if deriv else a * np.cos(k * th) + b * np.sin(k * th)
    return out


def _halves(u, v):
    """Tách đường viền khép kín (lồi) thành 2 nửa tại 2 đầu trục dài -> nửa bề rộng y(x) trên lưới x."""
    i0, i1 = int(np.argmin(u)), int(np.argmax(u))
    n = len(u)
    a = np.r_[i0:i1 + 1] if i0 <= i1 else np.r_[i0:n, 0:i1 + 1]
    b = np.r_[i1:i0 + 1] if i1 <= i0 else np.r_[i1:n, 0:i0 + 1]
    x = np.linspace(u[i0], u[i1], 801)

    def chain(ix):
        o = np.argsort(u[ix], kind="stable")
        return np.interp(x, u[ix][o], v[ix][o])
    return x, np.abs(chain(a) - chain(b)) / 2


def _hug_fit(x, y, L):
    """Khớp Hügelschäffer (B, w, dịch x0) vào nửa bề rộng đo được, L cố định -> w/L riêng của quả trứng."""
    try:
        from scipy.optimize import least_squares
    except ImportError:
        return None
    r = least_squares(lambda p: _hug_y(x - p[2], L, p[0], p[1]) - y, [2 * y.max(), 0.04 * L, 0.0])
    return {"B": float(r.x[0]), "wL": float(r.x[1] / L), "rms_mm": float(np.sqrt(np.mean(r.fun ** 2)))}


def _read_bgr(src):
    import cv2
    if isinstance(src, str):
        img = cv2.imread(src, cv2.IMREAD_COLOR)
    else:
        img = cv2.imdecode(np.frombuffer(src.getvalue(), np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("không đọc được ảnh")
    return img


def pixel_contour(img, cal, jpg_path=None):
    import cv2
    if SRC_DIR not in sys.path:
        sys.path.insert(0, SRC_DIR)
    import egg_size as es
    cnt = es.find_egg(img)
    if cnt is None:
        raise ValueError("không tìm thấy trứng trong ảnh")
    cnt = cnt.reshape(-1, 2).astype(float)
    cx, cy = cnt.mean(0)
    tc, rc = np.arctan2(cnt[:, 1] - cy, cnt[:, 0] - cx), np.hypot(cnt[:, 0] - cx, cnt[:, 1] - cy)
    o = np.argsort(tc)
    tc, rc = tc[o], rc[o]
    th = np.linspace(-np.pi, np.pi, NR, endpoint=False)
    r0 = np.interp(th, np.r_[tc - 2 * np.pi, tc, tc + 2 * np.pi], np.r_[rc, rc, rc])
    # vi phân theo bán kính trên ảnh R − B
    b, _, rch = cv2.split(img.astype(np.float32))
    red = cv2.GaussianBlur(rch - b, (0, 0), 2.0)
    s = np.linspace(-0.08, 0.08, 161)
    R = r0[:, None] * (1 + s[None, :])
    X = (cx + np.cos(th)[:, None] * R).astype(np.float32)
    Y = (cy + np.sin(th)[:, None] * R).astype(np.float32)
    prof = cv2.remap(red, X, Y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    dI = np.gradient(prof, axis=1) / np.gradient(R, axis=1)
    j = np.clip(np.argmin(dI, axis=1), 1, len(s) - 2)
    i = np.arange(NR)
    y0, y1, y2 = dI[i, j - 1], dI[i, j], dI[i, j + 1]
    den = y0 - 2 * y1 + y2
    off = np.clip(np.where(np.abs(den) > 1e-9, 0.5 * (y0 - y2) / np.where(den == 0, 1, den), 0), -1, 1)
    re = R[i, j] + off * (R[i, j + 1] - R[i, j])
    strength = -y1
    med = np.array([np.median(np.take(re, range(k - 7, k + 8), mode="wrap")) for k in range(NR)])
    mad = 1.4826 * np.median(np.abs(re - med))
    ok = (np.abs(re - med) < max(3 * mad, 3.0)) & (strength > 0.25 * np.median(strength))
    c = _fourier(th[ok], re[ok], NK)
    tt = np.linspace(-np.pi, np.pi, 2000, endpoint=False)
    rf = _feval(c, tt, NK)
    px, py = cx + rf * np.cos(tt), cy + rf * np.sin(tt)
    # hệ trục trứng (PCA), mm; đầu tù (phần rộng nhất lệch về) bên trái
    P = np.column_stack([px, py])
    c0 = P.mean(0)
    evec = np.linalg.eigh(np.cov((P - c0).T))[1]
    maj = evec[:, 1]
    mnr = np.array([-maj[1], maj[0]])
    u, v = (P - c0) @ maj * cal["kL"], (P - c0) @ mnr * cal["kW"]
    x, y = _halves(u, v)
    if x[np.argmax(y)] > (x[0] + x[-1]) / 2:
        u = -u
        x, y = _halves(u, v)
    xm = (x[0] + x[-1]) / 2
    u, x = u - xm, x - xm
    un, vn = np.roll(u, -1), np.roll(v, -1)
    L, B = float(u.max() - u.min()), float(v.max() - v.min())
    dy = np.gradient(y, x)
    out = {"L": L, "B": B, "SI": 100 * B / L,
           "P": float(np.hypot(un - u, vn - v).sum()), "A": float(abs(0.5 * np.sum(u * vn - un * v))),
           "V": float(np.pi * np.trapezoid(y ** 2, x) / 1000),
           "S": float(2 * np.pi * np.trapezoid(y * np.sqrt(1 + np.clip(dy, -50, 50) ** 2), x) / 100),
           "xb": float(x[np.argmax(y)]), "ok_frac": float(ok.mean()), "hug": _hug_fit(x, y, L),
           "contour": {"u": np.round(u[::5], 3).tolist(), "v": np.round(v[::5], 3).tolist()},
           "half": {"x": np.round(x[::4], 3).tolist(), "y": np.round(y[::4], 3).tolist()},
           "polar": {"th": np.round(np.degrees(th), 2).tolist(), "r": np.round(re, 2).tolist(), "ok": ok.astype(int).tolist(),
                     "fit": np.round(_feval(c, th, NK), 2).tolist(), "drdth": np.round(_feval(c, th, NK, True), 2).tolist()},
           "profiles": []}
    for deg in PROF_DEG:
        k = int(np.argmin(np.abs(np.degrees(th) - (deg if deg <= 180 else deg - 360))))
        out["profiles"].append({"deg": deg, "dr": np.round(R[k] - re[k], 2).tolist(), "I": np.round(prof[k], 1).tolist(),
                                "dI": np.round(dI[k], 2).tolist()})
    if jpg_path:          # ảnh cắt quanh trứng: chấm xanh lá = mép từ vi phân, đỏ = bị loại, vàng = chuỗi Fourier
        vis = img.copy()
        cv2.polylines(vis, [np.round(P).astype(np.int32).reshape(-1, 1, 2)], True, (0, 230, 255), 3)
        for a, bb, k in zip(cx + re * np.cos(th), cy + re * np.sin(th), ok):
            cv2.circle(vis, (int(a), int(bb)), 4, (60, 220, 60) if k else (40, 40, 255), -1)
        hx, hy = int(np.ptp(px) * 0.6), int(np.ptp(py) * 0.6)
        x0, y0 = max(0, int(cx) - hx), max(0, int(cy) - hy)
        crop = vis[y0:int(cy) + hy, x0:int(cx) + hx]
        crop = cv2.resize(crop, (900, int(900 * crop.shape[0] / crop.shape[1])), interpolation=cv2.INTER_AREA)
        os.makedirs(os.path.dirname(jpg_path), exist_ok=True)
        cv2.imwrite(jpg_path, crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return out


def _px_base(store, folder, key, cache_dir):
    return os.path.join(cache_dir, store.id, "shape_px", re.sub(r"[^\w.-]+", "_", f"{folder}_{key}"))


def pixel_egg(store, batches, label, cache_dir):
    """Đường viền từ pixel cho mọi ảnh của 1 trứng (song song, cache theo ảnh)."""
    from concurrent.futures import ThreadPoolExecutor
    sdir = shape_dir(store)
    cal = _calib(sdir) if sdir else None
    if not cal:
        return {"available": False}
    start = store.info().get("incubation_start")
    jobs = [(b, b["images"][label]) for b in batches if label in b["images"]]

    def one(job):
        b, im = job
        base = _px_base(store, b["folder"], im["key"], cache_dir)
        res = {"batch_no": b["batch_no"], "folder": b["folder"], "key": im["key"], "ts": im.get("ts"),
               "age_h": _hours(im.get("ts"), start)}
        try:
            src, mtime = store.image(b, im["key"])
            try:
                with open(base + ".json", encoding="utf-8") as f:
                    hit = json.load(f)
                if hit.get("ver") == PX_VER and hit.get("mtime") == mtime and hit.get("cal") == cal and os.path.isfile(base + ".jpg"):
                    return {**res, **hit["data"]}
            except (OSError, ValueError):
                pass
            img = _read_bgr(src)
            if cal["size"] and [img.shape[1], img.shape[0]] != list(cal["size"]):
                return {**res, "error": f"ảnh {img.shape[1]}x{img.shape[0]} khác độ phân giải lúc hiệu chuẩn"}
            data = pixel_contour(img, cal, base + ".jpg")
            os.makedirs(os.path.dirname(base), exist_ok=True)
            with open(base + ".json", "w", encoding="utf-8") as f:
                json.dump({"ver": PX_VER, "mtime": mtime, "cal": cal, "data": data}, f)
            return {**res, **data}
        except Exception as ex:  # noqa - lỗi 1 ảnh không làm hỏng cả trứng
            return {**res, "error": str(ex)}

    with ThreadPoolExecutor(4) as ex:
        rows = list(ex.map(one, jobs))
    return {"available": True, "label": label, "calib": cal, "images": rows}
