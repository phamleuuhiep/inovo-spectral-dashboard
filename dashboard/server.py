#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Batch Dashboard - xem từng quả trứng theo thời gian (phổ + ảnh).

Flow: mở app -> màn hình bắt đầu trống. Dữ liệu chỉ được phân tích & hiển thị khi người dùng
  (1) tải dữ liệu lên (thư mục session / batch / ảnh) -> lưu vào --store rồi mở session đó, hoặc
  (2) kết nối MongoDB và chọn một session.
Server không tự quét thư mục lưu trữ lúc khởi động.

Chạy:
    python dashboard/server.py                    # http://127.0.0.1:8050
    python dashboard/server.py --store data/BATCH_B --port 8050 --host 0.0.0.0

Cần: numpy, Pillow, plotly (để lấy plotly.min.js, chạy offline); pymongo nếu dùng MongoDB.
"""

import argparse
import json
import os
import re
import sys
import threading
import time
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analysis  # noqa: E402
import stores  # noqa: E402
from stores import BATCH_RE, IMG_EXT, safe_join  # noqa: E402

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
DEFAULT_STORE = os.path.join(PROJECT_ROOT, "data", "BATCH_B")
CACHE_DIR = os.path.join(PROJECT_ROOT, "outputs", "dashboard_cache")
RECENT_FILE = os.path.join(CACHE_DIR, "recent.json")
TAGS_DIR = os.path.join(PROJECT_ROOT, "outputs", "dashboard_tags")   # nhóm Trống/Mái/Không phôi - KHÔNG phải cache
STATUS_DIR = os.path.join(PROJECT_ROOT, "outputs", "egg_status")  # nhật ký tình trạng trứng - KHÔNG phải cache
DASH_DIR = os.path.dirname(os.path.abspath(__file__))
CHANGELOG = os.path.join(DASH_DIR, "CHANGELOG.json")
GROUPS = ("male", "female", "infertile", "fertile")   # fertile = có phôi, chưa rõ giới
ML_CACHE = {}           # (nguồn, cửa sổ λ, chữ ký batch) -> phổ theo ngày cho tab Học máy

STORE_ROOT = DEFAULT_STORE
SOURCES = {}            # id -> store đang mở (chỉ có sau khi upload / kết nối)
_lock = threading.RLock()


# ----------------------------------------------------------------------------
# Nguồn đã mở + lịch sử
# ----------------------------------------------------------------------------

def _recent():
    try:
        with open(RECENT_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def _remember(entry):
    """Lưu nguồn vừa mở (Mongo: URI đã che mật khẩu) để lần sau chọn lại nhanh."""
    key = json.dumps({k: entry.get(k) for k in ("kind", "session", "uri", "db", "store")}, sort_keys=True)
    items = [e for e in _recent()
             if json.dumps({k: e.get(k) for k in ("kind", "session", "uri", "db", "store")}, sort_keys=True) != key]
    items.insert(0, {**entry, "at": datetime.now().isoformat(timespec="seconds")})
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(RECENT_FILE, "w", encoding="utf-8") as f:
        json.dump(items[:12], f, ensure_ascii=False, indent=1)


def _get(src):
    st = SOURCES.get(src)
    if st is None:
        raise KeyError("nguồn chưa được mở (hãy tải lên hoặc kết nối lại)")
    return st


def _open(store, lo=604, hi=922):
    """Mở 1 nguồn: phân tích ngay, đăng ký, tạo thumbnail nền."""
    with _lock:
        summary = analysis.session_summary(store, lo, hi)
        SOURCES[store.id] = store
    threading.Thread(target=warm_thumbnails, args=(store,), daemon=True).start()
    return summary


def _tags_path(session):
    return os.path.join(TAGS_DIR, re.sub(r"[^\w.-]+", "_", session) + ".json")


def load_tags(session):
    """Nhóm của từng trứng, lưu theo tên session (dùng chung cho nguồn upload và Mongo)."""
    try:
        with open(_tags_path(session), encoding="utf-8") as f:
            return json.load(f).get("groups", {})
    except (OSError, ValueError):
        return {}


def save_tags(session, changes):
    with _lock:
        groups = load_tags(session)
        for label, g in changes.items():
            if g in GROUPS:
                groups[str(label)] = g
            else:
                groups.pop(str(label), None)
        os.makedirs(TAGS_DIR, exist_ok=True)
        tmp = _tags_path(session) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"session": session, "updated": datetime.now().isoformat(timespec="seconds"),
                       "groups": dict(sorted(groups.items()))}, f, ensure_ascii=False, indent=1)
        os.replace(tmp, _tags_path(session))
    return groups


def dashboard_version():
    """Lần cập nhật code dashboard gần nhất = mtime mới nhất của *.py / static/*, kèm nhật ký thay đổi."""
    files = [os.path.join(DASH_DIR, f) for f in os.listdir(DASH_DIR) if f.endswith((".py", ".pyw", ".json"))]
    files += [os.path.join(STATIC_DIR, f) for f in os.listdir(STATIC_DIR) if f.endswith((".js", ".html", ".css"))]
    mt = max((os.path.getmtime(f) for f in files if os.path.isfile(f)), default=None)
    try:
        with open(CHANGELOG, encoding="utf-8") as f:
            changes = json.load(f)
    except (OSError, ValueError):
        changes = []
    return {"updated": mt and datetime.fromtimestamp(mt).isoformat(timespec="seconds"), "changes": changes[:20]}


def _ml_prepare(st, x0, x1):
    """Phổ TB theo ngày (ml.prepare), cache theo nguồn + cửa sổ λ + chữ ký batch. Cần _lock."""
    import ml
    sig = tuple((b["folder"], len(b["eggs"])) for b in st.batches())
    key = (st.id, x0, x1, sig)
    if key not in ML_CACHE:
        ML_CACHE.clear()
        ML_CACHE[key] = ml.prepare(st, x0, x1)
    return ML_CACHE[key]


def _batch(store, folder):
    for b in store.batches():
        if b["folder"] == folder:
            return b
    raise KeyError(f"không có batch {folder}")


# ----------------------------------------------------------------------------
# Ảnh: BMP 4K -> JPEG thu nhỏ, cache ra outputs/dashboard_cache/<source id>/
# ----------------------------------------------------------------------------

def thumbnail(store, b, key, width):
    width = max(64, min(int(width), 3840))
    safe = re.sub(r"[^\w.-]+", "_", key)
    dst = os.path.join(CACHE_DIR, store.id, b["folder"], f"{os.path.splitext(safe)[0]}_w{width}.jpg")
    if os.path.isfile(dst) and store.kind == "mongo":
        return dst
    src, mtime = store.image(b, key)
    if os.path.isfile(dst) and mtime is not None and os.path.getmtime(dst) >= mtime:
        return dst
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with Image.open(src) as im:
        im = im.convert("RGB")
        if im.width > width:
            im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
        tmp = f"{dst}.{threading.get_ident()}.tmp"
        im.save(tmp, "JPEG", quality=88)
        os.replace(tmp, dst)
    return dst


def warm_thumbnails(store, widths=(240, 480)):
    try:
        for b in store.batches():
            for img in b["images"].values():
                for w in widths:
                    try:
                        thumbnail(store, b, img["key"], w)
                    except Exception:
                        pass
    except Exception:
        pass


def _rtf_to_text(s):
    """Bộ đọc RTF tối giản (đủ cho note.rtf của TextEdit): bỏ nhóm định dạng, giải mã \\uN và \\'xx."""
    out, stack, skip, uc, i, n = [], [], False, 1, 0, len(s)
    ctl = re.compile(r"\\([a-zA-Z]+)(-?\d+)? ?")
    while i < n:
        c = s[i]
        if c == "{":
            stack.append(skip); i += 1; continue
        if c == "}":
            skip = stack.pop() if stack else False; i += 1; continue
        if c == "\\" and i + 1 < n:
            nx = s[i + 1]
            if nx in "\\{}":
                if not skip: out.append(nx)
                i += 2; continue
            if nx == "'":
                if not skip: out.append(bytes.fromhex(s[i + 2:i + 4]).decode("cp1252", "replace"))
                i += 4; continue
            if nx in "\r\n":
                if not skip: out.append("\n")
                i += 2; continue
            if nx == "*":
                skip = True; i += 2; continue
            m = ctl.match(s, i)
            if not m:
                i += 2; continue
            word, arg = m.group(1), m.group(2)
            i = m.end()
            if word in ("fonttbl", "colortbl", "stylesheet", "info", "expandedcolortbl"):
                skip = True
            elif skip:
                pass
            elif word in ("par", "line"):
                out.append("\n")
            elif word == "tab":
                out.append("\t")
            elif word == "uc":
                uc = int(arg or 1)
            elif word == "u":
                out.append(chr(int(arg) % 65536))
                i += uc  # bỏ ký tự thay thế
            continue
        if c not in "\r\n" and not skip:
            out.append(c)
        i += 1
    return "".join(out).strip()


# ----------------------------------------------------------------------------
# Upload (ghi vào thư mục lưu trữ)
# ----------------------------------------------------------------------------

def _upload_target(session, rel):
    session = (session or "").strip()
    if not session or "/" in session or "\\" in session or session.startswith("."):
        raise ValueError("tên session không hợp lệ")
    parts = [p for p in rel.replace("\\", "/").split("/") if p]
    if not parts or any(p in ("..", ".") for p in parts):
        raise ValueError("đường dẫn không hợp lệ")
    if parts[0] not in ("session_info.json", "rename_log.csv", "outputs_shape") and not BATCH_RE.match(parts[0]):
        raise ValueError("file phải nằm trong thư mục batch_XX_... hoặc outputs_shape (hoặc là session_info.json)")
    return safe_join(STORE_ROOT, session, *parts)


# ----------------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "BatchDashboard/2.0"

    def log_message(self, fmt, *args):
        line = args[0] if args else ""
        if "/api/img" not in line and "/api/upload?" not in line:
            super().log_message(fmt, *args)

    def _send(self, code, body, ctype="application/json; charset=utf-8", cache=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache or "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path, ctype, cache=None):
        with open(path, "rb") as f:
            self._send(200, f.read(), ctype, cache)

    def _query(self):
        u = urllib.parse.urlparse(self.path)
        return u.path, {k: v[-1] for k, v in urllib.parse.parse_qs(u.query).items()}

    def _json_body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def _guard(self, fn):
        try:
            return fn()
        except (KeyError, ValueError) as ex:
            return self._send(400, {"error": str(ex).strip("'\"")})
        except FileNotFoundError as ex:
            return self._send(404, {"error": str(ex)})
        except Exception as ex:  # noqa
            name = type(ex).__name__
            if "ServerSelectionTimeout" in name or "ConfigurationError" in name or "OperationFailure" in name:
                return self._send(502, {"error": f"MongoDB: {ex}"})
            return self._send(500, {"error": repr(ex)})

    # ---------------- GET ----------------
    def do_GET(self):
        path, q = self._query()

        def run():
            if path in ("/", "/index.html"):
                return self._file(os.path.join(STATIC_DIR, "index.html"), "text/html; charset=utf-8")
            if path == "/plotly.min.js":
                import plotly
                p = os.path.join(os.path.dirname(plotly.__file__), "package_data", "plotly.min.js")
                return self._file(p, "application/javascript", "max-age=86400")
            if path == "/api/state":
                try:
                    import pymongo  # noqa: F401
                    mongo_ok = True
                except ImportError:
                    mongo_ok = False
                return self._send(200, {"store": STORE_ROOT, "mongo_available": mongo_ok,
                                        "sources": [s.describe() for s in SOURCES.values()],
                                        "recent": _recent(), "mongo_defaults": stores.DEFAULT_MONGO})
            if path == "/api/version":
                return self._send(200, dashboard_version())
            if path == "/api/status":
                import status
                st = _get(q["src"])
                with _lock:
                    sig = status.signals(st)
                return self._send(200, {"session": st.session, "statuses": status.STATUSES,
                                        "events": status.load(STATUS_DIR, st.session), "signals": sig,
                                        "patterns": status.load_patterns(), "matches": status.pattern_matches(sig)})
            if path == "/api/hatched":
                import status
                st = _get(q["src"])
                return self._send(200, {"session": st.session, "labels": sorted(status.hatched(STATUS_DIR, st.session))})
            if path == "/api/status_photo":
                import status
                p = status.photo_path(STATUS_DIR, _get(q["src"]).session, q["f"])
                ext = os.path.splitext(p)[1].lower()
                return self._file(p, {".png": "image/png", ".webp": "image/webp"}.get(ext, "image/jpeg"), "max-age=86400")
            if path == "/api/upload_targets":
                # chỉ là danh sách đích để ghi file, không phân tích gì
                return self._send(200, {"store": STORE_ROOT, "sessions": stores.list_folder_sessions(STORE_ROOT)})
            if path == "/api/session":
                st = _get(q["src"])
                with _lock:
                    data = analysis.session_summary(st, float(q.get("lo", 604)), float(q.get("hi", 922)))
                return self._send(200, data)
            if path.startswith("/static/") and path.endswith(".js"):
                name = os.path.basename(path)
                return self._file(safe_join(STATIC_DIR, name), "application/javascript; charset=utf-8")
            if path == "/api/compare":
                st = _get(q["src"])
                labels = [x for x in q.get("labels", "").split(",") if x]
                with _lock:
                    data = analysis.compare(st, labels, q.get("end", "blunt"))
                return self._send(200, data)
            if path == "/api/job":
                import paper
                job = paper.JOBS.get(q["id"])
                if not job:
                    raise FileNotFoundError("job không tồn tại (server đã khởi động lại?)")
                return self._send(200, paper.job_view(job))
            if path == "/api/tags":
                return self._send(200, {"session": _get(q["src"]).session, "groups": load_tags(_get(q["src"]).session)})
            if path == "/api/shape":
                import shape
                st = _get(q["src"])
                with _lock:
                    data = shape.summary(st, st.batches(), CACHE_DIR)
                return self._send(200, data)
            if path == "/api/shape_px":
                import shape
                st = _get(q["src"])
                with _lock:
                    batches = st.batches()
                return self._send(200, shape.pixel_egg(st, batches, q["label"], CACHE_DIR))
            if path == "/api/shape_pximg":
                import shape
                p = shape._px_base(_get(q["src"]), q["b"], q["k"], CACHE_DIR) + ".jpg"
                if not os.path.isfile(p):
                    raise FileNotFoundError("chưa có ảnh đường viền")
                return self._file(p, "image/jpeg", "max-age=600")
            if path == "/api/egg":
                st = _get(q["src"])
                with _lock:
                    data = analysis.egg_detail(st, q["label"])
                return self._send(200, data)
            if path == "/api/img":
                st = _get(q["src"])
                p = thumbnail(st, _batch(st, q["b"]), q["k"], q.get("w", 360))
                return self._file(p, "image/jpeg", "max-age=3600")
            if path == "/api/note":
                st = _get(q["src"])
                txt = st.note(_batch(st, q["b"]), q["f"]).decode("utf-8", "replace")
                if txt.lstrip().startswith("{\\rtf"):
                    txt = _rtf_to_text(txt)
                return self._send(200, txt, "text/plain; charset=utf-8")
            return self._send(404, {"error": "not found"})

        self._guard(run)

    # ---------------- POST ----------------
    def do_POST(self):
        path, q = self._query()

        def run():
            body = self._json_body()
            if path == "/api/upload/check":
                # cho client biết file nào đã có (cùng kích thước) để khỏi gửi lại
                existing = []
                for f in body.get("files", []):
                    try:
                        dst = _upload_target(body["session"], f["path"])
                    except ValueError:
                        continue
                    if os.path.isfile(dst) and os.path.getsize(dst) == int(f.get("size", -1)):
                        existing.append(f["path"])
                return self._send(200, {"existing": existing})
            if path == "/api/open/folder":
                st = stores.FolderStore(STORE_ROOT, body["session"])
                summary = _open(st, body.get("lo", 604), body.get("hi", 922))
                _remember({"kind": "folder", "session": st.session, "store": STORE_ROOT})
                return self._send(200, summary)
            if path in ("/api/mongo/test", "/api/open/mongo"):
                uri, dbname = body.get("uri", "").strip(), body.get("db", "").strip()
                if not uri or not dbname:
                    raise ValueError("cần URI và tên database")
                cols = {k: v.strip() for k, v in (body.get("cols") or {}).items() if v and v.strip()}
                client = stores.mongo_connect(uri)
                db = client[dbname]
                if path == "/api/mongo/test":
                    try:
                        return self._send(200, {"ok": True, "db": dbname,
                                                "collections": sorted(db.list_collection_names()),
                                                "sessions": stores.mongo_list_sessions(db, cols)})
                    finally:
                        client.close()
                st = stores.MongoStore(db, body["session"], cols, uri)
                summary = _open(st, body.get("lo", 604), body.get("hi", 922))
                _remember({"kind": "mongo", "session": st.session, "uri": stores.redact_uri(uri),
                           "db": dbname, "cols": st.cols})
                return self._send(200, summary)
            if path == "/api/paper_screen":
                import paper
                st = _get(body["src"])
                with _lock:
                    P = paper.prepare(st, load_tags(st.session), body.get("end", "blunt"), body.get("g1", "male"),
                                      body.get("g2", "female"), body.get("basis", "T"),
                                      float(body.get("lo", 580)), float(body.get("hi", 960)), only=body.get("labels"))
                if min(P["n1"], P["n2"]) < 3:
                    raise ValueError("mỗi nhóm cần ít nhất 3 trứng đã gán nhóm")
                job = paper.start_job(P, n_perm=max(0, min(int(body.get("n_perm", 20)), 200)))
                return self._send(200, paper.job_view(job))
            if path == "/api/unsup":
                import unsup
                X = body["X"]
                if len(X) > 2000 or (X and len(X[0]) > 5000):
                    raise ValueError("ma trận quá lớn")
                data = unsup.run([[float("nan") if v is None else v for v in row] for row in X],
                                 k=int(body.get("k", 3)), method=body.get("method", "kmeans"), embed=body.get("embed", "pca"),
                                 scale=body.get("scale", "z"), eps=body.get("eps") or None,
                                 min_samples=int(body.get("min_samples", 3)), groups=body.get("groups"))
                return self._send(200, data)
            if path == "/api/ml":
                import ml
                st = _get(body["src"])
                with _lock:
                    P = _ml_prepare(st, float(body.get("x0", 580)), float(body.get("x1", 960)))
                    groups = load_tags(st.session)
                ends = {"both": ("blunt", "pointed"), "blunt": ("blunt",), "pointed": ("pointed",)}[body.get("ends", "both")]
                num = lambda k: int(body[k]) if body.get(k) is not None else None
                data = ml.run(P, groups, task=body.get("task", "infertile"), feats=body.get("feats", "dev"), ends=ends,
                              band=(float(body.get("lo", 604)), float(body.get("hi", 922))),
                              da=num("da"), db=num("db"), dr=num("dr"), model=body.get("model", "lr"),
                              n_perm=max(0, min(int(body.get("n_perm", 50)), 200)))
                return self._send(200, data)
            if path == "/api/sex":
                import sexclust
                import status
                st = _get(body["src"])
                with _lock:
                    P = _ml_prepare(st, float(body.get("x0", 580)), float(body.get("x1", 960)))
                    groups = load_tags(st.session)
                hatch = status.hatched(STATUS_DIR, st.session)
                labels = [str(x) for x in body.get("labels") or []] or sorted(hatch)
                data = sexclust.run(P, labels, band=(float(body.get("lo", 604)), float(body.get("hi", 922))),
                                    smooth=max(1, min(int(body.get("smooth", 11)), 51)),
                                    base_day=int(body.get("base", 2)), n_null=max(0, min(int(body.get("n_null", 200)), 1000)),
                                    groups=groups, hatch={l: v["date"] for l, v in hatch.items()})
                data["hatched"] = sorted(hatch)
                return self._send(200, data)
            if path == "/api/unsup_scan":
                import unsup
                days = {int(d): [None if row is None else [float("nan") if v is None else v for v in row] for row in rows]
                        for d, rows in body["days"].items()}
                configs = body.get("configs", [])
                if not days or len(configs) > 300 or len(next(iter(days.values()))) > 2000:
                    raise ValueError("cấu hình quét không hợp lệ")
                data = unsup.scan(days, configs, groups=body.get("groups"), k=int(body.get("k", 3)),
                                  method=body.get("method", "kmeans"), scale=body.get("scale", "z"),
                                  eps=body.get("eps") or None, min_samples=int(body.get("min_samples", 3)))
                return self._send(200, {"results": data})
            if path == "/api/job_cancel":
                import paper
                if body.get("id") in paper.JOBS:
                    paper.JOBS[body["id"]]["cancel"] = True
                return self._send(200, {"ok": True})
            if path == "/api/tags":
                st = _get(body["src"])
                return self._send(200, {"session": st.session, "groups": save_tags(st.session, body.get("groups", {}))})
            if path == "/api/status":
                import status
                st = _get(body["src"])
                if body.get("op") == "delete":
                    ev = status.delete(STATUS_DIR, st.session, body["id"])
                else:
                    ev = status.add(STATUS_DIR, st.session, body.get("event") or {})
                return self._send(200, {"session": st.session, "events": ev})
            if path == "/api/close":
                with _lock:
                    SOURCES.pop(body.get("src"), None)
                return self._send(200, {"ok": True})
            return self._send(404, {"error": "not found"})

        self._guard(run)

    # ---------------- PUT (upload từng file) ----------------
    def do_PUT(self):
        path, q = self._query()
        if path == "/api/status_photo":
            def run_photo():
                import status
                st = _get(q["src"])
                n = int(self.headers.get("Content-Length", 0))
                fn = status.save_photo(STATUS_DIR, st.session, q.get("name", ""), self.rfile.read(n))
                return self._send(200, {"photo": fn})
            return self._guard(run_photo)
        if path != "/api/upload":
            return self._send(404, {"error": "not found"})

        def run():
            rel = q["path"].replace("\\", "/").strip("/")
            dst = _upload_target(q["s"], rel)
            n = int(self.headers.get("Content-Length", 0))
            data = self.rfile.read(n)
            if os.path.exists(dst) and q.get("overwrite") != "1":
                if os.path.getsize(dst) == len(data):
                    return self._send(200, {"status": "exists", "path": rel})
                return self._send(409, {"status": "conflict", "path": rel,
                                        "error": "file đã tồn tại với kích thước khác"})
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            tmp = dst + ".part"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, dst)
            return self._send(200, {"status": "saved", "path": rel, "bytes": len(data)})

        self._guard(run)


def main():
    global STORE_ROOT
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Batch dashboard cho dữ liệu in-ovo")
    ap.add_argument("--store", "--root", dest="store", default=DEFAULT_STORE,
                    help="thư mục lưu dữ liệu được upload (mặc định data/BATCH_B)")
    ap.add_argument("--host", default="127.0.0.1", help="0.0.0.0 để máy khác trong LAN truy cập")
    ap.add_argument("--port", type=int, default=8050)
    a = ap.parse_args()
    STORE_ROOT = os.path.abspath(a.store)
    os.makedirs(STORE_ROOT, exist_ok=True)
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"Lưu upload vào : {STORE_ROOT}")
    print(f"Dashboard      : http://{'127.0.0.1' if a.host == '0.0.0.0' else a.host}:{a.port}")
    print("Chưa có dữ liệu nào được mở - hãy tải lên hoặc kết nối MongoDB từ trình duyệt.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
