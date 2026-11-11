#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import csv
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
import numpy as np
from serial import Serial

# Thư mục gốc project (cha của src/) - dùng để đặt output dataset cố định,
# bất kể app.py được chạy từ đâu (không phụ thuộc os.getcwd())
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DATASET_DIR = os.path.join(PROJECT_ROOT, "data", "dataset_inovo_spectra")
# Dataset cũ chứa Dark/Lamp dùng lại cho chế độ "chỉ đo trứng đã mở vỏ"
DEFAULT_REF_DIR = os.path.join(PROJECT_ROOT, "data", "dataset_exp_3_white_horizontal")

os.environ["QT_LOGGING_RULES"] = "qt.qpa.fonts=false"

from PyQt5.QtCore import Qt, QThread, pyqtSignal, QPoint, QDateTime, QDir, QFile, QModelIndex, QTimer, QUrl
from PyQt5.QtGui import QColor, QFont, QTextCharFormat, QDesktopServices, QKeySequence
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton,
    QVBoxLayout, QHBoxLayout, QFrame, QDialog, QLineEdit,
    QComboBox, QFileDialog, QMessageBox, QSpinBox, QCheckBox, QInputDialog,
    QDateTimeEdit, QDialogButtonBox, QFileSystemModel, QTreeView, QAbstractItemView,
    QTextBrowser, QSplitter, QMenu, QAction
)
import pyqtgraph as pg

# Import từ protocol.py
from protocol import (
    MessageType,
    ExposureMode,
    ExposureStatus,
    build_message,
    build_set_exposure_mode,
    build_set_exposure_time,
    parse_messages
)


# ==============================================================================
# HẰNG SỐ ĐO LƯỜNG THEO EggVision_Spectrometer_Dev_Brief.md
# ==============================================================================
N_AVERAGE = 5                 # Số khung thô trung bình cho Dark/Reference - mục 7.1
EGG_N_FRAMES_DEFAULT = 3       # Số khung thô mỗi lần đo trứng: lưu trung bình + từng khung (ước lượng nhiễu thiết bị)
EGG_PLACEMENTS_DEFAULT = 1     # Số lần nhấc-đặt lại mỗi trứng ở bước đo batch (ước lượng sai số đặt trứng)
FRAME_RSD_BAND = (600, 900)    # Dải tính RSD giữa các khung (vùng có tín hiệu xuyên vỏ)
FRAME_RSD_MIN_COUNTS = 1000.0  # Chỉ tính RSD ở pixel có tín hiệu (đã trừ dark) đủ lớn
FRAME_RSD_WARN_PCT = 1.0       # Cảnh báo khi RSD trung vị giữa các khung > 1% (Quality Gate QG4)
ADC_MAX = 65535                # Thang ADC 16-bit của T3 - mục 5.2
SATURATION_THRESHOLD = 0.95    # Ngưỡng coi là bão hòa - mục 5.2
COLOR_BAND_A = (550, 650)      # Dải bước sóng A để theo dõi trôi nhiệt độ màu - mục 8.3
COLOR_BAND_B = (850, 950)      # Dải bước sóng B để theo dõi trôi nhiệt độ màu - mục 8.3
EPS = 1.0                      # Ngưỡng tối thiểu (ADC counts, đã trừ dark) để absorbance hợp lệ - mục 9.1
EXPOSURE_MS = 1000             # Exposure mặc định (ms, luôn Manual - không dùng auto-exposure), chỉnh trong Cấu hình
EXPOSURE_MIN_MS = 1
EXPOSURE_MAX_MS = 60000
EXPOSURE_TOLERANCE = 0.01      # Sai lệch tối đa (1%) giữa exposure thiết bị báo về và giá trị cài đặt

BATCH_SIZE_DEFAULT = 5         # Số trứng mỗi batch: đo hết N trứng chưa mở vỏ (Phase 3) rồi đo lại N trứng đó đã mở vỏ (Phase 4)
SESSION_INFO_FILE = "session_info.json"   # Thông tin session đo liên tục (thư mục cha của các batch)

# Phase 3 (batch trứng chưa mở vỏ): mỗi trứng 4 phép đo cố định, mỗi phép đo 1 file CSV.
# (tiền tố file CSV / measurement_type, key bản dịch mô tả bước)
SINGLE_EGG_STEPS = [
    ("unopened_blunt_undyed", "s3_step_1"),
    ("unopened_pointed_undyed", "s3_step_2"),
    ("unopened_blunt_dyed", "s3_step_3"),
    ("unopened_pointed_dyed", "s3_step_4"),
]
SINGLE_EGG_DYE_STEP = 2        # Trước bước thứ 3 (index 2) của mỗi trứng phải bơm màu vào trứng

# Chế độ chuẩn độ màu: đo tiếp N trứng ĐÃ MỞ VỎ (cùng số thứ tự #1..#N), Dark/Lamp dùng lại từ dataset cũ.
# Mỗi trứng (cốc KHÔNG xê dịch): nền x TITRATION_BASELINE_REPEATS -> [nước đối chứng x R]
#   -> màu mức 1..M (lượng TÍCH LŨY, ml, tăng dần, tối đa TITRATION_MAX_ML) x R
TITRATION_LEVELS_DEFAULT = "5, 10, 20, 40, 80"   # ml màu tích lũy sau mỗi lần thêm
TITRATION_MAX_ML = 80.0
TITRATION_REPEATS_DEFAULT = 2                    # R: số lần đo mỗi bậc nước/màu
TITRATION_BASELINE_REPEATS = 3                   # số lần đo nền -> ước lượng nhiễu thiết bị


# Chế độ thí nghiệm (key bản dịch, mã chế độ) theo thứ tự trong Cấu hình
EXPERIMENT_MODES = [
    ("exp_mode_full", "full"),
    ("exp_mode_opened", "opened_only"),
    ("exp_mode_titration", "dye_titration"),
    ("exp_mode_shell", "shell_only"),
    ("exp_mode_embryo", "embryo"),
]
# Chế độ đo batch (dùng chung logic Phase 4): tiền tố file + measurement_type, còn lại là "egg"
BATCH_KIND = {"shell_only": "shell", "embryo": "embryo"}
# Các đầu trứng đo lần lượt cho mỗi mẫu batch (None = không phân biệt đầu): trứng có phôi đo đầu tù rồi đầu nhọn
BATCH_ENDS = {"embryo": ("blunt", "pointed")}

# Spectrometer có thể nằm ở COM3 hoặc COM5: chế độ tự động thử lần lượt theo thứ tự này
SPECTRO_PORTS = ["COM3", "COM5"]


def parse_titration_levels(text, max_ml=TITRATION_MAX_ML):
    """'5, 10, 20' -> [5.0, 10.0, 20.0] (ml). Phân cách bằng dấu phẩy/chấm phẩy/khoảng trắng, thập phân dùng dấu chấm.
    Trả về None nếu rỗng, không phải số, <= 0, không tăng dần hoặc vượt max_ml."""
    try:
        levels = [float(x) for x in re.split(r"[,;\s]+", text.strip()) if x]
    except ValueError:
        return None
    if not levels or levels[0] <= 0 or levels[-1] > max_ml or any(b <= a for a, b in zip(levels, levels[1:])):
        return None
    return levels


def build_titration_plan(n_eggs, levels, repeats, with_water=True):
    """
    Thứ tự toàn bộ phép đo chuẩn độ. Mỗi phần tử = 1 lần bấm ENTER:
    egg (1..N), step (0 = nền, rồi nước nếu có, rồi các mức màu), kind ("base" | "water" | "dye"),
    amount = ml màu tích lũy, delta = ml cần thêm trước bậc này, rep / n_rep.
    Nước đối chứng thêm đúng bằng thể tích lần thêm màu đầu tiên.
    """
    steps = [("base", 0.0, 0.0, TITRATION_BASELINE_REPEATS)]
    if with_water:
        steps.append(("water", 0.0, levels[0], repeats))
    prev = 0.0
    for lv in levels:
        steps.append(("dye", lv, lv - prev, repeats))
        prev = lv
    plan = []
    for egg in range(1, n_eggs + 1):
        for step, (kind, amount, delta, n_rep) in enumerate(steps):
            for rep in range(1, n_rep + 1):
                plan.append({"egg": egg, "step": step, "n_steps": len(steps), "kind": kind,
                             "amount": amount, "delta": delta, "rep": rep, "n_rep": n_rep})
    return plan


def fmt_amount(v):
    return f"{v:g}"


def exposure_matches(measured_ms, target_ms, tolerance=EXPOSURE_TOLERANCE):
    """Exposure thiết bị báo về có khớp giá trị cài đặt (trong sai số tolerance) hay không."""
    if measured_ms is None or np.isnan(measured_ms) or target_ms <= 0:
        return False
    return abs(measured_ms - target_ms) / target_ms <= tolerance


def check_saturation(spectrum, adc_max=ADC_MAX, threshold=SATURATION_THRESHOLD):
    """Trả về (có bão hòa hay không, số pixel bão hòa) - Dev Brief mục 5.2."""
    saturated_mask = spectrum >= (adc_max * threshold)
    n_saturated = int(np.sum(saturated_mask))
    return n_saturated > 0, n_saturated


def frame_rsd_pct(frames, dark, wavelengths, band=FRAME_RSD_BAND, min_counts=FRAME_RSD_MIN_COUNTS):
    """
    Độ lặp lại giữa các khung của CÙNG một lần đặt trứng: trung vị theo bước sóng của
    std/mean (%) trên các pixel trong `band` có tín hiệu (đã trừ dark) >= min_counts.
    NaN nếu < 2 khung hoặc không có pixel đủ tín hiệu.
    """
    frames = np.asarray(frames, dtype=np.float64)
    if frames.ndim != 2 or len(frames) < 2:
        return float("nan")
    corr = frames - dark if dark is not None and len(dark) == frames.shape[1] else frames
    mean = corr.mean(axis=0)
    mask = (wavelengths >= band[0]) & (wavelengths <= band[1]) & (mean >= min_counts)
    if not mask.any():
        return float("nan")
    return float(np.median(corr[:, mask].std(axis=0, ddof=1) / mean[mask]) * 100.0)


def color_ratio(spectrum, wavelengths):
    """Tỷ lệ băng màu (550-650nm / 850-950nm) để phát hiện trôi nhiệt độ màu - mục 8.3."""
    band_a = spectrum[(wavelengths >= COLOR_BAND_A[0]) & (wavelengths <= COLOR_BAND_A[1])]
    band_b = spectrum[(wavelengths >= COLOR_BAND_B[0]) & (wavelengths <= COLOR_BAND_B[1])]
    if band_a.size == 0 or band_b.size == 0:
        return float("nan")
    denom = float(np.sum(band_b))
    if denom <= 0:
        return float("nan")
    return float(np.sum(band_a) / denom)


def safe_absorbance(ref_dark_corr, sample_dark_corr, t_ref_ms, t_sample_ms, eps=EPS):
    """
    Absorbance = log10( (R-D_r)/t_r / (S-D_s)/t_s ), chuẩn hóa theo exposure_time
    (Giải pháp A - mục 5.4) để bù sai lệch nhỏ giữa exposure Manual thiết bị báo về và giá trị cài đặt.
    EPS áp dụng trên giá trị đã trừ dark nhưng CHƯA chia thời gian (mục 9.1), điểm
    không hợp lệ được đánh dấu NaN thay vì điền giá trị giả.
    """
    ref_dark_corr = np.asarray(ref_dark_corr, dtype=np.float64)
    sample_dark_corr = np.asarray(sample_dark_corr, dtype=np.float64)
    valid = (ref_dark_corr > eps) & (sample_dark_corr > eps)

    absorbance = np.full_like(ref_dark_corr, np.nan, dtype=np.float64)
    if t_ref_ms and t_sample_ms and t_ref_ms > 0 and t_sample_ms > 0:
        ref_norm = ref_dark_corr / t_ref_ms
        sample_norm = sample_dark_corr / t_sample_ms
    else:
        ref_norm = ref_dark_corr
        sample_norm = sample_dark_corr

    absorbance[valid] = np.log10(ref_norm[valid] / sample_norm[valid])
    return absorbance, valid


REF_FILE_RE = re.compile(r"^(dark|lamp)_(\d{8}_\d{6})\.csv$")


def load_reference_dir(folder):
    """
    Nạp Dark/Lamp của 1 dataset cũ để dùng lại (chế độ "chỉ đo trứng đã mở vỏ").
    Lấy file dark_*/lamp_* MỚI NHẤT theo timestamp trong tên file; exposure_ms và
    n_averaged lấy từ metadata_log.csv của folder đó (NaN/None nếu không có).
    Trả về {"wavelengths", "dark": {...}, "lamp": {...}}; FileNotFoundError(kind) nếu thiếu file.
    """
    latest = {}
    for name in os.listdir(folder):
        m = REF_FILE_RE.match(name)
        if m and (m.group(1) not in latest or m.group(2) > latest[m.group(1)][0]):
            latest[m.group(1)] = (m.group(2), name)
    for kind in ("dark", "lamp"):
        if kind not in latest:
            raise FileNotFoundError(kind)

    log = {}
    log_path = os.path.join(folder, "metadata_log.csv")
    if os.path.exists(log_path):
        with open(log_path, newline="", encoding="utf-8") as f:
            log = {row["sample_id"]: row for row in csv.DictReader(f)}

    refs = {}
    for kind in ("dark", "lamp"):
        name = latest[kind][1]
        path = os.path.join(folder, name)
        data = np.loadtxt(path, delimiter=",", skiprows=1, usecols=(0, 1), ndmin=2)
        if "wavelengths" not in refs:
            refs["wavelengths"] = data[:, 0]
        elif len(data) != len(refs["wavelengths"]):
            raise ValueError(f"{name}: {len(data)} pixel ≠ {len(refs['wavelengths'])}")
        row = log.get(os.path.splitext(name)[0], {})
        try:
            exposure_ms = float(row.get("exposure_ms", ""))
        except ValueError:
            exposure_ms = float("nan")
        n_avg = row.get("n_averaged", "")
        refs[kind] = {
            "path": path, "name": name, "spectrum": data[:, 1], "exposure_ms": exposure_ms,
            "n_averaged": int(n_avg) if n_avg.isdigit() else None,
        }
    return refs


