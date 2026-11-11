# -*- coding: utf-8 -*-
"""
Tab "Tình trạng": nhật ký tình trạng từng trứng (soi trứng, chết phôi, nở, nở rồi chết, ...) + cờ tín hiệu phổ tự động.

Nhật ký lưu ở outputs/egg_status/<session>.json (KHÔNG phải cache), ảnh đính kèm ở outputs/egg_status/<session>_files/.
Mỗi sự kiện: {id, label, date (YYYY-MM-DD), status, note, photo, created}. Tình trạng hiện tại = sự kiện có
ngày muộn nhất (cùng ngày thì sự kiện tạo sau).

Tín hiệu phổ (giống src/egg_anomaly.py): mỗi batch + đầu đo, so với trung vị đàn, trừ baseline 2 batch đầu
của chính trứng đó, rồi robust z giữa các trứng:
    rel = log10 S(740–865)                 z ≥ +4  -> sáng bất thường (ngừng phát triển hoặc phổ "jump")
    rn  = log10 S(665–700) / S(740–865)    z ≤ −6  -> đỏ bị hấp thụ mạnh (pattern trứng 29: phôi chết, máu ứ đọng)
"""

import json
import os
import re
import threading
import uuid
from datetime import datetime

import numpy as np

from analysis import _darksub, _grid

STATUSES = ("ok", "watch", "abnormal", "dead", "infertile", "hatched", "hatched_dead", "removed")
BANDS = {"nir": (740, 865), "red": (665, 700)}
FLAG = {"rn": -6.0, "rel": 4.0}
WATCH = {"rn": -3.0, "rel": 3.0}
PHOTO_EXT = (".jpg", ".jpeg", ".png", ".webp")
_lock = threading.RLock()
_SIG_CACHE = {}


def _safe(session):
    return re.sub(r"[^\w.-]+", "_", session)


def _path(root, session):
    return os.path.join(root, _safe(session) + ".json")


def files_dir(root, session):
    return os.path.join(root, _safe(session) + "_files")


def load(root, session):
    try:
        with open(_path(root, session), encoding="utf-8") as f:
            return json.load(f).get("events", [])
    except (OSError, ValueError):
        return []


def _write(root, session, events):
    os.makedirs(root, exist_ok=True)
    events = sorted(events, key=lambda e: (e["label"], e["date"], e["created"]))
    tmp = _path(root, session) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"session": session, "updated": datetime.now().isoformat(timespec="seconds"), "events": events},
                  f, ensure_ascii=False, indent=1)
    os.replace(tmp, _path(root, session))
    return events


def add(root, session, ev):
    label = str(ev.get("label") or "").strip()
    status = ev.get("status")
    date = str(ev.get("date") or "")
    if not label or status not in STATUSES:
        raise ValueError("thiếu trứng hoặc tình trạng không hợp lệ")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise ValueError("ngày không hợp lệ")
    photo = ev.get("photo") or None
    if photo and (os.path.basename(photo) != photo or not photo.lower().endswith(PHOTO_EXT)):
        raise ValueError("ảnh không hợp lệ")
    rec = {"id": uuid.uuid4().hex[:10], "label": label, "date": date, "status": status,
           "note": str(ev.get("note") or "").strip()[:4000], "photo": photo,
           "created": datetime.now().isoformat(timespec="seconds")}
    with _lock:
        return _write(root, session, load(root, session) + [rec])


def delete(root, session, ev_id):
    with _lock:
        events = load(root, session)
        keep = [e for e in events if e["id"] != ev_id]
        if len(keep) == len(events):
            raise KeyError("không có sự kiện này")
        return _write(root, session, keep)


def hatched(root, session):
    """{label: sự kiện} của các trứng có tình trạng hiện tại = "hatched" (đã nở, còn sống)."""
    cur = {}
    for e in sorted(load(root, session), key=lambda e: (e["date"], e["created"])):
        cur[e["label"]] = e
    return {l: e for l, e in cur.items() if e["status"] == "hatched"}


