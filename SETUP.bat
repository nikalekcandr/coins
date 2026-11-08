@echo off
chcp 65001 >nul
set "PYTHONUTF8=1"
cd /d "%~dp0"
echo === coinarb setup ===
set "PY=python"
py -3 --version >nul 2>nul && set "PY=py -3"
%PY% --version >nul 2>nul
if errorlevel 1 goto nopython
%PY% --version
echo [1/3] Installing dependencies...
%PY% -m pip install -r requirements.txt
if errorlevel 1 goto pipfail
echo [2/3] Installing Chromium...
%PY% -m playwright install chromium
if errorlevel 1 goto browserfail
echo [3/3] Running offline demo...
%PY% scan.py demo
if errorlevel 1 goto demofail
echo Setup complete. Run RUN.bat to collect data.
pause
exit /b 0
:nopython
echo ERROR: Python 3 not found. Install from https://www.python.org/downloads/
echo Select Add python.exe to PATH during installation, then try again.
pause
exit /b 1
:pipfail
echo ERROR: dependency installation failed. See details above.
pause
exit /b 1
:browserfail
echo ERROR: Chromium installation failed. Check the error above and retry SETUP.bat.
pause
exit /b 1
:demofail
echo ERROR: demo failed. See details above.
pause
exit /b 1
