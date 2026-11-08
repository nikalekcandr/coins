"""Импорт и загрузка базы сопоставимых цен (comps) и базы сделок (deals).

Формат CSV comps (см. data/comps_seed.csv):
    comp_key,year,denomination,metal,grader,grade_num,grade_label,price_rub,source,note

comp_key можно не заполнять — соберётся как year|denomination|metal.

Comps — справочные цены (включая оценки и DEMO). Факты продаж идут в `deals`
через sources/sold.py (CSV/JSON/HTML аукционных архивов) и vanish-прокси.
Это главный вход для качества аналитики: чем больше реальных проданных цен,
тем достовернее оценка.
"""
from __future__ import annotations
import csv
import time
from pathlib import Path

from .analyze import grader_tier

def build_key(year, denomination, metal) -> str:
    return f"{year or ''}|{(denomination or '').strip()}|{metal or ''}"

def import_csv(con, path: str | Path) -> int:
    """Читает CSV с comps. Возвращает число добавленных строк."""
    n = 0
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            key = (row.get("comp_key") or "").strip() or build_key(
                row.get("year"), row.get("denomination"), row.get("metal"))
            raw = (row.get("price_rub") or "").strip()
            if not raw:
                continue
            try:
                price = float(raw.replace(" ", "").replace(",", "."))
            except ValueError:
                continue
            gnum = (row.get("grade_num") or "").strip()
            grade_num = int(gnum) if gnum.isdigit() and int(gnum) > 0 else None
            grader = (row.get("grader") or "").strip() or None
            bucket = ("raw" if grade_num is None else
                      "gem" if grade_num >= 65 else
                      "high" if grade_num >= 58 else
                      "mid" if grade_num >= 40 else "low")
            con.execute(
                "INSERT INTO comps(comp_key,source,title,url,price_rub,grader,grade_num,"
                "grade_bucket,is_slabbed,seen_at,note) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (key, row.get("source") or "csv", row.get("note") or "", "",
                 price, grader, grade_num, bucket,
                 int(grader_tier(grader) > 0), time.time(), row.get("note") or ""))
            n += 1
    con.commit()
    return n

def export_comps_csv(con, path: str | Path) -> int:
    """Выгрузить comps + deals в единый CSV (для обмена/бэкапа/ручной правки)."""
    n = 0
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["comp_key", "price_rub", "grader", "grade_num", "is_slabbed",
                    "source", "kind", "title", "url", "sold_at", "note"])
        for r in con.execute("SELECT * FROM comps ORDER BY comp_key"):
            w.writerow([r["comp_key"], r["price_rub"], r["grader"] or "", r["grade_num"] or "",
                        int(bool(r["is_slabbed"])), r["source"], "comp", r["title"],
                        r["url"], "", r["note"]])
            n += 1
        for r in con.execute("SELECT * FROM deals ORDER BY comp_key"):
            w.writerow([r["comp_key"], r["price_rub"], r["grader"] or "", r["grade_num"] or "",
                        int(bool(r["is_slabbed"])), r["source"], "deal", r["title"],
                        r["url"], time.strftime("%Y-%m-%d", time.gmtime(r["sold_at"]))
                        if r["sold_at"] else "", r["note"]])
            n += 1
    return n

def load_pool(con) -> tuple[list, dict, dict, dict]:
    """Достаёт из БД активные лоты + фичи так, чтобы их можно было скормить analyze.

    Возвращает (coins, prices_by_id(coin), lot_id_by_id(coin), meta_by_id(coin)),
    где meta = {url, source, section, seller, first_seen, last_seen}.
    """
    from .normalize import Coin
    rows = con.execute("""
        SELECT l.id, l.price_rub, l.title, l.url, l.source, l.section, l.seller,
               l.first_seen, l.last_seen,
               f.item_type, f.year, f.denomination, f.metal,
               f.weight_g, f.mintmaster, f.grader, f.grade_label, f.grade_num,
               f.grade_bucket, f.is_slabbed, f.is_romanov, f.is_set, f.variety
        FROM lots l JOIN features f ON f.lot_id = l.id
        WHERE l.is_active = 1
    """).fetchall()
    coins, prices, ids, meta = [], {}, {}, {}
    for r in rows:
        c = Coin(title=r["title"], item_type=r["item_type"] or "unknown",
                 year=r["year"], denomination=r["denomination"], metal=r["metal"],
                 weight_g=r["weight_g"], mintmaster=r["mintmaster"], grader=r["grader"],
                 grade_label=r["grade_label"], grade_num=r["grade_num"],
                 grade_bucket=r["grade_bucket"], is_slabbed=bool(r["is_slabbed"]),
                 is_romanov=bool(r["is_romanov"]), is_set=bool(r["is_set"]),
                 variety=r["variety"])
        coins.append(c); prices[id(c)] = r["price_rub"]; ids[id(c)] = r["id"]
        meta[id(c)] = {"url": r["url"], "source": r["source"], "section": r["section"],
                       "seller": r["seller"], "first_seen": r["first_seen"],
                       "last_seen": r["last_seen"]}
    return coins, prices, ids, meta

def load_comps_by_key(con) -> dict[str, list]:
    """Справочные comps из таблицы comps, сгруппированные по comp_key."""
    from .analyze import Comp
    out: dict[str, list] = {}
    for row in con.execute("SELECT * FROM comps"):
        out.setdefault(row["comp_key"], []).append(
            Comp(row["price_rub"], row["grader"], row["grade_num"],
                 bool(row["is_slabbed"]), "db"))
    return out

def load_deals_by_key(con) -> dict[str, list[dict]]:
    """Факты продаж из таблицы deals, сгруппированные по comp_key."""
    out: dict[str, list[dict]] = {}
    for r in con.execute("SELECT * FROM deals WHERE price_rub IS NOT NULL"):
        d = dict(r)
        # comp_key = year|denom|metal — восстанавливаем металл для проверок
        parts = (d.get("comp_key") or "").split("|")
        d["metal"] = parts[2] if len(parts) > 2 and parts[2] != "None" else None
        out.setdefault(d["comp_key"], []).append(d)
    return out

def comps_from_db(con, key: str) -> list[dict]:
    rows = con.execute("SELECT * FROM comps WHERE comp_key=?", (key,)).fetchall()
    return [dict(r) for r in rows]