def save_photo(root, session, name, data):
    ext = os.path.splitext(name or "")[1].lower()
    if ext not in PHOTO_EXT:
        raise ValueError("chỉ nhận ảnh jpg / png / webp")
    if len(data) > 15 * 1024 * 1024:
        raise ValueError("ảnh quá lớn (> 15 MB)")
    d = files_dir(root, session)
    os.makedirs(d, exist_ok=True)
    fn = datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6] + ext
    with open(os.path.join(d, fn), "wb") as f:
        f.write(data)
    return fn


def photo_path(root, session, fn):
    if os.path.basename(fn) != fn or not fn.lower().endswith(PHOTO_EXT):
        raise ValueError("ảnh không hợp lệ")
    p = os.path.join(files_dir(root, session), fn)
    if not os.path.isfile(p):
        raise FileNotFoundError("không có ảnh")
    return p


# ----------------------------------------------------------------------------
# Tín hiệu phổ
# ----------------------------------------------------------------------------

def _rz(x):
    x = np.asarray(x, float)
    med = np.nanmedian(x)
    mad = 1.4826 * np.nanmedian(np.abs(x - med))
    return (x - med) / mad if mad > 0 else np.full_like(x, np.nan)


def signals(store):
    """-> {label: [{batch_no, folder, age_h, blunt: {...}, pointed: {...}}]}, kèm cờ."""
    key = (store.id, store.signature())
    if key in _SIG_CACHE:
        return _SIG_CACHE[key]
    batches = store.batches()
    grid = _grid(store, batches)
    if grid is None:
        return {"labels": {}, "batches": []}
    m = {k: (grid >= a) & (grid <= c) for k, (a, c) in BANDS.items()}
    raw = {}            # (bi, end) -> {label: (rel, rn)}
    for bi, b in enumerate(batches):
        for end in ("blunt", "pointed"):
            vals = {}
            for label, e in b["eggs"].items():
                if not e.get(end):
                    continue
                y = _darksub(store, b, e[end][-1], grid)
                if y is None:
                    continue
                nir, red = np.mean(y[m["nir"]]), np.mean(y[m["red"]])
                if nir > 0 and red > 0:
                    vals[label] = (np.log10(nir), np.log10(red / nir))
            if vals:
                raw[(bi, end)] = vals

    # so với đàn trong cùng batch/đầu
    rel = {}
    for k, vals in raw.items():
        labs = list(vals)
        arr = np.array([vals[l] for l in labs])
        arr = arr - np.median(arr, axis=0)
        rel[k] = dict(zip(labs, arr))
    # baseline = trung bình 2 batch đầu có dữ liệu của chính trứng
    out, labels = {}, sorted({l for v in raw.values() for l in v})
    base = {}
    for end in ("blunt", "pointed"):
        bis = sorted(bi for (bi, e) in rel if e == end)[:2]
        for l in labels:
            xs = [rel[(bi, end)][l] for bi in bis if l in rel[(bi, end)]]
            if xs:
                base[(l, end)] = np.mean(xs, axis=0)
    zs = {}
    for (bi, end), vals in rel.items():
        labs = [l for l in vals if (l, end) in base]
        d = np.array([vals[l] - base[(l, end)] for l in labs])
        if len(labs) < 5:
            continue
        z_rel, z_rn = _rz(d[:, 0]), _rz(d[:, 1])
        for i, l in enumerate(labs):
            zs[(bi, end, l)] = (float(d[i, 0]), float(d[i, 1]), float(z_rel[i]), float(z_rn[i]))

    def lvl(zr, zn):
        if (np.isfinite(zn) and zn <= FLAG["rn"]) or (np.isfinite(zr) and zr >= FLAG["rel"]):
            return 2
        if (np.isfinite(zn) and zn <= WATCH["rn"]) or (np.isfinite(zr) and zr >= WATCH["rel"]):
            return 1
        return 0

    for l in labels:
        rows, worst, last = [], 0, None
        for bi, b in enumerate(batches):
            row = {"batch_no": b["batch_no"], "folder": b["folder"], "age_h": b["age_h"]}
            for end in ("blunt", "pointed"):
                v = zs.get((bi, end, l))
                if v:
                    f = lvl(v[2], v[3])
                    row[end] = {"rel": round(v[0], 4), "rn": round(v[1], 4),
                                "z_rel": round(v[2], 2) if np.isfinite(v[2]) else None,
                                "z_rn": round(v[3], 2) if np.isfinite(v[3]) else None, "flag": f}
                    worst = max(worst, f)
                    if f:
                        last = {"batch_no": b["batch_no"], "end": end, "level": f}
            rows.append(row)
        # cờ của batch mới nhất có dữ liệu
        latest = next((max((r[e]["flag"] for e in ("blunt", "pointed") if e in r), default=0)
                       for r in reversed(rows) if "blunt" in r or "pointed" in r), 0)
        out[l] = {"rows": rows, "worst": worst, "latest": latest, "last_flag": last}
    res = {"labels": out, "batches": [{"batch_no": b["batch_no"], "folder": b["folder"], "age_h": b["age_h"]} for b in batches],
           "thresholds": {"flag": FLAG, "watch": WATCH}}
    _SIG_CACHE.clear()
    _SIG_CACHE[key] = res
    return res


