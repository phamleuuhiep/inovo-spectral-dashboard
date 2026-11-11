# -*- coding: utf-8 -*-
"""
Giữ dashboard chạy liên tục trong LAN (không cửa sổ console). Được Task Scheduler gọi khi đăng nhập
(xem install_autostart.ps1), hoặc chạy tay: pythonw dashboard/service.pyw

  - chạy server.py --host 0.0.0.0 --port 8050
  - tự khởi động lại khi server bị dừng / lỗi
  - tự khởi động lại khi file .py trong dashboard/ thay đổi (sửa code không cần chạy lại script)
  - log: outputs/dashboard_logs/server.log (cắt bớt khi > 5 MB)
"""

import glob
import os
import socket
import subprocess
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PORT = int(os.environ.get("INOVO_DASH_PORT", "8050"))
LOG_DIR = os.path.join(ROOT, "outputs", "dashboard_logs")
LOG = os.path.join(LOG_DIR, "server.log")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def log(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] [service] {msg}\n")


def code_stamp():
    return max((os.path.getmtime(p) for p in glob.glob(os.path.join(HERE, "*.py"))), default=0)


def port_busy():
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def start():
    if os.path.exists(LOG) and os.path.getsize(LOG) > 5e6:
        os.replace(LOG, LOG + ".1")
    out = open(LOG, "a", encoding="utf-8")
    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    p = subprocess.Popen([sys.executable, os.path.join(HERE, "server.py"), "--host", "0.0.0.0", "--port", str(PORT)],
                         cwd=ROOT, stdout=out, stderr=subprocess.STDOUT, env=env, creationflags=NO_WINDOW)
    out.close()
    return p


def main():
    os.makedirs(LOG_DIR, exist_ok=True)
    log(f"bắt đầu, cổng {PORT}")
    warned = False
    while port_busy():          # đã có server khác (vd run_lan.bat) giữ cổng -> chờ, không chạy chồng
        if not warned:
            log(f"cổng {PORT} đang bận, chờ đến khi trống")
            warned = True
        time.sleep(15)
    fails = 0
    while True:
        stamp, t0 = code_stamp(), time.time()
        p = start()
        log(f"server pid {p.pid}")
        while p.poll() is None:
            time.sleep(2)
            if code_stamp() != stamp:
                time.sleep(1)       # chờ ghi file xong
                log("code thay đổi -> khởi động lại")
                p.terminate()
                try:
                    p.wait(10)
                except subprocess.TimeoutExpired:
                    p.kill()
                fails = 0
                break
        else:
            fails = fails + 1 if time.time() - t0 < 30 else 0
            log(f"server dừng (mã {p.returncode}), khởi động lại")
            time.sleep(min(60, 3 * 2 ** min(fails, 5)))   # lỗi liên tục -> chờ lâu dần


if __name__ == "__main__":
    main()