# ==============================================================================
# HỆ THỐNG ĐA NGÔN NGỮ (i18n)
# ==============================================================================
TRANSLATIONS = {
    "vi": {
        "app_title": "SPECTRALENS PRO • THU THẬP & PHÂN TÍCH QUANG PHỔ IN-OVO",
        "settings": "⚙ Cấu hình",
        "theme_light": "☀️ Sáng",
        "theme_dark": "🌙 Tối",
        "kpi_status": "TRẠNG THÁI",
        "kpi_peak": "GIÁ TRỊ ĐỈNH",
        "kpi_lambda_peak": "BƯỚC SÓNG ĐỈNH",
        "kpi_egg_count": "SỐ TRỨNG ĐÃ LƯU",
        "status_offline": "OFFLINE",
        "status_stopped": "ĐÃ DỪNG",
        "status_online": "ONLINE",
        "status_disconnected": "MẤT KẾT NỐI",
        "status_phase1": "PHASE 1: MÔI TRƯỜNG TỐI",
        "status_phase2": "PHASE 2: ĐÈN NỀN",
        "status_phase3": "PHASE 3: BATCH TRỨNG CHƯA MỞ VỎ",
        "status_phase4": "PHASE 4: BATCH TRỨNG ĐÃ MỞ VỎ",
        "status_egg_detected": "ĐÃ LƯU TRỨNG",
        "status_single_saved": "ĐÃ LƯU S3",
        "status_egg_capturing": "ĐANG ĐO TRỨNG",
        "status_remeasuring_ref": "ĐANG ĐO LẠI ĐÈN NỀN",
        "ctrl_title": "ĐIỀU KHIỂN & THÍ NGHIỆM",
        "btn_start_stream": "▶ ĐO LIVE LIÊN TỤC",
        "btn_stop_stream": "⏹ DỪNG LIVE",
        "btn_start_exp": "🧪 BẮT ĐẦU THÍ NGHIỆM",
        "btn_stop_exp": "⏹ DỪNG THÍ NGHIỆM",
        "btn_trigger_egg": "📸 LƯU MẪU TRỨNG (ENTER)",
        "btn_trigger_single": "📸 S3 #{egg}/{n}: {step} (ENTER)",
        "btn_trigger_batch": "📸 S4 #{i}/{n}: TRỨNG MỞ VỎ (ENTER)",
        "s3_step_1": "Đầu tù - chưa bơm màu",
        "s3_step_2": "Đầu nhọn - chưa bơm màu",
        "s3_step_3": "Đầu tù - đã bơm màu",
        "s3_step_4": "Đầu nhọn - đã bơm màu",
        "btn_undo_egg": "↩ HỦY MẪU VỪA LƯU",
        "btn_remeasure_ref": "🔄 ĐO LẠI ĐÈN NỀN",
        "btn_snapshot": "📸 CHỤP SNAPSHOT",
        "btn_clear_snapshot": "Xóa Snapshot",
        "warn_saturated": "⚠ Bão hòa",
        "warn_color_drift": "⚠ Trôi màu {pct:.1f}%",
        "warn_exposure_mismatch": "⚠ Lệch exposure Dark {pct:.0f}%",
        "mode_spectrum": "📊 Chế độ Phổ (Counts)",
        "mode_absorbance": "🧪 Chế độ Hấp Thụ (OD)",
        "axis_wavelength": "Bước sóng",
        "axis_intensity": "Cường độ",
        "axis_absorbance": "Độ hấp thụ",
        "dlg_title": "Cấu hình thiết bị & Thí nghiệm",
        "lbl_port": "Cổng Serial (COM / TTY):",
        "chk_port_auto": "Tự động dò cổng (COM3, không được thì COM5)",
        "lbl_baud": "Tốc độ Baud:",
        "lbl_exposure": "Exposure:",
        "lbl_batch_size": "Số trứng mỗi batch:",
        "lbl_egg_frames": "Số khung mỗi lần đo trứng (lưu từng khung + trung bình):",
        "lbl_egg_placements": "Số lần nhấc–đặt lại mỗi trứng (đo batch):",
        "placement_suffix": " · lần đặt {p}/{P}",
        "status_replace_egg": "Nhấc trứng #{egg} ra rồi đặt lại → [ENTER] đo lần đặt {p}/{P}",
        "warn_frame_rsd": "⚠ RSD giữa các khung {pct:.1f}%",
        "msg_exposure_mismatch": "⚠ Thiết bị chưa nhận đúng exposure.\nCài đặt: Manual {target:.0f} ms. Thiết bị báo: chế độ {mode}, {measured} ms.",
        "warn_exposure_target": "⚠ Exposure {ms:.0f} ms ≠ cài đặt {target:.0f} ms",
        "lbl_dir": "Thư mục gốc lưu dataset (mỗi lần thí nghiệm tạo thư mục con <timestamp>_<chế độ>):",
        "btn_save": "Lưu cấu hình",
        "btn_cancel": "Hủy",
        "msg_conn_err_title": "Lỗi kết nối",
        "msg_conn_err_body": "Không thể kết nối Spectrometer:\n",
        "msg_exp_start_title": "Phase 1: Môi trường tối",
        "msg_exp_start_body": "Hãy tắt hết đèn (môi trường tối hoàn toàn, chưa đặt vật thể).\nNhấn OK để đo Dark (trung bình 5 khung).",
        "msg_phase2_title": "Phase 2: Đèn nền",
        "msg_phase2_body": "Đã hoàn tất đo Dark.\nHãy bật đèn nền (vẫn chưa đặt trứng), nhấn OK để đo Reference (trung bình 5 khung).",
        "msg_phase3_title": "Phase 3: Batch trứng chưa mở vỏ",
        "instr_full": "Đo lần lượt {n} trứng chưa mở vỏ, mỗi trứng 4 lần: đầu tù và đầu nhọn, trước và sau khi bơm màu.\nĐặt trứng #1 (chưa bơm màu) theo hướng ĐẦU TÙ, nhấn [ENTER] hoặc bấm nút lưu để chụp.",
        "msg_dye_title": "Phase 3: Bơm màu",
        "msg_dye_body": "Đã đo xong trứng #{egg} chưa bơm màu (đầu tù + đầu nhọn).\nHãy bơm màu vào trứng #{egg} rồi nhấn OK, sau đó đo tiếp đầu tù và đầu nhọn.",
        "msg_next_egg_title": "Phase 3: Trứng tiếp theo",
        "msg_next_egg_body": "Đã đo xong trứng #{done}.\nĐặt trứng #{next} (chưa bơm màu) theo hướng ĐẦU TÙ, nhấn [ENTER] để đo.",
        "msg_phase4_title": "Phase 4: Batch trứng đã mở vỏ",
        "msg_phase4_body": "Đã đo xong {n} trứng chưa mở vỏ.\nMở vỏ và đo lại đúng {n} trứng đó theo thứ tự #1 → #{n}: đặt từng trứng vào khay, nhấn [ENTER] hoặc bấm nút lưu để chụp.",
        "msg_batch_done_title": "Hoàn tất batch",
        "msg_batch_done_body": "Đã đo xong {n} trứng mở vỏ.\nNhấn 'Dừng thí nghiệm' để kết thúc, hoặc 'Hủy mẫu vừa lưu' để đo lại trứng cuối.",
        "msg_exp_finished_title": "Hoàn tất thí nghiệm",
        "msg_exp_finished_body": "Đã dừng thí nghiệm. Tổng số mẫu trứng đã lưu: ",
        "msg_restart_worker": "Đã lưu cấu hình. Hãy dừng rồi bật lại Live/Thí nghiệm để áp dụng cổng/baudrate mới.",
        "lbl_exp_mode": "Chế độ thí nghiệm:",
        "exp_mode_full": "Đầy đủ 4 phase (Dark → Lamp → chưa mở → mở vỏ)",
        "exp_mode_opened": "Chỉ đo trứng đã mở vỏ (Dark → Lamp → N trứng mở vỏ)",
        "exp_mode_shell": "Chỉ đo vỏ trứng (Dark → Lamp → N vỏ)",
        "status_phase_shell": "ĐO VỎ TRỨNG",
        "btn_trigger_shell": "📸 VỎ #{i}/{n} (ENTER)",
        "msg_shell_title": "Đo vỏ trứng",
        "msg_refs_done": "Đã hoàn tất Dark + Lamp.",
        "instr_shell": "Đo lần lượt {n} vỏ trứng (#1 → #{n}, đánh số trùng mã trứng nếu có). Đặt vỏ #1 vào khay đúng vị trí và hướng như khi đo trứng, nhấn [ENTER] hoặc bấm nút lưu để chụp.",
        "msg_shell_done": "Đã đo xong {n} vỏ trứng.\nNhấn 'Dừng thí nghiệm' để kết thúc, hoặc 'Hủy mẫu vừa lưu' để đo lại vỏ cuối.",
        "lbl_ref_dir": "Thư mục dataset chứa Dark/Lamp dùng lại:",
        "chk_reuse_ref": "Dùng lại Dark/Lamp từ dataset cũ (không đo mới)",
        "exp_mode_embryo": "Đo trứng có phôi thực tế (Dark → Lamp → N trứng, không mở vỏ)",
        "status_phase_embryo": "ĐO TRỨNG CÓ PHÔI",
        "btn_trigger_embryo": "📸 TRỨNG CÓ PHÔI #{i}/{n} (ENTER)",
        "msg_embryo_title": "Đo trứng có phôi",
        "instr_embryo": "Đo lần lượt {n} trứng có phôi (KHÔNG mở vỏ, không bơm màu). Mỗi trứng: đặt theo hướng ĐẦU TÙ → [ENTER] → nhập ID → đo đầu tù → xoay sang ĐẦU NHỌN → [ENTER] → đo đầu nhọn → chuyển trứng sang camera Basler chụp ảnh → nhấn OK xác nhận.",
        "msg_embryo_done": "Đã đo xong {n} trứng có phôi.\nNhấn 'Dừng thí nghiệm' để kết thúc, hoặc 'Hủy mẫu vừa lưu' để đo lại trứng cuối.",
        "dlg_egg_id_title": "Nhập ID trứng",
        "dlg_egg_id_prompt": "Trứng #{egg}/{n}: đặt trứng vào khay theo hướng ĐẦU TÙ rồi nhập ID (dùng ID này để đặt tên ảnh Basler).\nNhấn OK để bắt đầu đo phổ.",
        "err_egg_id_empty": "ID trứng không được để trống.",
        "err_egg_id_dup": "ID '{id}' đã dùng cho trứng #{egg} trong lần thí nghiệm này.",
        "msg_photo_title": "Chụp ảnh Basler",
        "msg_photo_body": "Đã đo xong phổ trứng ID {id} (#{egg}/{n}).\nChuyển trứng sang camera Basler và chụp ảnh, đặt tên ảnh theo ID {id}.\nNhấn OK khi đã chụp xong.",
        "end_blunt": "Đầu tù",
        "end_pointed": "Đầu nhọn",
        "msg_turn_pointed": "Đã đo xong ĐẦU TÙ trứng #{egg} (ID {id}).\nXoay trứng, đặt theo hướng ĐẦU NHỌN, nhấn [ENTER] để đo.",
        "dlg_incub_title": "Thời điểm bắt đầu ấp",
        "dlg_incub_prompt": "Ngày và giờ bắt đầu ấp lô trứng này:",
        "err_incub_future": "Thời điểm bắt đầu ấp không được ở tương lai.",
        "chk_continuous": "Lưu theo session: mỗi lần đo = 1 thư mục con batch_XX_<timestamp>, có thể đo tiếp vào session cũ",
        "msg_session_title": "Session đo liên tục",
        "msg_session_body": "Bắt đầu session mới, hay đo tiếp batch kế tiếp của session cũ?",
        "btn_session_new": "Session mới",
        "btn_session_continue": "Tiếp tục session cũ...",
        "err_session_missing": "Thư mục này không phải session (không có session_info.json):",
        "msg_session_resume": "Tiếp tục session:\n{dir}\n\nĐã có {done} batch. Sắp đo batch {n}.\nBắt đầu ấp: {incub}",
        "status_phase_opened": "ĐO TRỨNG ĐÃ MỞ VỎ",
        "btn_trigger_opened": "📸 TRỨNG MỞ VỎ #{i}/{n} (ENTER)",
        "msg_ref_load_err_title": "Không nạp được Dark/Lamp",
        "err_ref_missing": "Không tìm thấy file {kind}_<timestamp>.csv trong thư mục.",
        "msg_ref_wl_mismatch": "Số pixel của thiết bị ({n_dev}) khác file Dark/Lamp ({n_ref}).\nKhông thể dùng lại Dark/Lamp này.",
        "msg_opened_start_title": "Đo trứng đã mở vỏ",
        "msg_reuse_ref_body": "Dùng lại Dark/Lamp từ:\n{dir}\n\n• Dark: {dark} ({dark_ms})\n• Lamp: {lamp} ({lamp_ms})\n\nExposure hiện tại: {cur}\n{warn}",
        "instr_opened": "Đặt trứng #1 đã mở vỏ vào khay, nhấn OK rồi [ENTER] để đo lần lượt {n} trứng.",
        "warn_ref_exposure": "⚠ Khác exposure lúc đo Lamp → nên đặt exposure {ms:.0f} ms trong Cấu hình trước khi đo.\n",
        "exp_mode_titration": "Chuẩn độ màu trên trứng đã mở vỏ",
        "lbl_titr_levels": "Lượng màu TÍCH LŨY sau mỗi lần thêm, ml, từ ít đến nhiều (tối đa 80):",
        "lbl_titr_repeats": "Số lần đo mỗi mức:",
        "chk_titr_water": "Có bước nước đối chứng",
        "err_titr_levels": "Các mức màu phải là số ml dương, TĂNG DẦN, tối đa {max:g} ml, phân cách bằng dấu phẩy (thập phân dùng dấu chấm).\nVí dụ: 5, 10, 20, 40, 80",
        "status_titration": "CHUẨN ĐỘ MÀU",
        "btn_trigger_titr": "📸 #{egg}/{n} · {step} · lần {rep}/{n_rep} (ENTER)",
        "titr_step_base": "Nền (chưa thêm)",
        "titr_step_water": "Nước {ml} ml",
        "titr_step_dye": "Màu {ml} ml",
        "msg_titr_title": "Chuẩn độ màu",
        "titr_plan_water": " → nước {ml} ml ×{rep}",
        "titr_plan_line": "Mỗi trứng: nền ×{base_rep}{water} → màu {levels} ml (tích lũy) ×{rep} mỗi mức.\nTổng: {n} trứng × {per_egg} lần đo = {total} lần đo.",
        "instr_titr": "{plan}\n\nĐặt cốc trứng #1 (đã mở vỏ) đúng vị trí đánh dấu. KHÔNG xê dịch cốc cho tới khi đo xong trứng này.\nNhấn OK rồi [ENTER] để đo nền.",
        "msg_titr_water": "Đã đo xong nền trứng #{egg}.\nThêm {ml} ml NƯỚC (bằng thể tích lần thêm màu đầu tiên), khuấy nhẹ, chờ ~1 phút rồi nhấn OK.\nKhông xê dịch cốc.",
        "msg_titr_dye": "Trứng #{egg}: thêm {ml} ml màu (tổng {total} ml), khuấy nhẹ giống các lần trước, chờ ~1 phút rồi nhấn OK.\nKhông xê dịch cốc.",
        "msg_titr_next_egg": "Đã đo xong trứng #{done}.\nĐặt cốc trứng #{next} (đã mở vỏ) đúng vị trí đánh dấu, nhấn OK rồi [ENTER] để đo nền.",
        "msg_titr_done": "Đã đo xong {n} trứng ({total} lần đo).\nNhấn 'Dừng thí nghiệm' để kết thúc, hoặc 'Hủy mẫu vừa lưu' để đo lại lần cuối.",
        "rtl": False
    },
    "en": {
        "app_title": "SPECTRALENS PRO • IN-OVO SPECTRAL ACQUISITION DASHBOARD",
        "settings": "⚙ Settings",
        "theme_light": "☀️ Light",
        "theme_dark": "🌙 Dark",
        "kpi_status": "STATUS",
        "kpi_peak": "PEAK VALUE",
        "kpi_lambda_peak": "PEAK WAVELENGTH",
        "kpi_egg_count": "SAVED EGGS",
        "status_offline": "OFFLINE",
        "status_stopped": "STOPPED",
        "status_online": "ONLINE",
        "status_disconnected": "DISCONNECTED",
        "status_phase1": "PHASE 1: DARK ROOM",
        "status_phase2": "PHASE 2: BACKGROUND LAMP",
        "status_phase3": "PHASE 3: UNOPENED EGG BATCH",
        "status_phase4": "PHASE 4: OPENED EGG BATCH",
        "status_egg_detected": "EGG SAVED",
        "status_single_saved": "S3 SAVED",
        "status_egg_capturing": "CAPTURING EGG",
        "status_remeasuring_ref": "RE-MEASURING REFERENCE",
        "ctrl_title": "CONTROL & EXPERIMENT",
        "btn_start_stream": "▶ START LIVE MONITOR",
        "btn_stop_stream": "⏹ STOP LIVE",
        "btn_start_exp": "🧪 START EXPERIMENT",
        "btn_stop_exp": "⏹ STOP EXPERIMENT",
        "btn_trigger_egg": "📸 SAVE EGG SAMPLE (ENTER)",
        "btn_trigger_single": "📸 S3 #{egg}/{n}: {step} (ENTER)",
        "btn_trigger_batch": "📸 S4 #{i}/{n}: OPENED EGG (ENTER)",
        "s3_step_1": "Blunt end - before dye",
        "s3_step_2": "Pointed end - before dye",
        "s3_step_3": "Blunt end - after dye",
        "s3_step_4": "Pointed end - after dye",
        "btn_undo_egg": "↩ UNDO LAST EGG",
        "btn_remeasure_ref": "🔄 RE-MEASURE REFERENCE",
        "btn_snapshot": "📸 TAKE SNAPSHOT",
        "btn_clear_snapshot": "Clear Snapshot",
        "warn_saturated": "⚠ Saturated",
        "warn_color_drift": "⚠ Color drift {pct:.1f}%",
        "warn_exposure_mismatch": "⚠ Dark exposure mismatch {pct:.0f}%",
        "mode_spectrum": "📊 Raw Counts Mode",
        "mode_absorbance": "🧪 Absorbance Mode (OD)",
        "axis_wavelength": "Wavelength",
        "axis_intensity": "Intensity",
        "axis_absorbance": "Absorbance",
        "dlg_title": "Device & Experiment Settings",
        "lbl_port": "Serial Port (COM / TTY):",
        "chk_port_auto": "Auto-detect port (COM3, fall back to COM5)",
        "lbl_baud": "Baud Rate:",
        "lbl_exposure": "Exposure:",
        "lbl_batch_size": "Eggs per batch:",
        "lbl_egg_frames": "Frames per egg measurement (each frame + mean saved):",
        "lbl_egg_placements": "Re-placements per egg (batch):",
        "placement_suffix": " · placement {p}/{P}",
        "status_replace_egg": "Lift egg #{egg} out and place it again → [ENTER] for placement {p}/{P}",
        "warn_frame_rsd": "⚠ Frame-to-frame RSD {pct:.1f}%",
        "msg_exposure_mismatch": "⚠ The device did not apply the exposure.\nSet: Manual {target:.0f} ms. Device reports: mode {mode}, {measured} ms.",
        "warn_exposure_target": "⚠ Exposure {ms:.0f} ms ≠ set {target:.0f} ms",
        "lbl_dir": "Dataset root folder (each experiment run creates a <timestamp>_<mode> subfolder):",
        "btn_save": "Save Settings",
        "btn_cancel": "Cancel",
        "msg_conn_err_title": "Connection Error",
        "msg_conn_err_body": "Could not connect to Spectrometer:\n",
        "msg_exp_start_title": "Phase 1: Dark Room",
        "msg_exp_start_body": "Turn off all lights (fully dark, no object placed).\nClick OK to capture the Dark measurement (average of 5 frames).",
        "msg_phase2_title": "Phase 2: Background Lamp",
        "msg_phase2_body": "Dark measurement complete.\nTurn on the background lamp (still no egg placed), click OK to capture the Reference measurement (average of 5 frames).",
        "msg_phase3_title": "Phase 3: Unopened Egg Batch",
        "instr_full": "Measure {n} unopened eggs one by one, 4 times each: blunt and pointed end, before and after dye injection.\nPlace egg #1 (undyed) BLUNT END first, then press [ENTER] or click the save button to capture.",
        "msg_dye_title": "Phase 3: Dye Injection",
        "msg_dye_body": "Undyed measurements of egg #{egg} done (blunt + pointed end).\nInject the dye into egg #{egg}, click OK, then measure the blunt and pointed ends again.",
        "msg_next_egg_title": "Phase 3: Next Egg",
        "msg_next_egg_body": "Egg #{done} done.\nPlace egg #{next} (undyed) BLUNT END first and press [ENTER] to measure.",
        "msg_phase4_title": "Phase 4: Opened Egg Batch",
        "msg_phase4_body": "All {n} unopened eggs measured.\nOpen and re-measure the same {n} eggs in order #1 → #{n}: place each egg in the fixture and press [ENTER] or click the save button.",
        "msg_batch_done_title": "Batch Complete",
        "msg_batch_done_body": "All {n} opened eggs measured.\nClick 'Stop Experiment' to finish, or 'Undo Last Egg' to re-measure the last one.",
        "msg_exp_finished_title": "Experiment Finished",
        "msg_exp_finished_body": "Experiment stopped. Total eggs collected: ",
        "msg_restart_worker": "Settings saved. Stop and restart Live/Experiment to apply the new port/baudrate.",
        "lbl_exp_mode": "Experiment mode:",
        "exp_mode_full": "Full 4 phases (Dark → Lamp → unopened → opened)",
        "exp_mode_opened": "Opened eggs only (Dark → Lamp → N opened eggs)",
        "exp_mode_shell": "Eggshell only (Dark → Lamp → N shells)",
        "status_phase_shell": "MEASURING SHELLS",
        "btn_trigger_shell": "📸 SHELL #{i}/{n} (ENTER)",
        "msg_shell_title": "Eggshell measurement",
        "msg_refs_done": "Dark + Lamp complete.",
        "instr_shell": "Measure {n} eggshells in turn (#1 → #{n}, numbered like the eggs if available). Place shell #1 in the tray at the same position and orientation used for eggs, press [ENTER] or click save to capture.",
        "msg_shell_done": "Finished measuring {n} eggshells.\nClick 'Stop experiment' to finish, or 'Undo last sample' to re-measure the last shell.",
        "lbl_ref_dir": "Dataset folder with Dark/Lamp to reuse:",
        "chk_reuse_ref": "Reuse Dark/Lamp from a previous dataset (skip new measurement)",
        "exp_mode_embryo": "Real embryonated eggs (Dark → Lamp → N eggs, shell not opened)",
        "status_phase_embryo": "EMBRYONATED EGGS",
        "btn_trigger_embryo": "📸 EMBRYO EGG #{i}/{n} (ENTER)",
        "msg_embryo_title": "Embryonated egg measurement",
        "instr_embryo": "Measure {n} embryonated eggs in turn (shell NOT opened, no dye). For each egg: place it BLUNT END → [ENTER] → enter the ID → blunt end is measured → turn to POINTED END → [ENTER] → pointed end is measured → move the egg to the Basler camera and take the photo → click OK to confirm.",
        "msg_embryo_done": "Finished measuring {n} embryonated eggs.\nClick 'Stop experiment' to finish, or 'Undo last sample' to re-measure the last egg.",
        "dlg_egg_id_title": "Egg ID",
        "dlg_egg_id_prompt": "Egg #{egg}/{n}: place the egg in the tray BLUNT END and enter its ID (use this ID to name the Basler photo).\nClick OK to start the spectral measurement.",
        "err_egg_id_empty": "Egg ID must not be empty.",
        "err_egg_id_dup": "ID '{id}' is already used for egg #{egg} in this experiment run.",
        "msg_photo_title": "Basler photo",
        "msg_photo_body": "Spectrum of egg ID {id} (#{egg}/{n}) is done.\nMove the egg to the Basler camera and take the photo, naming it with ID {id}.\nClick OK once the photo is taken.",
        "end_blunt": "Blunt end",
        "end_pointed": "Pointed end",
        "msg_turn_pointed": "BLUNT END of egg #{egg} (ID {id}) is done.\nTurn the egg to the POINTED END orientation and press [ENTER] to measure.",
        "dlg_incub_title": "Incubation start",
        "dlg_incub_prompt": "Date and time this batch of eggs started incubating:",
        "err_incub_future": "Incubation start must not be in the future.",
        "chk_continuous": "Save as session: each run = one batch_XX_<timestamp> subfolder, later runs can continue the session",
        "msg_session_title": "Continuous session",
        "msg_session_body": "Start a new session, or measure the next batch of an existing session?",
        "btn_session_new": "New session",
        "btn_session_continue": "Continue existing session...",
        "err_session_missing": "This folder is not a session (no session_info.json):",
        "msg_session_resume": "Continue session:\n{dir}\n\n{done} batch(es) done. Next is batch {n}.\nIncubation start: {incub}",
        "status_phase_opened": "OPENED EGGS",
        "btn_trigger_opened": "📸 OPENED EGG #{i}/{n} (ENTER)",
        "msg_ref_load_err_title": "Cannot load Dark/Lamp",
        "err_ref_missing": "No {kind}_<timestamp>.csv file found in the folder.",
        "msg_ref_wl_mismatch": "Device pixel count ({n_dev}) differs from the Dark/Lamp files ({n_ref}).\nThese Dark/Lamp files cannot be reused.",
        "msg_opened_start_title": "Opened eggs measurement",
        "msg_reuse_ref_body": "Reusing Dark/Lamp from:\n{dir}\n\n• Dark: {dark} ({dark_ms})\n• Lamp: {lamp} ({lamp_ms})\n\nCurrent exposure: {cur}\n{warn}",
        "instr_opened": "Place opened egg #1 in the tray, click OK then press [ENTER] to measure {n} eggs in turn.",
        "warn_ref_exposure": "⚠ Exposure differs from the Lamp measurement → set exposure to {ms:.0f} ms in Settings before measuring.\n",
        "exp_mode_titration": "Dye titration on opened eggs",
        "lbl_titr_levels": "CUMULATIVE dye after each addition, ml, low to high (max 80):",
        "lbl_titr_repeats": "Measurements per level:",
        "chk_titr_water": "Include water control step",
        "err_titr_levels": "Dye levels must be positive ml values, INCREASING, at most {max:g} ml, separated by commas (use a dot for decimals).\nExample: 5, 10, 20, 40, 80",
        "status_titration": "DYE TITRATION",
        "btn_trigger_titr": "📸 #{egg}/{n} · {step} · {rep}/{n_rep} (ENTER)",
        "titr_step_base": "Baseline (nothing added)",
        "titr_step_water": "Water {ml} ml",
        "titr_step_dye": "Dye {ml} ml",
        "msg_titr_title": "Dye titration",
        "titr_plan_water": " → water {ml} ml ×{rep}",
        "titr_plan_line": "Each egg: baseline ×{base_rep}{water} → dye {levels} ml (cumulative) ×{rep} per level.\nTotal: {n} eggs × {per_egg} measurements = {total} measurements.",
        "instr_titr": "{plan}\n\nPlace the cup of opened egg #1 at the marked position. Do NOT move the cup until this egg is finished.\nClick OK then press [ENTER] to measure the baseline.",
        "msg_titr_water": "Baseline of egg #{egg} done.\nAdd {ml} ml of WATER (same volume as the first dye addition), stir gently, wait ~1 min, then click OK.\nDo not move the cup.",
        "msg_titr_dye": "Egg #{egg}: add {ml} ml of dye (total {total} ml), stir gently as before, wait ~1 min, then click OK.\nDo not move the cup.",
        "msg_titr_next_egg": "Egg #{done} done.\nPlace the cup of opened egg #{next} at the marked position, click OK then press [ENTER] to measure the baseline.",
        "msg_titr_done": "All {n} eggs measured ({total} measurements).\nClick 'Stop Experiment' to finish, or 'Undo Last Egg' to re-measure the last one.",
        "rtl": False
    },
    "zh_CN": {
        "app_title": "SPECTRALENS PRO • 活胚蛋光谱实时采集与数据集构建系统",
        "settings": "⚙ 参数设置",
        "theme_light": "☀️ 明亮模式",
        "theme_dark": "🌙 暗黑模式",
        "kpi_status": "系统状态",
        "kpi_peak": "峰值强度",
        "kpi_lambda_peak": "峰值波长",
        "kpi_egg_count": "已保存蛋样数",
        "status_offline": "离线",
        "status_stopped": "已停止",
        "status_online": "正常联机",
        "status_disconnected": "连接断开",
        "status_phase1": "阶段1：暗室环境",
        "status_phase2": "阶段2：背景光源",
        "status_phase3": "阶段3：未开壳蛋批次",
        "status_phase4": "阶段4：已开壳蛋批次",
        "status_egg_detected": "已保存当前蛋样",
        "status_single_saved": "已保存 S3",
        "status_egg_capturing": "正在采集蛋样",
        "status_remeasuring_ref": "正在重新测量参考光",
        "ctrl_title": "采集与试验控制",
        "btn_start_stream": "▶ 开始实时监视",
        "btn_stop_stream": "⏹ 停止监视",
        "btn_start_exp": "🧪 开始自动试验",
        "btn_stop_exp": "⏹ 停止试验",
        "btn_trigger_egg": "📸 保存蛋样 (回车)",
        "btn_trigger_single": "📸 S3 #{egg}/{n}: {step} (回车)",
        "btn_trigger_batch": "📸 S4 #{i}/{n}: 开壳蛋 (回车)",
        "s3_step_1": "钝端 - 未注色",
        "s3_step_2": "尖端 - 未注色",
        "s3_step_3": "钝端 - 已注色",
        "s3_step_4": "尖端 - 已注色",
        "btn_undo_egg": "↩ 撤销上一个蛋样",
        "btn_remeasure_ref": "🔄 重新测量背景光",
        "btn_snapshot": "📸 瞬时快照",
        "btn_clear_snapshot": "清除快照",
        "warn_saturated": "⚠ 饱和",
        "warn_color_drift": "⚠ 色彩漂移 {pct:.1f}%",
        "warn_exposure_mismatch": "⚠ 暗场曝光不匹配 {pct:.0f}%",
        "mode_spectrum": "📊 原始光谱 (Counts)",
        "mode_absorbance": "🧪 吸光度 (OD)",
        "axis_wavelength": "波长",
        "axis_intensity": "光谱强度",
        "axis_absorbance": "吸光度",
        "dlg_title": "设备与试验参数配置",
        "lbl_port": "串口号 (COM / TTY):",
        "chk_port_auto": "自动检测串口 (COM3，失败则用 COM5)",
        "lbl_baud": "波特率:",
        "lbl_exposure": "曝光:",
        "lbl_batch_size": "每批蛋数:",
        "lbl_egg_frames": "每次测蛋的帧数 (保存每帧及平均):",
        "lbl_egg_placements": "每枚蛋重新放置次数 (批量):",
        "placement_suffix": " · 放置 {p}/{P}",
        "status_replace_egg": "取出蛋 #{egg} 后重新放置 → [回车] 测量放置 {p}/{P}",
        "warn_frame_rsd": "⚠ 帧间 RSD {pct:.1f}%",
        "msg_exposure_mismatch": "⚠ 设备未正确应用曝光时间。\n设定: 手动 {target:.0f} ms。设备回报: 模式 {mode}, {measured} ms。",
        "warn_exposure_target": "⚠ 曝光 {ms:.0f} ms ≠ 设定 {target:.0f} ms",
        "lbl_dir": "数据集根目录 (每次实验创建子文件夹 <时间戳>_<模式>):",
        "btn_save": "确认保存",
        "btn_cancel": "取消",
        "msg_conn_err_title": "通讯错误",
        "msg_conn_err_body": "无法建立与光谱仪的通讯:\n",
        "msg_exp_start_title": "阶段1：暗室环境",
        "msg_exp_start_body": "请关闭所有灯光（完全黑暗，未放置任何物体）。\n点击确定采集Dark测量值（5帧平均）。",
        "msg_phase2_title": "阶段2：背景光源",
        "msg_phase2_body": "Dark测量已完成。\n请开启背景灯（仍未放置鸡蛋），点击确定采集Reference测量值（5帧平均）。",
        "msg_phase3_title": "阶段3：未开壳蛋批次",
        "instr_full": "依次测量 {n} 个未开壳蛋，每个蛋4次：钝端与尖端，注色前与注色后。\n请将1号蛋（未注色）以钝端放置，按 [回车] 键或点击保存按钮进行采集。",
        "msg_dye_title": "阶段3：注色",
        "msg_dye_body": "{egg}号蛋未注色测量已完成（钝端 + 尖端）。\n请向{egg}号蛋注入色素后点击确定，然后再次测量钝端和尖端。",
        "msg_next_egg_title": "阶段3：下一个蛋",
        "msg_next_egg_body": "{done}号蛋已完成。\n请将{next}号蛋（未注色）以钝端放置，按 [回车] 键测量。",
        "msg_phase4_title": "阶段4：已开壳蛋批次",
        "msg_phase4_body": "{n} 个未开壳蛋已测量完成。\n请开壳并按 1 → {n} 的顺序重新测量这 {n} 个蛋：逐个放入检测槽，按 [回车] 键或点击保存按钮。",
        "msg_batch_done_title": "批次完成",
        "msg_batch_done_body": "{n} 个开壳蛋已测量完成。\n点击「停止试验」结束，或点击「撤销上一个蛋样」重新测量最后一个。",
        "msg_exp_finished_title": "试验结束",
        "msg_exp_finished_body": "自动采集已停止。本次保存蛋样总数: ",
        "msg_restart_worker": "设置已保存。请停止并重新开始 实时监视/试验 以套用新的连接埠/波特率。",
        "lbl_exp_mode": "试验模式:",
        "exp_mode_full": "完整 4 阶段 (Dark → Lamp → 未开壳 → 开壳)",
        "exp_mode_opened": "仅测开壳蛋 (暗场 → 光源 → N 个开壳蛋)",
        "exp_mode_shell": "仅测蛋壳 (暗场 → 光源 → N 个蛋壳)",
        "status_phase_shell": "测量蛋壳",
        "btn_trigger_shell": "📸 蛋壳 #{i}/{n} (回车)",
        "msg_shell_title": "蛋壳测量",
        "msg_refs_done": "暗场与光源测量完成。",
        "instr_shell": "依次测量 {n} 个蛋壳 (#1 → #{n}，如有请与蛋编号一致)。将蛋壳 #1 按测蛋时相同的位置和方向放入托盘，按 [回车] 或点击保存进行采集。",
        "msg_shell_done": "已完成 {n} 个蛋壳的测量。\n点击“停止实验”结束，或点击“撤销上一个样本”重测最后一个蛋壳。",
        "lbl_ref_dir": "沿用 Dark/Lamp 的数据集文件夹:",
        "chk_reuse_ref": "沿用旧数据集的 Dark/Lamp (不重新测量)",
        "exp_mode_embryo": "实测胚蛋 (暗场 → 光源 → N 个蛋，不开壳)",
        "status_phase_embryo": "测量胚蛋",
        "btn_trigger_embryo": "📸 胚蛋 #{i}/{n} (回车)",
        "msg_embryo_title": "胚蛋测量",
        "instr_embryo": "依次测量 {n} 个胚蛋 (不开壳、不注色)。每个蛋：按钝端方向放入 → [回车] → 输入 ID → 测钝端 → 转为尖端 → [回车] → 测尖端 → 将蛋移到 Basler 相机拍照 → 点击确定确认。",
        "msg_embryo_done": "已完成 {n} 个胚蛋的测量。\n点击“停止实验”结束，或点击“撤销上一个样本”重测最后一个蛋。",
        "dlg_egg_id_title": "输入蛋 ID",
        "dlg_egg_id_prompt": "蛋 #{egg}/{n}：按钝端方向将蛋放入托盘并输入 ID (Basler 照片用此 ID 命名)。\n点击确定开始测量光谱。",
        "err_egg_id_empty": "蛋 ID 不能为空。",
        "err_egg_id_dup": "ID '{id}' 已在本次实验中用于蛋 #{egg}。",
        "msg_photo_title": "Basler 拍照",
        "msg_photo_body": "蛋 ID {id} (#{egg}/{n}) 的光谱已测完。\n请将蛋移到 Basler 相机拍照，照片以 ID {id} 命名。\n拍完后点击确定。",
        "end_blunt": "钝端",
        "end_pointed": "尖端",
        "msg_turn_pointed": "蛋 #{egg} (ID {id}) 的钝端已测完。\n请将蛋转为尖端方向，按 [回车] 测量。",
        "dlg_incub_title": "开始孵化时间",
        "dlg_incub_prompt": "这批蛋开始孵化的日期和时间:",
        "err_incub_future": "开始孵化时间不能晚于当前时间。",
        "chk_continuous": "按会话保存：每次测量 = 一个子文件夹 batch_XX_<时间戳>，之后可继续该会话",
        "msg_session_title": "连续测量会话",
        "msg_session_body": "开始新会话，还是继续测量已有会话的下一批次？",
        "btn_session_new": "新会话",
        "btn_session_continue": "继续已有会话...",
        "err_session_missing": "该文件夹不是会话 (没有 session_info.json):",
        "msg_session_resume": "继续会话:\n{dir}\n\n已完成 {done} 批次。下一批次为 {n}。\n开始孵化: {incub}",
        "status_phase_opened": "测量开壳蛋",
        "btn_trigger_opened": "📸 开壳蛋 #{i}/{n} (回车)",
        "msg_ref_load_err_title": "无法载入 Dark/Lamp",
        "err_ref_missing": "文件夹中找不到 {kind}_<timestamp>.csv 文件。",
        "msg_ref_wl_mismatch": "设备像素数 ({n_dev}) 与 Dark/Lamp 文件 ({n_ref}) 不同。\n无法沿用该 Dark/Lamp。",
        "msg_opened_start_title": "测量开壳蛋",
        "msg_reuse_ref_body": "沿用 Dark/Lamp 自:\n{dir}\n\n• Dark: {dark} ({dark_ms})\n• Lamp: {lamp} ({lamp_ms})\n\n当前曝光: {cur}\n{warn}",
        "instr_opened": "将开壳蛋 #1 放入托盘，点击确定后按 [回车] 依次测量 {n} 个蛋。",
        "warn_ref_exposure": "⚠ 曝光与测量 Lamp 时不同 → 测量前请在设置中将曝光设为 {ms:.0f} ms。\n",
        "exp_mode_titration": "开壳蛋色素滴定",
        "lbl_titr_levels": "每次添加后的累计色素量，ml，由少到多 (最多 80):",
        "lbl_titr_repeats": "每个级别测量次数:",
        "chk_titr_water": "包含清水对照步骤",
        "err_titr_levels": "色素级别必须为正的 ml 数值、递增、最多 {max:g} ml，以逗号分隔 (小数用点)。\n例如: 5, 10, 20, 40, 80",
        "status_titration": "色素滴定",
        "btn_trigger_titr": "📸 #{egg}/{n} · {step} · 第 {rep}/{n_rep} 次 (回车)",
        "titr_step_base": "基线 (未添加)",
        "titr_step_water": "清水 {ml} ml",
        "titr_step_dye": "色素 {ml} ml",
        "msg_titr_title": "色素滴定",
        "titr_plan_water": " → 清水 {ml} ml ×{rep}",
        "titr_plan_line": "每个蛋: 基线 ×{base_rep}{water} → 色素 {levels} ml (累计) 每级 ×{rep}。\n合计: {n} 个蛋 × {per_egg} 次 = {total} 次测量。",
        "instr_titr": "{plan}\n\n将开壳蛋 #1 的杯子放在标记位置。测完此蛋前请勿移动杯子。\n点击确定后按 [回车] 测量基线。",
        "msg_titr_water": "蛋 #{egg} 基线已测完。\n加入 {ml} ml 清水 (与第一次加色素的体积相同)，轻轻搅拌，等待约 1 分钟后点击确定。\n请勿移动杯子。",
        "msg_titr_dye": "蛋 #{egg}: 加入 {ml} ml 色素 (累计 {total} ml)，与之前一样轻轻搅拌，等待约 1 分钟后点击确定。\n请勿移动杯子。",
        "msg_titr_next_egg": "蛋 #{done} 已测完。\n将开壳蛋 #{next} 的杯子放在标记位置，点击确定后按 [回车] 测量基线。",
        "msg_titr_done": "{n} 个蛋已测完 (共 {total} 次测量)。\n点击「停止试验」结束，或点击「撤销上一个蛋样」重新测量最后一次。",
        "rtl": False
    },
    "zh_TW": {
        "app_title": "SPECTRALENS PRO • 活胚蛋光譜即時擷取與資料集構建系統",
        "settings": "⚙ 參數設定",
        "theme_light": "☀️ 明亮模式",
        "theme_dark": "🌙 暗黑模式",
        "kpi_status": "系統狀態",
        "kpi_peak": "峰值強度",
        "kpi_lambda_peak": "峰值波長",
        "kpi_egg_count": "已儲存蛋數",
        "status_offline": "離線",
        "status_stopped": "已停止",
        "status_online": "連線正常",
        "status_disconnected": "連線中斷",
        "status_phase1": "階段1：暗室環境",
        "status_phase2": "階段2：背景光源",
        "status_phase3": "階段3：未開殼蛋批次",
        "status_phase4": "階段4：已開殼蛋批次",
        "status_egg_detected": "已儲存當前蛋樣",
        "status_single_saved": "已儲存 S3",
        "status_egg_capturing": "正在擷取蛋樣",
        "status_remeasuring_ref": "正在重新量測參考光",
        "ctrl_title": "擷取與試驗控制",
        "btn_start_stream": "▶ 開始即時監控",
        "btn_stop_stream": "⏹ 停止監控",
        "btn_start_exp": "🧪 開始自動試驗",
        "btn_stop_exp": "⏹ 停止試驗",
        "btn_trigger_egg": "📸 儲存蛋樣 (Enter)",
        "btn_trigger_single": "📸 S3 #{egg}/{n}: {step} (Enter)",
        "btn_trigger_batch": "📸 S4 #{i}/{n}: 開殼蛋 (Enter)",
        "s3_step_1": "鈍端 - 未注色",
        "s3_step_2": "尖端 - 未注色",
        "s3_step_3": "鈍端 - 已注色",
        "s3_step_4": "尖端 - 已注色",
        "btn_undo_egg": "↩ 復原上一個蛋樣",
        "btn_remeasure_ref": "🔄 重新量測背景光",
        "btn_snapshot": "📸 擷取快照",
        "btn_clear_snapshot": "清除快照",
        "warn_saturated": "⚠ 飽和",
        "warn_color_drift": "⚠ 色彩飄移 {pct:.1f}%",
        "warn_exposure_mismatch": "⚠ 暗場曝光不匹配 {pct:.0f}%",
        "mode_spectrum": "📊 原始光譜 (Counts)",
        "mode_absorbance": "🧪 吸光度 (OD)",
        "axis_wavelength": "波長",
        "axis_intensity": "光譜強度",
        "axis_absorbance": "吸光度",
        "dlg_title": "設備與試驗參數設定",
        "lbl_port": "序列埠 (COM / TTY):",
        "chk_port_auto": "自動偵測序列埠 (COM3，失敗則用 COM5)",
        "lbl_baud": "鮑率:",
        "lbl_exposure": "曝光:",
        "lbl_batch_size": "每批蛋數:",
        "lbl_egg_frames": "每次量測蛋的幀數 (儲存每幀及平均):",
        "lbl_egg_placements": "每顆蛋重新放置次數 (批次):",
        "placement_suffix": " · 放置 {p}/{P}",
        "status_replace_egg": "取出蛋 #{egg} 後重新放置 → [Enter] 量測放置 {p}/{P}",
        "warn_frame_rsd": "⚠ 幀間 RSD {pct:.1f}%",
        "msg_exposure_mismatch": "⚠ 設備未正確套用曝光時間。\n設定: 手動 {target:.0f} ms。設備回報: 模式 {mode}, {measured} ms。",
        "warn_exposure_target": "⚠ 曝光 {ms:.0f} ms ≠ 設定 {target:.0f} ms",
        "lbl_dir": "資料集根目錄 (每次實驗建立子資料夾 <時間戳>_<模式>):",
        "btn_save": "儲存設定",
        "btn_cancel": "取消",
        "msg_conn_err_title": "連線錯誤",
        "msg_conn_err_body": "無法與光譜儀建立通訊:\n",
        "msg_exp_start_title": "階段1：暗室環境",
        "msg_exp_start_body": "請關閉所有燈光（完全黑暗，尚未放置任何物體）。\n點擊確定擷取Dark量測值（5幀平均）。",
        "msg_phase2_title": "階段2：背景光源",
        "msg_phase2_body": "Dark量測已完成。\n請開啟背景燈（仍未放置雞蛋），點擊確定擷取Reference量測值（5幀平均）。",
        "msg_phase3_title": "階段3：未開殼蛋批次",
        "instr_full": "依序量測 {n} 顆未開殼蛋，每顆4次：鈍端與尖端，注色前與注色後。\n請將1號蛋（未注色）以鈍端放置，按 [Enter] 鍵或點擊儲存按鈕進行擷取。",
        "msg_dye_title": "階段3：注色",
        "msg_dye_body": "{egg}號蛋未注色量測已完成（鈍端 + 尖端）。\n請向{egg}號蛋注入色素後點擊確定，然後再次量測鈍端與尖端。",
        "msg_next_egg_title": "階段3：下一顆蛋",
        "msg_next_egg_body": "{done}號蛋已完成。\n請將{next}號蛋（未注色）以鈍端放置，按 [Enter] 鍵量測。",
        "msg_phase4_title": "階段4：已開殼蛋批次",
        "msg_phase4_body": "{n} 顆未開殼蛋已量測完成。\n請開殼並依 1 → {n} 的順序重新量測這 {n} 顆蛋：逐顆放入檢測槽，按 [Enter] 鍵或點擊儲存按鈕。",
        "msg_batch_done_title": "批次完成",
        "msg_batch_done_body": "{n} 顆開殼蛋已量測完成。\n點擊「停止試驗」結束，或點擊「復原上一個蛋樣」重新量測最後一顆。",
        "msg_exp_finished_title": "試驗結束",
        "msg_exp_finished_body": "自動量測已停止。本次儲存蛋樣總數: ",
        "msg_restart_worker": "設定已儲存。請停止並重新開始 即時監控/試驗 以套用新的連接埠/鮑率。",
        "lbl_exp_mode": "試驗模式:",
        "exp_mode_full": "完整 4 階段 (Dark → Lamp → 未開殼 → 開殼)",
        "exp_mode_opened": "僅量測開殼蛋 (暗場 → 光源 → N 顆開殼蛋)",
        "exp_mode_shell": "僅量測蛋殼 (暗場 → 光源 → N 個蛋殼)",
        "status_phase_shell": "量測蛋殼",
        "btn_trigger_shell": "📸 蛋殼 #{i}/{n} (Enter)",
        "msg_shell_title": "蛋殼量測",
        "msg_refs_done": "暗場與光源量測完成。",
        "instr_shell": "依序量測 {n} 個蛋殼 (#1 → #{n}，如有請與蛋編號一致)。將蛋殼 #1 依量測蛋時相同的位置與方向放入托盤，按 [Enter] 或點擊儲存進行擷取。",
        "msg_shell_done": "已完成 {n} 個蛋殼的量測。\n點擊「停止實驗」結束，或點擊「撤銷上一個樣本」重測最後一個蛋殼。",
        "lbl_ref_dir": "沿用 Dark/Lamp 的資料集資料夾:",
        "chk_reuse_ref": "沿用舊資料集的 Dark/Lamp (不重新量測)",
        "exp_mode_embryo": "實測胚蛋 (暗場 → 光源 → N 顆蛋，不開殼)",
        "status_phase_embryo": "量測胚蛋",
        "btn_trigger_embryo": "📸 胚蛋 #{i}/{n} (Enter)",
        "msg_embryo_title": "胚蛋量測",
        "instr_embryo": "依序量測 {n} 顆胚蛋 (不開殼、不注色)。每顆蛋：依鈍端方向放入 → [Enter] → 輸入 ID → 量鈍端 → 轉為尖端 → [Enter] → 量尖端 → 將蛋移到 Basler 相機拍照 → 點擊確定確認。",
        "msg_embryo_done": "已完成 {n} 顆胚蛋的量測。\n點擊「停止實驗」結束，或點擊「撤銷上一個樣本」重測最後一顆蛋。",
        "dlg_egg_id_title": "輸入蛋 ID",
        "dlg_egg_id_prompt": "蛋 #{egg}/{n}：依鈍端方向將蛋放入托盤並輸入 ID (Basler 照片以此 ID 命名)。\n點擊確定開始量測光譜。",
        "err_egg_id_empty": "蛋 ID 不可為空。",
        "err_egg_id_dup": "ID '{id}' 已在本次實驗中用於蛋 #{egg}。",
        "msg_photo_title": "Basler 拍照",
        "msg_photo_body": "蛋 ID {id} (#{egg}/{n}) 的光譜已量測完成。\n請將蛋移到 Basler 相機拍照，照片以 ID {id} 命名。\n拍完後點擊確定。",
        "end_blunt": "鈍端",
        "end_pointed": "尖端",
        "msg_turn_pointed": "蛋 #{egg} (ID {id}) 的鈍端已量測完成。\n請將蛋轉為尖端方向，按 [Enter] 量測。",
        "dlg_incub_title": "開始孵化時間",
        "dlg_incub_prompt": "這批蛋開始孵化的日期與時間:",
        "err_incub_future": "開始孵化時間不可晚於目前時間。",
        "chk_continuous": "依工作階段儲存：每次量測 = 一個子資料夾 batch_XX_<時間戳>，之後可繼續該工作階段",
        "msg_session_title": "連續量測工作階段",
        "msg_session_body": "開始新工作階段，還是繼續量測既有工作階段的下一批次？",
        "btn_session_new": "新工作階段",
        "btn_session_continue": "繼續既有工作階段...",
        "err_session_missing": "此資料夾不是工作階段 (沒有 session_info.json):",
        "msg_session_resume": "繼續工作階段:\n{dir}\n\n已完成 {done} 批次。下一批次為 {n}。\n開始孵化: {incub}",
        "status_phase_opened": "量測開殼蛋",
        "btn_trigger_opened": "📸 開殼蛋 #{i}/{n} (Enter)",
        "msg_ref_load_err_title": "無法載入 Dark/Lamp",
        "err_ref_missing": "資料夾中找不到 {kind}_<timestamp>.csv 檔案。",
        "msg_ref_wl_mismatch": "裝置像素數 ({n_dev}) 與 Dark/Lamp 檔案 ({n_ref}) 不同。\n無法沿用此 Dark/Lamp。",
        "msg_opened_start_title": "量測開殼蛋",
        "msg_reuse_ref_body": "沿用 Dark/Lamp 自:\n{dir}\n\n• Dark: {dark} ({dark_ms})\n• Lamp: {lamp} ({lamp_ms})\n\n目前曝光: {cur}\n{warn}",
        "instr_opened": "將開殼蛋 #1 放入托盤，點擊確定後按 [Enter] 依序量測 {n} 顆蛋。",
        "warn_ref_exposure": "⚠ 曝光與量測 Lamp 時不同 → 量測前請在設定中將曝光設為 {ms:.0f} ms。\n",
        "exp_mode_titration": "開殼蛋色素滴定",
        "lbl_titr_levels": "每次添加後的累計色素量，ml，由少到多 (最多 80):",
        "lbl_titr_repeats": "每個級別量測次數:",
        "chk_titr_water": "包含清水對照步驟",
        "err_titr_levels": "色素級別必須為正的 ml 數值、遞增、最多 {max:g} ml，以逗號分隔 (小數用點)。\n例如: 5, 10, 20, 40, 80",
        "status_titration": "色素滴定",
        "btn_trigger_titr": "📸 #{egg}/{n} · {step} · 第 {rep}/{n_rep} 次 (Enter)",
        "titr_step_base": "基線 (未添加)",
        "titr_step_water": "清水 {ml} ml",
        "titr_step_dye": "色素 {ml} ml",
        "msg_titr_title": "色素滴定",
        "titr_plan_water": " → 清水 {ml} ml ×{rep}",
        "titr_plan_line": "每顆蛋: 基線 ×{base_rep}{water} → 色素 {levels} ml (累計) 每級 ×{rep}。\n合計: {n} 顆蛋 × {per_egg} 次 = {total} 次量測。",
        "instr_titr": "{plan}\n\n將開殼蛋 #1 的杯子放在標記位置。量測完此蛋前請勿移動杯子。\n點擊確定後按 [Enter] 量測基線。",
        "msg_titr_water": "蛋 #{egg} 基線已量測完。\n加入 {ml} ml 清水 (與第一次加色素的體積相同)，輕輕攪拌，等待約 1 分鐘後點擊確定。\n請勿移動杯子。",
        "msg_titr_dye": "蛋 #{egg}: 加入 {ml} ml 色素 (累計 {total} ml)，與之前一樣輕輕攪拌，等待約 1 分鐘後點擊確定。\n請勿移動杯子。",
        "msg_titr_next_egg": "蛋 #{done} 已量測完。\n將開殼蛋 #{next} 的杯子放在標記位置，點擊確定後按 [Enter] 量測基線。",
        "msg_titr_done": "{n} 顆蛋已量測完 (共 {total} 次量測)。\n點擊「停止試驗」結束，或點擊「復原上一個蛋樣」重新量測最後一次。",
        "rtl": False
    },
    "ar": {
        "app_title": "SPECTRALENS PRO • لوحة جمع أطياف البيض وبناء مجموعات البيانات",
        "settings": "⚙ الإعدادات",
        "theme_light": "☀️ وضع النهار",
        "theme_dark": "🌙 وضع الليل",
        "kpi_status": "الحالة",
        "kpi_peak": "أعلى قراءة",
        "kpi_lambda_peak": "ذروة الطول الموجي",
        "kpi_egg_count": "عدد البيض المحفوظ",
        "status_offline": "غير متصل",
        "status_stopped": "متوقف",
        "status_online": "متصل",
        "status_disconnected": "انقطع الاتصال",
        "status_phase1": "المرحلة 1: غرفة مظلمة",
        "status_phase2": "المرحلة 2: إضاءة خلفية",
        "status_phase3": "المرحلة 3: دفعة بيض غير مفتوح",
        "status_phase4": "المرحلة 4: دفعة بيض مفتوح",
        "status_egg_detected": "تم حفظ طيف البيضة",
        "status_single_saved": "تم حفظ S3",
        "status_egg_capturing": "جارٍ التقاط البيضة",
        "status_remeasuring_ref": "جارٍ إعادة قياس المرجع",
        "ctrl_title": "التحكم والتجربة",
        "btn_start_stream": "▶ بدء المراقبة الحية",
        "btn_stop_stream": "⏹ إيقاف المراقبة",
        "btn_start_exp": "🧪 بدء التجربة التلقائية",
        "btn_stop_exp": "⏹ إيقاف التجربة",
        "btn_trigger_egg": "📸 حفظ عينة البيضة (Enter)",
        "btn_trigger_single": "📸 S3 #{egg}/{n}: {step} (Enter)",
        "btn_trigger_batch": "📸 S4 #{i}/{n}: بيضة مفتوحة (Enter)",
        "s3_step_1": "الطرف العريض - قبل الصبغ",
        "s3_step_2": "الطرف المدبب - قبل الصبغ",
        "s3_step_3": "الطرف العريض - بعد الصبغ",
        "s3_step_4": "الطرف المدبب - بعد الصبغ",
        "btn_undo_egg": "↩ تراجع عن آخر بيضة",
        "btn_remeasure_ref": "🔄 إعادة قياس الإضاءة الخلفية",
        "btn_snapshot": "📸 لقطة فورية",
        "btn_clear_snapshot": "مسح اللقطة",
        "warn_saturated": "⚠ تشبع",
        "warn_color_drift": "⚠ انحراف اللون {pct:.1f}%",
        "warn_exposure_mismatch": "⚠ عدم تطابق زمن التعريض مع الظلام {pct:.0f}%",
        "mode_spectrum": "📊 وضع الطيف الخام",
        "mode_absorbance": "🧪 وضع الامتصاصية",
        "axis_wavelength": "الطول الموجي",
        "axis_intensity": "الكثافة",
        "axis_absorbance": "الامتصاصية",
        "dlg_title": "إعدادات الجهاز والتجربة",
        "lbl_port": "منفذ التسلسل:",
        "chk_port_auto": "اكتشاف المنفذ تلقائيًا (COM3 ثم COM5)",
        "lbl_baud": "معدل البود:",
        "lbl_exposure": "التعريض:",
        "lbl_batch_size": "عدد البيض في الدفعة:",
        "lbl_egg_frames": "عدد الإطارات لكل قياس بيضة (يُحفظ كل إطار والمتوسط):",
        "lbl_egg_placements": "عدد مرات إعادة وضع كل بيضة (الدفعة):",
        "placement_suffix": " · الوضع {p}/{P}",
        "status_replace_egg": "ارفع البيضة #{egg} ثم ضعها مجددًا → [Enter] لقياس الوضع {p}/{P}",
        "warn_frame_rsd": "⚠ RSD بين الإطارات {pct:.1f}%",
        "msg_exposure_mismatch": "⚠ لم يطبّق الجهاز زمن التعريض.\nالمضبوط: يدوي {target:.0f} ms. الجهاز يُبلغ: الوضع {mode}، {measured} ms.",
        "warn_exposure_target": "⚠ زمن التعريض {ms:.0f} ms ≠ المضبوط {target:.0f} ms",
        "lbl_dir": "المجلد الرئيسي للبيانات (كل تجربة تنشئ مجلدًا فرعيًا <الوقت>_<الوضع>):",
        "btn_save": "حفظ الإعدادات",
        "btn_cancel": "إلغاء",
        "msg_conn_err_title": "خطأ في الاتصال",
        "msg_conn_err_body": "تعذر الاتصال بمقياس الطيف:\n",
        "msg_exp_start_title": "المرحلة 1: غرفة مظلمة",
        "msg_exp_start_body": "أطفئ جميع الأضواء (ظلام تام، دون وضع أي جسم).\nاضغط موافق لالتقاط قياس Dark (متوسط 5 إطارات).",
        "msg_phase2_title": "المرحلة 2: إضاءة خلفية",
        "msg_phase2_body": "اكتمل قياس Dark.\nقم بتشغيل إضاءة الخلفية (دون وضع بيضة بعد)، ثم اضغط موافق لالتقاط قياس Reference (متوسط 5 إطارات).",
        "msg_phase3_title": "المرحلة 3: دفعة بيض غير مفتوح",
        "instr_full": "قِس {n} بيضات غير مفتوحة واحدة تلو الأخرى، 4 مرات لكل بيضة: الطرف العريض والمدبب، قبل حقن الصبغة وبعده.\nضع البيضة رقم 1 (غير المصبوغة) على الطرف العريض، ثم اضغط [Enter] أو زر الحفظ.",
        "msg_dye_title": "المرحلة 3: حقن الصبغة",
        "msg_dye_body": "اكتملت قياسات البيضة رقم {egg} قبل الصبغ (الطرف العريض + المدبب).\nاحقن الصبغة في البيضة رقم {egg} ثم اضغط موافق، وبعدها قِس الطرفين مجددًا.",
        "msg_next_egg_title": "المرحلة 3: البيضة التالية",
        "msg_next_egg_body": "اكتملت البيضة رقم {done}.\nضع البيضة رقم {next} (غير المصبوغة) على الطرف العريض واضغط [Enter] للقياس.",
        "msg_phase4_title": "المرحلة 4: دفعة بيض مفتوح",
        "msg_phase4_body": "اكتمل قياس {n} بيضات غير مفتوحة.\nافتح البيضات نفسها وأعد قياسها بالترتيب 1 → {n}: ضع كل بيضة في الحامل واضغط [Enter] أو زر الحفظ.",
        "msg_batch_done_title": "اكتملت الدفعة",
        "msg_batch_done_body": "اكتمل قياس {n} بيضات مفتوحة.\nاضغط 'إيقاف التجربة' للإنهاء، أو 'تراجع عن آخر بيضة' لإعادة قياس الأخيرة.",
        "msg_exp_finished_title": "اكتملت التجربة",
        "msg_exp_finished_body": "تم إيقاف التجربة. إجمالي عينات البيض المحفوظة: ",
        "msg_restart_worker": "تم حفظ الإعدادات. أوقف ثم أعد تشغيل المراقبة الحية/التجربة لتطبيق المنفذ/معدل البود الجديد.",
        "lbl_exp_mode": "وضع التجربة:",
        "exp_mode_full": "4 مراحل كاملة (Dark → Lamp → غير مفتوح → مفتوح)",
        "exp_mode_opened": "البيض المفتوح فقط (ظلام ← مصباح ← N بيضة مفتوحة)",
        "exp_mode_shell": "قشرة البيض فقط (ظلام ← مصباح ← N قشرة)",
        "status_phase_shell": "قياس قشور البيض",
        "btn_trigger_shell": "📸 قشرة #{i}/{n} (Enter)",
        "msg_shell_title": "قياس قشرة البيض",
        "msg_refs_done": "اكتمل قياس الظلام والمصباح.",
        "instr_shell": "قِس {n} قشرة بالتتابع (#1 ← #{n}، بنفس ترقيم البيض إن وجد). ضع القشرة #1 في الحامل بنفس الموضع والاتجاه المستخدمين للبيض، ثم اضغط [Enter] أو زر الحفظ للالتقاط.",
        "msg_shell_done": "تم قياس {n} قشرة.\nاضغط 'إيقاف التجربة' للإنهاء، أو 'تراجع عن آخر عينة' لإعادة قياس القشرة الأخيرة.",
        "lbl_ref_dir": "مجلد البيانات الذي يحتوي Dark/Lamp لإعادة الاستخدام:",
        "chk_reuse_ref": "إعادة استخدام Dark/Lamp من مجموعة بيانات سابقة (بدون قياس جديد)",
        "exp_mode_embryo": "بيض مُخصَّب فعلي (ظلام ← مصباح ← N بيضة، دون فتح القشرة)",
        "status_phase_embryo": "قياس البيض المُخصَّب",
        "btn_trigger_embryo": "📸 بيضة مُخصَّبة #{i}/{n} (Enter)",
        "msg_embryo_title": "قياس البيض المُخصَّب",
        "instr_embryo": "قِس {n} بيضات مُخصَّبة بالتتابع (دون فتح القشرة ودون صبغة). لكل بيضة: ضعها على الطرف العريض ← [Enter] ← أدخل الرقم ← قياس الطرف العريض ← أدرها إلى الطرف المدبب ← [Enter] ← قياس الطرف المدبب ← انقلها إلى كاميرا Basler والتقط الصورة ← اضغط موافق للتأكيد.",
        "msg_embryo_done": "تم قياس {n} بيضات مُخصَّبة.\nاضغط 'إيقاف التجربة' للإنهاء، أو 'تراجع عن آخر عينة' لإعادة قياس البيضة الأخيرة.",
        "dlg_egg_id_title": "رقم البيضة",
        "dlg_egg_id_prompt": "البيضة #{egg}/{n}: ضع البيضة في الحامل على الطرف العريض وأدخل رقمها (استخدمه لتسمية صورة Basler).\nاضغط موافق لبدء قياس الطيف.",
        "err_egg_id_empty": "لا يمكن ترك رقم البيضة فارغًا.",
        "err_egg_id_dup": "الرقم '{id}' مستخدم بالفعل للبيضة #{egg} في هذه التجربة.",
        "msg_photo_title": "صورة Basler",
        "msg_photo_body": "اكتمل طيف البيضة {id} (#{egg}/{n}).\nانقل البيضة إلى كاميرا Basler والتقط الصورة وسمّها بالرقم {id}.\nاضغط موافق بعد التقاط الصورة.",
        "end_blunt": "الطرف العريض",
        "end_pointed": "الطرف المدبب",
        "msg_turn_pointed": "اكتمل قياس الطرف العريض للبيضة #{egg} (الرقم {id}).\nأدر البيضة إلى اتجاه الطرف المدبب واضغط [Enter] للقياس.",
        "dlg_incub_title": "بدء الحضانة",
        "dlg_incub_prompt": "تاريخ ووقت بدء حضانة هذه الدفعة من البيض:",
        "err_incub_future": "لا يمكن أن يكون وقت بدء الحضانة في المستقبل.",
        "chk_continuous": "الحفظ كجلسة: كل قياس = مجلد فرعي batch_XX_<الوقت>، ويمكن متابعة الجلسة لاحقًا",
        "msg_session_title": "جلسة قياس مستمر",
        "msg_session_body": "بدء جلسة جديدة، أم قياس الدفعة التالية من جلسة سابقة؟",
        "btn_session_new": "جلسة جديدة",
        "btn_session_continue": "متابعة جلسة سابقة...",
        "err_session_missing": "هذا المجلد ليس جلسة (لا يوجد session_info.json):",
        "msg_session_resume": "متابعة الجلسة:\n{dir}\n\nاكتملت {done} دفعة. التالية هي الدفعة {n}.\nبدء الحضانة: {incub}",
        "status_phase_opened": "قياس البيض المفتوح",
        "btn_trigger_opened": "📸 بيضة مفتوحة #{i}/{n} (Enter)",
        "msg_ref_load_err_title": "تعذر تحميل Dark/Lamp",
        "err_ref_missing": "لم يتم العثور على ملف {kind}_<timestamp>.csv في المجلد.",
        "msg_ref_wl_mismatch": "عدد بكسلات الجهاز ({n_dev}) يختلف عن ملفات Dark/Lamp ({n_ref}).\nلا يمكن إعادة استخدام Dark/Lamp هذه.",
        "msg_opened_start_title": "قياس البيض المفتوح",
        "msg_reuse_ref_body": "إعادة استخدام Dark/Lamp من:\n{dir}\n\n• Dark: {dark} ({dark_ms})\n• Lamp: {lamp} ({lamp_ms})\n\nالتعريض الحالي: {cur}\n{warn}",
        "instr_opened": "ضع البيضة المفتوحة #1 في الصينية، اضغط موافق ثم [Enter] لقياس {n} بيضات بالتتابع.",
        "warn_ref_exposure": "⚠ التعريض يختلف عن قياس Lamp → اضبط التعريض على {ms:.0f} ms في الإعدادات قبل القياس.\n",
        "exp_mode_titration": "معايرة الصبغة على البيض المفتوح",
        "lbl_titr_levels": "كمية الصبغة التراكمية بعد كل إضافة، مل، من الأقل إلى الأكثر (حد أقصى 80):",
        "lbl_titr_repeats": "عدد القياسات لكل مستوى:",
        "chk_titr_water": "تضمين خطوة الماء الضابطة",
        "err_titr_levels": "يجب أن تكون مستويات الصبغة قيم مل موجبة، تصاعدية، بحد أقصى {max:g} مل، مفصولة بفواصل (استخدم النقطة للكسور).\nمثال: 5, 10, 20, 40, 80",
        "status_titration": "معايرة الصبغة",
        "btn_trigger_titr": "📸 #{egg}/{n} · {step} · {rep}/{n_rep} (Enter)",
        "titr_step_base": "خط الأساس (بدون إضافة)",
        "titr_step_water": "ماء {ml} مل",
        "titr_step_dye": "صبغة {ml} مل",
        "msg_titr_title": "معايرة الصبغة",
        "titr_plan_water": " → ماء {ml} مل ×{rep}",
        "titr_plan_line": "لكل بيضة: خط الأساس ×{base_rep}{water} → صبغة {levels} مل (تراكمي) ×{rep} لكل مستوى.\nالإجمالي: {n} بيضات × {per_egg} قياس = {total} قياس.",
        "instr_titr": "{plan}\n\nضع كوب البيضة المفتوحة #1 في الموضع المحدد. لا تحرك الكوب حتى تنتهي من هذه البيضة.\nاضغط موافق ثم [Enter] لقياس خط الأساس.",
        "msg_titr_water": "تم قياس خط الأساس للبيضة #{egg}.\nأضف {ml} مل من الماء (نفس حجم أول إضافة للصبغة)، حرّك برفق، انتظر دقيقة تقريبًا ثم اضغط موافق.\nلا تحرك الكوب.",
        "msg_titr_dye": "البيضة #{egg}: أضف {ml} مل من الصبغة (الإجمالي {total} مل)، حرّك برفق كما سبق، انتظر دقيقة تقريبًا ثم اضغط موافق.\nلا تحرك الكوب.",
        "msg_titr_next_egg": "انتهت البيضة #{done}.\nضع كوب البيضة المفتوحة #{next} في الموضع المحدد، اضغط موافق ثم [Enter] لقياس خط الأساس.",
        "msg_titr_done": "تم قياس {n} بيضات ({total} قياس).\nاضغط 'إيقاف التجربة' للإنهاء، أو 'تراجع عن آخر بيضة' لإعادة القياس الأخير.",
        "rtl": True
    }
}


