# -*- coding: utf-8 -*-
"""
Nguồn dữ liệu cho dashboard. Mỗi nguồn = 1 session thí nghiệm, đến từ:

  FolderStore : session đã được upload vào thư mục lưu trữ (mặc định data/BATCH_B)
                <root>/<session>/session_info.json
                <root>/<session>/batch_XX_<ts>/metadata_log.csv + {dark,lamp,embryo}_*.csv
                <root>/<session>/batch_XX_<ts>/<thư mục ảnh>/<N>__YYYY-MM-DD__HH-MM-SS.bmp

  MongoStore  : session lưu trong MongoDB (schema mặc định, đổi tên collection được):
                sessions     {session, incubation_start, created, ...}                      (tuỳ chọn)
                measurements 1 document / 1 phép đo = 1 dòng metadata_log.csv
                             + wavelengths: [nm...] + counts: [Raw_ADC_Counts...]
                             (+ batch_folder tuỳ chọn; nếu không có -> "batch_XX")
                images       {session, batch_no, egg_label, timestamp, filename,
                              gridfs_id | data (Binary) | path}

Hai store trả về cùng một mô hình batch (dict) để lớp phân tích (analysis.py) dùng chung.
"""

import csv
import hashlib
import io
import json
import os
import re
from datetime import datetime

import numpy as np

IMG_EXT = (".bmp", ".png", ".jpg", ".jpeg", ".tif", ".tiff")
IMG_RE = re.compile(r"^(\d+)__(\d{4}-\d{2}-\d{2})__(\d{2})-(\d{2})-(\d{2})", re.I)
BATCH_RE = re.compile(r"^batch_(\d+)", re.I)
NOTE_EXT = (".txt", ".rtf", ".md")


def safe_join(base, *parts):
    """Ghép đường dẫn và chắc chắn kết quả nằm trong base (chống ../)."""
    p = os.path.realpath(os.path.join(base, *parts))
    b = os.path.realpath(base)
    if p != b and not p.startswith(b + os.sep):
        raise ValueError("path ngoài thư mục cho phép")
    return p


def _float(x):
    try:
        v = float(x)
        return v if np.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _str(x):
    if x is None:
        return None
    if isinstance(x, datetime):
        return x.isoformat()
    return str(x).strip()


def _short_id(*parts):
    return hashlib.sha1("|".join(map(str, parts)).encode("utf-8")).hexdigest()[:10]


def make_record(r, ref, exists=True):
    """Chuẩn hoá 1 dòng metadata (CSV row hoặc Mongo doc) thành record dùng chung."""
    return {
        "sample_id": _str(r.get("sample_id")),
        "ref": ref,                      # khoá để store đọc lại phổ
        "file": _str(r.get("sample_id")) and _str(r.get("sample_id")) + ".csv",
        "exists": exists,
        "timestamp": _str(r.get("timestamp")),
        "exposure_ms": _float(r.get("exposure_ms")),
        "n_averaged": _float(r.get("n_averaged")),
        "peak_adc": _float(r.get("peak_adc")),
        "saturated": str(r.get("saturated")).strip() in ("1", "True", "true"),
        "saturated_pixels": _float(r.get("saturated_pixels")),
        "color_ratio": _float(r.get("color_ratio")),
        "color_ratio_drift_pct": _float(r.get("color_ratio_drift_pct")),
        "invalid_pct": _float(r.get("invalid_pct_600_1000nm")),
        "frame_rsd_pct": _float(r.get("frame_rsd_pct")),
        "n_frames_saved": _float(r.get("n_frames_saved")),
        "incubation_age_h": _float(r.get("incubation_age_h")),
    }


def new_batch(folder, batch_no):
    return {"folder": folder, "batch_no": batch_no, "dark": None, "lamp": None,
            "eggs": {}, "images": {}, "image_dirs": [], "notes": [],
            "age_h": None, "started": None, "finished": None, "warnings": []}


def add_row(b, r, rec):
    """Đưa 1 phép đo vào batch theo measurement_type."""
    mtype = _str(r.get("measurement_type")) or ""
    if mtype == "dark":
        b["dark"] = rec
    elif mtype == "reference":
        b["lamp"] = rec
    elif mtype == "embryo":
        label = _str(r.get("egg_label")) or ""
        end = _str(r.get("egg_end")) or "unknown"
        if not label:
            b["warnings"].append(f"{rec['sample_id']}: thiếu egg_label")
            return
        # ID trứng = egg_label (0…30, số viết trên vỏ); egg_id của app đo (1…31) chỉ để đối chiếu
        app_id = _str(r.get("egg_id"))
        if label.isdigit() and app_id and app_id.isdigit() and int(app_id) != int(label) + 1:
            b["warnings"].append(f"{rec['sample_id']}: egg_id={app_id} không khớp egg_label={label}")
        egg_id = str(int(label)) if label.isdigit() else app_id
        e = b["eggs"].setdefault(label, {"egg_id": egg_id, "blunt": [], "pointed": [],
                                         "photo_confirmed_at": None, "placement": _str(r.get("placement"))})
        e.setdefault(end, []).append(rec)
        if r.get("photo_confirmed_at"):
            e["photo_confirmed_at"] = _str(r["photo_confirmed_at"])
        if not rec["exists"]:
            b["warnings"].append(f"Thiếu dữ liệu phổ {rec['sample_id']}")


