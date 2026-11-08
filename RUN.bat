@echo off
chcp 65001 >nul
set "PYTHONUTF8=1"
cd /d "%~dp0"
echo === coinarb collection ===
set "PY=python"
py -3 --version >nul 2>nul && set "PY=py -3"
%PY% --version >nul 2>nul
if errorlevel 1 goto nopython
if exist cookies.json goto withcookies
echo NOTE: cookies.json not found. Meshok will be skipped.
%PY% scan.py all
goto finished
:withcookies
echo Using cookies.json. Access to Meshok is not guaranteed.
%PY% scan.py all --storage-state cookies.json
:finished
if errorlevel 1 goto failed
echo Check the messages above for collection errors or skipped sites.
if exist data\report.html start "" "data\report.html"
pause
exit /b 0
:nopython
echo ERROR: Python 3 not found. Run SETUP.bat first.
pause
exit /b 1
:failed
echo ERROR: scan failed. See details above. Existing reports may be old.
pause
exit /b 1
