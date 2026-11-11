#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Đẩy 1 session (thư mục do app.py ghi ra) lên MongoDB theo schema mà dashboard đọc.

    python dashboard/mongo_import.py --uri mongodb://localhost:27017 --db inovo \
        --session-dir data/BATCH_B/20261007_105144_embryo_session [--images gridfs|path|none]

Schema (xem stores.py):
    sessions     : nội dung session_info.json + {session}
    measurements : 1 doc / dòng metadata_log.csv + batch_folder + wavelengths + counts
    images       : {session, batch_no, batch_folder, egg_label, timestamp, filename, gridfs_id | path}
Chạy lại nhiều lần an toàn (upsert theo session + sample_id / filename); ảnh đã có thì bỏ qua.
"""

import argparse
import csv
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stores import BATCH_RE, DEFAULT_MONGO, IMG_EXT, IMG_RE  # noqa: E402

NUMERIC = {"exposure_ms", "n_averaged", "peak_adc", "saturated", "saturated_pixels", "exposure_status_over",
           "color_ratio", "color_ratio_drift_pct", "invalid_pct_600_1000nm", "dark_exposure_mismatch_pct",
           "wavelength_start_nm", "wavelength_end_nm", "batch_no", "incubation_age_h", "n_frames_saved",
           "frame_rsd_pct", "egg_id", "placement"}


def _num(v):
    try:
        f = float(v)
        return int(f) if f.is_integer() else f
    except (TypeError, ValueError):
        return None


def _read_csv_spectrum(path):
    wl, counts = [], []
    with open(path, newline="", encoding="utf-8-sig") as f:
        r = csv.reader(f)
        next(r, None)
        for row in r:
            if len(row) >= 2 and _num(row[0]) is not None:
                wl.append(float(row[0]))
                c = _num(row[1])
                counts.append(None if c is None else float(c))
    return wl, counts


def import_session(db, session_dir, images="gridfs", cols=None, log=print):
    cols = {**DEFAULT_MONGO, **(cols or {})}
    session = os.path.basename(os.path.normpath(session_dir))
    info_p = os.path.join(session_dir, "session_info.json")
    if os.path.isfile(info_p):
        with open(info_p, encoding="utf-8") as f:
            info = json.load(f)
        db[cols["sessions"]].replace_one({"session": session}, {**info, "session": session}, upsert=True)

    M, I = db[cols["measurements"]], db[cols["images"]]
    fs = None
    if images == "gridfs":
        import gridfs
        fs = gridfs.GridFS(db, collection=cols["bucket"])

    n_m = n_i = 0
    for folder in sorted(x for x in os.listdir(session_dir) if BATCH_RE.match(x)):
        bdir = os.path.join(session_dir, folder)
        batch_no = int(BATCH_RE.match(folder).group(1))
        meta = os.path.join(bdir, "metadata_log.csv")
        if os.path.isfile(meta):
            with open(meta, newline="", encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    sid = (row.get("sample_id") or "").strip()
                    p = os.path.join(bdir, sid + ".csv")
                    if not sid or not os.path.isfile(p):
                        continue
                    doc = {k: (_num(v) if k in NUMERIC else (v if v != "" else None)) for k, v in row.items()}
                    doc.update(session=session, batch_no=batch_no, batch_folder=folder)
                    doc["wavelengths"], doc["counts"] = _read_csv_spectrum(p)
                    M.replace_one({"session": session, "sample_id": sid}, doc, upsert=True)
                    n_m += 1
        if images == "none":
            continue
        for d in [""] + [x for x in sorted(os.listdir(bdir)) if os.path.isdir(os.path.join(bdir, x))]:
            p = os.path.join(bdir, d)
            for fn in sorted(os.listdir(p)):
                m = IMG_RE.match(fn)
                if not m or not fn.lower().endswith(IMG_EXT):
                    continue
                key = {"session": session, "batch_no": batch_no, "filename": fn}
                if I.find_one(key, {"_id": 1}):
                    continue
                doc = {**key, "batch_folder": folder, "egg_label": str(int(m.group(1))).zfill(4),
                       "timestamp": f"{m.group(2)}T{m.group(3)}:{m.group(4)}:{m.group(5)}"}
                src = os.path.join(p, fn)
                if fs is not None:
                    with open(src, "rb") as f:
                        doc["gridfs_id"] = fs.put(f, filename=fn, metadata={"session": session, "batch_no": batch_no})
                else:
                    doc["path"] = os.path.abspath(src)
                I.insert_one(doc)
                n_i += 1
        log(f"  {folder}: xong")
    M.create_index([("session", 1), ("batch_no", 1)])
    I.create_index([("session", 1), ("batch_no", 1), ("egg_label", 1)])
    log(f"Session {session}: {n_m} phép đo, {n_i} ảnh mới")
    return n_m, n_i


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--uri", default="mongodb://localhost:27017")
    ap.add_argument("--db", default="inovo")
    ap.add_argument("--session-dir", required=True)
    ap.add_argument("--images", choices=["gridfs", "path", "none"], default="gridfs",
                    help="gridfs: lưu ảnh vào Mongo; path: chỉ lưu đường dẫn file; none: bỏ ảnh")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    from stores import mongo_connect
    import_session(mongo_connect(a.uri)[a.db], a.session_dir, a.images)


if __name__ == "__main__":
    main()