def finish_batch(b):
    stamps, ages = [], []
    for rec in [b["dark"], b["lamp"]] + [x for e in b["eggs"].values() for k in ("blunt", "pointed") for x in e.get(k, [])]:
        if rec and rec["timestamp"]:
            stamps.append(rec["timestamp"])
    for e in b["eggs"].values():
        for k in ("blunt", "pointed"):
            ages += [x["incubation_age_h"] for x in e.get(k, []) if x["incubation_age_h"] is not None]
    for e in b["eggs"].values():  # phép đo lặp: dùng lần muộn nhất
        for k in ("blunt", "pointed"):
            e.get(k, []).sort(key=lambda x: x["timestamp"] or "")
    if stamps:
        b["started"], b["finished"] = min(stamps), max(stamps)
    if ages:
        b["age_h"] = float(np.median(ages))
    return b


def add_image(b, label, img, label_width):
    label = str(int(label)).zfill(label_width) if str(label).isdigit() else str(label)
    if label in b["images"]:
        b["warnings"].append(f"Trứng {label} có >1 ảnh, dùng ảnh muộn nhất")
        if (img["ts"] or "") < (b["images"][label]["ts"] or ""):
            return
    b["images"][label] = img


# ============================================================================
# Folder
# ============================================================================

class FolderStore:
    kind = "folder"

    def __init__(self, root, session):
        self.root = os.path.abspath(root)
        self.session = session
        self.dir = safe_join(self.root, session)
        if not os.path.isdir(self.dir):
            raise FileNotFoundError(f"Không có session {session}")
        self.id = _short_id("folder", self.root, session)
        self._spec = {}    # path -> (mtime, wl, raw)
        self._batch = {}   # folder -> (signature, batch)

    def describe(self):
        return {"id": self.id, "kind": self.kind, "session": self.session, "location": self.dir}

    def info(self):
        p = os.path.join(self.dir, "session_info.json")
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _folders(self):
        fs = [x for x in os.listdir(self.dir) if BATCH_RE.match(x) and os.path.isdir(os.path.join(self.dir, x))]
        return sorted(fs, key=lambda x: (int(BATCH_RE.match(x).group(1)), x))

    def _sig(self, bdir):
        sig = []
        meta = os.path.join(bdir, "metadata_log.csv")
        if os.path.isfile(meta):
            st = os.stat(meta)
            sig.append(("meta", st.st_mtime_ns, st.st_size))
        for x in sorted(os.listdir(bdir)):
            p = os.path.join(bdir, x)
            if os.path.isdir(p):
                sig.append((x, len(os.listdir(p)), os.stat(p).st_mtime_ns))
        sig.append(("n", len(os.listdir(bdir))))
        return tuple(sig)

    def signature(self):
        return tuple((f, self._sig(os.path.join(self.dir, f))) for f in self._folders())

    def batches(self):
        return [self._parse(f) for f in self._folders()]

    def _parse(self, folder):
        bdir = safe_join(self.dir, folder)
        sig = self._sig(bdir)
        hit = self._batch.get(folder)
        if hit and hit[0] == sig:
            return hit[1]
        b = new_batch(folder, int(BATCH_RE.match(folder).group(1)))
        meta = os.path.join(bdir, "metadata_log.csv")
        rows = []
        if os.path.isfile(meta):
            with open(meta, newline="", encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(f))
        else:
            b["warnings"].append("Không có metadata_log.csv")
        for r in rows:
            sid = (r.get("sample_id") or "").strip()
            fn = sid + ".csv"
            add_row(b, r, make_record(r, fn, os.path.isfile(os.path.join(bdir, fn))))
        finish_batch(b)

        # Ảnh: N trong tên file = egg_label (0-based), pad hay không đều được
        width = max((len(k) for k in b["eggs"]), default=4)
        subdirs = [""] + [x for x in sorted(os.listdir(bdir)) if os.path.isdir(os.path.join(bdir, x))]
        for d in subdirs:
            p = os.path.join(bdir, d) if d else bdir
            has_img = False
            for fn in sorted(os.listdir(p)):
                low = fn.lower()
                if low.endswith(NOTE_EXT):
                    b["notes"].append("/".join(x for x in (d, fn) if x))
                    continue
                if not low.endswith(IMG_EXT):
                    continue
                m = IMG_RE.match(fn)
                if not m:
                    continue
                has_img = True
                ts = f"{m.group(2)}T{m.group(3)}:{m.group(4)}:{m.group(5)}"
                add_image(b, m.group(1), {"key": "/".join(x for x in (d, fn) if x), "file": fn, "ts": ts}, width)
            if has_img:
                b["image_dirs"].append(d)
        self._batch[folder] = (sig, b)
        return b

    def spectrum(self, b, rec):
        if not rec or not rec.get("exists"):
            return None, None
        path = safe_join(self.dir, b["folder"], rec["ref"])
        mt = os.path.getmtime(path)
        hit = self._spec.get(path)
        if hit and hit[0] == mt:
            return hit[1], hit[2]
        try:
            arr = np.loadtxt(path, delimiter=",", skiprows=1, usecols=(0, 1), ndmin=2, encoding="utf-8-sig")
            wl, raw = arr[:, 0], arr[:, 1]
        except ValueError:
            wl, raw = [], []
            with open(path, newline="", encoding="utf-8-sig") as f:
                rd = csv.reader(f)
                next(rd, None)
                for row in rd:
                    if len(row) >= 2 and _float(row[0]) is not None:
                        wl.append(_float(row[0]))
                        raw.append(_float(row[1]) if _float(row[1]) is not None else np.nan)
            wl, raw = np.asarray(wl), np.asarray(raw)
        self._spec[path] = (mt, wl, raw)
        return wl, raw

    def image(self, b, key):
        """-> (file path hoặc BytesIO, mtime|None)"""
        path = safe_join(self.dir, b["folder"], key)
        if not os.path.isfile(path) or not path.lower().endswith(IMG_EXT):
            raise FileNotFoundError(key)
        return path, os.path.getmtime(path)

    def note(self, b, key):
        with open(safe_join(self.dir, b["folder"], key), "rb") as f:
            return f.read(200_000)


