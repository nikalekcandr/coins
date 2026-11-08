@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul && (set "PY=py -3") || (set "PY=python")
echo ================================================================
echo  coinarb - ПОЛНЫЙ СБОР ДАННЫХ
echo ================================================================
echo.
if exist cookies.json (
    echo Найден cookies.json - собираю всё, включая Мешок и базу сделок.
    echo.
    %PY% scan.py all --storage-state cookies.json
) else (
    echo [!] cookies.json НЕ найден - Мешок будет пропущен ^(Cloudflare^).
    echo     Как включить: зайдите на meshok.net в браузере, выгрузите cookies
    echo     расширением EditThisCookie в файл cookies.json в эту папку.
    echo.
    %PY% scan.py all
)
echo.
if exist data\report.html (
    echo Открываю отчёт в браузере...
    start "" "data\report.html"
)
echo Готово. Файлы: data\report.html, data\price_index.csv, data\opportunities.csv
pause