# ==============================================================================
# GIAO DIỆN DARK & LIGHT (TƯƠNG PHẢN CAO)
# ==============================================================================
DARK_THEME = """
QMainWindow { background-color: #0b0f19; }
QWidget { font-family: 'Segoe UI', Arial, sans-serif; color: #f1f5f9; font-size: 12px; }
QFrame#TitleBar { background-color: #0d1527; border-bottom: 1px solid #1e293b; }
QLabel#WinTitle { font-size: 13px; font-weight: 700; color: #38bdf8; }
QPushButton#WinBtn { background-color: transparent; border: none; font-size: 13px; font-weight: bold; color: #94a3b8; min-width: 32px; max-width: 32px; min-height: 24px; }
QPushButton#WinBtn:hover { background-color: #1e293b; color: #ffffff; }
QPushButton#WinCloseBtn { background-color: transparent; border: none; font-size: 13px; font-weight: bold; color: #f87171; min-width: 32px; max-width: 32px; min-height: 24px; }
QPushButton#WinCloseBtn:hover { background-color: #dc2626; color: #ffffff; }
QFrame#CardFrame { background-color: #111827; border: 1px solid #1f2937; border-radius: 6px; }
QFrame#MetricCard { background-color: #161f30; border: 1px solid #243044; border-radius: 6px; }
QLabel#MetricVal { font-size: 15px; font-weight: 800; color: #ffffff; }
QLabel#MetricSub { font-size: 10px; font-weight: 600; color: #64748b; }
QPushButton { background-color: #1e293b; border: 1px solid #334155; border-radius: 5px; color: #cbd5e1; padding: 6px 12px; font-weight: 600; min-height: 24px; }
QPushButton:hover { background-color: #334155; color: #ffffff; }
QPushButton#PrimaryBtn { background-color: #0284c7; border: 1px solid #38bdf8; color: #ffffff; font-weight: 700; }
QPushButton#PrimaryBtn:hover { background-color: #0369a1; }
QPushButton#SuccessBtn { background-color: #065f46; border: 1px solid #10b981; color: #a7f3d0; font-weight: 700; }
QPushButton#SuccessBtn:hover { background-color: #047857; }
QPushButton#DangerBtn { background-color: #7f1d1d; border: 1px solid #ef4444; color: #fecaca; font-weight: 700; }
QPushButton#DangerBtn:hover { background-color: #991b1b; }
QPushButton#ExpBtn { background-color: #6b21a8; border: 1px solid #a855f7; color: #f3e8ff; font-weight: 800; }
QPushButton#ExpBtn:hover { background-color: #581c87; }
QDialog { background-color: #0f172a; }
QLineEdit, QDoubleSpinBox, QSpinBox, QDateTimeEdit { background-color: #1e293b; border: 1px solid #334155; border-radius: 4px; color: #f1f5f9; padding: 4px; min-height: 22px; }
QSpinBox:disabled { background-color: #0f172a; color: #64748b; border: 1px solid #1e293b; }
QComboBox { background-color: #1e293b; border: 1px solid #475569; border-radius: 4px; color: #f8fafc; padding: 4px 8px; min-height: 22px; font-weight: 600; }
QComboBox:hover { border: 1px solid #38bdf8; }
QComboBox QAbstractItemView { background-color: #0f172a; border: 1px solid #38bdf8; color: #f8fafc; selection-background-color: #0284c7; selection-color: #ffffff; outline: none; padding: 4px; }
QComboBox QAbstractItemView::item { min-height: 28px; padding: 4px 8px; color: #f8fafc; }
QComboBox QAbstractItemView::item:hover { background-color: #1e293b; color: #38bdf8; }
QComboBox QAbstractItemView::item:selected { background-color: #0284c7; color: #ffffff; }
QCheckBox { color: #cbd5e1; font-weight: 600; }
QDateTimeEdit:focus { border: 1px solid #38bdf8; }
QCalendarWidget QWidget#qt_calendar_navigationbar { background-color: #0284c7; }
QCalendarWidget QToolButton { background-color: transparent; color: #ffffff; font-weight: 700; padding: 2px 6px; }
QCalendarWidget QToolButton:hover { background-color: #0369a1; border-radius: 4px; }
QCalendarWidget QMenu { background-color: #0f172a; color: #f1f5f9; }
QCalendarWidget QSpinBox { background-color: #0f172a; color: #ffffff; selection-background-color: #38bdf8; }
QCalendarWidget QAbstractItemView { background-color: #111827; color: #f1f5f9; alternate-background-color: #1e293b; selection-background-color: #0284c7; selection-color: #ffffff; outline: none; }
QCalendarWidget QAbstractItemView:disabled { color: #475569; }
QTreeView, QTextBrowser { background-color: #0d1424; border: 1px solid #1f2937; border-radius: 4px; color: #e2e8f0; outline: none; }
QTreeView::item { padding: 2px 0; }
QTreeView::item:hover { background-color: #1e293b; }
QTreeView::item:selected { background-color: #0284c7; color: #ffffff; }
QSplitter::handle { background-color: #1f2937; }
QSplitter::handle:hover { background-color: #38bdf8; }
QMenu { background-color: #0f172a; border: 1px solid #334155; color: #f1f5f9; padding: 4px; }
QMenu::item { padding: 5px 18px; border-radius: 3px; }
QMenu::item:selected { background-color: #0284c7; color: #ffffff; }
QMenu::item:disabled { color: #475569; }
QMenu::separator { height: 1px; background: #1e293b; margin: 4px 6px; }
QToolTip { background-color: #0f172a; color: #f1f5f9; border: 1px solid #38bdf8; padding: 4px; }
QScrollBar:vertical, QScrollBar:horizontal { background: #0d1424; border: none; width: 10px; height: 10px; }
QScrollBar::handle:vertical, QScrollBar::handle:horizontal { background: #334155; border-radius: 4px; min-height: 24px; min-width: 24px; }
QScrollBar::handle:hover { background: #475569; }
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page { background: none; border: none; width: 0; height: 0; }
"""

