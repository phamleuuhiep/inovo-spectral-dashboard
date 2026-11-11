@echo off
REM Chạy dashboard cho các máy trong cùng mạng LAN truy cập (http://<IP máy này>:8050)
chcp 65001 >nul
cd /d "%~dp0.."
echo Dia chi cho may khac trong LAN:
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do for /f "tokens=*" %%b in ("%%a") do echo    http://%%b:8050
echo.
python dashboard\server.py --host 0.0.0.0 --port 8050
pause
