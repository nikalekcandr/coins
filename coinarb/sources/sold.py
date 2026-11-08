"""Универсальный импорт ФАКТОВ ПРОДАЖ в базу сделок (таблица deals).

Зачем: «полная база сделок» не собирается с одного сайта. Реальные hammer-цены
живут в архивах аукционов: Конрос, Волмар, Редкие Монеты, Александр, Империя,
а также в завершённых лотах Мешка. Форматы у всех разные, но суть одна:
строка «название — итоговая цена — (дата)».

Три входа:
  1. CSV/JSON — каноническая схема (см. DEALS_COLUMNS); подходит для выгрузок
     из личных таблиц и результатов ручного сбора;
  2. HTML — parse_generic_archive(): эвристический разбор ТАБЛИЦ любого
     аукционного архива без знания конкретной вёрстки: ищем строки, где есть
     (а) ячейка-цена и (б) ячейка-название с годом/номиналом. Работает для
     Конроса/Волмара/RareCoins «из коробки» с точностью до полей;
  3. Завершённые лоты Мешка — sources/meshok.parse_ended (отдельный адаптер).

Каждая запись нормализуется (normalize.parse + reference.infer), получает
comp_key и дедуплицируется в store.record_deal. Повторный импорт того же
архива не создаёт дублей — можно смело гонять по расписанию.
"""
from __future__ import annotations
import csv
import json
import re
import time
from pathlib import Path

from .. import reference, store
from ..analyze import enrich
from ..normalize import parse

# Канонические колонки CSV/JSON. Обязательны: title, price_rub.
# Остальное (sold_at, grader, grade_num, url, seller, note, source) — опционально;
# grader/grade_num/comp_key доопределяются из title автоматически.
DEALS_COLUMNS = ["title", "price_rub", "sold_at", "grader", "grade_num",
                 "url", "seller", "note", "source", "comp_key"]