LIGHT_THEME = """
QMainWindow { background-color: #f1f5f9; }
QWidget { font-family: 'Segoe UI', Arial, sans-serif; color: #0f172a; font-size: 12px; }
QFrame#TitleBar { background-color: #ffffff; border-bottom: 1px solid #cbd5e1; }
QLabel#WinTitle { font-size: 13px; font-weight: 700; color: #0284c7; }
QPushButton#WinBtn { background-color: transparent; border: none; font-size: 13px; font-weight: bold; color: #64748b; min-width: 32px; max-width: 32px; min-height: 24px; }
QPushButton#WinBtn:hover { background-color: #e2e8f0; color: #0f172a; }
QPushButton#WinCloseBtn { background-color: transparent; border: none; font-size: 13px; font-weight: bold; color: #ef4444; min-width: 32px; max-width: 32px; min-height: 24px; }
QPushButton#WinCloseBtn:hover { background-color: #ef4444; color: #ffffff; }
QFrame#CardFrame { background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 6px; }
QFrame#MetricCard { background-color: #ffffff; border: 1px solid #cbd5e1; border-radius: 6px; }
QLabel#MetricVal { font-size: 15px; font-weight: 800; color: #0f172a; }
QLabel#MetricSub { font-size: 10px; font-weight: 600; color: #64748b; }
QPushButton { background-color: #ffffff; border: 1px solid #cbd5e1; border-radius: 5px; color: #334155; padding: 6px 12px; font-weight: 600; min-height: 24px; }
QPushButton:hover { background-color: #f8fafc; border-color: #94a3b8; }
QPushButton#PrimaryBtn { background-color: #0284c7; border: 1px solid #0369a1; color: #ffffff; font-weight: 700; }
QPushButton#PrimaryBtn:hover { background-color: #0369a1; }
QPushButton#SuccessBtn { background-color: #10b981; border: 1px solid #059669; color: #ffffff; font-weight: 700; }
QPushButton#SuccessBtn:hover { background-color: #059669; }
QPushButton#DangerBtn { background-color: #ef4444; border: 1px solid #dc2626; color: #ffffff; font-weight: 700; }
QPushButton#DangerBtn:hover { background-color: #dc2626; }
QPushButton#ExpBtn { background-color: #9333ea; border: 1px solid #7e22ce; color: #ffffff; font-weight: 800; }
QPushButton#ExpBtn:hover { background-color: #7e22ce; }
QDialog { background-color: #f8fafc; }
QLineEdit, QDoubleSpinBox, QSpinBox, QDateTimeEdit { background-color: #ffffff; border: 1px solid #cbd5e1; border-radius: 4px; color: #0f172a; padding: 4px; min-height: 22px; }
QSpinBox:disabled { background-color: #f1f5f9; color: #94a3b8; border: 1px solid #e2e8f0; }
QComboBox { background-color: #ffffff; border: 1px solid #94a3b8; border-radius: 4px; color: #0f172a; padding: 4px 8px; min-height: 22px; font-weight: 600; }
QComboBox:hover { border: 1px solid #0284c7; }
QComboBox QAbstractItemView { background-color: #ffffff; border: 1px solid #0284c7; color: #0f172a; selection-background-color: #0284c7; selection-color: #ffffff; outline: none; padding: 4px; }
QComboBox QAbstractItemView::item { min-height: 28px; padding: 4px 8px; color: #0f172a; }
QComboBox QAbstractItemView::item:hover { background-color: #e0f2fe; color: #0369a1; }
QComboBox QAbstractItemView::item:selected { background-color: #0284c7; color: #ffffff; }
QCheckBox { color: #334155; font-weight: 600; }
QDateTimeEdit:focus { border: 1px solid #0284c7; }
QCalendarWidget QWidget#qt_calendar_navigationbar { background-color: #0284c7; }
QCalendarWidget QToolButton { background-color: transparent; color: #ffffff; font-weight: 700; padding: 2px 6px; }
QCalendarWidget QToolButton:hover { background-color: #0369a1; border-radius: 4px; }
QCalendarWidget QMenu { background-color: #ffffff; color: #0f172a; }
QCalendarWidget QSpinBox { background-color: #ffffff; color: #0f172a; selection-background-color: #0284c7; }
QCalendarWidget QAbstractItemView { background-color: #ffffff; color: #0f172a; alternate-background-color: #f1f5f9; selection-background-color: #0284c7; selection-color: #ffffff; outline: none; }
QCalendarWidget QAbstractItemView:disabled { color: #cbd5e1; }
QTreeView, QTextBrowser { background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 4px; color: #0f172a; outline: none; }
QTreeView::item { padding: 2px 0; }
QTreeView::item:hover { background-color: #e0f2fe; }
QTreeView::item:selected { background-color: #0284c7; color: #ffffff; }
QSplitter::handle { background-color: #e2e8f0; }
QSplitter::handle:hover { background-color: #0284c7; }
QMenu { background-color: #ffffff; border: 1px solid #cbd5e1; color: #0f172a; padding: 4px; }
QMenu::item { padding: 5px 18px; border-radius: 3px; }
QMenu::item:selected { background-color: #0284c7; color: #ffffff; }
QMenu::item:disabled { color: #94a3b8; }
QMenu::separator { height: 1px; background: #e2e8f0; margin: 4px 6px; }
QToolTip { background-color: #ffffff; color: #0f172a; border: 1px solid #0284c7; padding: 4px; }
"""


# ==============================================================================
# HỘP THOẠI CẤU HÌNH THÔNG SỐ
# ==============================================================================
class SettingsDialog(QDialog):
    def __init__(self, current_settings, current_lang="vi", theme_qss=DARK_THEME, parent=None):
        super().__init__(parent)
        self.lang = current_lang
        self.t = TRANSLATIONS[self.lang]
        self.setWindowTitle(self.t["dlg_title"])
        self.setFixedWidth(460)   # chiều cao tự co giãn theo các mục đang hiện
        self.settings = current_settings.copy()
        self.setStyleSheet(theme_qss)
        if self.t["rtl"]:
            self.setLayoutDirection(Qt.RightToLeft)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        layout.addWidget(QLabel(self.t["lbl_port"]))
        # Tắt tự động để chọn tay COM3/COM5 (hoặc gõ cổng khác) khi cần kiểm tra từng cổng
        self.port_auto_chk = QCheckBox(self.t["chk_port_auto"])
        self.port_auto_chk.setChecked(bool(self.settings.get("port_auto", True)))
        self.port_auto_chk.toggled.connect(self.update_port_enabled)
        self.port_combo = QComboBox()
        self.port_combo.setEditable(True)
        self.port_combo.addItems(SPECTRO_PORTS)
        self.port_combo.setCurrentText(self.settings.get("port", SPECTRO_PORTS[0]))
        layout.addWidget(self.port_auto_chk)
        layout.addWidget(self.port_combo)
        self.update_port_enabled()

        layout.addWidget(QLabel(self.t["lbl_baud"]))
        self.baud_combo = QComboBox()
        for b in [115200, 921600, 460800, 256000, 57600]:
            self.baud_combo.addItem(str(b), b)
        self.baud_combo.setCurrentText(str(self.settings.get("baud", 115200)))
        layout.addWidget(self.baud_combo)

        # Exposure luôn Manual (cố định) - auto-exposure làm Reference và mẫu khác thời gian phơi sáng
        layout.addWidget(QLabel(self.t["lbl_exposure"]))
        self.exposure_spin = QSpinBox()
        self.exposure_spin.setRange(EXPOSURE_MIN_MS, EXPOSURE_MAX_MS)
        self.exposure_spin.setSuffix(" ms")
        self.exposure_spin.setValue(int(self.settings.get("exposure_ms", EXPOSURE_MS)))
        layout.addWidget(self.exposure_spin)

        layout.addWidget(QLabel(self.t["lbl_batch_size"]))
        self.batch_size_spin = QSpinBox()
        self.batch_size_spin.setRange(1, 999)
        self.batch_size_spin.setValue(int(self.settings.get("batch_size", BATCH_SIZE_DEFAULT)))
        layout.addWidget(self.batch_size_spin)

        layout.addWidget(QLabel(self.t["lbl_egg_frames"]))
        self.egg_frames_spin = QSpinBox()
        self.egg_frames_spin.setRange(1, 20)
        self.egg_frames_spin.setValue(int(self.settings.get("egg_n_frames", EGG_N_FRAMES_DEFAULT)))
        layout.addWidget(self.egg_frames_spin)

        layout.addWidget(QLabel(self.t["lbl_egg_placements"]))
        self.egg_placements_spin = QSpinBox()
        self.egg_placements_spin.setRange(1, 10)
        self.egg_placements_spin.setValue(int(self.settings.get("egg_placements", EGG_PLACEMENTS_DEFAULT)))
        layout.addWidget(self.egg_placements_spin)

        # Chế độ thí nghiệm: đầy đủ 4 phase / chỉ đo trứng đã mở vỏ / chuẩn độ màu trên trứng đã mở vỏ
        # / chỉ đo vỏ / đo trứng có phôi thực tế (không mở vỏ)
        layout.addWidget(QLabel(self.t["lbl_exp_mode"]))
        self.exp_mode_combo = QComboBox()
        for key, mode in EXPERIMENT_MODES:
            self.exp_mode_combo.addItem(self.t[key], mode)
        self.exp_mode_combo.setCurrentIndex(
            max(0, self.exp_mode_combo.findData(self.settings.get("experiment_mode", "full")))
        )
        self.exp_mode_combo.currentIndexChanged.connect(self.update_mode_widgets)
        layout.addWidget(self.exp_mode_combo)

        # Thông số chuẩn độ màu: chỉ hiện ở chế độ chuẩn độ
        self.titr_box = QWidget()
        titr_layout = QVBoxLayout(self.titr_box)
        titr_layout.setContentsMargins(0, 0, 0, 0)
        titr_layout.addWidget(QLabel(self.t["lbl_titr_levels"]))
        self.titr_levels_edit = QLineEdit(self.settings.get("titration_levels", TITRATION_LEVELS_DEFAULT))
        titr_layout.addWidget(self.titr_levels_edit)
        titr_row = QHBoxLayout()
        titr_row.addWidget(QLabel(self.t["lbl_titr_repeats"]))
        self.titr_repeats_spin = QSpinBox()
        self.titr_repeats_spin.setRange(1, 10)
        self.titr_repeats_spin.setValue(int(self.settings.get("titration_repeats", TITRATION_REPEATS_DEFAULT)))
        titr_row.addWidget(self.titr_repeats_spin)
        self.titr_water_chk = QCheckBox(self.t["chk_titr_water"])
        self.titr_water_chk.setChecked(bool(self.settings.get("titration_water", True)))
        titr_row.addWidget(self.titr_water_chk)
        titr_layout.addLayout(titr_row)
        layout.addWidget(self.titr_box)

        # Đo liên tục nhiều batch (chỉ chế độ trứng có phôi): session = thư mục cha, mỗi batch 1 thư mục con
        self.cont_box = QWidget()
        cont_layout = QVBoxLayout(self.cont_box)
        cont_layout.setContentsMargins(0, 0, 0, 0)
        self.continuous_chk = QCheckBox(self.t["chk_continuous"])
        self.continuous_chk.setChecked(bool(self.settings.get("continuous", False)))
        cont_layout.addWidget(self.continuous_chk)
        layout.addWidget(self.cont_box)

        # Dùng lại Dark/Lamp của dataset cũ (mọi chế độ): chỉ khi tích chọn mới hiện thư mục nguồn,
        # không tích -> đo Dark + Lamp mới
        self.reuse_ref_chk = QCheckBox(self.t["chk_reuse_ref"])
        self.reuse_ref_chk.setChecked(bool(self.settings.get("reuse_ref", False)))
        self.reuse_ref_chk.toggled.connect(self.update_mode_widgets)
        layout.addWidget(self.reuse_ref_chk)
        self.ref_box = QWidget()
        ref_layout = QVBoxLayout(self.ref_box)
        ref_layout.setContentsMargins(0, 0, 0, 0)
        ref_layout.addWidget(QLabel(self.t["lbl_ref_dir"]))
        ref_row = QHBoxLayout()
        self.ref_dir_edit = QLineEdit(self.settings.get("ref_dir", DEFAULT_REF_DIR))
        self.ref_browse_btn = QPushButton("...")
        self.ref_browse_btn.setFixedWidth(35)
        self.ref_browse_btn.clicked.connect(self.browse_ref_dir)
        ref_row.addWidget(self.ref_dir_edit)
        ref_row.addWidget(self.ref_browse_btn)
        ref_layout.addLayout(ref_row)
        layout.addWidget(self.ref_box)

        layout.addWidget(QLabel(self.t["lbl_dir"]))
        dir_row = QHBoxLayout()
        self.dir_edit = QLineEdit(self.settings.get("save_dir", os.getcwd()))
        browse_btn = QPushButton("...")
        browse_btn.setFixedWidth(35)
        browse_btn.clicked.connect(self.browse_dir)
        dir_row.addWidget(self.dir_edit)
        dir_row.addWidget(browse_btn)
        layout.addLayout(dir_row)

        layout.addStretch()

        btn_row = QHBoxLayout()
        save_btn = QPushButton(self.t["btn_save"])
        save_btn.setObjectName("PrimaryBtn")
        save_btn.clicked.connect(self.accept_settings)
        cancel_btn = QPushButton(self.t["btn_cancel"])
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(save_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)
        self.update_mode_widgets()

    def update_port_enabled(self):
        self.port_combo.setEnabled(not self.port_auto_chk.isChecked())

    def update_mode_widgets(self):
        """Chỉ hiện thông số chuẩn độ ở chế độ chuẩn độ, thư mục Dark/Lamp khi tích dùng lại."""
        self.titr_box.setVisible(self.exp_mode_combo.currentData() == "dye_titration")
        self.cont_box.setVisible(self.exp_mode_combo.currentData() == "embryo")
        self.ref_box.setVisible(self.reuse_ref_chk.isChecked())
        self.layout().activate()
        self.adjustSize()

    def browse_dir(self):
        folder = QFileDialog.getExistingDirectory(self, self.t["lbl_dir"])
        if folder:
            self.dir_edit.setText(folder)

    def browse_ref_dir(self):
        folder = QFileDialog.getExistingDirectory(self, self.t["lbl_ref_dir"], self.ref_dir_edit.text())
        if folder:
            self.ref_dir_edit.setText(folder)

    def accept_settings(self):
        if (self.exp_mode_combo.currentData() == "dye_titration"
                and parse_titration_levels(self.titr_levels_edit.text()) is None):
            QMessageBox.warning(self, self.t["dlg_title"], self.t["err_titr_levels"].format(max=TITRATION_MAX_ML))
            return
        self.settings["titration_levels"] = self.titr_levels_edit.text().strip()
        self.settings["titration_repeats"] = self.titr_repeats_spin.value()
        self.settings["titration_water"] = self.titr_water_chk.isChecked()
        self.settings["continuous"] = self.continuous_chk.isChecked()
        self.settings["port_auto"] = self.port_auto_chk.isChecked()
        self.settings["port"] = self.port_combo.currentText().strip()
        self.settings["baud"] = self.baud_combo.currentData()
        self.settings["exposure_ms"] = self.exposure_spin.value()
        self.settings["batch_size"] = self.batch_size_spin.value()
        self.settings["egg_n_frames"] = self.egg_frames_spin.value()
        self.settings["egg_placements"] = self.egg_placements_spin.value()
        self.settings["experiment_mode"] = self.exp_mode_combo.currentData()
        self.settings["reuse_ref"] = self.reuse_ref_chk.isChecked()
        self.settings["ref_dir"] = self.ref_dir_edit.text().strip()
        self.settings["save_dir"] = self.dir_edit.text().strip()
        os.makedirs(self.settings["save_dir"], exist_ok=True)
        self.accept()


# ==============================================================================
# SIDEBAR QUẢN LÝ DỮ LIỆU: BATCH (BATCH_A, BATCH_B...) / SESSION / LẦN ĐO / FILE PHỔ
# ==============================================================================
DATA_ROOT_DEFAULT = os.path.join(PROJECT_ROOT, "data")
BATCH_DIR_RE = re.compile(r"^batch_(\d+)_(\d{8}_\d{6})$")   # Thư mục batch con trong session đo liên tục
FILE_TS_RE = re.compile(r"_\d{8}_\d{6}$")                    # Timestamp cuối tên file đo
DIR_SCAN_LIMIT = 20000                                       # Số entry tối đa khi đếm dung lượng thư mục

# Chuỗi giao diện của sidebar; ngôn ngữ chưa có bản dịch dùng tiếng Anh
BROWSER_TEXT = {
    "vi": {
        "br_toggle": "📁 Dữ liệu",
        "br_title": "QUẢN LÝ DỮ LIỆU",
        "br_filter": "🔍 Lọc tên file (vd: embryo, dark, *.json)",
        "br_new_batch": "➕ Batch mới",
        "br_new_folder": "📂 Thư mục con mới",
        "br_import": "📥 Nhập thư mục...",
        "br_rename": "✏️ Đổi tên  (F2)",
        "br_delete": "🗑 Xóa  (Del)",
        "br_set_save": "💾 Đặt làm thư mục lưu",
        "br_resume": "▶ Tiếp tục session này",
        "br_sync": "🔧 Đồng bộ session_info.json",
        "br_preview": "📈 Xem phổ trên đồ thị",
        "br_clear_preview": "✕ Xóa phổ đang xem",
        "br_open": "🔎 Mở trong Explorer",
        "br_copy_path": "📋 Sao chép đường dẫn",
        "br_refresh": "⟳ Làm mới",
        "br_go_save": "🎯 Đến thư mục lưu",
        "br_change_root": "📁 Đổi thư mục gốc...",
        "br_save_dir": "💾 Đang lưu vào: {path}",
        "br_hint": "Chọn 1 thư mục / file để xem cấu trúc.<br>Nhấp đúp file CSV để xem phổ, chuột phải để thao tác.",
        "br_new_batch_prompt": "Tên batch mới (tạo trong {root}):",
        "br_new_folder_prompt": "Tên thư mục con mới (trong {parent}):",
        "br_rename_prompt": "Tên mới:",
        "br_ask_set_save": "Đã tạo:\n{path}\n\nĐặt làm thư mục lưu dữ liệu đo?",
        "br_delete_confirm": "Chuyển vào Thùng rác?\n\n{items}",
        "br_delete_perm": "Không chuyển được vào Thùng rác:\n{path}\n\nXóa VĨNH VIỄN?",
        "br_busy": "Đang đo vào thư mục này, không thể đổi tên / xóa / di chuyển:\n{path}",
        "br_exists": "Đã tồn tại:\n{path}",
        "br_bad_name": "Tên không hợp lệ: {name}",
        "br_error": "Lỗi: {err}",
        "br_import_done": "Đã nhập vào:\n{path}",
        "br_not_session": "Thư mục này không phải session (không có session_info.json).",
        "br_sync_none": "session_info.json đã khớp với các thư mục batch trên ổ đĩa.",
        "br_sync_confirm": "Các thay đổi sẽ ghi vào session_info.json:\n\n{changes}\n\nTiếp tục?",
        "br_sync_rename": "• batch {old_no} '{old}' → '{new}'",
        "br_sync_drop": "• bỏ batch {no} '{folder}' (thư mục không còn)",
        "br_preview_err": "Không đọc được phổ từ file:\n{path}\n{err}",
        "br_saved_here": "💾 thư mục lưu hiện tại",
        "info_path": "Đường dẫn",
        "info_subdirs": "Thư mục con",
        "info_files": "File",
        "info_size": "Dung lượng",
        "info_modified": "Sửa lần cuối",
        "info_by_type": "Số file theo loại",
        "info_session": "Session đo liên tục",
        "info_incub": "Bắt đầu ấp",
        "info_created": "Tạo lúc",
        "info_batch": "Batch",
        "info_folder": "Thư mục",
        "info_eggs": "Số trứng",
        "info_started": "Bắt đầu",
        "info_status": "Trạng thái",
        "info_ok": "✓",
        "info_missing": "⚠ thiếu thư mục",
        "info_stopped": "dừng sớm",
        "info_running": "đang đo",
        "info_unlisted": "⚠ Thư mục batch không có trong session_info.json: {names}",
        "info_metadata": "metadata_log.csv: {n} dòng",
        "info_points": "Số điểm",
        "info_range": "Dải bước sóng",
        "info_peak": "Đỉnh ADC",
        "info_meta_row": "Dòng metadata",
        "info_more": "(hơn {n} mục, chỉ đếm {n} mục đầu)",
        "info_selected": "Đã chọn {n} mục",
        "info_sync_hint": "⚠ session_info.json không khớp thư mục trên ổ đĩa → chuột phải → Đồng bộ session_info.json",
    },
    "en": {
        "br_toggle": "📁 Data",
        "br_title": "DATA MANAGER",
        "br_filter": "🔍 Filter file names (e.g. embryo, dark, *.json)",
        "br_new_batch": "➕ New batch",
        "br_new_folder": "📂 New subfolder",
        "br_import": "📥 Import folder...",
        "br_rename": "✏️ Rename  (F2)",
        "br_delete": "🗑 Delete  (Del)",
        "br_set_save": "💾 Use as save folder",
        "br_resume": "▶ Continue this session",
        "br_sync": "🔧 Sync session_info.json",
        "br_preview": "📈 Show spectrum on plot",
        "br_clear_preview": "✕ Clear previewed spectrum",
        "br_open": "🔎 Show in Explorer",
        "br_copy_path": "📋 Copy path",
        "br_refresh": "⟳ Refresh",
        "br_go_save": "🎯 Go to save folder",
        "br_change_root": "📁 Change root folder...",
        "br_save_dir": "💾 Saving to: {path}",
        "br_hint": "Select a folder / file to inspect its structure.<br>Double-click a CSV to plot it, right-click for actions.",
        "br_new_batch_prompt": "New batch name (created in {root}):",
        "br_new_folder_prompt": "New subfolder name (in {parent}):",
        "br_rename_prompt": "New name:",
        "br_ask_set_save": "Created:\n{path}\n\nUse it as the save folder for measurements?",
        "br_delete_confirm": "Move to Recycle Bin?\n\n{items}",
        "br_delete_perm": "Could not move to Recycle Bin:\n{path}\n\nDelete PERMANENTLY?",
        "br_busy": "A measurement is writing into this folder; it cannot be renamed / deleted / moved:\n{path}",
        "br_exists": "Already exists:\n{path}",
        "br_bad_name": "Invalid name: {name}",
        "br_error": "Error: {err}",
        "br_import_done": "Imported into:\n{path}",
        "br_not_session": "This folder is not a session (no session_info.json).",
        "br_sync_none": "session_info.json already matches the batch folders on disk.",
        "br_sync_confirm": "These changes will be written to session_info.json:\n\n{changes}\n\nContinue?",
        "br_sync_rename": "• batch {old_no} '{old}' → '{new}'",
        "br_sync_drop": "• drop batch {no} '{folder}' (folder is gone)",
        "br_preview_err": "Could not read a spectrum from:\n{path}\n{err}",
        "br_saved_here": "💾 current save folder",
        "info_path": "Path",
        "info_subdirs": "Subfolders",
        "info_files": "Files",
        "info_size": "Size",
        "info_modified": "Modified",
        "info_by_type": "Files by type",
        "info_session": "Continuous session",
        "info_incub": "Incubation start",
        "info_created": "Created",
        "info_batch": "Batch",
        "info_folder": "Folder",
        "info_eggs": "Eggs",
        "info_started": "Started",
        "info_status": "Status",
        "info_ok": "✓",
        "info_missing": "⚠ folder missing",
        "info_stopped": "stopped early",
        "info_running": "running",
        "info_unlisted": "⚠ Batch folders not in session_info.json: {names}",
        "info_metadata": "metadata_log.csv: {n} rows",
        "info_points": "Points",
        "info_range": "Wavelength range",
        "info_peak": "Peak ADC",
        "info_meta_row": "Metadata row",
        "info_more": "(more than {n} entries, only the first {n} counted)",
        "info_selected": "{n} items selected",
        "info_sync_hint": "⚠ session_info.json does not match the folders on disk → right-click → Sync session_info.json",
    },
}
for _lang, _texts in TRANSLATIONS.items():
    for _key, _val in BROWSER_TEXT["en"].items():
        _texts.setdefault(_key, BROWSER_TEXT.get(_lang, {}).get(_key, _val))


def fmt_size(n_bytes):
    for unit in ("B", "KB", "MB", "GB"):
        if n_bytes < 1024 or unit == "GB":
            return f"{n_bytes:.0f} {unit}" if unit == "B" else f"{n_bytes:.1f} {unit}"
        n_bytes /= 1024


def file_kind(name):
    """Loại file đo theo tên: 'embryo_blunt_0001_20261007_105333.csv' -> 'embryo_blunt'."""
    stem, ext = os.path.splitext(name)
    if ext.lower() != ".csv" or not FILE_TS_RE.search(stem):
        return f"*{ext.lower()}" if ext else name
    tokens = FILE_TS_RE.sub("", stem).split("_")
    return "_".join(tok for tok in tokens if not re.fullmatch(r"p?\d+", tok)) or stem


def scan_dir(folder, limit=DIR_SCAN_LIMIT):
    """(số thư mục con trực tiếp, số file trực tiếp, tổng dung lượng đệ quy, đã chạm giới hạn hay chưa)."""
    n_dirs = n_files = total = seen = 0
    stack = [(folder, True)]
    while stack and seen < limit:
        path, top = stack.pop()
        try:
            entries = list(os.scandir(path))
        except OSError:
            continue
        for e in entries:
            seen += 1
            try:
                if e.is_dir(follow_symlinks=False):
                    n_dirs += top
                    stack.append((e.path, False))
                else:
                    n_files += top
                    total += e.stat(follow_symlinks=False).st_size
            except OSError:
                pass
    return n_dirs, n_files, total, seen >= limit


