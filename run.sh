#!/usr/bin/env bash
# coinarb — полный сбор данных, macOS/Linux
cd "$(dirname "$0")"
if [ -f cookies.json ]; then
    python3 scan.py all --storage-state cookies.json
else
    echo "[!] cookies.json не найден — Мешок будет пропущен (Cloudflare)."
    python3 scan.py all
fi
echo "Отчёт: data/report.html"
