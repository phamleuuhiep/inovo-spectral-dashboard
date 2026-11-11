# -*- coding: utf-8 -*-
"""Phân tích dùng chung cho mọi nguồn dữ liệu (FolderStore / MongoStore)."""

from datetime import datetime

import numpy as np


def _darksub(store, b, rec, grid):
    """Counts đã trừ dark của chính batch đó, nội suy về lưới bước sóng chung."""
    wl, raw = store.spectrum(b, rec)
    if wl is None:
        return None
    y = raw.astype(float)
    dwl, draw = store.spectrum(b, b["dark"])
    if dwl is not None:
        y = y - np.interp(wl, dwl, draw)
    if grid is not None and (len(wl) != len(grid) or not np.allclose(wl, grid)):
        y = np.interp(grid, wl, y)
    return y


def _raw(store, b, rec, grid):
    """Raw ADC đúng như trong file. Chỉ trả kèm lưới bước sóng riêng khi nó khác lưới chung
    (không nội suy - để xem dữ liệu gốc hoàn toàn)."""
    wl, raw = store.spectrum(b, rec)
    if wl is None:
        return {}
    out = {"raw": _round(raw)}
    if grid is None or len(wl) != len(grid) or not np.allclose(wl, grid):
        out["raw_wl"] = _round(wl, 2)
    return out


def _on_grid(store, b, rec, grid):
    """Raw ADC trên lưới chung (dùng cho so sánh nhiều trứng; nội suy chỉ khi lưới khác)."""
    wl, raw = store.spectrum(b, rec)
    if wl is None:
        return None
    if grid is not None and (len(wl) != len(grid) or not np.allclose(wl, grid)):
        return np.interp(grid, wl, raw)
    return raw


def _grid(store, batches):
    for b in batches:
        recs = [b["lamp"], b["dark"]] + [e[k][-1] for e in b["eggs"].values() for k in ("blunt", "pointed") if e.get(k)]
        for rec in recs:
            wl, _ = store.spectrum(b, rec)
            if wl is not None:
                return wl
    return None


def _round(arr, nd=1):
    return [None if not np.isfinite(v) else round(float(v), nd) for v in arr]


def _info(store):
    i = store.info()
    return {"incubation_start": i.get("incubation_start") and str(i["incubation_start"]),
            "created": i.get("created") and str(i["created"])}


def session_summary(store, lo, hi):
    batches = store.batches()
    grid = _grid(store, batches)
    band = None if grid is None else (grid >= lo) & (grid <= hi)

    labels = sorted({l for b in batches for l in b["eggs"]} | {l for b in batches for l in b["images"]})
    egg_ids, out_b, overview = {}, [], {}
    for b in batches:
        lamp = _darksub(store, b, b["lamp"], grid)
        lamp_band = float(np.nanmean(lamp[band])) if lamp is not None and band is not None and band.any() else None
        n_spec = sum(len(e.get("blunt", [])) + len(e.get("pointed", [])) for e in b["eggs"].values())
        out_b.append({
            "folder": b["folder"], "batch_no": b["batch_no"],
            "started": b["started"], "finished": b["finished"], "age_h": b["age_h"],
            "n_eggs": len(b["eggs"]), "n_spectra": n_spec, "n_images": len(b["images"]),
            "image_dirs": b["image_dirs"], "notes": b["notes"],
            "lamp_peak": b["lamp"]["peak_adc"] if b["lamp"] else None,
            "lamp_band": lamp_band,
            "lamp_ts": b["lamp"]["timestamp"] if b["lamp"] else None,
            "dark_peak": b["dark"]["peak_adc"] if b["dark"] else None,
            "warnings": b["warnings"][:30], "n_warnings": len(b["warnings"]),
        })
        for l in labels:
            e, cell = b["eggs"].get(l), {}
            if e:
                if e.get("egg_id"):
                    egg_ids[l] = e["egg_id"]
                for end in ("blunt", "pointed"):
                    if e.get(end) and band is not None and band.any():
                        y = _darksub(store, b, e[end][-1], grid)
                        if y is not None:
                            cell[end] = float(np.nanmean(y[band]))
                            cell[end + "_sat"] = e[end][-1]["saturated"]
                        r = _on_grid(store, b, e[end][-1], grid)
                        if r is not None:
                            cell[end + "_raw"] = float(np.nanmean(r[band]))
            img = b["images"].get(l)
            if img:
                cell["img_ts"] = img["ts"]
                cell["img"] = {"key": img["key"], "file": img["file"]}
            overview.setdefault(l, []).append(cell)

    return {
        "source": store.describe(),
        "session": store.session, **_info(store),
        "band": [lo, hi], "batches": out_b, "labels": labels,
        "egg_ids": egg_ids, "overview": overview,
        "server_time": datetime.now().isoformat(timespec="seconds"),
    }


