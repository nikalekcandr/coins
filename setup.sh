#!/usr/bin/env bash
# coinarb — установка (один раз), macOS/Linux
set -e
cd "$(dirname "$0")"
python3 -m pip install -r requirements.txt
python3 -m playwright install chromium
echo "--- самопроверка (демо, без интернета) ---"
python3 scan.py demo
echo "Готово. Полный сбор: ./run.sh"