MONTHS = {"янв": 1, "фев": 2, "мар": 3, "апр": 4, "ма": 5, "мая": 5, "июн": 6,
          "июл": 7, "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12}


def parse_date(s: str | float | None) -> float | None:
    """Дата в epoch: '2024-03-15', '15.03.2024', '15 марта 2024', epoch float."""
    if s is None or s == "":
        return None
    if isinstance(s, (int, float)):
        return float(s)
    s = str(s).strip().lower()
    if re.fullmatch(r"\d{9,12}(\.\d+)?", s):
        return float(s)
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        y, mo, d = map(int, m.groups())
    else:
        m = re.match(r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{2,4})", s)
        if m:
            d, mo, y = map(int, m.groups())
            if y < 100: y += 2000
        else:
            m = re.match(r"(\d{1,2})\s+([а-я]+)\w*\s+(\d{4})", s)
            if not m: return None
            d, mon, y = int(m.group(1)), MONTHS.get(m.group(2)[:3]), int(m.group(3))
            if not mon: return None
            mo = mon
    try:
        return time.mktime(time.strptime(f"{y:04d}-{mo:02d}-{min(d,28):02d}", "%Y-%m-%d"))
    except ValueError:
        return None


def _to_deal(row: dict, default_source: str) -> dict | None:
    """Строка любого формата → запись deals с авто-нормализацией."""
    title = (row.get("title") or "").strip()
    price = row.get("price_rub") or row.get("price")
    if not title or price in (None, ""):
        return None
    try:
        price = float(str(price).replace(" ", "").replace("\u00a0", "").replace(",", "."))
    except ValueError:
        return None
    if price <= 0:
        return None

    coin = enrich(parse(title))
    grader = (row.get("grader") or "").strip() or coin.grader
    gnum = row.get("grade_num")
    try:
        grade_num = int(gnum) if gnum not in (None, "") else coin.grade_num
    except (TypeError, ValueError):
        grade_num = coin.grade_num
    comp_key = (row.get("comp_key") or "").strip() or coin.key()
    return {
        "title": title, "price_rub": price,
        "sold_at": parse_date(row.get("sold_at") or row.get("date")),
        "grader": grader, "grade_num": grade_num,
        "grade_bucket": coin.grade_bucket, "is_slabbed": coin.is_slabbed,
        "item_type": coin.item_type, "comp_key": comp_key,
        "url": row.get("url") or "", "seller": row.get("seller") or "",
        "note": row.get("note") or "",
        "source": row.get("source") or default_source,
    }


def import_rows(con, rows: list[dict], source: str, method: str = "manual") -> dict:
    """Записывает список строк в deals. Возвращает статистику импорта."""
    added = skipped = nokey = 0
    for row in rows:
        deal = _to_deal(row, source)
        if not deal:
            skipped += 1
            continue
        if not deal["comp_key"]:
            nokey += 1
        if deal["item_type"] not in ("coin",):
            skipped += 1                     # сделки по банкнотам/медалям не в comps монет
            continue
        ok = store.record_deal(con, method=method, **deal)
        added += 1 if ok else 0
    return {"added": added, "duplicates": max(0, len(rows) - skipped - added),
            "skipped": skipped, "no_comp_key": nokey}


def import_csv(con, path: str | Path, source: str = "csv") -> dict:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return import_rows(con, list(csv.DictReader(f)), source, "manual")


def import_json(con, path: str | Path, source: str = "json") -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("deals") or data.get("items") or []
    return import_rows(con, data, source, "manual")


# ------------------------------------------------------------------ HTML
_PRICE_CELL_RE = re.compile(r"^[\d\s\u00a0.,]+$")
_YEAR_RE = re.compile(r"\b(1[5-9]\d{2}|20[0-2]\d)\b")
_DENOM_HINT_RE = re.compile(
    r"рубл|руб\b|коп|полтин|червон|империал|гривенник|пятак|копейк", re.I)
_GRADE_CELL_RE = re.compile(
    r"\b(MS|AU|XF|VF|PF|PR|UNC|F|VG|G)\s*-?\s*(\d{1,2})\b", re.I)


def _num(s: str) -> float | None:
    s = s.strip().replace("\u00a0", "").replace(" ", "")
    if not s:
        return None
    s = s.replace(",", ".")
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", s) or s.count(".") > 1:
        s = s.replace(".", "")
    try:
        v = float(s)
        return v if v > 0 else None
    except ValueError:
        return None


def parse_generic_archive(html: str, source: str = "auction-archive") -> list[dict]:
    """Эвристический разбор HTML-архива аукциона БЕЗ знания вёрстки.

    Алгоритм: в каждой таблице каждая строка — кандидат сделки, если в ней есть
      * ячейка-цена: короткая, чисто цифровая, правдоподобный диапазон
        (30 … 50 000 000 ₽) — берётся ПОСЛЕДНЯЯ подходящая (в архивах итоговая
        цена обычно правее стартовой), и
      * ячейка-название: самый длинный текст строки, содержащий год или
        номинальные слова.
    Дата — из любой ячейки строки или из ближайшего заголовка над таблицей
    («Аукцион 15.03.2024», «Торги 12 марта 2024»).

    Это best-effort: для экзотической вёрстки точность падает, но строки без
    цены/года отбрасываются — мусор в базу сделок не попадает. Для проверки
    есть тесты на синтетических архивах в стиле Конроса/Волмара/RareCoins.
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    rows_out: list[dict] = []

    for table in soup.find_all("table"):
        # дата аукциона из подписи/заголовка над таблицей
        table_date = None
        prev = table.find_previous(re.compile(r"^(h\d|p|div|caption)$"))
        if prev:
            dm = re.search(r"(\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4}|\d{1,2}\s+[а-яА-Я]{3,}\s+\d{4})",
                           prev.get_text(" ", strip=True))
            if dm:
                table_date = parse_date(dm.group(1))

        for tr in table.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            if len(cells) < 2:
                continue
            texts = [" ".join(c.get_text(" ", strip=True).split()) for c in cells]

            # ячейка-название: длинный текст с годом или номиналом
            title_idx, title = None, ""
            for i, t in enumerate(texts):
                if len(t) > len(title) and len(t) >= 12 and \
                   (_YEAR_RE.search(t) or _DENOM_HINT_RE.search(t)):
                    title_idx, title = i, t
            if title_idx is None:
                continue

            # ячейки-цены: правдоподобные числа, не похожие на дату/год
            price = None
            for i, t in enumerate(texts):
                if i == title_idx or not t or not _PRICE_CELL_RE.match(t):
                    continue
                bare = t.replace(" ", "").replace("\u00a0", "")
                if re.fullmatch(r"\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4}", bare) or \
                   re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", bare):
                    continue                     # это дата, а не цена
                v = _num(t)
                if v is None or not (30 <= v <= 50_000_000):
                    continue
                # «голое» число 1500–2029 слева от названия — колонка года, не цена;
                # справа от названия — уже похоже на hammer price (1 850 ₽ и т.п.)
                if re.fullmatch(r"(1[5-9]\d{2}|20[0-2]\d)([.,]0+)?", bare) and i < title_idx:
                    continue
                price = v                        # берём последнюю (итоговая)

            if price is None:
                continue

            sold_at = table_date
            for t in texts:                        # дата в самой строке приоритетнее
                dm = re.match(r"^(\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4}|\d{4}-\d{1,2}-\d{1,2})$", t)
                if dm:
                    sold_at = parse_date(dm.group(1)) or sold_at
                    break

            gm = _GRADE_CELL_RE.search(" ".join(texts))
            rows_out.append({
                "title": title, "price_rub": price, "sold_at": sold_at,
                "grade_num": (int(gm.group(2)) if gm and gm.group(1).upper() in
                              ("MS", "AU", "XF", "VF", "PF", "PR", "UNC") else None),
                "source": source, "note": "",
            })
    return rows_out


def import_archive_html(con, path: str | Path, source: str) -> dict:
    html = Path(path).read_text(encoding="utf-8", errors="replace")
    rows = parse_generic_archive(html, source)
    return import_rows(con, rows, source, "auction-archive")


def import_deals_file(con, path: str | Path, source: str | None = None,
                      fmt: str | None = None) -> dict:
    """Единая точка входа: scan.py import-sold FILE [--source NAME --format FMT]."""
    path = Path(path)
    fmt = (fmt or path.suffix.lstrip(".").lower()).lower()
    src = source or path.stem
    if fmt == "csv":
        return import_csv(con, path, src)
    if fmt == "json":
        return import_json(con, path, src)
    if fmt in ("html", "htm"):
        return import_archive_html(con, path, src)
    raise ValueError(f"неизвестный формат {fmt!r} (поддерживаются csv/json/html)")