def read_session_info(folder):
    """Nội dung session_info.json của thư mục session, None nếu không phải session."""
    try:
        with open(os.path.join(folder, SESSION_INFO_FILE), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_session_info(folder, info):
    with open(os.path.join(folder, SESSION_INFO_FILE), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)


def session_sync_plan(folder, info):
    """
    So khớp session_info.json với các thư mục batch_<k>_<timestamp> trên ổ đĩa (người dùng có thể đã
    xóa / đổi tên thư mục bằng tay). Entry thiếu thư mục được ghép lại theo timestamp cuối tên thư mục.
    Trả về (renames [(entry, tên mới)], drops [entry], thư mục batch không có trong session_info).
    """
    on_disk = {n for n in os.listdir(folder) if os.path.isdir(os.path.join(folder, n)) and BATCH_DIR_RE.match(n)}
    listed = {b.get("folder") for b in info.get("batches", [])}
    by_ts = {BATCH_DIR_RE.match(n).group(2): n for n in on_disk - listed}
    renames, drops = [], []
    for b in info.get("batches", []):
        if b.get("folder") in on_disk:
            continue
        m = BATCH_DIR_RE.match(b.get("folder") or "")
        new = by_ts.pop(m.group(2), None) if m else None
        if new:
            renames.append((b, new))
        else:
            drops.append(b)
    return renames, drops, sorted(by_ts.values())


def load_spectrum_csv(path):
    """(wavelengths, raw counts, absorbance hoặc None) từ file CSV phổ app đã lưu."""
    with open(path, encoding="utf-8") as f:
        header = f.readline().strip().split(",")
    if not header or not header[0].lower().startswith("wavelength"):
        raise ValueError(f"header: {','.join(header)}")
    data = np.genfromtxt(path, delimiter=",", skip_header=1, ndmin=2)
    if data.shape[1] < 2:
        raise ValueError("< 2 cột")
    absorbance = data[:, 2] if data.shape[1] > 2 and np.isfinite(data[:, 2]).any() else None
    return data[:, 0], data[:, 1], absorbance


def norm_path(path):
    """Dạng chuẩn để so sánh đường dẫn (Windows: tên ngắn 8.3 / tên dài, hoa / thường, dấu phân cách)."""
    return os.path.normcase(os.path.realpath(path))


def path_inside(child, parent):
    """child nằm trong (hoặc chính là) parent."""
    child, parent = norm_path(child), norm_path(parent)
    try:
        return os.path.commonpath([child, parent]) == parent
    except ValueError:   # khác ổ đĩa
        return False


def path_overlaps(a, b):
    """a nằm trong b, b nằm trong a, hoặc a == b."""
    return path_inside(a, b) or path_inside(b, a)


class DataBrowserPanel(QFrame):
    """
    Sidebar cây thư mục dữ liệu (mặc định <project>/data): tạo / nhập / đổi tên / xóa (Thùng rác) batch,
    xem cấu trúc (số file theo loại, session_info.json, metadata_log.csv), xem phổ CSV trên đồ thị chính,
    đặt thư mục lưu và tiếp tục session đo liên tục.
    """
    preview_requested = pyqtSignal(str)      # Đường dẫn CSV cần vẽ lên đồ thị
    preview_cleared = pyqtSignal()
    save_dir_requested = pyqtSignal(str)     # Đặt settings["save_dir"]
    resume_requested = pyqtSignal(str)       # Tiếp tục session đo liên tục ở thư mục này

    def __init__(self, root_dir, busy_paths, parent=None):
        super().__init__(parent)
        self.setObjectName("CardFrame")
        self.root_dir = os.path.abspath(root_dir)
        self.busy_paths = busy_paths         # callable -> các thư mục đang ghi dữ liệu đo (không cho sửa)
        self.save_dir = None
        self.lang = "vi"
        os.makedirs(self.root_dir, exist_ok=True)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        self.title_lbl = QLabel()
        self.title_lbl.setStyleSheet("font-weight: 700; color: #94a3b8;")
        lay.addWidget(self.title_lbl)

        self.root_lbl = QLabel()
        self.root_lbl.setObjectName("MetricSub")
        self.root_lbl.setWordWrap(True)
        lay.addWidget(self.root_lbl)

        self.filter_edit = QLineEdit()
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self.apply_filter)
        lay.addWidget(self.filter_edit)

        self.actions = {}
        for key, slot, shortcut in [
            ("br_new_batch", self.new_batch, None),
            ("br_new_folder", self.new_folder, None),
            ("br_import", self.import_folder, None),
            ("br_rename", self.rename_selected, "F2"),
            ("br_delete", self.delete_selected, "Del"),
            ("br_set_save", self.set_save_selected, None),
            ("br_resume", self.resume_selected, None),
            ("br_sync", self.sync_selected, None),
            ("br_preview", self.preview_selected, None),
            ("br_clear_preview", self.preview_cleared.emit, None),
            ("br_open", self.open_selected, None),
            ("br_copy_path", self.copy_selected_path, None),
            ("br_refresh", self.refresh, "F5"),
            ("br_go_save", self.go_to_save_dir, None),
            ("br_change_root", self.change_root, None),
        ]:
            act = QAction(self)
            act.triggered.connect(lambda _=False, s=slot: s())
            if shortcut:
                act.setShortcut(QKeySequence(shortcut))
                act.setShortcutContext(Qt.WidgetShortcut)   # chỉ khi cây thư mục đang focus
            self.actions[key] = act

        # Hàng nút nhanh (các thao tác còn lại ở menu chuột phải)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)
        self.quick_btns = {}
        for key in ("br_new_batch", "br_import", "br_set_save", "br_delete", "br_refresh"):
            btn = QPushButton()
            btn.setFixedHeight(26)
            btn.setStyleSheet("padding: 2px 6px;")
            btn.clicked.connect(self.actions[key].trigger)
            btn_row.addWidget(btn)
            self.quick_btns[key] = btn
        lay.addLayout(btn_row)

        self.model = QFileSystemModel(self)
        self.model.setReadOnly(True)         # Đổi tên / xóa chỉ qua các thao tác có xác nhận bên dưới
        self.model.setNameFilterDisables(False)
        self.tree = QTreeView()
        self.tree.setModel(self.model)
        for col in (1, 2, 3):                # Chỉ hiện cột tên; kích thước / ngày xem ở khung thông tin
            self.tree.hideColumn(col)
        self.tree.setHeaderHidden(True)
        self.tree.setAnimated(True)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.show_menu)
        self.tree.doubleClicked.connect(self.on_double_click)
        self.tree.selectionModel().selectionChanged.connect(lambda *_: self.update_info())
        for act in self.actions.values():
            if not act.shortcut().isEmpty():
                self.tree.addAction(act)

        self.info = QTextBrowser()
        self.info.setOpenLinks(False)

        split = QSplitter(Qt.Vertical)
        split.addWidget(self.tree)
        split.addWidget(self.info)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        lay.addWidget(split, stretch=1)

        self.save_lbl = QLabel()
        self.save_lbl.setObjectName("MetricSub")
        self.save_lbl.setWordWrap(True)
        lay.addWidget(self.save_lbl)

        self.set_root(self.root_dir)

    # ------------------------------------------------------------------ tiện ích
    def t(self, key, **kw):
        text = TRANSLATIONS[self.lang][key]
        return text.format(**kw) if kw else text

    def rel(self, path):
        try:
            rel = os.path.relpath(path, os.path.dirname(self.root_dir))
        except ValueError:
            return path
        return path if rel.startswith("..") else rel

    def selected_paths(self):
        rows = self.tree.selectionModel().selectedRows(0)
        return [os.path.normpath(self.model.filePath(i)) for i in rows]

    def current_path(self):
        idx = self.tree.currentIndex()
        return os.path.normpath(self.model.filePath(idx)) if idx.isValid() else None

    def target_dir(self):
        """Thư mục đang chọn (hoặc thư mục chứa file đang chọn), mặc định thư mục gốc."""
        path = self.current_path()
        if not path:
            return self.root_dir
        return path if os.path.isdir(path) else os.path.dirname(path)

    def check_not_busy(self, paths):
        for p in paths:
            for busy in self.busy_paths():
                if busy and path_overlaps(p, busy):
                    QMessageBox.warning(self, self.t("br_title"), self.t("br_busy", path=p))
                    return False
        return True

    def ask_name(self, title, prompt, default=""):
        name, ok = QInputDialog.getText(self, title, prompt, QLineEdit.Normal, default)
        name = name.strip()
        if not ok or not name:
            return None
        if re.search(r'[<>:"/\\|?*]', name) or name in (".", ".."):
            QMessageBox.warning(self, title, self.t("br_bad_name", name=name))
            return None
        return name

    def select_path(self, path, retry=True):
        idx = self.model.index(QDir.fromNativeSeparators(path))
        if idx.isValid():
            self.tree.setCurrentIndex(idx)
            self.tree.scrollTo(idx)
        elif retry:   # QFileSystemModel nạp thư mục bất đồng bộ: thư mục vừa tạo có thể chưa có trong model
            QTimer.singleShot(300, lambda: self.select_path(path, retry=False))

    # ------------------------------------------------------------------ trạng thái
    def set_root(self, root):
        self.root_dir = os.path.abspath(root)
        self.model.setRootPath(QDir.fromNativeSeparators(self.root_dir))
        self.tree.setRootIndex(self.model.index(QDir.fromNativeSeparators(self.root_dir)))
        self.root_lbl.setText(f"📂 {self.root_dir}")
        self.root_lbl.setToolTip(self.root_dir)
        self.update_info()

    def set_save_dir(self, path):
        self.save_dir = os.path.abspath(path)
        self.save_lbl.setText(self.t("br_save_dir", path=self.rel(self.save_dir)))
        self.save_lbl.setToolTip(self.save_dir)

    def retranslate(self, lang):
        self.lang = lang
        self.title_lbl.setText(self.t("br_title"))
        self.filter_edit.setPlaceholderText(self.t("br_filter"))
        for key, act in self.actions.items():
            act.setText(self.t(key))
        for key, btn in self.quick_btns.items():
            label = self.t(key)
            btn.setText(label.split()[0])        # Chỉ hiện biểu tượng, chữ đầy đủ ở tooltip
            btn.setToolTip(label)
        if self.save_dir:
            self.set_save_dir(self.save_dir)
        self.update_info()

    def apply_filter(self, text):
        text = text.strip()
        if not text:
            self.model.setNameFilters([])
        else:
            self.model.setNameFilters([p if any(c in p for c in "*?") else f"*{p}*" for p in text.split()])

    # ------------------------------------------------------------------ menu
    def show_menu(self, pos):
        path = self.current_path() if self.tree.indexAt(pos).isValid() else None
        if path is None:
            self.tree.clearSelection()
            self.tree.setCurrentIndex(QModelIndex())
        is_dir = path is not None and os.path.isdir(path)
        is_csv = path is not None and path.lower().endswith(".csv")
        is_session = is_dir and os.path.exists(os.path.join(path, SESSION_INFO_FILE))
        a = self.actions
        a["br_rename"].setEnabled(path is not None)
        a["br_delete"].setEnabled(path is not None)
        a["br_set_save"].setEnabled(path is None or is_dir)
        a["br_resume"].setEnabled(is_session)
        a["br_sync"].setEnabled(is_session)
        a["br_preview"].setEnabled(is_csv)
        a["br_copy_path"].setEnabled(True)

        menu = QMenu(self)
        groups = [
            ["br_new_batch", "br_new_folder", "br_import"],
            ["br_preview", "br_clear_preview"],
            ["br_set_save", "br_resume", "br_sync"],
            ["br_rename", "br_delete"],
            ["br_open", "br_copy_path", "br_go_save", "br_refresh", "br_change_root"],
        ]
        for i, group in enumerate(groups):
            if i:
                menu.addSeparator()
            for key in group:
                menu.addAction(a[key])
        menu.exec_(self.tree.viewport().mapToGlobal(pos))
        for act in a.values():
            act.setEnabled(True)

    def on_double_click(self, idx):
        path = self.model.filePath(idx)
        if os.path.isdir(path):
            return                                # mở / đóng nhánh như bình thường
        if path.lower().endswith(".csv"):
            self.preview_requested.emit(path)
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    # ------------------------------------------------------------------ thao tác
    def new_batch(self):
        existing = {n.upper() for n in os.listdir(self.root_dir)}
        letter = next((c for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if f"BATCH_{c}" not in existing), "")
        name = self.ask_name(self.t("br_new_batch"), self.t("br_new_batch_prompt", root=self.root_dir),
                             f"BATCH_{letter}" if letter else f"BATCH_{datetime.now():%Y%m%d}")
        path = name and self._make_dir(self.root_dir, name)
        if path:
            self.select_path(path)
            reply = QMessageBox.question(self, self.t("br_new_batch"), self.t("br_ask_set_save", path=path),
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if reply == QMessageBox.Yes:
                self.save_dir_requested.emit(path)

    def new_folder(self):
        parent = self.target_dir()
        name = self.ask_name(self.t("br_new_folder"), self.t("br_new_folder_prompt", parent=self.rel(parent)))
        path = name and self._make_dir(parent, name)
        if path:
            self.select_path(path)

    def _make_dir(self, parent, name):
        path = os.path.join(parent, name)
        if os.path.exists(path):
            QMessageBox.warning(self, self.t("br_title"), self.t("br_exists", path=path))
            return None
        try:
            os.makedirs(path)
        except OSError as e:
            QMessageBox.critical(self, self.t("br_title"), self.t("br_error", err=e))
            return None
        return path

    def import_folder(self):
        """Sao chép 1 thư mục từ ngoài (USB, máy đo khác...) vào thư mục đang chọn / thư mục gốc."""
        src = QFileDialog.getExistingDirectory(self, self.t("br_import"), os.path.expanduser("~"))
        if not src:
            return
        dst = os.path.join(self.target_dir(), os.path.basename(os.path.normpath(src)))
        if os.path.exists(dst):
            QMessageBox.warning(self, self.t("br_import"), self.t("br_exists", path=dst))
            return
        if path_overlaps(src, dst):
            QMessageBox.warning(self, self.t("br_import"), self.t("br_bad_name", name=src))
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            shutil.copytree(src, dst)
        except OSError as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, self.t("br_import"), self.t("br_error", err=e))
            return
        QApplication.restoreOverrideCursor()
        self.select_path(dst)
        QMessageBox.information(self, self.t("br_import"), self.t("br_import_done", path=dst))

    def rename_selected(self):
        path = self.current_path()
        if not path or not self.check_not_busy([path]):
            return
        old = os.path.basename(path)
        name = self.ask_name(self.t("br_rename"), self.t("br_rename_prompt"), old)
        if not name or name == old:
            return
        new_path = os.path.join(os.path.dirname(path), name)
        if os.path.exists(new_path) and os.path.normcase(new_path) != os.path.normcase(path):
            QMessageBox.warning(self, self.t("br_rename"), self.t("br_exists", path=new_path))
            return
        try:
            os.rename(path, new_path)
        except OSError as e:
            QMessageBox.critical(self, self.t("br_rename"), self.t("br_error", err=e))
            return
        self._update_session_entry(path, new_name=name)
        if self.save_dir and path_inside(self.save_dir, path):   # thư mục lưu nằm trong mục vừa đổi tên
            rel = os.path.relpath(norm_path(self.save_dir), norm_path(path))
            self.save_dir_requested.emit(os.path.normpath(os.path.join(new_path, rel)))
        self.select_path(new_path)

    def delete_selected(self):
        paths = self.selected_paths()
        if not paths or not self.check_not_busy(paths):
            return
        listing = "\n".join(self.rel(p) for p in paths[:15]) + ("\n..." if len(paths) > 15 else "")
        reply = QMessageBox.question(self, self.t("br_delete"), self.t("br_delete_confirm", items=listing),
                                     QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
        if reply != QMessageBox.Yes:
            return
        for path in paths:
            if not os.path.exists(path):          # đã bị xóa cùng thư mục cha đứng trước trong danh sách
                continue
            res = QFile.moveToTrash(path)
            if not (res[0] if isinstance(res, tuple) else res):
                reply = QMessageBox.warning(self, self.t("br_delete"), self.t("br_delete_perm", path=path),
                                            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
                if reply != QMessageBox.Yes:
                    continue
                try:
                    shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
                except OSError as e:
                    QMessageBox.critical(self, self.t("br_delete"), self.t("br_error", err=e))
                    continue
            self._update_session_entry(path, new_name=None)
        self.update_info()

    def _update_session_entry(self, path, new_name):
        """Thư mục batch trong session bị đổi tên / xóa -> cập nhật session_info.json tương ứng."""
        session_dir = os.path.dirname(path)
        info = read_session_info(session_dir)
        if info is None:
            return
        old = os.path.basename(path)
        batches = info.get("batches", [])
        entry = next((b for b in batches if b.get("folder") == old), None)
        if entry is None:
            return
        if new_name is None:
            batches.remove(entry)
        else:
            entry["folder"] = new_name
            m = BATCH_DIR_RE.match(new_name)
            if m:
                entry["batch"] = int(m.group(1))
        try:
            write_session_info(session_dir, info)
        except OSError as e:
            QMessageBox.critical(self, self.t("br_title"), self.t("br_error", err=e))

    def sync_selected(self):
        folder = self.current_path()
        info = read_session_info(folder) if folder else None
        if info is None:
            QMessageBox.warning(self, self.t("br_sync"), self.t("br_not_session"))
            return
        if not self.check_not_busy([folder]):
            return
        renames, drops, _ = session_sync_plan(folder, info)
        if not renames and not drops:
            QMessageBox.information(self, self.t("br_sync"), self.t("br_sync_none"))
            return
        changes = [self.t("br_sync_rename", old_no=b.get("batch"), old=b.get("folder"), new=new)
                   for b, new in renames]
        changes += [self.t("br_sync_drop", no=b.get("batch"), folder=b.get("folder")) for b in drops]
        reply = QMessageBox.question(self, self.t("br_sync"), self.t("br_sync_confirm", changes="\n".join(changes)),
                                     QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Yes)
        if reply != QMessageBox.Yes:
            return
        for b, new in renames:
            b["folder"] = new
            b["batch"] = int(BATCH_DIR_RE.match(new).group(1))
        for b in drops:
            info["batches"].remove(b)
        try:
            write_session_info(folder, info)
        except OSError as e:
            QMessageBox.critical(self, self.t("br_sync"), self.t("br_error", err=e))
        self.update_info()

    def set_save_selected(self):
        self.save_dir_requested.emit(self.target_dir())

    def resume_selected(self):
        path = self.current_path()
        if path and os.path.isdir(path):
            self.resume_requested.emit(path)

    def preview_selected(self):
        path = self.current_path()
        if path and path.lower().endswith(".csv"):
            self.preview_requested.emit(path)

    def open_selected(self):
        path = self.current_path() or self.root_dir
        if sys.platform.startswith("win") and os.path.isfile(path):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path if os.path.isdir(path) else os.path.dirname(path)))

    def copy_selected_path(self):
        paths = self.selected_paths() or [self.root_dir]
        QApplication.clipboard().setText("\n".join(os.path.normpath(p) for p in paths))

    def refresh(self):
        # QFileSystemModel tự theo dõi thay đổi; đặt lại root để chắc chắn đọc lại thư mục từ ổ đĩa
        current = self.current_path()
        self.model.setRootPath("")
        self.set_root(self.root_dir)
        if current:
            self.select_path(current)

    def go_to_save_dir(self):
        if not self.save_dir:
            return
        if not path_inside(self.save_dir, self.root_dir):
            self.set_root(os.path.dirname(self.save_dir))
        self.select_path(self.save_dir)
        self.tree.expand(self.model.index(QDir.fromNativeSeparators(self.save_dir)))

    def change_root(self):
        root = QFileDialog.getExistingDirectory(self, self.t("br_change_root"), self.root_dir)
        if root:
            self.set_root(root)

    # ------------------------------------------------------------------ khung thông tin
    def update_info(self):
        paths = self.selected_paths()
        path = paths[0] if len(paths) == 1 else (self.current_path() if not paths else None)
        if len(paths) > 1:
            total = sum(scan_dir(p)[2] if os.path.isdir(p) else os.path.getsize(p)
                        for p in paths if os.path.exists(p))
            self.info.setHtml(f"<b>{self.t('info_selected', n=len(paths))}</b><br>{self.t('info_size')}: {fmt_size(total)}")
            return
        if not path:
            path = self.root_dir
        if not os.path.exists(path):
            self.info.setHtml(self.t("br_hint"))
            return
        try:
            html = self._dir_html(path) if os.path.isdir(path) else self._file_html(path)
        except (OSError, ValueError) as e:
            html = self.t("br_error", err=e)
        self.info.setHtml(html)

    def _row(self, key, value):
        return f"<tr><td style='color:#64748b; padding-right:8px'>{self.t(key)}</td><td>{value}</td></tr>"

    def _dir_html(self, path):
        n_dirs, n_files, total, truncated = scan_dir(path)
        mtime = datetime.fromtimestamp(os.path.getmtime(path))
        is_save = self.save_dir and norm_path(path) == norm_path(self.save_dir)
        parts = [f"<b style='font-size:13px'>📁 {os.path.basename(path) or path}</b>"
                 + (f" &nbsp;<span style='color:#10b981'>{self.t('br_saved_here')}</span>" if is_save else ""),
                 "<table cellspacing=1>",
                 self._row("info_path", self.rel(path)),
                 self._row("info_subdirs", n_dirs),
                 self._row("info_files", n_files),
                 self._row("info_size", fmt_size(total) + (" +" if truncated else "")),
                 self._row("info_modified", f"{mtime:%d/%m/%Y %H:%M}"),
                 "</table>"]
        if truncated:
            parts.append(f"<i>{self.t('info_more', n=DIR_SCAN_LIMIT)}</i>")

        kinds = {}
        for name in os.listdir(path):
            if os.path.isfile(os.path.join(path, name)):
                kinds[file_kind(name)] = kinds.get(file_kind(name), 0) + 1
        if kinds:
            parts.append(f"<p><b>{self.t('info_by_type')}</b></p><table cellspacing=1>")
            parts += [f"<tr><td style='padding-right:8px'>{k}</td><td align=right>{v}</td></tr>"
                      for k, v in sorted(kinds.items())]
            parts.append("</table>")

        meta = os.path.join(path, "metadata_log.csv")
        if os.path.exists(meta):
            with open(meta, newline="", encoding="utf-8") as f:
                n_rows = sum(1 for _ in csv.DictReader(f))
            parts.append(f"<p>{self.t('info_metadata', n=n_rows)}</p>")

        info = read_session_info(path)
        if info is not None:
            parts.append(self._session_html(path, info))
        return "".join(parts)

    def _session_html(self, path, info):
        busy = [norm_path(p) for p in self.busy_paths() if p]
        parts = [f"<p><b>{self.t('info_session')}</b><br>"
                 f"{self.t('info_incub')}: {info.get('incubation_start', '?').replace('T', ' ')}<br>"
                 f"{self.t('info_created')}: {info.get('created', '?').replace('T', ' ')}</p>",
                 "<table cellspacing=0 cellpadding=2 border=1 style='border-color:#334155'>",
                 "<tr>" + "".join(f"<th>{self.t(k)}</th>" for k in
                                  ("info_batch", "info_folder", "info_eggs", "info_started", "info_status")) + "</tr>"]
        for b in info.get("batches", []):
            folder = b.get("folder") or ""
            full = os.path.join(path, folder)
            if not os.path.isdir(full):
                status = f"<span style='color:#f59e0b'>{self.t('info_missing')}</span>"
            elif norm_path(full) in busy:
                status = f"<span style='color:#38bdf8'>{self.t('info_running')}</span>"
            elif b.get("stopped_early"):
                status = self.t("info_stopped")
            else:
                status = f"<span style='color:#10b981'>{self.t('info_ok')}</span>"
            started = (b.get("started") or "").replace("T", " ")[:16]
            parts.append(f"<tr><td align=center>{b.get('batch', '')}</td><td>{folder}</td>"
                         f"<td align=center>{b.get('n_eggs', '')}</td><td>{started}</td><td>{status}</td></tr>")
        parts.append("</table>")
        renames, drops, unlisted = session_sync_plan(path, info)
        if renames or drops:
            parts.append(f"<p style='color:#f59e0b'>{self.t('info_sync_hint')}</p>")
        if unlisted:
            parts.append(f"<p style='color:#f59e0b'>{self.t('info_unlisted', names=', '.join(unlisted))}</p>")
        return "".join(parts)

    def _file_html(self, path):
        st = os.stat(path)
        name = os.path.basename(path)
        parts = [f"<b style='font-size:13px'>📄 {name}</b>", "<table cellspacing=1>",
                 self._row("info_path", self.rel(path)),
                 self._row("info_size", fmt_size(st.st_size)),
                 self._row("info_modified", f"{datetime.fromtimestamp(st.st_mtime):%d/%m/%Y %H:%M:%S}")]
        if name.lower().endswith(".csv") and name != "metadata_log.csv":
            try:
                wl, raw, _ = load_spectrum_csv(path)
                parts.append(self._row("info_points", len(wl)))
                parts.append(self._row("info_range", f"{np.nanmin(wl):.1f} – {np.nanmax(wl):.1f} nm"))
                parts.append(self._row("info_peak", f"{np.nanmax(raw):.0f}"))
            except (OSError, ValueError):
                pass
        parts.append("</table>")

        # Dòng metadata_log.csv tương ứng (sample_id = tên file bỏ đuôi)
        meta = os.path.join(os.path.dirname(path), "metadata_log.csv")
        sample_id = os.path.splitext(name)[0]
        if os.path.exists(meta) and name != "metadata_log.csv":
            with open(meta, newline="", encoding="utf-8") as f:
                row = next((r for r in csv.DictReader(f) if r.get("sample_id") == sample_id), None)
            if row:
                parts.append(f"<p><b>{self.t('info_meta_row')}</b></p><table cellspacing=1>")
                parts += [f"<tr><td style='color:#64748b; padding-right:8px'>{k}</td><td>{v}</td></tr>"
                          for k, v in row.items() if v not in ("", None) and k != "sample_id"]
                parts.append("</table>")
        return "".join(parts)


# ==============================================================================
# WORKER LUỒNG SPECTROMETER ĐO REALTIME & HỖ TRỢ THÍ NGHIỆM
# ==============================================================================
class SpectrometerWorker(QThread):
    spectrum_ready = pyqtSignal(np.ndarray, float, object, float)  # spectrum, exposure_ms, exposure_status, timestamp
    connection_status = pyqtSignal(bool, str)
    device_info_ready = pyqtSignal(str)
    exposure_configured = pyqtSignal(float, str, float)  # target_ms, mode đọc lại, exposure_ms đọc lại

    def __init__(self, ports=("COM3",), baud=115200, exposure_ms=EXPOSURE_MS, probe=False):
        super().__init__()
        # Danh sách cổng thử lần lượt; probe=True: cổng chỉ được chọn khi thiết bị trả lời
        # (cổng mở được nhưng là thiết bị khác thì chuyển sang cổng kế tiếp)
        self.ports = list(ports)
        self.probe = probe
        self.port = self.ports[0]
        self.baud = baud
        self.exposure_ms = float(exposure_ms)
        # Thời gian chờ 1 khung GET_DATA: exposure + 1 s dự phòng truyền
        self.frame_timeout_s = self.exposure_ms / 1000.0 + 1.0
        self.running = False
        self.ser = None
        self.start_wl = 380.0
        self.end_wl = 1050.0
        self.device_id = "UNKNOWN"

    def run(self):
        try:
            self.connect_serial()
            self.start_wl, self.end_wl = self.fetch_wavelength_range()
            self.device_id = self.fetch_device_id()
            self.device_info_ready.emit(self.device_id)
            # Luôn cố định exposure (Manual): thiết bị có thể còn lưu chế độ Auto từ Flameeye
            mode, readback_ms = self.configure_exposure(self.exposure_ms)
            self.exposure_configured.emit(self.exposure_ms, mode, readback_ms)
            self.connection_status.emit(True, f"{self.port} ({self.start_wl:.0f}-{self.end_wl:.0f} nm)")
        except Exception as e:
            self.connection_status.emit(False, str(e))
            return

        self.running = True

        while self.running:
            try:
                m = self.read_frame()
                if m is not None:
                    spectrum = np.array(m["raw_spectrum"], dtype=np.float32)
                    exposure_ms = float(m.get("exposure_time", float("nan")))
                    exposure_status = m.get("exposure_status")
                    self.spectrum_ready.emit(spectrum, exposure_ms, exposure_status, time.time())
                else:
                    time.sleep(0.02)
            except Exception:
                time.sleep(0.05)

        if self.ser and self.ser.is_open:
            try:
                self.ser.write(build_message(MessageType.STOP, b""))
                self.ser.flush()
            except Exception:
                pass
            self.ser.close()

    def connect_serial(self):
        """Mở cổng đầu tiên trong self.ports kết nối được; lỗi -> thử cổng kế tiếp."""
        errors = []
        for port in self.ports:
            ser = None
            try:
                ser = Serial(port, self.baud, timeout=0.2)
                ser.reset_input_buffer()
                ser.reset_output_buffer()
                time.sleep(0.2)
                self.ser, self.port = ser, port
                if self.probe and self.request(
                        build_message(MessageType.GET_EXPOSURE_MODE, b""), MessageType.GET_EXPOSURE_MODE) is None:
                    raise IOError("thiết bị không phản hồi")
                return
            except Exception as e:
                errors.append(f"{port}: {e}")
                if ser is not None and ser.is_open:
                    ser.close()
                self.ser = None
        raise IOError("; ".join(errors))

    def stop(self):
        self.running = False
        self.wait(int(self.frame_timeout_s * 1000) + 500)

    def read_frame(self):
        """Gửi GET_DATA và chờ 1 khung phổ (tối đa frame_timeout_s). Trả về message hoặc None."""
        self.ser.reset_input_buffer()
        self.ser.write(build_message(MessageType.GET_DATA, b""))
        self.ser.flush()

        buffer = b""
        frame = None
        start_req = time.time()
        try:
            while frame is None and time.time() - start_req < self.frame_timeout_s:
                waiting = self.ser.in_waiting
                chunk = self.ser.read(waiting if waiting > 0 else 1)
                if chunk:
                    buffer += chunk
                    try:
                        buffer, msgs = parse_messages(buffer)
                    except ValueError:
                        # Buffer bị lệch khung (byte rác / checksum sai) -> reset để resync
                        buffer = b""
                        continue
                    for m in msgs:
                        if m.get("message_type") == MessageType.GET_DATA and "raw_spectrum" in m:
                            frame = m
                            break
                time.sleep(0.005)
        finally:
            # Dừng streaming của thiết bị trước khi gửi GET_DATA tiếp theo
            self.ser.write(build_message(MessageType.STOP, b""))
            self.ser.flush()
        return frame

    def fetch_wavelength_range(self):
        try:
            self.ser.write(build_message(MessageType.GET_RANGE, b""))
            self.ser.flush()
            time.sleep(0.15)
            buf = self.ser.read(self.ser.in_waiting or 64)
            _, msgs = parse_messages(buf)
            for m in msgs:
                if m.get("message_type") == MessageType.GET_RANGE:
                    return float(m["start_wavelength"]), float(m["end_wavelength"])
        except Exception:
            pass
        return 380.0, 1050.0

    def fetch_device_id(self):
        try:
            self.ser.write(build_message(MessageType.GET_DEVICE_ID, b""))
            self.ser.flush()
            time.sleep(0.15)
            buf = self.ser.read(self.ser.in_waiting or 64)
            _, msgs = parse_messages(buf)
            for m in msgs:
                if m.get("message_type") == MessageType.GET_DEVICE_ID:
                    return str(m.get("device_id", "UNKNOWN")).strip() or "UNKNOWN"
        except Exception:
            pass
        return "UNKNOWN"


    def request(self, message, expected_type, timeout_s=1.0):
        """
        Gửi 1 lệnh cấu hình và chờ tới khi nhận được message trả lời có kiểu expected_type
        (ack của lệnh SET hoặc dữ liệu của lệnh GET) - giống Flameeye, lệnh sau chỉ gửi khi
        lệnh trước đã được thiết bị trả lời. Trả về message hoặc None nếu hết thời gian.
        """
        self.ser.reset_input_buffer()
        self.ser.write(message)
        self.ser.flush()

        buffer = b""
        start = time.time()
        while time.time() - start < timeout_s:
            chunk = self.ser.read(self.ser.in_waiting or 1)
            if chunk:
                buffer += chunk
                try:
                    buffer, msgs = parse_messages(buffer)
                except ValueError:
                    buffer = b""
                    continue
                for m in msgs:
                    if m.get("message_type") == expected_type:
                        return m
        return None

    def set_exposure_mode(self, mode):
        """Gửi SET_EXPOSURE_MODE, trả về True nếu thiết bị ack thành công (status 0x00)."""
        try:
            ack = self.request(build_set_exposure_mode(mode), MessageType.SET_EXPOSURE_MODE)
            return ack is not None and ack.get("ack_status") == 0
        except Exception:
            return False

    def configure_exposure(self, exposure_ms, retries=3):
        """
        Cố định exposure theo đúng trình tự Flameeye (Auto -> Manual rồi "Set"):
        SET_EXPOSURE_MODE(MANUAL) -> GET_EXPOSURE_MODE -> SET_EXPOSURE_TIME -> GET_EXPOSURE_TIME.
        Thử lại tối đa `retries` lần. Trả về (tên mode đọc lại, exposure_ms đọc lại - NaN nếu không đọc được).
        """
        mode, readback_ms = "UNKNOWN", float("nan")
        for _ in range(retries):
            try:
                self.set_exposure_mode(ExposureMode.MANUAL)
                m = self.request(build_message(MessageType.GET_EXPOSURE_MODE, b""), MessageType.GET_EXPOSURE_MODE)
                if m is not None:
                    mode = m["exposure_mode"].name
                self.request(build_set_exposure_time(exposure_ms), MessageType.SET_EXPOSURE_TIME)
                m = self.request(build_message(MessageType.GET_EXPOSURE_TIME, b""), MessageType.GET_EXPOSURE_TIME)
                if m is not None:
                    readback_ms = float(m["exposure_time"])
                if mode == ExposureMode.MANUAL.name and exposure_matches(readback_ms, exposure_ms):
                    break
            except Exception:
                pass
        return mode, readback_ms