# ----------------------------------------------------------------------------
# Thư viện pattern phổ (dashboard/patterns.json): dấu hiệu nhận biết + luật so khớp tự động
# ----------------------------------------------------------------------------
PATTERNS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "patterns.json")


def load_patterns():
    try:
        with open(PATTERNS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def _match(rule, rows):
    """-> dict chi tiết nếu khớp, None nếu không. rows = tín hiệu theo batch của 1 trứng."""
    data = [r for r in rows if "blunt" in r or "pointed" in r]
    if rule["kind"] == "red_drop":
        last = data[-rule["last"]:]
        if len(last) < rule["last"]:
            return None
        zs = [r[e]["z_rn"] for r in last for e in ("blunt", "pointed") if r.get(e) and r[e]["z_rn"] is not None]
        hits = sum(z <= rule["z"] for z in zs)
        if hits < rule["min_hits"] or min(zs) > rule["z_strong"]:
            return None
        before = [r[e]["z_rn"] for r in data[:-rule["last"]] for e in ("blunt", "pointed")
                  if r.get(e) and r[e]["z_rn"] is not None]
        # đột ngột: trước 2 batch cuối chưa từng lệch; từ từ: đã lệch từ trước
        # batch bắt đầu chuỗi lệch liên tục kéo tới batch cuối
        onset = None
        for r in reversed(data):
            if not any(r.get(e) and (r[e]["z_rn"] or 0) <= rule["z"] for e in ("blunt", "pointed")):
                break
            onset = r["batch_no"]
        return {"variant": "gradual" if any(z <= rule["z"] for z in before[-4:]) else "abrupt",
                "hits": f"{hits}/{len(zs)}", "min_z": round(min(zs), 1), "onset": onset,
                "batches": [r["batch_no"] for r in last]}
    if rule["kind"] == "jump":
        for r in reversed(data):
            for e in ("blunt", "pointed"):
                v = r.get(e)
                if v and v["z_rel"] is not None and v["z_rel"] >= rule["z_rel"] and abs(v["z_rn"] or 0) < rule["z_rn_abs"]:
                    return {"batch": r["batch_no"], "end": e, "z_rel": round(v["z_rel"], 1)}
        return None
    return None


def pattern_matches(sig):
    """-> {pattern_id: {label: chi tiết}}"""
    out = {}
    for p in load_patterns():
        m = {}
        for label, v in sig.get("labels", {}).items():
            r = _match(p["rule"], v["rows"])
            if r:
                m[label] = r
        out[p["id"]] = m
    return out
