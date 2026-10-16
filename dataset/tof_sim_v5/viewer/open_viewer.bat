@echo off
rem 3D viewer: serves this folder with Python and opens it in the browser.
cd /d "%~dp0"
start "tof-sim viewer server" /min "..\.venv\Scripts\python.exe" -m http.server 8766 --bind 127.0.0.1
timeout /t 2 >nul
start "" "http://127.0.0.1:8766/index.html"