def egg_detail(store, label):
    batches = store.batches()
    grid = _grid(store, batches)
    res = {"session": store.session, "label": label, "egg_id": None,
           "wavelengths": _round(grid, 2) if grid is not None else [], "batches": []}
    for b in batches:
        e, img = b["eggs"].get(label), b["images"].get(label)
        lamp = _darksub(store, b, b["lamp"], grid)
        lamp_raw, dark_raw = _raw(store, b, b["lamp"], grid), _raw(store, b, b["dark"], grid)
        item = {"folder": b["folder"], "batch_no": b["batch_no"], "age_h": b["age_h"],
                "started": b["started"], "lamp": _round(lamp) if lamp is not None else None,
                "lamp_raw": lamp_raw.get("raw"), "lamp_raw_wl": lamp_raw.get("raw_wl"),
                "dark_raw": dark_raw.get("raw"), "dark_raw_wl": dark_raw.get("raw_wl"),
                "lamp_ts": b["lamp"]["timestamp"] if b["lamp"] else None,
                "ends": {}, "image": None, "photo_confirmed_at": None}
        if e:
            res["egg_id"] = res["egg_id"] or e.get("egg_id")
            item["photo_confirmed_at"] = e.get("photo_confirmed_at")
            for end in ("blunt", "pointed"):
                recs = e.get(end) or []
                if not recs:
                    continue
                rec = {k: v for k, v in recs[-1].items() if k != "ref"}
                y = _darksub(store, b, recs[-1], grid)
                rec["counts"] = _round(y) if y is not None else None
                rec.update(_raw(store, b, recs[-1], grid))
                rec["n_repeats"] = len(recs)
                item["ends"][end] = rec
        if img:
            item["image"] = dict(img)
        res["batches"].append(item)
    return res


def compare(store, labels, end):
    """Dữ liệu gọn cho tab So sánh: mỗi trứng × mỗi batch, một đầu đo (blunt/pointed).
    counts = trừ dark, raw = ADC gốc (cùng lưới bước sóng)."""
    batches = store.batches()
    grid = _grid(store, batches)
    if not labels:
        labels = sorted({l for b in batches for l in b["eggs"]})
    out_b = []
    for b in batches:
        lamp = _darksub(store, b, b["lamp"], grid)
        lamp_r, dark_r = _on_grid(store, b, b["lamp"], grid), _on_grid(store, b, b["dark"], grid)
        out_b.append({"folder": b["folder"], "batch_no": b["batch_no"], "age_h": b["age_h"], "started": b["started"],
                      "lamp": _round(lamp) if lamp is not None else None,
                      "lamp_raw": _round(lamp_r) if lamp_r is not None else None,
                      "dark_raw": _round(dark_r) if dark_r is not None else None})
    eggs = {}
    for l in labels:
        series, egg_id = [], None
        for bi, b in enumerate(batches):
            e = b["eggs"].get(l)
            recs = (e or {}).get(end) or []
            if not recs:
                continue
            egg_id = egg_id or e.get("egg_id")
            r = recs[-1]
            y, raw = _darksub(store, b, r, grid), _on_grid(store, b, r, grid)
            if y is None:
                continue
            series.append({"bi": bi, "ts": r["timestamp"], "age_h": r["incubation_age_h"],
                           "peak_adc": r["peak_adc"], "saturated": r["saturated"],
                           "counts": _round(y), "raw": _round(raw)})
        eggs[l] = {"egg_id": egg_id, "series": series}
    return {"session": store.session, "end": end, "wavelengths": _round(grid, 2) if grid is not None else [],
            "batches": out_b, "eggs": eggs}
