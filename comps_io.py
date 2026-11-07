"""Импорт и загрузка базы сопоставимых цен (comps).

Формат CSV (см. data/comps_seed.csv):
    comp_key,year,denomination,metal,grader,grade_num,grade_label,price_rub,source,note

comp_key можно не заполнять — соберётся как year|denomination|metal.
Это главный вход для качества аналитики: чем больше реальных проданных цен,
тем достовернее оценка. Собирать их можно из архивов аукционов, из истории
своих сделок и из «исчезнувших» лотов при регулярных прогонах.
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

def load_pool(con) -> tuple[list, dict, dict]:
    """Достаёт из БД лоты + фичи так, чтобы их можно было скормить analyze.screen.

    Возвращает (coins, prices_by_id(coin), lot_id_by_id(coin)).
    """
    from .normalize import Coin
    rows = con.execute("""
        SELECT l.id, l.price_rub, l.title, f.item_type, f.year, f.denomination, f.metal,
               f.weight_g, f.mintmaster, f.grader, f.grade_label, f.grade_num,
               f.grade_bucket, f.is_slabbed, f.is_romanov, f.is_set, f.variety
        FROM lots l JOIN features f ON f.lot_id = l.id
        WHERE l.is_active = 1
    """).fetchall()
    coins, prices, ids = [], {}, {}
    for r in rows:
        c = Coin(title=r["title"], item_type=r["item_type"] or "unknown",
                 year=r["year"], denomination=r["denomination"], metal=r["metal"],
                 weight_g=r["weight_g"], mintmaster=r["mintmaster"], grader=r["grader"],
                 grade_label=r["grade_label"], grade_num=r["grade_num"],
                 grade_bucket=r["grade_bucket"], is_slabbed=bool(r["is_slabbed"]),
                 is_romanov=bool(r["is_romanov"]), is_set=bool(r["is_set"]),
                 variety=r["variety"])
        coins.append(c); prices[id(c)] = r["price_rub"]; ids[id(c)] = r["id"]
    return coins, prices, ids

def comps_from_db(con, key: str) -> list[dict]:
    rows = con.execute("SELECT * FROM comps WHERE comp_key=?", (key,)).fetchall()
    return [dict(r) for r in rows]
