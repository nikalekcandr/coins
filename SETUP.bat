@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ================================================================
echo  coinarb - УСТАНОВКА (запускать ОДИН РАЗ)
echo ================================================================
echo.
where py >nul 2>nul && (set "PY=py -3") || (set "PY=python")
%PY% --version >nul 2>nul
if errorlevel 1 (
    echo [ОШИБКА] Python не найден.
    echo.
    echo  1. Скачайте Python 3.10+ : https://www.python.org/downloads/
    echo  2. При установке ОБЯЗАТЕЛЬНО поставьте галочку "Add python.exe to PATH"
    echo  3. Закройте это окно и запустите SETUP.bat заново
    echo.
    pause
    exit /b 1
)
%PY% --version
echo.
echo [1/3] Устанавливаю библиотеки (нужен интернет)...
%PY% -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [ОШИБКА] Установка библиотек не удалась - текст ошибки выше.
    pause
    exit /b 1
)
echo.
echo [2/3] Скачиваю браузер Chromium для сбора сайтов (может занять несколько минут)...
%PY% -m playwright install chromium
if errorlevel 1 (
    echo.
    echo [ВНИМАНИЕ] Chromium не скачался. Живой сбор сайтов пока не заработает,
    echo но анализ/импорт/отчёты работают. Позже повторите:
    echo     %PY% -m playwright install chromium
)
echo.
echo [3/3] Самопроверка (демо-прогон, интернет не нужен)...
%PY% scan.py demo
echo.
echo ================================================================
echo  ГОТОВО. Для полного сбора данных запускайте RUN.bat
echo ================================================================
pause
