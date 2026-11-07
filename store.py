"""Хранилище на SQLite: история цен, наблюдения, сделки-кандидаты.

Именно накопление истории — главное преимущество перед теми, кто смотрит руками.
Первый запуск даёт срез; к пятому запуску вы видите динамику, скорость продаж
и реальный spread, а не догадку о нём.
"""
from __future__ import annotations
import json, sqlite3, time
from dataclasses import asdict
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "coins.db"
DB.parent.mkdir(exist_ok=True)

SCHEMA = """
CREATE TABLE IF NOT EXISTS lots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,            -- staraya-moneta | meshok
    source_id TEXT NOT NULL,         -- ID лота на площадке
    url TEXT,
    title TEXT,
    price_rub REAL,
    section TEXT,
    seller TEXT,
    first_seen REAL, last_seen REAL,
    is_active INTEGER DEFAULT 1,
    UNIQUE(source, source_id)
);
CREATE TABLE IF NOT EXISTS observations (      -- временной ряд цен
    lot_id INTEGER, seen_at REAL, price_rub REAL, is_active INTEGER,
    FOREIGN KEY(lot_id) REFERENCES lots(id)
);
CREATE TABLE IF NOT EXISTS features (          -- нормализованные атрибуты лота
    lot_id INTEGER PRIMARY KEY,
    item_type TEXT, year INTEGER, denomination TEXT, metal TEXT,
    weight_g REAL, mintmaster TEXT, grader TEXT, grade_label TEXT,
    grade_num INTEGER, grade_bucket TEXT, is_slabbed INTEGER,
    is_romanov INTEGER, is_set INTEGER, variety TEXT, comp_key TEXT,
    FOREIGN KEY(lot_id) REFERENCES lots(id)
);
CREATE TABLE IF NOT EXISTS comps (             -- цены проданных/выставленных аналогов
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    comp_key TEXT, source TEXT, title TEXT, url TEXT,
    price_rub REAL, grader TEXT, grade_num INTEGER, grade_bucket TEXT,
    is_slabbed INTEGER, seen_at REAL, note TEXT
);
CREATE TABLE IF NOT EXISTS opportunities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lot_id INTEGER, created_at REAL,
    buy_price REAL, expected_sale REAL, slab_cost REAL, net_profit REAL,
    roi_pct REAL, confidence REAL, reason TEXT, payload TEXT,
    status TEXT DEFAULT 'new'
);
CREATE INDEX IF NOT EXISTS ix_comps_key ON comps(comp_key);
CREATE INDEX IF NOT EXISTS ix_obs_lot ON observations(lot_id);
CREATE INDEX IF NOT EXISTS ix_feat_key ON features(comp_key);
"""

def connect(path: Path | str = DB) -> sqlite3.Connection:
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con

def upsert_lot(con, *, source: str, source_id: str, url: str, title: str,
               price: float | None, section: str = "", seller: str = "") -> int:
    now = time.time()
    row = con.execute("SELECT id FROM lots WHERE source=? AND source_id=?",
                      (source, str(source_id))).fetchone()
    if row:
        lot_id = row["id"]
        # фиксируем изменение цены отдельным наблюдением
        prev = con.execute("SELECT price_rub FROM observations WHERE lot_id=? "
                           "ORDER BY seen_at DESC LIMIT 1", (lot_id,)).fetchone()
        con.execute("UPDATE lots SET last_seen=?, price_rub=?, title=?, is_active=1 WHERE id=?",
                    (now, price, title, lot_id))
        if not prev or (price is not None and prev["price_rub"] != price):
            con.execute("INSERT INTO observations VALUES (?,?,?,?)", (lot_id, now, price, 1))
    else:
        cur = con.execute(
            "INSERT INTO lots(source,source_id,url,title,price_rub,section,seller,"
            "first_seen,last_seen,is_active) VALUES (?,?,?,?,?,?,?,?,?,1)",
            (source, str(source_id), url, title, price, section, seller, now, now))
        lot_id = cur.lastrowid
        con.execute("INSERT INTO observations VALUES (?,?,?,?)", (lot_id, now, price, 1))
    con.commit()
    return lot_id

def save_features(con, lot_id: int, coin) -> None:
    d = coin.to_dict() if hasattr(coin, "to_dict") else coin
    con.execute(
        "INSERT OR REPLACE INTO features(lot_id,item_type,year,denomination,metal,weight_g,"
        "mintmaster,grader,grade_label,grade_num,grade_bucket,is_slabbed,is_romanov,is_set,"
        "variety,comp_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (lot_id, d.get("item_type"), d.get("year"), d.get("denomination"), d.get("metal"),
         d.get("weight_g"), d.get("mintmaster"), d.get("grader"), d.get("grade_label"),
         d.get("grade_num"), d.get("grade_bucket"), int(bool(d.get("is_slabbed"))),
         int(bool(d.get("is_romanov"))), int(bool(d.get("is_set"))), d.get("variety"),
         d.get("comp_key") or f'{d.get("year")}|{d.get("denomination")}|{d.get("metal")}'))
    con.commit()

def add_comp(con, *, comp_key, source, title, url, price_rub, grader=None,
             grade_num=None, grade_bucket=None, is_slabbed=False, note="") -> None:
    con.execute("INSERT INTO comps(comp_key,source,title,url,price_rub,grader,grade_num,"
                "grade_bucket,is_slabbed,seen_at,note) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (comp_key, source, title, url, price_rub, grader, grade_num,
                 grade_bucket, int(bool(is_slabbed)), time.time(), note))
    con.commit()

def comps_for(con, comp_key: str) -> list[sqlite3.Row]:
    return list(con.execute("SELECT * FROM comps WHERE comp_key=?", (comp_key,)))

def mark_missing_inactive(con, source: str, seen_ids: set[str]) -> int:
    """Лоты, которых больше нет в выдаче, помечаем неактивными — это прокси 'продано'."""
    rows = con.execute("SELECT id, source_id FROM lots WHERE source=? AND is_active=1",
                       (source,)).fetchall()
    n = 0
    now = time.time()
    for r in rows:
        if str(r["source_id"]) not in seen_ids:
            con.execute("UPDATE lots SET is_active=0 WHERE id=?", (r["id"],))
            con.execute("INSERT INTO observations VALUES (?,?,?,?)", (r["id"], now, None, 0))
            n += 1
    con.commit()
    return n

def save_opportunity(con, lot_id, buy_price, expected_sale, slab_cost, net_profit,
                     roi_pct, confidence, reason, payload: dict) -> None:
    con.execute("INSERT INTO opportunities(lot_id,created_at,buy_price,expected_sale,"
                "slab_cost,net_profit,roi_pct,confidence,reason,payload) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (lot_id, time.time(), buy_price, expected_sale, slab_cost, net_profit,
                 roi_pct, confidence, reason, json.dumps(payload, ensure_ascii=False)))
    con.commit()