def list_folder_sessions(root):
    out = []
    if not os.path.isdir(root):
        return out
    for name in sorted(os.listdir(root)):
        d = os.path.join(root, name)
        if not os.path.isdir(d):
            continue
        n_batch = sum(1 for x in os.listdir(d) if BATCH_RE.match(x) and os.path.isdir(os.path.join(d, x)))
        if n_batch or os.path.isfile(os.path.join(d, "session_info.json")):
            out.append({"name": name, "n_batches": n_batch})
    return out


# ============================================================================
# MongoDB
# ============================================================================

DEFAULT_MONGO = {"sessions": "sessions", "measurements": "measurements", "images": "images", "bucket": "images"}


def redact_uri(uri):
    """Bỏ mật khẩu khỏi URI trước khi lưu / hiển thị."""
    return re.sub(r"(mongodb(?:\+srv)?://[^:/@]+):[^@]*@", r"\1:***@", uri or "")


def mongo_connect(uri, timeout_ms=4000):
    import pymongo  # import muộn: chỉ cần khi dùng Mongo
    client = pymongo.MongoClient(uri, serverSelectionTimeoutMS=timeout_ms, connectTimeoutMS=timeout_ms)
    client.admin.command("ping")
    return client


def mongo_list_sessions(db, cols=None):
    cols = {**DEFAULT_MONGO, **(cols or {})}
    m = db[cols["measurements"]]
    out = {}
    for r in m.aggregate([{"$group": {"_id": "$session", "n": {"$sum": 1},
                                      "batches": {"$addToSet": "$batch_no"}, "last": {"$max": "$timestamp"}}}]):
        if r["_id"] is None:
            continue
        out[str(r["_id"])] = {"session": str(r["_id"]), "n_measurements": r["n"],
                              "n_batches": len([x for x in r["batches"] if x is not None]),
                              "last": _str(r.get("last")), "n_images": 0}
    for r in db[cols["images"]].aggregate([{"$group": {"_id": "$session", "n": {"$sum": 1}}}]):
        if str(r["_id"]) in out:
            out[str(r["_id"])]["n_images"] = r["n"]
    return sorted(out.values(), key=lambda x: x["session"])