# ==============================================================================
# CỬA SỔ DASHBOARD TRUNG TÂM (SPECTRALENS PRO)
# ==============================================================================
class SpectraLensDashboard(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        
        self.current_theme = "dark"
        self.setStyleSheet(DARK_THEME)
        self.resize(1400, 760)

        self.current_lang = "vi"
        self.settings = {
            "port_auto": True,          # Tự động: COM3, không được thì COM5 (SPECTRO_PORTS)
            "port": SPECTRO_PORTS[0],   # Cổng dùng khi tắt tự động
            "baud": 115200,
            "exposure_ms": EXPOSURE_MS,
            "batch_size": BATCH_SIZE_DEFAULT,
            "egg_n_frames": EGG_N_FRAMES_DEFAULT,
            "egg_placements": EGG_PLACEMENTS_DEFAULT,
            # "full" (4 phase) | "opened_only" (chỉ đo trứng mở vỏ) | "dye_titration" (chuẩn độ màu trên trứng mở vỏ)
            # | "shell_only" (chỉ đo vỏ trứng) | "embryo" (trứng có phôi thực tế, không mở vỏ)
            "experiment_mode": "full",
            "reuse_ref": False,          # True: bỏ qua đo Dark/Lamp, dùng lại Dark/Lamp mới nhất trong ref_dir
            "ref_dir": DEFAULT_REF_DIR,
            "titration_levels": TITRATION_LEVELS_DEFAULT,
            "titration_repeats": TITRATION_REPEATS_DEFAULT,
            "titration_water": True,
            "continuous": False,         # Chế độ "embryo": đo liên tục nhiều batch trong 1 session
            "save_dir": DEFAULT_DATASET_DIR,
        }
        os.makedirs(self.settings["save_dir"], exist_ok=True)

        self.drag_position = QPoint()
        self.worker = None
        self.device_id = "UNKNOWN"

        # Buffers dữ liệu
        self.current_raw = None
        self.current_exposure_ms = None
        self.wavelengths = None
        self.snapshot_spectrum = None
        self.view_mode = "spectrum"
        self.preview_data = None        # Phổ nạp từ file CSV ở sidebar: (wavelengths, raw, absorbance|None)

        # BIẾN QUẢN LÝ THÍ NGHIỆM 4 PHASE: dark -> lamp -> single -> batch (Dev Brief mục 2, 4, 7)
        self.is_experiment_running = False
        self.run_mode = "full"         # Chế độ của thí nghiệm đang chạy (chốt lúc bắt đầu): mã trong EXPERIMENT_MODES
        self.current_phase = None      # None, "dark", "lamp", "single", "batch", "titration"
        self.titr_plan = []            # Chuẩn độ màu: danh sách phép đo (build_titration_plan)
        self.titr_idx = 0              # Index phép đo chuẩn độ kế tiếp trong titr_plan
        self.titr_plan_text = ""       # Mô tả kế hoạch chuẩn độ (hiện trong hướng dẫn bắt đầu)
        self.batch_size = BATCH_SIZE_DEFAULT  # Chốt từ settings lúc bắt đầu thí nghiệm
        self.single_step = 0           # Tổng số phép đo Phase 3 đã lưu (0..batch_size * len(SINGLE_EGG_STEPS))
        self.dark_spectrum = None      # D(λ): trung bình N khung, chưa trừ gì (mục 2.1)
        self.dark_exposure_ms = None
        self.ref_spectrum = None       # R(λ): trung bình N khung đèn nền, thô
        self.ref_exposure_ms = None
        self.ref_dark_corr = None      # R(λ) - D(λ), cache để tính absorbance mỗi trứng (mục 4.1)
        self.ref_color_ratio = None    # Baseline màu của Reference, dùng phát hiện trôi (mục 8.3)
        self.egg_counter = 0           # Số lượng trứng batch (Phase 4) đã đo xong (đủ mọi lần đặt)
        self.batch_meas = 0            # Tổng số phép đo batch đã lưu (0..batch_size * egg_placements)
        self.egg_n_frames = EGG_N_FRAMES_DEFAULT      # Chốt từ settings lúc bắt đầu thí nghiệm
        self.egg_placements = EGG_PLACEMENTS_DEFAULT  # Chốt từ settings lúc bắt đầu thí nghiệm
        self.batch_ends = (None,)      # Các đầu trứng đo cho mỗi mẫu batch (BATCH_ENDS), chốt lúc bắt đầu
        self.last_egg = None          # {"csv_path", "sample_id", "phase"} của mẫu vừa lưu, phục vụ Hủy
        self.metadata_rows = []        # Log 1 dòng / phép đo, ghi ra metadata_log.csv (mục 10)
        self.run_dir = None            # Thư mục của lần thí nghiệm đang chạy: save_dir/<timestamp>_<chế độ>
        self.egg_labels = {}           # Chế độ "embryo": số thứ tự trứng -> ID người dùng nhập (cột egg_label)
        self.incubation_start = None   # Chế độ "embryo": datetime bắt đầu ấp lô trứng (cột incubation_start)
        self.last_incubation_start = None   # Gợi ý cho lần thí nghiệm sau (cùng lô đo nhiều ngày)
        # Đo liên tục: session = {"dir", "info" (nội dung session_info.json)}; None khi đo 1 lần
        self.session = None
        self.batch_no = None           # Số thứ tự batch đang đo / sắp đo trong session
        self.label_hints = {}          # ID trứng của batch trước (gợi ý điền sẵn cho cùng số thứ tự)

        # State machine chụp trung bình N khung, dùng chung cho Dark/Reference/Trứng
        self.capture_state = None

        self.last_time = time.time()
        self.frame_count = 0

        self.init_ui()
        self.update_plot_theme()
        self.retranslate_ui()

    def init_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # 1. Title Bar
        title_bar = QFrame()
        title_bar.setObjectName("TitleBar")
        title_bar.setFixedHeight(38)
        t_layout = QHBoxLayout(title_bar)
        t_layout.setContentsMargins(12, 0, 8, 0)
        t_layout.setSpacing(8)

        self.browser_btn = QPushButton()
        self.browser_btn.setFixedHeight(26)
        self.browser_btn.setCheckable(True)
        self.browser_btn.setChecked(True)
        self.browser_btn.toggled.connect(self.toggle_browser)
        t_layout.addWidget(self.browser_btn)

        t_layout.addWidget(QLabel("🔬"))
        self.title_lbl = QLabel()
        self.title_lbl.setObjectName("WinTitle")
        t_layout.addWidget(self.title_lbl)
        t_layout.addStretch()

        self.lang_combo = QComboBox()
        self.lang_combo.addItem("Tiếng Việt", "vi")
        self.lang_combo.addItem("English", "en")
        self.lang_combo.addItem("简体中文", "zh_CN")
        self.lang_combo.addItem("繁體中文", "zh_TW")
        self.lang_combo.addItem("العربية", "ar")
        self.lang_combo.currentIndexChanged.connect(self.on_language_changed)
        t_layout.addWidget(self.lang_combo)

        self.theme_btn = QPushButton("☀️ Sáng")
        self.theme_btn.setFixedHeight(26)
        self.theme_btn.clicked.connect(self.toggle_theme)
        t_layout.addWidget(self.theme_btn)

        self.settings_btn = QPushButton()
        self.settings_btn.setFixedHeight(26)
        self.settings_btn.clicked.connect(self.open_settings)
        t_layout.addWidget(self.settings_btn)

        min_btn = QPushButton("─")
        min_btn.setObjectName("WinBtn")
        min_btn.clicked.connect(self.showMinimized)
        t_layout.addWidget(min_btn)

        self.max_btn = QPushButton("□")
        self.max_btn.setObjectName("WinBtn")
        self.max_btn.clicked.connect(self.toggle_maximize)
        t_layout.addWidget(self.max_btn)

        close_btn = QPushButton("✕")
        close_btn.setObjectName("WinCloseBtn")
        close_btn.clicked.connect(self.close)
        t_layout.addWidget(close_btn)

        title_bar.mousePressEvent = self.title_mouse_press
        title_bar.mouseMoveEvent = self.title_mouse_move
        root_layout.addWidget(title_bar)

        # 2. Main Body Container
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(12, 10, 12, 12)
        body_layout.setSpacing(10)

        # KPI HUD
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(10)
        self.m_status = self.create_metric_card("", "OFFLINE", "#f87171")
        self.m_peak = self.create_metric_card("", "0", "#38bdf8")
        self.m_lambda_peak = self.create_metric_card("", "-- nm", "#fb923c")
        self.m_egg_count = self.create_metric_card("", "0", "#a855f7")

        kpi_row.addWidget(self.m_status, 2)
        kpi_row.addWidget(self.m_peak, 1)
        kpi_row.addWidget(self.m_lambda_peak, 1)
        kpi_row.addWidget(self.m_egg_count, 1)
        body_layout.addLayout(kpi_row)

        content_split = QHBoxLayout()
        content_split.setSpacing(10)

        # Plot Widget
        plot_frame = QFrame()
        plot_frame.setObjectName("CardFrame")
        pf_layout = QVBoxLayout(plot_frame)
        pf_layout.setContentsMargins(8, 8, 8, 8)

        self.plot_widget = pg.PlotWidget()
        self.plot_widget.addLegend(offset=(10, 10))
        self.curve_realtime = self.plot_widget.plot(name="Live")
        self.curve_dark = self.plot_widget.plot(name="Dark (Môi trường tối)")
        self.curve_ref = self.plot_widget.plot(name="Đèn Nền (Reference)")
        self.curve_last_egg = self.plot_widget.plot(name="Trứng vừa đo (Egg)")
        self.curve_snapshot = self.plot_widget.plot(name="Snapshot")
        self.curve_preview = self.plot_widget.plot(name="File")   # Phổ xem lại từ sidebar dữ liệu

        pf_layout.addWidget(self.plot_widget)
        content_split.addWidget(plot_frame, stretch=7)

        # Control Panel
        ctrl_frame = QFrame()
        ctrl_frame.setObjectName("CardFrame")
        ctrl_layout = QVBoxLayout(ctrl_frame)
        ctrl_layout.setContentsMargins(12, 12, 12, 12)
        ctrl_layout.setSpacing(10)

        self.ctrl_title = QLabel()
        self.ctrl_title.setStyleSheet("font-weight: 700; color: #94a3b8;")
        ctrl_layout.addWidget(self.ctrl_title)

        # NÚT BẮT ĐẦU / DỪNG THÍ NGHIỆM 4 PHASE (dark -> lamp -> single -> batch)
        self.exp_btn = QPushButton()
        self.exp_btn.setObjectName("ExpBtn")
        self.exp_btn.setFixedHeight(38)
        self.exp_btn.clicked.connect(self.toggle_experiment)
        ctrl_layout.addWidget(self.exp_btn)

        # NÚT TRIGGER LƯU MẪU TRỨNG THỦ CÔNG (Phase 3 + 4, tương đương phím Enter)
        self.trigger_egg_btn = QPushButton()
        self.trigger_egg_btn.setObjectName("PrimaryBtn")
        self.trigger_egg_btn.setFixedHeight(34)
        self.trigger_egg_btn.setEnabled(False)
        self.trigger_egg_btn.clicked.connect(self.trigger_egg_capture)
        ctrl_layout.addWidget(self.trigger_egg_btn)

        # NÚT HỦY MẪU TRỨNG VỪA LƯU (lỡ tay / cần đo lại)
        self.undo_egg_btn = QPushButton()
        self.undo_egg_btn.setFixedHeight(28)
        self.undo_egg_btn.setEnabled(False)
        self.undo_egg_btn.clicked.connect(self.undo_last_egg)
        ctrl_layout.addWidget(self.undo_egg_btn)

        # NÚT ĐO LẠI ĐÈN NỀN (Reference) GIỮA CHỪNG PHASE 3/4 - Dev Brief mục 8.4
        self.remeasure_ref_btn = QPushButton()
        self.remeasure_ref_btn.setFixedHeight(28)
        self.remeasure_ref_btn.setEnabled(False)
        self.remeasure_ref_btn.clicked.connect(self.remeasure_reference)
        ctrl_layout.addWidget(self.remeasure_ref_btn)

        # Nút Live Stream cơ bản
        self.stream_btn = QPushButton()
        self.stream_btn.setObjectName("SuccessBtn")
        self.stream_btn.setFixedHeight(32)
        self.stream_btn.clicked.connect(self.toggle_stream)
        ctrl_layout.addWidget(self.stream_btn)

        # Snapshot
        self.snap_btn = QPushButton()
        self.snap_btn.setObjectName("PrimaryBtn")
        self.snap_btn.setFixedHeight(30)
        self.snap_btn.clicked.connect(self.take_snapshot)
        ctrl_layout.addWidget(self.snap_btn)

        self.clear_snap_btn = QPushButton()
        self.clear_snap_btn.clicked.connect(self.clear_snapshot)
        ctrl_layout.addWidget(self.clear_snap_btn)

        # View Mode Selector
        self.mode_combo = QComboBox()
        self.mode_combo.currentIndexChanged.connect(self.on_mode_switched)
        ctrl_layout.addWidget(self.mode_combo)

        ctrl_layout.addStretch()
        content_split.addWidget(ctrl_frame, stretch=3)

        body_layout.addLayout(content_split, stretch=1)

        # 3. Sidebar quản lý dữ liệu (trái) | phần đo (phải), kéo được độ rộng
        self.browser = DataBrowserPanel(DATA_ROOT_DEFAULT, self._busy_paths)
        self.browser.set_save_dir(self.settings["save_dir"])
        self.browser.preview_requested.connect(self.preview_file)
        self.browser.preview_cleared.connect(self.clear_preview)
        self.browser.save_dir_requested.connect(self.set_save_dir)
        self.browser.resume_requested.connect(self.resume_session)
        self.browser_wrap = QWidget()
        bw_layout = QVBoxLayout(self.browser_wrap)
        bw_layout.setContentsMargins(12, 10, 0, 12)
        bw_layout.addWidget(self.browser)
        self.browser_wrap.setMinimumWidth(240)
        self.main_split = QSplitter(Qt.Horizontal)
        self.main_split.setChildrenCollapsible(False)
        self.main_split.addWidget(self.browser_wrap)
        self.main_split.addWidget(body)
        self.main_split.setStretchFactor(0, 0)
        self.main_split.setStretchFactor(1, 1)
        self.main_split.setSizes([320, 1080])
        root_layout.addWidget(self.main_split, stretch=1)

    def create_metric_card(self, title, init_val, color):
        card = QFrame()
        card.setObjectName("MetricCard")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(2)

        sub = QLabel(title)
        sub.setObjectName("MetricSub")
        val = QLabel(init_val)
        val.setObjectName("MetricVal")
        val.setStyleSheet(f"color: {color};")
        lay.addWidget(sub)
        lay.addWidget(val)
        card.sub_lbl = sub
        card.val_lbl = val
        return card

    def toggle_theme(self):
        t = TRANSLATIONS[self.current_lang]
        if self.current_theme == "dark":
            self.current_theme = "light"
            self.setStyleSheet(LIGHT_THEME)
            self.theme_btn.setText(t["theme_dark"])
        else:
            self.current_theme = "dark"
            self.setStyleSheet(DARK_THEME)
            self.theme_btn.setText(t["theme_light"])
        self.update_plot_theme()

    def update_plot_theme(self):
        if self.current_theme == "dark":
            self.plot_widget.setBackground("#030712")
            self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
            self.curve_realtime.setPen(pg.mkPen(color="#38bdf8", width=2.2))
            self.curve_dark.setPen(pg.mkPen(color="#64748b", width=1.5, style=Qt.DashDotLine))
            self.curve_ref.setPen(pg.mkPen(color="#c084fc", width=1.5, style=Qt.DotLine))
            self.curve_last_egg.setPen(pg.mkPen(color="#f97316", width=2.0))
            self.curve_snapshot.setPen(pg.mkPen(color="#facc15", width=2.0, style=Qt.DashLine))
            self.curve_preview.setPen(pg.mkPen(color="#22c55e", width=2.0))
        else:
            self.plot_widget.setBackground("#ffffff")
            self.plot_widget.showGrid(x=True, y=True, alpha=0.2)
            self.curve_realtime.setPen(pg.mkPen(color="#0284c7", width=2.2))
            self.curve_dark.setPen(pg.mkPen(color="#475569", width=1.5, style=Qt.DashDotLine))
            self.curve_ref.setPen(pg.mkPen(color="#7c3aed", width=1.5, style=Qt.DotLine))
            self.curve_last_egg.setPen(pg.mkPen(color="#ea580c", width=2.0))
            self.curve_snapshot.setPen(pg.mkPen(color="#d97706", width=2.0, style=Qt.DashLine))
            self.curve_preview.setPen(pg.mkPen(color="#16a34a", width=2.0))

    def on_language_changed(self, idx):
        self.current_lang = self.lang_combo.itemData(idx)
        self.retranslate_ui()

    def retranslate_ui(self):
        t = TRANSLATIONS[self.current_lang]

        if t["rtl"]:
            self.setLayoutDirection(Qt.RightToLeft)
        else:
            self.setLayoutDirection(Qt.LeftToRight)

        self.title_lbl.setText(t["app_title"])
        self.settings_btn.setText(t["settings"])
        self.browser_btn.setText(t["br_toggle"])
        self.browser.retranslate(self.current_lang)

        if self.current_theme == "dark":
            self.theme_btn.setText(t["theme_light"])
        else:
            self.theme_btn.setText(t["theme_dark"])

        self.m_status.sub_lbl.setText(t["kpi_status"])
        self.m_peak.sub_lbl.setText(t["kpi_peak"])
        self.m_lambda_peak.sub_lbl.setText(t["kpi_lambda_peak"])
        self.m_egg_count.sub_lbl.setText(t["kpi_egg_count"])

        self.ctrl_title.setText(t["ctrl_title"])
        
        # Cập nhật nút Thí nghiệm
        if not self.is_experiment_running:
            self.exp_btn.setText(t["btn_start_exp"])
            self.exp_btn.setObjectName("ExpBtn")
        else:
            self.exp_btn.setText(t["btn_stop_exp"])
            self.exp_btn.setObjectName("DangerBtn")
        self.exp_btn.setStyle(self.exp_btn.style())
        self._update_trigger_btn_text()
        self.undo_egg_btn.setText(t["btn_undo_egg"])
        self.remeasure_ref_btn.setText(t["btn_remeasure_ref"])

        # Cập nhật nút Live Stream
        if self.worker is None or not self.worker.isRunning():
            self.stream_btn.setText(t["btn_start_stream"])
            self.stream_btn.setObjectName("SuccessBtn")
        else:
            self.stream_btn.setText(t["btn_stop_stream"])
            self.stream_btn.setObjectName("DangerBtn")
        self.stream_btn.setStyle(self.stream_btn.style())

        self.snap_btn.setText(t["btn_snapshot"])
        self.clear_snap_btn.setText(t["btn_clear_snapshot"])

        cur_idx = self.mode_combo.currentIndex()
        self.mode_combo.blockSignals(True)
        self.mode_combo.clear()
        self.mode_combo.addItem(t["mode_spectrum"], "spectrum")
        self.mode_combo.addItem(t["mode_absorbance"], "absorbance")
        self.mode_combo.setCurrentIndex(cur_idx if cur_idx >= 0 else 0)
        self.mode_combo.blockSignals(False)

        self.plot_widget.setLabel("bottom", t["axis_wavelength"], units="nm")
        if self.view_mode == "spectrum":
            self.plot_widget.setLabel("left", t["axis_intensity"], units="ADC Counts")
        else:
            self.plot_widget.setLabel("left", t["axis_absorbance"], units="OD")

    # ==========================================================================
    # LOGIC THÍ NGHIỆM 4 PHASE:
    #   (1) TỐI -> (2) ĐÈN NỀN
    #   -> (3) BATCH N TRỨNG CHƯA MỞ VỎ: mỗi trứng 4 file (đầu tù / đầu nhọn x chưa / đã bơm màu)
    #   -> (4) ĐO LẠI ĐÚNG N TRỨNG ĐÓ SAU KHI MỞ VỎ (CÓ MÀU), CÙNG THỨ TỰ #1..#N
    # CHẾ ĐỘ "opened_only": sau (1)-(2) đo thẳng N trứng đã mở vỏ (dùng chung logic Phase 4)
    # CHẾ ĐỘ "dye_titration": N trứng đã mở vỏ, mỗi trứng đo nền -> [nước] -> các mức màu ml tăng dần
    # CHẾ ĐỘ "shell_only": sau (1)-(2) đo N vỏ trứng (logic batch Phase 4, file shell_<ID>[_p<k>]_<ts>.csv)
    # CHẾ ĐỘ "embryo": sau (1)-(2) đo N trứng có phôi thực tế, KHÔNG mở vỏ / bơm màu
    #   (logic batch Phase 4, file embryo_<ID>[_p<k>]_<ts>.csv)
    # Mọi chế độ: settings["reuse_ref"] = True -> bỏ qua (1)-(2), Dark/Lamp nạp từ dataset cũ (settings["ref_dir"])
    # ==========================================================================
    def toggle_experiment(self):
        mode = self.settings.get("experiment_mode", "full")
        if self.is_experiment_running:
            self.stop_experiment()
        elif mode == "embryo" and self.settings.get("continuous", False):
            self._start_session()
        else:
            self.session = None
            self.label_hints = {}
            self._start_mode(mode)

    def _start_mode(self, mode, incubation_start=None):
        """Bắt đầu 1 lần đo (1 batch) của chế độ. True nếu đã bắt đầu, False nếu người dùng hủy / lỗi."""
        if self.settings.get("reuse_ref", False):
            return self._start_reused_ref_experiment(mode, incubation_start)
        return self._start_full_experiment(mode, incubation_start)

    # --------------------------------------------------------------------
    # ĐO LIÊN TỤC (chế độ "embryo" + settings["continuous"]):
    #   <save_dir>/<timestamp>_embryo_session/
    #       session_info.json            (bắt đầu ấp, khoảng cách batch, danh sách batch + ID trứng)
    #       batch_01_<timestamp>/ ...    (Dark, Lamp, embryo_*.csv, metadata_log.csv như 1 lần đo thường)
    #       batch_02_<timestamp>/ ...
    # Mỗi lần bấm "Bắt đầu thí nghiệm" = 1 batch (thư mục con có timestamp); thời gian giữa các batch do
    # người dùng tự canh. Lần sau chọn "Tiếp tục session cũ" để ghi batch kế tiếp vào cùng thư mục session.
    # --------------------------------------------------------------------
    def _start_session(self):
        t = TRANSLATIONS[self.current_lang]
        box = QMessageBox(QMessageBox.Question, t["msg_session_title"], t["msg_session_body"], parent=self)
        new_btn = box.addButton(t["btn_session_new"], QMessageBox.AcceptRole)
        cont_btn = box.addButton(t["btn_session_continue"], QMessageBox.ActionRole)
        box.addButton(t["btn_cancel"], QMessageBox.RejectRole)
        box.exec_()
        if box.clickedButton() is new_btn:
            incubation_start = self._ask_incubation_start()
            if incubation_start is None:
                return
            session_dir = os.path.join(self.settings["save_dir"], f"{datetime.now():%Y%m%d_%H%M%S}_embryo_session")
            info = {"mode": "embryo", "created": datetime.now().isoformat(timespec="seconds"),
                    "incubation_start": incubation_start.isoformat(timespec="minutes"), "batches": []}
        elif box.clickedButton() is cont_btn:
            session_dir = QFileDialog.getExistingDirectory(self, t["btn_session_continue"], self.settings["save_dir"])
            if session_dir:
                self.resume_session(session_dir)
            return
        else:
            return
        self.session = {"dir": session_dir, "info": info, "incubation_start": incubation_start}
        if not self._start_next_batch():
            self.session = None

    def resume_session(self, session_dir):
        """Đo batch kế tiếp vào session có sẵn (chọn trong hộp thoại hoặc từ sidebar dữ liệu)."""
        t = TRANSLATIONS[self.current_lang]
        if self.is_experiment_running:
            QMessageBox.warning(self, t["msg_session_title"], t["br_busy"].format(path=self.run_dir))
            return
        try:
            with open(os.path.join(session_dir, SESSION_INFO_FILE), encoding="utf-8") as f:
                info = json.load(f)
            incubation_start = datetime.fromisoformat(info["incubation_start"])
        except (OSError, ValueError, KeyError):
            QMessageBox.critical(self, t["msg_session_title"], f"{t['err_session_missing']}\n{session_dir}")
            return
        reply = QMessageBox.question(
            self, t["msg_session_title"],
            t["msg_session_resume"].format(dir=session_dir, done=len(info["batches"]),
                                           n=self._next_batch_no(info["batches"]),
                                           incub=f"{incubation_start:%d/%m/%Y %H:%M}"),
            QMessageBox.Ok | QMessageBox.Cancel, QMessageBox.Ok)
        if reply != QMessageBox.Ok:
            return
        self.last_incubation_start = incubation_start
        self.session = {"dir": session_dir, "info": info, "incubation_start": incubation_start}
        if not self._start_next_batch():
            self.session = None

    @staticmethod
    def _next_batch_no(batches):
        """Số batch kế tiếp = số lớn nhất + 1 (không trùng số khi đã xóa batch ở giữa)."""
        return max((int(b.get("batch") or 0) for b in batches), default=0) + 1

    def _save_session_info(self):
        os.makedirs(self.session["dir"], exist_ok=True)
        with open(os.path.join(self.session["dir"], SESSION_INFO_FILE), "w", encoding="utf-8") as f:
            json.dump(self.session["info"], f, ensure_ascii=False, indent=2)

    def _start_next_batch(self):
        """Bắt đầu batch kế tiếp của session (ID trứng gợi ý theo batch trước). False nếu bị hủy."""
        batches = self.session["info"]["batches"]
        self.batch_no = self._next_batch_no(batches)
        last_labels = next((b["egg_labels"] for b in reversed(batches) if b.get("egg_labels")), {})
        self.label_hints = {int(k): v for k, v in last_labels.items()}
        return self._start_mode("embryo", self.session["incubation_start"])

    def _finish_batch(self, stopped=False):
        """Ghi kết thúc batch hiện tại vào session_info.json (stopped=True: dừng giữa chừng)."""
        entry = self.session["info"]["batches"][-1]
        entry["finished"] = datetime.now().isoformat(timespec="seconds")
        entry["n_eggs"] = self.egg_counter
        entry["egg_labels"] = {str(k): v for k, v in sorted(self.egg_labels.items())}
        if stopped:
            entry["stopped_early"] = True
        self._save_session_info()

    def _prepare_titration_plan(self):
        """Lập kế hoạch chuẩn độ từ settings: (plan, mô tả kế hoạch) hoặc None (đã báo lỗi)."""
        t = TRANSLATIONS[self.current_lang]
        n_eggs = int(self.settings.get("batch_size", BATCH_SIZE_DEFAULT))
        levels = parse_titration_levels(self.settings.get("titration_levels", TITRATION_LEVELS_DEFAULT))
        if levels is None:
            QMessageBox.critical(self, t["msg_titr_title"], t["err_titr_levels"].format(max=TITRATION_MAX_ML))
            return None
        repeats = int(self.settings.get("titration_repeats", TITRATION_REPEATS_DEFAULT))
        with_water = bool(self.settings.get("titration_water", True))
        plan = build_titration_plan(n_eggs, levels, repeats, with_water)
        water = t["titr_plan_water"].format(ml=fmt_amount(levels[0]), rep=repeats) if with_water else ""
        plan_text = t["titr_plan_line"].format(
            base_rep=TITRATION_BASELINE_REPEATS, water=water, levels=" → ".join(fmt_amount(v) for v in levels),
            rep=repeats, n=n_eggs, per_egg=len(plan) // n_eggs, total=len(plan),
        )
        return plan, plan_text

    def _mode_instructions(self, run_mode, n, plan_text=""):
        """(tiêu đề, hướng dẫn) cho bước đo mẫu đầu tiên của chế độ, hiện khi đã có Dark/Lamp."""
        t = TRANSLATIONS[self.current_lang]
        if run_mode == "full":
            return t["msg_phase3_title"], t["instr_full"].format(n=n)
        if run_mode == "shell_only":
            return t["msg_shell_title"], t["instr_shell"].format(n=n)
        if run_mode == "embryo":
            return t["msg_embryo_title"], t["instr_embryo"].format(n=n)
        if run_mode == "dye_titration":
            return t["msg_titr_title"], t["instr_titr"].format(plan=plan_text)
        return t["msg_opened_start_title"], t["instr_opened"].format(n=n)

    def _enter_measure_phase(self):
        """Đã có Dark + Lamp (đo mới hoặc dùng lại) -> chuyển sang bước đo mẫu đầu tiên của chế độ."""
        if self.run_mode == "full":
            self.current_phase = "single"
        elif self.run_mode == "dye_titration":
            self.current_phase = "titration"
            self.titr_idx = 0
        else:
            self.current_phase = "batch"
        self.single_step = 0
        self.egg_counter = 0
        self.batch_meas = 0
        self.m_egg_count.val_lbl.setText("0")
        self._update_trigger_btn_text()
        self.trigger_egg_btn.setEnabled(True)
        self.remeasure_ref_btn.setEnabled(True)
        self._show_phase_status()

    def _style_calendar(self, cal):
        """Màu hàng tên thứ + ngày cuối tuần của lịch (đặt bằng text format, stylesheet không chỉnh được)."""
        dark = self.current_theme == "dark"
        cal.setFirstDayOfWeek(Qt.Monday)
        cal.setGridVisible(False)
        cal.setVerticalHeaderFormat(cal.NoVerticalHeader)
        header = QTextCharFormat()
        header.setBackground(QColor("#1e293b" if dark else "#e2e8f0"))
        header.setForeground(QColor("#94a3b8" if dark else "#475569"))
        header.setFontWeight(QFont.Bold)
        cal.setHeaderTextFormat(header)
        weekend = QTextCharFormat()
        weekend.setForeground(QColor("#f87171" if dark else "#dc2626"))
        for day in (Qt.Saturday, Qt.Sunday):
            cal.setWeekdayTextFormat(day, weekend)
        # Ngày sau maximumDate (tương lai) không chọn được -> tô mờ cho dễ nhận biết
        future = QTextCharFormat()
        future.setForeground(QColor("#475569" if dark else "#cbd5e1"))
        day = cal.maximumDate().addDays(1)
        while day.month() == cal.maximumDate().month():
            cal.setDateTextFormat(day, future)
            day = day.addDays(1)

    def _ask_incubation_start(self):
        """Hỏi ngày giờ bắt đầu ấp lô trứng (chế độ "embryo"). Trả về datetime, hoặc None nếu bấm Hủy."""
        t = TRANSLATIONS[self.current_lang]
        dlg = QDialog(self)
        dlg.setWindowTitle(t["dlg_incub_title"])
        dlg.setWindowFlags(dlg.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dlg.setMinimumWidth(340)
        lay = QVBoxLayout(dlg)
        lay.setSpacing(10)
        lay.addWidget(QLabel(t["dlg_incub_prompt"]))
        edit = QDateTimeEdit(dlg)
        edit.setCalendarPopup(True)
        edit.setDisplayFormat("dd/MM/yyyy HH:mm")
        edit.setMaximumDateTime(QDateTime.currentDateTime())   # ngày tương lai bị mờ trong lịch
        edit.setDateTime(QDateTime(self.last_incubation_start) if self.last_incubation_start
                         else QDateTime.currentDateTime())
        self._style_calendar(edit.calendarWidget())
        lay.addWidget(edit)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=dlg)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        lay.addWidget(buttons)
        while dlg.exec_() == QDialog.Accepted:
            start = edit.dateTime().toPyDateTime().replace(second=0, microsecond=0)
            if start > datetime.now():
                QMessageBox.warning(self, t["dlg_incub_title"], t["err_incub_future"])
                continue
            self.last_incubation_start = start
            return start
        return None

    def _begin_experiment(self, run_mode, incubation_start=None):
        """Phần khởi tạo chung của 2 chế độ: kết nối worker + reset toàn bộ trạng thái thí nghiệm."""
        t = TRANSLATIONS[self.current_lang]
        # Đảm bảo luồng đọc Serial đang chạy với đúng cấu hình hiện tại (exposure chỉ gửi lúc kết nối)
        if self.worker is None or not self.worker.isRunning():
            self.start_worker()
        elif not self._worker_matches_settings():
            self.restart_worker()

        self.is_experiment_running = True
        self.run_mode = run_mode
        self.current_phase = None
        self.dark_spectrum = None
        self.dark_exposure_ms = None
        self.ref_spectrum = None
        self.ref_exposure_ms = None
        self.ref_dark_corr = None
        self.ref_color_ratio = None
        self.batch_size = int(self.settings.get("batch_size", BATCH_SIZE_DEFAULT))
        self.egg_n_frames = int(self.settings.get("egg_n_frames", EGG_N_FRAMES_DEFAULT))
        self.egg_placements = int(self.settings.get("egg_placements", EGG_PLACEMENTS_DEFAULT))
        self.batch_ends = BATCH_ENDS.get(run_mode, (None,))
        self.single_step = 0
        self.egg_counter = 0
        self.batch_meas = 0
        self.last_egg = None
        self.metadata_rows = []
        self.egg_labels = {}
        self.incubation_start = incubation_start
        # Mỗi lần thí nghiệm ghi vào 1 thư mục con riêng, tên = thời điểm bắt đầu (+ chế độ);
        # đo liên tục: thư mục batch_<k>_<timestamp> bên trong thư mục session
        now = datetime.now()
        if self.session is not None:
            self.run_dir = os.path.join(self.session["dir"], f"batch_{self.batch_no:02d}_{now:%Y%m%d_%H%M%S}")
            self.session["info"]["batches"].append({
                "batch": self.batch_no, "folder": os.path.basename(self.run_dir),
                "started": now.isoformat(timespec="seconds"), "finished": None,
            })
        else:
            self.run_dir = os.path.join(self.settings["save_dir"], f"{now:%Y%m%d_%H%M%S}_{run_mode}")
        os.makedirs(self.run_dir, exist_ok=True)
        if self.session is not None:
            self._save_session_info()
        self.curve_dark.clear()
        self.curve_ref.clear()
        self.curve_last_egg.clear()
        self.m_egg_count.val_lbl.setText("0")
        self.trigger_egg_btn.setEnabled(False)
        self.undo_egg_btn.setEnabled(False)
        self.remeasure_ref_btn.setEnabled(False)
        self._update_trigger_btn_text()

        self.exp_btn.setText(t["btn_stop_exp"])
        self.exp_btn.setObjectName("DangerBtn")
        self.exp_btn.setStyle(self.exp_btn.style())

    def _start_full_experiment(self, run_mode="full", incubation_start=None):
        """Đo Dark + Lamp mới rồi vào bước đo mẫu của chế độ (_on_reference_complete)."""
        t = TRANSLATIONS[self.current_lang]
        titr = None
        if run_mode == "dye_titration":
            titr = self._prepare_titration_plan()
            if titr is None:
                return False
        if run_mode == "embryo" and incubation_start is None:
            incubation_start = self._ask_incubation_start()
            if incubation_start is None:
                return False
        # Phase 1: nhắc người dùng tắt hết đèn trước khi bắt đầu
        reply = QMessageBox.question(
            self,
            t["msg_exp_start_title"],
            t["msg_exp_start_body"],
            QMessageBox.Ok | QMessageBox.Cancel,
            QMessageBox.Ok
        )
        if reply != QMessageBox.Ok:
            return False

        self._begin_experiment(run_mode, incubation_start)
        if titr is not None:
            self.titr_plan, self.titr_plan_text = titr
        self.current_phase = "dark"
        self.start_averaged_capture("dark", N_AVERAGE, self._on_dark_complete)
        return True

    def _start_reused_ref_experiment(self, run_mode, incubation_start=None):
        """
        Dùng lại Dark/Lamp (settings["reuse_ref"]): bỏ qua đo Dark/Lamp, nạp từ dataset cũ (được chép sang
        thư mục lưu mới) rồi vào thẳng bước đo mẫu của chế độ.
        """
        t = TRANSLATIONS[self.current_lang]
        n_eggs = int(self.settings.get("batch_size", BATCH_SIZE_DEFAULT))
        titr = None
        if run_mode == "dye_titration":
            titr = self._prepare_titration_plan()
            if titr is None:
                return False
        ref_dir = self.settings.get("ref_dir", DEFAULT_REF_DIR)
        try:
            refs = load_reference_dir(ref_dir)
        except FileNotFoundError as e:
            kind = e.args[0] if e.args else ""
            msg = t["err_ref_missing"].format(kind=kind) if kind in ("dark", "lamp") else str(e)
            QMessageBox.critical(self, t["msg_ref_load_err_title"], f"{ref_dir}\n\n{msg}")
            return False
        except (OSError, ValueError) as e:
            QMessageBox.critical(self, t["msg_ref_load_err_title"], f"{ref_dir}\n\n{e}")
            return False

        n_ref = len(refs["wavelengths"])
        if self.wavelengths is not None and len(self.wavelengths) != n_ref:
            QMessageBox.critical(self, t["msg_ref_load_err_title"],
                                 t["msg_ref_wl_mismatch"].format(n_dev=len(self.wavelengths), n_ref=n_ref))
            return False

        def fmt_ms(v):
            return "-- ms" if np.isnan(v) else f"{v:.0f} ms"

        lamp_ms = refs["lamp"]["exposure_ms"]
        current = f"Manual {self.settings['exposure_ms']:.0f} ms"
        exposure_ok = np.isnan(lamp_ms) or exposure_matches(self.settings["exposure_ms"], lamp_ms)
        ref_info = dict(
            dir=ref_dir, dark=refs["dark"]["name"], dark_ms=fmt_ms(refs["dark"]["exposure_ms"]),
            lamp=refs["lamp"]["name"], lamp_ms=fmt_ms(lamp_ms), cur=current,
            warn="" if exposure_ok else t["warn_ref_exposure"].format(ms=lamp_ms),
        )
        title, instr = self._mode_instructions(run_mode, n_eggs, titr[1] if titr else "")
        body = t["msg_reuse_ref_body"].format(**ref_info) + "\n" + instr
        reply = QMessageBox.question(self, title, body, QMessageBox.Ok | QMessageBox.Cancel, QMessageBox.Ok)
        if reply != QMessageBox.Ok:
            return False
        if run_mode == "embryo" and incubation_start is None:
            incubation_start = self._ask_incubation_start()
            if incubation_start is None:
                return False

        self._begin_experiment(run_mode, incubation_start)
        if titr is not None:
            self.titr_plan, self.titr_plan_text = titr
        if self.wavelengths is None:
            self.wavelengths = refs["wavelengths"]   # thiết bị chưa gửi frame nào -> tạm dùng lưới của file
        self.dark_spectrum = refs["dark"]["spectrum"]
        self.dark_exposure_ms = refs["dark"]["exposure_ms"]
        self.curve_dark.setData(self.wavelengths, self.dark_spectrum)
        self._update_reference_cache(refs["lamp"]["spectrum"], lamp_ms)

        # Chép Dark/Lamp gốc sang thư mục lưu để dataset mới tự đủ (notebook đọc dark/lamp cùng folder),
        # metadata ghi lại đường dẫn nguồn ở cột ref_source
        for kind, measurement_type in (("dark", "dark"), ("lamp", "reference")):
            r = refs[kind]
            dst = os.path.join(self.run_dir, r["name"])
            if os.path.abspath(dst) != os.path.abspath(r["path"]):
                shutil.copy2(r["path"], dst)
            self._append_metadata_row(
                measurement_type, r["name"], r["exposure_ms"], r["spectrum"], [],
                color_ratio_val=self.ref_color_ratio if kind == "lamp" else None,
                color_drift_pct=0.0 if kind == "lamp" else None,
                n_averaged=r["n_averaged"], ref_source=r["path"],
            )

        self._enter_measure_phase()
        return True

    def stop_experiment(self):
        t = TRANSLATIONS[self.current_lang]
        if self.session is not None:
            # Dừng khi batch chưa đo đủ trứng -> ghi batch dở dang (đủ trứng thì đã ghi lúc đo xong)
            batches = self.session["info"]["batches"]
            if batches and batches[-1]["finished"] is None:
                self._finish_batch(stopped=True)
            self.session = None
        self.capture_state = None
        self.is_experiment_running = False
        self.current_phase = None
        self.trigger_egg_btn.setEnabled(False)
        self.undo_egg_btn.setEnabled(False)
        self.remeasure_ref_btn.setEnabled(False)
        self._update_trigger_btn_text()

        self.exp_btn.setText(t["btn_start_exp"])
        self.exp_btn.setObjectName("ExpBtn")
        self.exp_btn.setStyle(self.exp_btn.style())

        self.m_status.val_lbl.setText(t["status_online"])
        self.m_status.val_lbl.setStyleSheet("color: #10b981;")

        if self.run_mode == "full":
            s3_line = f"S3: {self.single_step // len(SINGLE_EGG_STEPS)}/{self.batch_size}\n"
        elif self.run_mode == "dye_titration":
            s3_line = f"{t['status_titration']}: {self.titr_idx}/{len(self.titr_plan)}\n"
        else:
            s3_line = ""
        QMessageBox.information(
            self,
            t["msg_exp_finished_title"],
            f"{t['msg_exp_finished_body']}{self.egg_counter}\n"
            f"{s3_line}"
            f"Thư mục: {self.run_dir}"
        )

    def _single_position(self):
        """Vị trí phép đo Phase 3 kế tiếp: (số thứ tự trứng 1..N, index bước 0..3)."""
        n_steps = len(SINGLE_EGG_STEPS)
        return self.single_step // n_steps + 1, self.single_step % n_steps

    def _meas_per_egg(self):
        """Số phép đo batch của 1 trứng = số đầu trứng x số lần nhấc-đặt lại."""
        return len(self.batch_ends) * self.egg_placements

    def _batch_slot(self):
        """Phép đo batch kế tiếp: (số thứ tự trứng 1..N, đầu trứng hoặc None, lần đặt 1..egg_placements).
        Thứ tự trong 1 trứng: hết các lần đặt của đầu thứ nhất rồi mới sang đầu kế tiếp."""
        per_egg = self._meas_per_egg()
        meas = min(self.batch_meas, self.batch_size * per_egg - 1)
        within = meas % per_egg
        return (meas // per_egg + 1, self.batch_ends[within // self.egg_placements],
                within % self.egg_placements + 1)

    def _batch_position(self):
        """Phép đo batch kế tiếp: (số thứ tự trứng 1..N, lần đặt 1..egg_placements)."""
        egg_no, _, placement = self._batch_slot()
        return egg_no, placement

    def _end_suffix(self):
        """' · Đầu tù' / ' · Đầu nhọn' cho phép đo batch kế tiếp, '' nếu chế độ không phân biệt đầu."""
        end = self._batch_slot()[1]
        return f" · {TRANSLATIONS[self.current_lang]['end_' + end]}" if end else ""

    def _placement_suffix(self, placement):
        """' · lần đặt p/P' khi mỗi trứng được nhấc-đặt lại nhiều lần, '' nếu chỉ đặt 1 lần."""
        if self.egg_placements <= 1:
            return ""
        return TRANSLATIONS[self.current_lang]["placement_suffix"].format(p=placement, P=self.egg_placements)

    def _update_trigger_btn_text(self):
        """Nút lưu mẫu: Phase 3 hiển thị trứng + bước đo kế tiếp, Phase 4 hiển thị trứng mở vỏ kế tiếp."""
        t = TRANSLATIONS[self.current_lang]
        if self.current_phase == "single" and self.single_step < self.batch_size * len(SINGLE_EGG_STEPS):
            egg_no, step_idx = self._single_position()
            self.trigger_egg_btn.setText(t["btn_trigger_single"].format(
                egg=egg_no, n=self.batch_size, step=t[SINGLE_EGG_STEPS[step_idx][1]]
            ))
        elif self.current_phase == "batch":
            key = {"opened_only": "btn_trigger_opened", "shell_only": "btn_trigger_shell",
                   "embryo": "btn_trigger_embryo"}.get(
                self.run_mode, "btn_trigger_batch")
            egg_no, placement = self._batch_position()
            self.trigger_egg_btn.setText(
                t[key].format(i=egg_no, n=self.batch_size) + self._end_suffix() + self._placement_suffix(placement)
            )
        elif self.current_phase == "titration":
            m = self.titr_plan[min(self.titr_idx, len(self.titr_plan) - 1)]
            self.trigger_egg_btn.setText(t["btn_trigger_titr"].format(
                egg=m["egg"], n=self.batch_size, step=self._titr_step_label(m), rep=m["rep"], n_rep=m["n_rep"]
            ))
        else:
            self.trigger_egg_btn.setText(t["btn_trigger_egg"])

    def _titr_step_label(self, m):
        """Nhãn 1 bậc chuẩn độ: 'Nền (chưa thêm)' / 'Nước 5 ml' / 'Màu 20 ml' (lượng màu tích lũy)."""
        t = TRANSLATIONS[self.current_lang]
        if m["kind"] == "base":
            return t["titr_step_base"]
        if m["kind"] == "water":
            return t["titr_step_water"].format(ml=fmt_amount(m["delta"]))
        return t["titr_step_dye"].format(ml=fmt_amount(m["amount"]))

    def _titr_eggs_done(self):
        """Số trứng đã đo xong toàn bộ các bậc chuẩn độ (theo titr_idx)."""
        if self.titr_idx >= len(self.titr_plan):
            return self.titr_plan[-1]["egg"] if self.titr_plan else 0
        return self.titr_plan[self.titr_idx]["egg"] - 1

    def _show_phase_status(self):
        """Hiển thị trạng thái chờ trigger của Phase 3/4 lên KPI."""
        t = TRANSLATIONS[self.current_lang]
        if self.current_phase == "single":
            egg_no, step_idx = self._single_position()
            text = f"{t['status_phase3']} (#{egg_no}/{self.batch_size} · {step_idx}/{len(SINGLE_EGG_STEPS)})"
        elif self.current_phase == "titration":
            m = self.titr_plan[min(self.titr_idx, len(self.titr_plan) - 1)]
            text = (f"{t['status_titration']} #{m['egg']}/{self.batch_size} · {self._titr_step_label(m)} "
                    f"({self.titr_idx}/{len(self.titr_plan)})")
        else:
            key = {"opened_only": "status_phase_opened", "shell_only": "status_phase_shell",
                   "embryo": "status_phase_embryo"}.get(
                self.run_mode, "status_phase4")
            text = f"{t[key]} ({self.egg_counter}/{self.batch_size})"
            if self.batch_meas < self.batch_size * self._meas_per_egg():
                text += self._end_suffix() + self._placement_suffix(self._batch_position()[1])
        self.m_status.val_lbl.setText(text)
        self.m_status.val_lbl.setStyleSheet("color: #38bdf8;")

    # --------------------------------------------------------------------
    # State machine chụp trung bình N khung thô (Dev Brief mục 7.1/7.3):
    # dùng chung cho Dark, Reference và mỗi mẫu trứng, không block UI vì
    # dựa trên chính luồng live đang chạy (handle_spectrum cấp frame).
    # --------------------------------------------------------------------
    def start_averaged_capture(self, stage, n, on_complete, label=None):
        t = TRANSLATIONS[self.current_lang]
        if label is None:
            label = {
                "dark": t["status_phase1"],
                "lamp": t["status_phase2"],
                "egg": t["status_egg_capturing"],
            }.get(stage, stage)
        self.capture_state = {
            "stage": stage, "frames": [], "exposures": [], "statuses": [],
            "n_target": n, "on_complete": on_complete, "label": label,
        }
        self.m_status.val_lbl.setText(f"{label} (0/{n})")
        self.m_status.val_lbl.setStyleSheet("color: #a855f7;")

    def _on_dark_complete(self, avg_spectrum, avg_exposure, statuses, frames=None):
        self.dark_spectrum = avg_spectrum
        self.dark_exposure_ms = avg_exposure
        if self.wavelengths is not None:
            self.curve_dark.setData(self.wavelengths, self.dark_spectrum)
        filename, _ = self._save_stage_csv("dark", avg_spectrum)
        self._append_metadata_row("dark", filename, avg_exposure, avg_spectrum, statuses)

        t = TRANSLATIONS[self.current_lang]
        QMessageBox.information(self, t["msg_phase2_title"], t["msg_phase2_body"])
        self.current_phase = "lamp"
        self.start_averaged_capture("lamp", N_AVERAGE, self._on_reference_complete)

    def _update_reference_cache(self, avg_spectrum, avg_exposure):
        self.ref_spectrum = avg_spectrum
        self.ref_exposure_ms = avg_exposure
        self.ref_dark_corr = (
            avg_spectrum - self.dark_spectrum if self.dark_spectrum is not None else avg_spectrum.copy()
        )
        if self.wavelengths is not None:
            self.ref_color_ratio = color_ratio(self.ref_dark_corr, self.wavelengths)
            self.curve_ref.setData(self.wavelengths, self.ref_spectrum)

    def _on_reference_complete(self, avg_spectrum, avg_exposure, statuses, frames=None):
        self._update_reference_cache(avg_spectrum, avg_exposure)
        filename, _ = self._save_stage_csv("lamp", avg_spectrum)
        self._append_metadata_row(
            "reference", filename, avg_exposure, avg_spectrum, statuses,
            color_ratio_val=self.ref_color_ratio, color_drift_pct=0.0,
        )

        t = TRANSLATIONS[self.current_lang]
        self._enter_measure_phase()
        title, instr = self._mode_instructions(self.run_mode, self.batch_size, self.titr_plan_text)
        QMessageBox.information(self, title, t["msg_refs_done"] + "\n" + instr)

    def remeasure_reference(self):
        """Đo lại Reference giữa Phase 3/4 (Dev Brief mục 8.4: nguồn sáng trôi theo thời gian)."""
        if not (self.is_experiment_running and self.current_phase in ("single", "batch", "titration")):
            return
        if self.capture_state is not None:
            return
        t = TRANSLATIONS[self.current_lang]
        self.trigger_egg_btn.setEnabled(False)
        self.remeasure_ref_btn.setEnabled(False)
        self.start_averaged_capture(
            "lamp", N_AVERAGE, self._on_reference_remeasure_complete, label=t["status_remeasuring_ref"]
        )

    def _on_reference_remeasure_complete(self, avg_spectrum, avg_exposure, statuses, frames=None):
        self._update_reference_cache(avg_spectrum, avg_exposure)
        filename, _ = self._save_stage_csv("lamp", avg_spectrum)
        self._append_metadata_row(
            "reference_remeasure", filename, avg_exposure, avg_spectrum, statuses,
            color_ratio_val=self.ref_color_ratio, color_drift_pct=0.0,
        )

        self.trigger_egg_btn.setEnabled(self._has_pending_capture())
        self.remeasure_ref_btn.setEnabled(True)
        self._show_phase_status()

    def _has_pending_capture(self):
        """Còn phép đo trứng nào chưa làm trong phase hiện tại không."""
        if self.current_phase == "batch":
            return self.batch_meas < self.batch_size * self._meas_per_egg()
        if self.current_phase == "titration":
            return self.titr_idx < len(self.titr_plan)
        return self.current_phase == "single"

    def trigger_egg_capture(self):
        """Phase 3/4/chuẩn độ: được gọi khi bấm nút hoặc nhấn phím [ENTER] để đo & lưu 1 mẫu trứng."""
        if not (self.is_experiment_running and self.current_phase in ("single", "batch", "titration")):
            return
        if self.capture_state is not None or self.wavelengths is None:
            return
        if not self._has_pending_capture():
            return

        t = TRANSLATIONS[self.current_lang]
        # Dark/Lamp nạp từ dataset cũ phải cùng số pixel với thiết bị đang đo
        if self.dark_spectrum is not None and len(self.dark_spectrum) != len(self.wavelengths):
            QMessageBox.critical(self, t["msg_ref_load_err_title"], t["msg_ref_wl_mismatch"].format(
                n_dev=len(self.wavelengths), n_ref=len(self.dark_spectrum)))
            return

        # Trứng có phôi: nhập ID trước phép đo đầu tiên của mỗi trứng (đầu tù, lần đặt 1);
        # đầu nhọn và các lần đặt lại dùng chung ID
        if self.current_phase == "batch" and self.run_mode == "embryo":
            egg_no = self._batch_position()[0]
            if self.batch_meas % self._meas_per_egg() == 0 and self._ask_egg_label(egg_no) is None:
                return

        self.trigger_egg_btn.setEnabled(False)
        self.remeasure_ref_btn.setEnabled(False)
        if self.current_phase == "single":
            on_complete = self._on_single_egg_complete
            egg_no, step_idx = self._single_position()
            label = f"{t['status_egg_capturing']} S3 #{egg_no}/{self.batch_size}: {t[SINGLE_EGG_STEPS[step_idx][1]]}"
        elif self.current_phase == "titration":
            on_complete = self._on_titration_complete
            m = self.titr_plan[self.titr_idx]
            label = (f"{t['status_egg_capturing']} #{m['egg']}/{self.batch_size}: "
                     f"{self._titr_step_label(m)} ({m['rep']}/{m['n_rep']})")
        else:
            on_complete = self._on_batch_egg_complete
            prefix = "S4 " if self.run_mode == "full" else ""
            egg_no, placement = self._batch_position()
            label = (f"{t['status_egg_capturing']} {prefix}#{egg_no}/{self.batch_size}"
                     f"{self._end_suffix()}{self._placement_suffix(placement)}")
        self.start_averaged_capture("egg", self.egg_n_frames, on_complete, label=label)

    def _ask_egg_label(self, egg_no):
        """Hỏi ID của trứng #egg_no (gợi ý ID cũ nếu đo lại sau Hủy, hoặc ID cùng số thứ tự ở batch trước).
        Trả về ID, hoặc None nếu bấm Hủy."""
        t = TRANSLATIONS[self.current_lang]
        label = self.egg_labels.get(egg_no) or self.label_hints.get(egg_no, "")
        while True:
            label, ok = QInputDialog.getText(
                self, t["dlg_egg_id_title"], t["dlg_egg_id_prompt"].format(egg=egg_no, n=self.batch_size),
                QLineEdit.Normal, label,
            )
            if not ok:
                return None
            label = label.strip()
            dup = next((no for no, lb in self.egg_labels.items() if lb == label and no != egg_no), None)
            if not label:
                QMessageBox.warning(self, t["dlg_egg_id_title"], t["err_egg_id_empty"])
            elif dup is not None:
                QMessageBox.warning(self, t["dlg_egg_id_title"], t["err_egg_id_dup"].format(id=label, egg=dup))
            else:
                self.egg_labels[egg_no] = label
                return label

    def _confirm_basler_photo(self, egg_no):
        """Đo phổ xong 1 trứng có phôi -> chờ xác nhận đã chụp ảnh Basler (chụp trên laptop khác),
        ghi thời điểm xác nhận vào cột photo_confirmed_at của các dòng metadata của trứng đó."""
        t = TRANSLATIONS[self.current_lang]
        label = self.egg_labels.get(egg_no, "")
        QMessageBox.information(self, t["msg_photo_title"],
                                t["msg_photo_body"].format(id=label, egg=egg_no, n=self.batch_size))
        confirmed_at = datetime.now().isoformat()
        for row in self.metadata_rows:
            if row.get("measurement_type") == "embryo" and row.get("egg_id") == egg_no:
                row["photo_confirmed_at"] = confirmed_at
        self._write_metadata_log()

    def _save_egg_sample(self, avg_spectrum, avg_exposure, statuses, stage_tag, measurement_type, index=None,
                         extra=None, frames=None):
        """
        Lưu 1 mẫu trứng (Phase 3/4/chuẩn độ): tính absorbance, ghi CSV + metadata, vẽ lên đồ thị.
        extra: các cột metadata bổ sung (vd. thông tin bậc chuẩn độ).
        frames: các khung thô (n_frames x n_pixel) đã trung bình thành avg_spectrum - được lưu kèm từng cột
        Frame_XX trong CSV, dùng để kiểm tra bão hòa từng khung và tính RSD giữa các khung.
        Trả về (filename, danh sách cảnh báo).
        """
        t = TRANSLATIONS[self.current_lang]

        sample_dark_corr = (
            avg_spectrum - self.dark_spectrum if self.dark_spectrum is not None else avg_spectrum.copy()
        )
        abs_data, valid_mask = safe_absorbance(
            self.ref_dark_corr, sample_dark_corr, self.ref_exposure_ms, avg_exposure
        )

        if frames is not None and len(frames) < 2:
            frames = None   # 1 khung = chính avg_spectrum, không cần lưu thêm cột
        filename, file_path = self._save_stage_csv(stage_tag, avg_spectrum, absorbance=abs_data, index=index,
                                                   frames=frames)
        if self.wavelengths is not None:
            self.curve_last_egg.setData(self.wavelengths, avg_spectrum)

        # Bão hòa xét trên từng khung: trung bình có thể dưới ngưỡng dù 1 khung đã bão hòa
        sat_spectrum = np.max(frames, axis=0) if frames is not None else avg_spectrum
        saturated, _ = check_saturation(sat_spectrum)
        rsd_pct = frame_rsd_pct(frames, self.dark_spectrum, self.wavelengths) if frames is not None else float("nan")
        extra = {"n_frames_saved": len(frames) if frames is not None else 0,
                 "frame_rsd_pct": "" if np.isnan(rsd_pct) else f"{rsd_pct:.3f}", **(extra or {})}
        c_ratio = color_ratio(sample_dark_corr, self.wavelengths)
        if self.ref_color_ratio and not np.isnan(self.ref_color_ratio) and self.ref_color_ratio != 0:
            drift_pct = abs(c_ratio - self.ref_color_ratio) / abs(self.ref_color_ratio) * 100.0
        else:
            drift_pct = float("nan")

        in_band = (self.wavelengths >= 600) & (self.wavelengths <= 1000)
        invalid_pct = 100.0 * (1.0 - valid_mask[in_band].mean()) if in_band.any() else float("nan")

        if self.dark_exposure_ms and not np.isnan(self.dark_exposure_ms) and self.dark_exposure_ms > 0 and not np.isnan(avg_exposure):
            dark_mismatch_pct = abs(self.dark_exposure_ms - avg_exposure) / self.dark_exposure_ms * 100.0
        else:
            dark_mismatch_pct = float("nan")

        self._append_metadata_row(
            measurement_type, filename, avg_exposure, avg_spectrum, statuses,
            color_ratio_val=c_ratio, color_drift_pct=drift_pct,
            invalid_pct=invalid_pct, dark_mismatch_pct=dark_mismatch_pct, extra=extra,
            sat_spectrum=sat_spectrum,
        )

        self.last_egg = {
            "csv_path": file_path,
            "sample_id": os.path.splitext(filename)[0],
            "phase": self.current_phase,
        }
        self.undo_egg_btn.setEnabled(True)

        warnings = []
        if saturated:
            warnings.append(t["warn_saturated"])
        if not np.isnan(drift_pct) and drift_pct > 1.0:
            warnings.append(t["warn_color_drift"].format(pct=drift_pct))
        if not np.isnan(dark_mismatch_pct) and dark_mismatch_pct > 20.0:
            warnings.append(t["warn_exposure_mismatch"].format(pct=dark_mismatch_pct))
        target_ms = self.settings["exposure_ms"]
        if not np.isnan(avg_exposure) and not exposure_matches(avg_exposure, target_ms):
            warnings.append(t["warn_exposure_target"].format(ms=avg_exposure, target=target_ms))
        if not np.isnan(rsd_pct) and rsd_pct > FRAME_RSD_WARN_PCT:
            warnings.append(t["warn_frame_rsd"].format(pct=rsd_pct))

        print(
            f"🎉 [LƯU] {filename} | Peak: {avg_spectrum.max():.0f} | Sat: {saturated} | "
            f"ColorDrift: {drift_pct:.2f}% | Invalid(600-1000nm): {invalid_pct:.1f}% | FrameRSD: {rsd_pct:.2f}%"
        )
        return filename, warnings

    def _show_saved_status(self, text, warnings):
        if warnings:
            text += " | " + " | ".join(warnings)
            self.m_status.val_lbl.setStyleSheet("color: #f59e0b;")
        else:
            self.m_status.val_lbl.setStyleSheet("color: #4ade80;")
        self.m_status.val_lbl.setText(text)

    def _on_single_egg_complete(self, avg_spectrum, avg_exposure, statuses, frames=None):
        """Phase 3: lưu 1 trong 4 phép đo của trứng chưa mở vỏ hiện tại (file đánh số theo trứng)."""
        t = TRANSLATIONS[self.current_lang]
        n_steps = len(SINGLE_EGG_STEPS)
        egg_no, step_idx = self._single_position()
        stage_tag, step_key = SINGLE_EGG_STEPS[step_idx]
        _, warnings = self._save_egg_sample(
            avg_spectrum, avg_exposure, statuses, stage_tag, stage_tag, index=egg_no, frames=frames
        )
        self.single_step += 1
        self._show_saved_status(
            f"{t['status_single_saved']} #{egg_no}/{self.batch_size} · {step_idx + 1}/{n_steps}: {t[step_key]}",
            warnings,
        )

        steps_done_this_egg = self.single_step % n_steps
        if self.single_step >= self.batch_size * n_steps:
            QMessageBox.information(self, t["msg_phase4_title"], t["msg_phase4_body"].format(n=self.batch_size))
            self.current_phase = "batch"
        elif steps_done_this_egg == SINGLE_EGG_DYE_STEP:
            QMessageBox.information(self, t["msg_dye_title"], t["msg_dye_body"].format(egg=egg_no))
        elif steps_done_this_egg == 0:
            QMessageBox.information(
                self, t["msg_next_egg_title"], t["msg_next_egg_body"].format(done=egg_no, next=egg_no + 1)
            )

        self._update_trigger_btn_text()
        self.trigger_egg_btn.setEnabled(True)
        self.remeasure_ref_btn.setEnabled(True)

    def _on_batch_egg_complete(self, avg_spectrum, avg_exposure, statuses, frames=None):
        """
        Phase 4 / đo batch: lưu 1 lần đặt của trứng (vỏ ở "shell_only", trứng có phôi ở "embryo") hiện tại.
        Khi egg_placements > 1, mỗi mẫu được nhấc ra - đặt lại và đo P lần:
        file egg|shell|embryo_<ID>_p<k>_<ts>.csv, metadata có egg_id + placement.
        """
        t = TRANSLATIONS[self.current_lang]
        end_text = self._end_suffix()
        egg_no, end, placement = self._batch_slot()
        kind = BATCH_KIND.get(self.run_mode, "egg")   # measurement_type
        prefix = f"{kind}_{end}" if end else kind     # tiền tố file: embryo_blunt / embryo_pointed
        stage_tag, index = (prefix, egg_no) if self.egg_placements == 1 else (f"{prefix}_{egg_no:04d}_p{placement}", None)
        extra = {"egg_id": egg_no, "placement": placement}
        if end:
            extra["egg_end"] = end
        id_text = ""
        if self.run_mode == "embryo":
            # Tên file giữ số thứ tự (spectral_ml.FNAME_RE), ID người dùng nhập nằm ở cột egg_label
            extra["egg_label"] = self.egg_labels.get(egg_no, "")
            id_text = f" (ID {extra['egg_label']})"
        _, warnings = self._save_egg_sample(
            avg_spectrum, avg_exposure, statuses, stage_tag, kind, index=index, extra=extra, frames=frames,
        )
        self.batch_meas += 1
        egg_done = self.batch_meas % self._meas_per_egg() == 0   # đã đo hết mọi đầu + lần đặt của trứng này
        self.egg_counter = self.batch_meas // self._meas_per_egg()
        self.m_egg_count.val_lbl.setText(str(self.egg_counter))
        self._show_saved_status(
            f"{t['status_egg_detected']} #{egg_no}/{self.batch_size}{id_text}{end_text}"
            f"{self._placement_suffix(placement)}",
            warnings,
        )

        self._update_trigger_btn_text()
        self.remeasure_ref_btn.setEnabled(True)
        if self.run_mode == "embryo" and egg_done:
            self._confirm_basler_photo(egg_no)
        if placement < self.egg_placements:
            # Còn lần đặt lại của cùng trứng -> nhắc nhấc ra/đặt lại (không chặn bằng hộp thoại)
            self.m_status.val_lbl.setText(
                self.m_status.val_lbl.text() + " | "
                + t["status_replace_egg"].format(egg=egg_no, p=placement + 1, P=self.egg_placements)
            )
            self.trigger_egg_btn.setEnabled(True)
        elif not egg_done:
            # Xong đầu tù -> nhắc xoay trứng sang đầu nhọn
            QMessageBox.information(self, t["msg_embryo_title"], t["msg_turn_pointed"].format(
                egg=egg_no, id=self.egg_labels.get(egg_no, "")))
            self.trigger_egg_btn.setEnabled(True)
        elif self.egg_counter >= self.batch_size:
            self.trigger_egg_btn.setEnabled(False)
            if self.session is not None:
                self._finish_batch()
            QMessageBox.information(
                self, t["msg_batch_done_title"],
                t[{"shell_only": "msg_shell_done", "embryo": "msg_embryo_done"}.get(
                    self.run_mode, "msg_batch_done_body")].format(n=self.batch_size)
            )
        else:
            self.trigger_egg_btn.setEnabled(True)

    def _on_titration_complete(self, avg_spectrum, avg_exposure, statuses, frames=None):
        """Chuẩn độ màu: lưu 1 phép đo, rồi nhắc thao tác (thêm nước / thêm màu / đổi trứng) trước bậc kế tiếp."""
        t = TRANSLATIONS[self.current_lang]
        m = self.titr_plan[self.titr_idx]
        label = {"base": "base", "water": "water"}.get(m["kind"], f"dye{fmt_amount(m['amount']).replace('.', 'p')}ml")
        stage_tag = f"titration_egg{m['egg']:02d}_s{m['step']:02d}_{label}"
        _, warnings = self._save_egg_sample(
            avg_spectrum, avg_exposure, statuses, stage_tag, f"titration_{m['kind']}", index=m["rep"],
            extra={
                "egg_id": m["egg"], "titration_step": m["step"], "titration_kind": m["kind"],
                "dye_ml_cumulative": fmt_amount(m["amount"]),
                "water_ml_added": fmt_amount(m["delta"]) if m["kind"] == "water" else "",
                "repeat": m["rep"],
            },
            frames=frames,
        )
        self.titr_idx += 1
        self.egg_counter = self._titr_eggs_done()
        self.m_egg_count.val_lbl.setText(str(self.egg_counter))
        self._show_saved_status(
            f"{t['status_titration']} #{m['egg']}/{self.batch_size} · {self._titr_step_label(m)} "
            f"({m['rep']}/{m['n_rep']})", warnings,
        )
        self._update_trigger_btn_text()
        self.remeasure_ref_btn.setEnabled(True)

        if self.titr_idx >= len(self.titr_plan):
            self.trigger_egg_btn.setEnabled(False)
            QMessageBox.information(self, t["msg_titr_title"], t["msg_titr_done"].format(
                n=self.batch_size, total=len(self.titr_plan)))
            return
        nxt = self.titr_plan[self.titr_idx]
        if nxt["rep"] == 1:   # sang bậc mới -> nhắc thao tác trước khi đo
            if nxt["egg"] != m["egg"]:
                msg = t["msg_titr_next_egg"].format(done=m["egg"], next=nxt["egg"])
            elif nxt["kind"] == "water":
                msg = t["msg_titr_water"].format(egg=nxt["egg"], ml=fmt_amount(nxt["delta"]))
            else:
                msg = t["msg_titr_dye"].format(egg=nxt["egg"], ml=fmt_amount(nxt["delta"]),
                                               total=fmt_amount(nxt["amount"]))
            QMessageBox.information(self, t["msg_titr_title"], msg)
        self.trigger_egg_btn.setEnabled(True)

    def undo_last_egg(self):
        """Xóa mẫu trứng vừa lưu (lỡ tay bấm Enter / cần đo lại) và lùi lại bước/bộ đếm."""
        if self.last_egg is None or self.capture_state is not None:
            return
        try:
            if os.path.exists(self.last_egg["csv_path"]):
                os.remove(self.last_egg["csv_path"])
        except OSError:
            pass

        if self.metadata_rows and self.metadata_rows[-1]["sample_id"] == self.last_egg["sample_id"]:
            self.metadata_rows.pop()
            self._write_metadata_log()

        if self.last_egg["phase"] == "single":
            # Hủy phép đo S3 (kể cả phép đo cuối vừa chuyển sang Phase 4) -> quay lại bước đó
            self.current_phase = "single"
            self.single_step = max(0, self.single_step - 1)
        elif self.last_egg["phase"] == "titration":
            # Lùi về đúng phép đo vừa hủy (cùng trứng, cùng bậc, cùng lần đo) - không nhắc lại thao tác
            self.titr_idx = max(0, self.titr_idx - 1)
            self.egg_counter = self._titr_eggs_done()
            self.m_egg_count.val_lbl.setText(str(self.egg_counter))
        else:
            # Lùi về đúng lần đặt vừa hủy của cùng trứng
            if self.session is not None and self.session["info"]["batches"]:
                self.session["info"]["batches"][-1]["finished"] = None   # batch chưa đủ trứng nữa
                self._save_session_info()
            self.batch_meas = max(0, self.batch_meas - 1)
            self.egg_counter = self.batch_meas // self._meas_per_egg()
            self.m_egg_count.val_lbl.setText(str(self.egg_counter))
        self.last_egg = None
        self.undo_egg_btn.setEnabled(False)

        self._update_trigger_btn_text()
        self.trigger_egg_btn.setEnabled(True)
        self._show_phase_status()

    # --------------------------------------------------------------------
    # Lưu CSV + metadata log (Dev Brief mục 10)
    # --------------------------------------------------------------------
    def _save_stage_csv(self, stage_tag, spectrum, absorbance=None, index=None, frames=None):
        """
        Ghi 1 phổ ra CSV. 3 cột đầu giữ nguyên định dạng cũ (Wavelength_nm, Raw_ADC_Counts = trung bình,
        Absorbance_OD); frames (n_frames x n_pixel) được ghi thêm thành các cột Frame_01..Frame_NN (ADC thô).
        """
        os.makedirs(self.run_dir, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{stage_tag}_{index:04d}_{timestamp}.csv" if index is not None else f"{stage_tag}_{timestamp}.csv"
        file_path = os.path.join(self.run_dir, filename)

        if absorbance is not None:
            columns = [self.wavelengths, spectrum, absorbance]
            header = "Wavelength_nm,Raw_ADC_Counts,Absorbance_OD"
            fmt = "%.2f,%.1f,%.6f"
        else:
            columns = [self.wavelengths, spectrum]
            header = "Wavelength_nm,Raw_ADC_Counts"
            fmt = "%.2f,%.1f"
        if frames is not None:
            columns.extend(frames)
            header += "".join(f",Frame_{k:02d}" for k in range(1, len(frames) + 1))
            fmt += ",%.1f" * len(frames)
        data = np.column_stack(columns)

        np.savetxt(file_path, data, delimiter=",", header=header, comments="", fmt=fmt)
        return filename, file_path

    def _append_metadata_row(self, measurement_type, filename, exposure_ms, spectrum, statuses,
                              color_ratio_val=None, color_drift_pct=None, invalid_pct=None,
                              dark_mismatch_pct=None, n_averaged=None, ref_source="", extra=None,
                              sat_spectrum=None):
        # n_averaged/ref_source: dùng khi ghi Dark/Lamp nạp lại từ dataset cũ (không có frame nào đo trong phiên này)
        # extra: cột bổ sung (vd. bậc chuẩn độ) - các dòng không có cột này để trống
        # sat_spectrum: phổ dùng kiểm tra bão hòa (max từng pixel qua các khung), mặc định = spectrum
        saturated, n_sat = check_saturation(spectrum if sat_spectrum is None else sat_spectrum)
        any_over = any(s == ExposureStatus.OVER for s in statuses if s is not None)

        def fmt(v, spec=".3f"):
            if v is None:
                return ""
            try:
                if np.isnan(v):
                    return ""
            except TypeError:
                pass
            return format(v, spec)

        now = datetime.now()
        row = {
            "sample_id": os.path.splitext(filename)[0],
            "measurement_type": measurement_type,
            "timestamp": now.isoformat(),
            "exposure_ms": fmt(exposure_ms),
            "n_averaged": len(statuses) if n_averaged is None else n_averaged,
            "peak_adc": fmt(float(np.max(spectrum)), ".1f"),
            "saturated": int(saturated),
            "saturated_pixels": n_sat,
            "exposure_status_over": int(any_over),
            "color_ratio": fmt(color_ratio_val, ".6f"),
            "color_ratio_drift_pct": fmt(color_drift_pct, ".3f"),
            "invalid_pct_600_1000nm": fmt(invalid_pct, ".2f"),
            "dark_exposure_mismatch_pct": fmt(dark_mismatch_pct, ".2f"),
            "wavelength_start_nm": fmt(float(self.wavelengths[0]), ".1f") if self.wavelengths is not None else "",
            "wavelength_end_nm": fmt(float(self.wavelengths[-1]), ".1f") if self.wavelengths is not None else "",
            "device_id": self.device_id,
            "software_mode": "raw_adc_counts",
            "ref_source": ref_source,
        }
        if self.session is not None:
            row["session"] = os.path.basename(self.session["dir"])
            row["batch_no"] = self.batch_no
        if self.incubation_start is not None:
            # Tuổi ấp (giờ) tại thời điểm đo = timestamp - thời điểm bắt đầu ấp
            row["incubation_start"] = self.incubation_start.isoformat(timespec="minutes")
            row["incubation_age_h"] = f"{(now - self.incubation_start).total_seconds() / 3600.0:.2f}"
        row.update(extra or {})
        self.metadata_rows.append(row)
        self._write_metadata_log()

    def _write_metadata_log(self):
        if not self.metadata_rows:
            return
        os.makedirs(self.run_dir, exist_ok=True)
        path = os.path.join(self.run_dir, "metadata_log.csv")
        fieldnames = list(dict.fromkeys(k for row in self.metadata_rows for k in row))   # hợp các cột, giữ thứ tự
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, restval="")
            writer.writeheader()
            writer.writerows(self.metadata_rows)

    # ==========================================================================
    # CÁC TÁC VỤ QUANG PHỔ & ĐO LƯỜNG
    # ==========================================================================
    def start_worker(self):
        self.worker = SpectrometerWorker(
            ports=self._candidate_ports(),
            baud=self.settings["baud"],
            exposure_ms=self.settings["exposure_ms"],
            probe=self.settings.get("port_auto", True),
        )
        self.worker.spectrum_ready.connect(self.handle_spectrum)
        self.worker.exposure_configured.connect(self.handle_exposure_configured)
        self.worker.connection_status.connect(self.handle_connection_status)
        self.worker.device_info_ready.connect(self.handle_device_info)
        self.worker.start()

    def _candidate_ports(self):
        if self.settings.get("port_auto", True):
            return list(SPECTRO_PORTS)
        return [self.settings["port"]]

    def handle_device_info(self, device_id):
        self.device_id = device_id

    def toggle_stream(self):
        t = TRANSLATIONS[self.current_lang]
        if self.worker is None or not self.worker.isRunning():
            self.start_worker()
            self.stream_btn.setText(t["btn_stop_stream"])
            self.stream_btn.setObjectName("DangerBtn")
            self.stream_btn.setStyle(self.stream_btn.style())
        else:
            if self.is_experiment_running:
                self.toggle_experiment()
            self.worker.stop()
            self.worker = None
            self.stream_btn.setText(t["btn_start_stream"])
            self.stream_btn.setObjectName("SuccessBtn")
            self.stream_btn.setStyle(self.stream_btn.style())
            self.m_status.val_lbl.setText(t["status_stopped"])
            self.m_status.val_lbl.setStyleSheet("color: #facc15;")

    def handle_connection_status(self, success, msg):
        t = TRANSLATIONS[self.current_lang]
        if success:
            if not self.is_experiment_running:
                self.m_status.val_lbl.setText(t["status_online"])
                self.m_status.val_lbl.setStyleSheet("color: #10b981;")
        else:
            self.m_status.val_lbl.setText(t["status_disconnected"])
            self.m_status.val_lbl.setStyleSheet("color: #ef4444;")
            QMessageBox.critical(self, t["msg_conn_err_title"], f"{t['msg_conn_err_body']}{msg}")

    def handle_exposure_configured(self, target_ms, mode, readback_ms):
        if mode == ExposureMode.MANUAL.name and exposure_matches(readback_ms, target_ms):
            return
        t = TRANSLATIONS[self.current_lang]
        measured = "--" if np.isnan(readback_ms) else f"{readback_ms:.1f}"
        QMessageBox.warning(
            self, t["app_title"],
            t["msg_exposure_mismatch"].format(target=target_ms, mode=mode, measured=measured)
        )

    def handle_spectrum(self, spectrum, exposure_time, exposure_status, timestamp):
        self.current_raw = spectrum
        self.current_exposure_ms = exposure_time
        n_pixels = len(spectrum)

        if self.wavelengths is None or len(self.wavelengths) != n_pixels:
            start_wl = self.worker.start_wl if self.worker else 380.0
            end_wl = self.worker.end_wl if self.worker else 1050.0
            self.wavelengths = np.linspace(start_wl, end_wl, n_pixels)
            self.plot_widget.setXRange(start_wl, end_wl)

        # Vẽ đồ thị
        if self.view_mode == "spectrum":
            peak_val = spectrum.max()
            peak_idx = spectrum.argmax()
            peak_wl = self.wavelengths[peak_idx]

            self.m_peak.val_lbl.setText(f"{peak_val:.0f}")
            self.m_lambda_peak.val_lbl.setText(f"{peak_wl:.1f} nm")
            self.curve_realtime.setData(self.wavelengths, spectrum)

        elif self.view_mode == "absorbance":
            abs_data = self.calculate_absorbance(spectrum, exposure_time)
            if abs_data is not None and np.isfinite(abs_data).any():
                peak_abs = np.nanmax(abs_data)
                peak_idx = int(np.nanargmax(abs_data))
                peak_wl = self.wavelengths[peak_idx]

                self.m_peak.val_lbl.setText(f"{peak_abs:.3f} OD")
                self.m_lambda_peak.val_lbl.setText(f"{peak_wl:.1f} nm")
                self.curve_realtime.setData(self.wavelengths, abs_data)

        # Nếu đang trong 1 phép đo trung bình N khung (Dark/Reference/Trứng), gom frame lại
        if self.capture_state is not None:
            cs = self.capture_state
            cs["frames"].append(spectrum.astype(np.float64))
            cs["exposures"].append(exposure_time)
            cs["statuses"].append(exposure_status)
            n_done = len(cs["frames"])
            self.m_status.val_lbl.setText(f"{cs['label']} ({n_done}/{cs['n_target']})")

            if n_done >= cs["n_target"]:
                self.capture_state = None
                avg_spectrum = np.mean(cs["frames"], axis=0)
                valid_exposures = [e for e in cs["exposures"] if e is not None and not np.isnan(e)]
                avg_exposure = float(np.mean(valid_exposures)) if valid_exposures else float("nan")
                cs["on_complete"](avg_spectrum, avg_exposure, cs["statuses"], np.array(cs["frames"]))

    def calculate_absorbance(self, sample_counts, exposure_ms=None):
        """Absorbance = log10[(R-D_r)/t_r / (S-D_s)/t_s] - Dev Brief mục 4.2 + Giải pháp A mục 5.4."""
        if self.ref_dark_corr is None or self.dark_spectrum is None:
            return None
        if len(sample_counts) != len(self.dark_spectrum):   # Dark/Lamp nạp từ dataset khác lưới pixel
            return None
        sample_dark_corr = sample_counts - self.dark_spectrum
        t_sample = exposure_ms if exposure_ms is not None else self.ref_exposure_ms
        abs_data, _ = safe_absorbance(self.ref_dark_corr, sample_dark_corr, self.ref_exposure_ms, t_sample)
        return abs_data

    def on_mode_switched(self, idx):
        t = TRANSLATIONS[self.current_lang]
        self.view_mode = self.mode_combo.itemData(idx)
        if self.view_mode == "spectrum":
            self.plot_widget.setLabel("left", t["axis_intensity"], units="ADC Counts")
            self.plot_widget.enableAutoRange(axis=pg.ViewBox.YAxis)
        else:
            if self.ref_dark_corr is None or self.dark_spectrum is None:
                QMessageBox.warning(self, t["app_title"], "Chưa có Dark/Reference. Hãy bấm 'Bắt đầu thí nghiệm' trước.")
                self.mode_combo.setCurrentIndex(0)
                return
            self.plot_widget.setLabel("left", t["axis_absorbance"], units="OD")
            self.plot_widget.setYRange(0, 3.0)
        self._draw_preview()

    def take_snapshot(self):
        if self.current_raw is not None and self.wavelengths is not None:
            if self.view_mode == "spectrum":
                self.snapshot_spectrum = self.current_raw.copy()
                self.curve_snapshot.setData(self.wavelengths, self.snapshot_spectrum)
            else:
                snap_abs = self.calculate_absorbance(self.current_raw, self.current_exposure_ms)
                if snap_abs is not None and np.isfinite(snap_abs).any():
                    self.snapshot_spectrum = snap_abs.copy()
                    self.curve_snapshot.setData(self.wavelengths, self.snapshot_spectrum)

    def clear_snapshot(self):
        self.snapshot_spectrum = None
        self.curve_snapshot.clear()

    # ==========================================================================
    # SIDEBAR DỮ LIỆU
    # ==========================================================================
    def toggle_browser(self, visible):
        self.browser_wrap.setVisible(visible)

    def _busy_paths(self):
        """Thư mục đang được ghi dữ liệu đo -> sidebar không cho đổi tên / xóa."""
        if not self.is_experiment_running:
            return []
        return [self.run_dir] + ([self.session["dir"]] if self.session else [])

    def set_save_dir(self, path):
        t = TRANSLATIONS[self.current_lang]
        try:
            os.makedirs(path, exist_ok=True)
        except OSError as e:
            QMessageBox.critical(self, t["br_title"], t["br_error"].format(err=e))
            return
        self.settings["save_dir"] = path
        self.browser.set_save_dir(path)
        self.browser.update_info()

    def preview_file(self, path):
        """Vẽ phổ của 1 file CSV đã lưu lên đồ thị (đường 'File'), theo chế độ xem hiện tại."""
        t = TRANSLATIONS[self.current_lang]
        try:
            self.preview_data = load_spectrum_csv(path)
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, t["br_title"], t["br_preview_err"].format(path=path, err=e))
            return
        legend = self.plot_widget.plotItem.legend
        label = legend.getLabel(self.curve_preview) if hasattr(legend, "getLabel") else None
        if label is not None:
            label.setText(os.path.splitext(os.path.basename(path))[0])
        self._draw_preview()
        self.plot_widget.enableAutoRange()

    def _draw_preview(self):
        if self.preview_data is None:
            self.curve_preview.clear()
            return
        wl, raw, absorbance = self.preview_data
        if self.view_mode == "spectrum":
            self.curve_preview.setData(wl, raw)
        elif absorbance is not None:
            self.curve_preview.setData(wl, absorbance)
        else:
            self.curve_preview.clear()

    def clear_preview(self):
        self.preview_data = None
        self.curve_preview.clear()

    def open_settings(self):
        t = TRANSLATIONS[self.current_lang]
        theme_qss = LIGHT_THEME if self.current_theme == "light" else DARK_THEME
        dlg = SettingsDialog(self.settings, self.current_lang, theme_qss, self)
        if dlg.exec_() == QDialog.Accepted:
            self.settings = dlg.settings
            self.browser.set_save_dir(self.settings["save_dir"])
            if self.worker and self.worker.isRunning() and not self._worker_matches_settings():
                if self.is_experiment_running:
                    # Đổi exposure giữa thí nghiệm làm Dark/Reference mất hiệu lực -> để người dùng tự dừng
                    QMessageBox.information(self, t["app_title"], t["msg_restart_worker"])
                else:
                    self.restart_worker()

    def _worker_matches_settings(self):
        """Worker đang chạy có đúng danh sách cổng/baud/exposure trong settings hay không."""
        w = self.worker
        return (
            w.ports == self._candidate_ports()
            and w.baud == self.settings["baud"]
            and w.exposure_ms == float(self.settings["exposure_ms"])
        )

    def restart_worker(self):
        """Kết nối lại thiết bị để áp dụng cấu hình mới (exposure chỉ được gửi lúc kết nối)."""
        if self.worker is not None:
            self.worker.stop()
        self.start_worker()

    def toggle_maximize(self):
        if self.isMaximized():
            self.showNormal()
            self.max_btn.setText("□")
        else:
            self.showMaximized()
            self.max_btn.setText("❐")

    def title_mouse_press(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_position = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()

    def title_mouse_move(self, event):
        if event.buttons() == Qt.LeftButton and not self.isMaximized():
            self.move(event.globalPos() - self.drag_position)
            event.accept()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.trigger_egg_capture()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        if self.worker is not None:
            self.worker.stop()
        event.accept()


# ==============================================================================
# ENTRY POINT
# ==============================================================================
if __name__ == "__main__":
    app = QApplication(sys.argv)
    dashboard = SpectraLensDashboard()
    dashboard.show()
    sys.exit(app.exec_())