class MongoStore:
    kind = "mongo"

    def __init__(self, db, session, cols=None, uri=""):
        self.db = db
        self.session = session
        self.cols = {**DEFAULT_MONGO, **(cols or {})}
        self.uri = redact_uri(uri)
        self.id = _short_id("mongo", self.uri, db.name, session, json.dumps(self.cols, sort_keys=True))
        self._spec = {}
        self._sig = None
        self._batches = None
        if not self.db[self.cols["measurements"]].find_one({"session": session}, {"_id": 1}):
            raise FileNotFoundError(f"Không có phép đo nào của session {session} trong {db.name}.{self.cols['measurements']}")

    def describe(self):
        return {"id": self.id, "kind": self.kind, "session": self.session,
                "location": f"{self.uri} / {self.db.name}", "db": self.db.name, "cols": self.cols}

    def info(self):
        d = self.db[self.cols["sessions"]].find_one({"session": self.session}) or {}
        return {k: _str(v) for k, v in d.items() if k != "_id"}

    def signature(self):
        m = self.db[self.cols["measurements"]]
        im = self.db[self.cols["images"]]
        last_m = m.find_one({"session": self.session}, {"_id": 1}, sort=[("_id", -1)])
        last_i = im.find_one({"session": self.session}, {"_id": 1}, sort=[("_id", -1)])
        return (m.count_documents({"session": self.session}), str(last_m and last_m["_id"]),
                im.count_documents({"session": self.session}), str(last_i and last_i["_id"]))

    def batches(self):
        sig = self.signature()
        if self._batches is not None and sig == self._sig:
            return self._batches
        by_no = {}
        proj = {"wavelengths": 0, "counts": 0}
        for r in self.db[self.cols["measurements"]].find({"session": self.session}, proj):
            no = int(r.get("batch_no") or 0)
            b = by_no.get(no)
            if b is None:
                b = by_no[no] = new_batch(_str(r.get("batch_folder")) or f"batch_{no:02d}", no)
            add_row(b, r, make_record(r, str(r["_id"]), True))
        for b in by_no.values():
            finish_batch(b)
        for r in self.db[self.cols["images"]].find({"session": self.session}, {"data": 0}):
            no = int(r.get("batch_no") or 0)
            b = by_no.get(no)
            if b is None or r.get("egg_label") is None:
                continue
            ts = _str(r.get("timestamp"))
            fn = _str(r.get("filename")) or f"{r['egg_label']}.img"
            if not ts:
                m = IMG_RE.match(fn)
                ts = m and f"{m.group(2)}T{m.group(3)}:{m.group(4)}:{m.group(5)}"
            width = max((len(k) for k in b["eggs"]), default=4)
            add_image(b, _str(r["egg_label"]), {"key": str(r["_id"]), "file": fn, "ts": ts}, width)
            if "mongo" not in b["image_dirs"]:
                b["image_dirs"].append("mongo")
        self._batches = [by_no[k] for k in sorted(by_no)]
        self._sig = sig
        self._prefetch()
        return self._batches

    def _prefetch(self):
        """Tải toàn bộ mảng phổ của session trong 1 truy vấn (nhanh hơn N truy vấn lẻ)."""
        missing = {rec["ref"] for b in self._batches
                   for rec in [b["dark"], b["lamp"]] + [x for e in b["eggs"].values() for k in ("blunt", "pointed") for x in e.get(k, [])]
                   if rec and rec["ref"] not in self._spec}
        if not missing:
            return
        from bson import ObjectId
        ids = [ObjectId(x) if ObjectId.is_valid(x) else x for x in missing]
        for r in self.db[self.cols["measurements"]].find({"_id": {"$in": ids}}, {"wavelengths": 1, "counts": 1}):
            self._store_spec(r)

    def _store_spec(self, r):
        wl = np.asarray(r.get("wavelengths") or [], dtype=float)
        raw = np.asarray([np.nan if v is None else v for v in (r.get("counts") or [])], dtype=float)
        self._spec[str(r["_id"])] = (wl, raw) if len(wl) and len(wl) == len(raw) else (None, None)

    def spectrum(self, b, rec):
        if not rec:
            return None, None
        if rec["ref"] not in self._spec:
            from bson import ObjectId
            k = rec["ref"]
            r = self.db[self.cols["measurements"]].find_one({"_id": ObjectId(k) if ObjectId.is_valid(k) else k},
                                                           {"wavelengths": 1, "counts": 1})
            if not r:
                return None, None
            self._store_spec(r)
        return self._spec[rec["ref"]]

    def image(self, b, key):
        from bson import ObjectId
        r = self.db[self.cols["images"]].find_one({"_id": ObjectId(key) if ObjectId.is_valid(key) else key, "session": self.session})
        if not r:
            raise FileNotFoundError(key)
        if r.get("gridfs_id") is not None:
            import gridfs
            data = gridfs.GridFS(self.db, collection=self.cols["bucket"]).get(r["gridfs_id"]).read()
        elif r.get("data") is not None:
            data = bytes(r["data"])
        elif r.get("path") and os.path.isfile(r["path"]):
            return r["path"], os.path.getmtime(r["path"])
        else:
            raise FileNotFoundError(f"ảnh {key} không có gridfs_id/data/path hợp lệ")
        return io.BytesIO(data), None

    def note(self, b, key):
        raise FileNotFoundError("Mongo không có note")
