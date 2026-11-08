"""Хранилище на SQLite: история цен, наблюдения, сделки, кандидаты в арбитраж.

Именно накопление истории — главное преимущество перед теми, кто смотрит руками.
Первый запуск даёт срез; к пятому запуску вы видите динамику, скорость продаж
и реальный spread, а не догадку о нём.

Таблица `deals` — ядро «базы данных сделок». Наполняется четырьмя способами:
  1. vanished-proxy — лот исчез из выдачи между прогонами (прокси «продано»);
  2. ended-listing  — завершённые аукционы Мешка с итоговой ценой;
  3. auction-archive — архивы аукционных домов (Конрос, Волмар, Редкие Монеты…)
     через универсальный импорт sources/sold.py;
  4. manual / own-deal — ручная загрузка CSV/JSON, в т.ч. свои сделки.
"""
from __future__ import annotations
import hashlib
import json
import sqlite3
import time
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "coins.db"
DB.parent.mkdir(parents=True, exist_ok=True)

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
CREATE TABLE IF NOT EXISTS comps (             -- справочные цены (включая оценки/демо)
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    comp_key TEXT, source TEXT, title TEXT, url TEXT,
    price_rub REAL, grader TEXT, grade_num INTEGER, grade_bucket TEXT,
    is_slabbed INTEGER, seen_at REAL, note TEXT
);
CREATE TABLE IF NOT EXISTS deals (             -- БАЗА СДЕЛОК: факты продаж
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dedup_key TEXT UNIQUE,
    lot_id INTEGER,                  -- связь с lots, если это «исчезнувший» лот
    comp_key TEXT, source TEXT,      -- vanished-proxy:staraya-moneta | meshok-ended | conros | wolmar | manual | own
    title TEXT, url TEXT,
    price_rub REAL, grader TEXT, grade_num INTEGER, grade_bucket TEXT,
    is_slabbed INTEGER, item_type TEXT,
    sold_at REAL,                    -- момент продажи (epoch) или дата архива
    method TEXT,                     -- vanished-proxy | ended-listing | auction-archive | manual | own-deal
    seller TEXT, note TEXT, created_at REAL
);
CREATE TABLE IF NOT EXISTS opportunities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lot_id INTEGER, created_at REAL,
    buy_price REAL, expected_sale REAL, slab_cost REAL, net_profit REAL,
    roi_pct REAL, confidence REAL, reason TEXT, payload TEXT,
    status TEXT DEFAULT 'new',
    strategy TEXT DEFAULT 'slab_arb'
);
CREATE INDEX IF NOT EXISTS ix_comps_key ON comps(comp_key);
CREATE INDEX IF NOT EXISTS ix_deals_key ON deals(comp_key);
CREATE INDEX IF NOT EXISTS ix_obs_lot ON observations(lot_id);
CREATE INDEX IF NOT EXISTS ix_feat_key ON features(comp_key);
"""


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    con = sqlite3.connect(str(path or DB))
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    _migrate(con)
    return con


def _migrate(con: sqlite3.Connection) -> None:
    """Мягкие миграции для баз, созданных предыдущими версиями схемы."""
    cols = {r["name"] for r in con.execute("PRAGMA table_info(opportunities)")}
    if "strategy" not in cols:
        con.execute("ALTER TABLE opportunities ADD COLUMN strategy TEXT DEFAULT 'slab_arb'")
    con.commit()


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


def renormalize(con, parse_fn) -> int:
    """Пересчитать features всех лотов текущим парсером (после апгрейда normalize)."""
    n = 0
    for row in con.execute("SELECT id, title, section FROM lots"):
        coin = parse_fn(row["title"], row["section"] or None)
        save_features(con, row["id"], coin)
        n += 1
    return n


def add_comp(con, *, comp_key, source, title, url, price_rub, grader=None,
             grade_num=None, grade_bucket=None, is_slabbed=False, note="") -> None:
    con.execute("INSERT INTO comps(comp_key,source,title,url,price_rub,grader,grade_num,"
                "grade_bucket,is_slabbed,seen_at,note) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (comp_key, source, title, url, price_rub, grader, grade_num,
                 grade_bucket, int(bool(is_slabbed)), time.time(), note))
    con.commit()


def comps_for(con, comp_key: str) -> list[sqlite3.Row]:
    return list(con.execute("SELECT * FROM comps WHERE comp_key=?", (comp_key,)))


def _dedup_key(*parts) -> str:
    return hashlib.sha1("|".join(str(p or "") for p in parts).encode()).hexdigest()


def record_deal(con, *, title: str, price_rub: float, source: str, method: str,
                comp_key: str | None = None, url: str = "", grader: str | None = None,
                grade_num: int | None = None, grade_bucket: str | None = None,
                is_slabbed: bool = False, item_type: str = "coin",
                sold_at: float | None = None, seller: str = "", note: str = "",
                lot_id: int | None = None) -> bool:
    """Записать факт сделки. Возвращает False, если такая уже есть (дедупликация).

    Дедупликация по (title, price, sold_at, url) БЕЗ source/method: одна и та же
    сделка, загруженная и из CSV, и из HTML архива, не должна считаться дважды.
    """
    key = _dedup_key(title, price_rub, sold_at, url)
    try:
        con.execute(
            "INSERT INTO deals(dedup_key,lot_id,comp_key,source,title,url,price_rub,grader,"
            "grade_num,grade_bucket,is_slabbed,item_type,sold_at,method,seller,note,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (key, lot_id, comp_key, source, title, url, price_rub, grader, grade_num,
             grade_bucket, int(bool(is_slabbed)), item_type, sold_at or time.time(),
             method, seller, note, time.time()))
        con.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def deals_by_key(con) -> dict[str, list[sqlite3.Row]]:
    out: dict[str, list[sqlite3.Row]] = {}
    for row in con.execute("SELECT * FROM deals WHERE comp_key IS NOT NULL AND comp_key != ''"):
        out.setdefault(row["comp_key"], []).append(row)
    return out


def mark_missing_inactive(con, source: str, seen_ids: set[str],
                          record_vanished: bool = True) -> int:
    """Лоты, которых больше нет в выдаче, помечаем неактивными — это прокси 'продано'.

    При record_vanished каждый такой лот попадает в базу сделок (method=vanished-proxy):
    последняя известная цена — нижняя граница цены сделки (лот могли и снять).
    """
    rows = con.execute(
        "SELECT l.id, l.source_id, l.title, l.price_rub, l.url, l.seller, "
        "f.comp_key, f.grader, f.grade_num, f.grade_bucket, f.is_slabbed, f.item_type "
        "FROM lots l LEFT JOIN features f ON f.lot_id = l.id "
        "WHERE l.source=? AND l.is_active=1", (source,)).fetchall()
    n = 0
    now = time.time()
    for r in rows:
        if str(r["source_id"]) not in seen_ids:
            con.execute("UPDATE lots SET is_active=0 WHERE id=?", (r["id"],))
            con.execute("INSERT INTO observations VALUES (?,?,?,?)", (r["id"], now, None, 0))
            if record_vanished and r["price_rub"]:
                record_deal(con, title=r["title"] or "", price_rub=r["price_rub"],
                            source=f"vanished-proxy:{source}", method="vanished-proxy",
                            comp_key=r["comp_key"], url=r["url"] or "",
                            grader=r["grader"], grade_num=r["grade_num"],
                            grade_bucket=r["grade_bucket"],
                            is_slabbed=bool(r["is_slabbed"]),
                            item_type=r["item_type"] or "coin",
                            sold_at=now, seller=r["seller"] or "",
                            note="исчез из выдачи — прокси «продано», цена = последний ask",
                            lot_id=r["id"])
            n += 1
    con.commit()
    return n


def save_opportunity(con, lot_id, buy_price, expected_sale, slab_cost, net_profit,
                     roi_pct, confidence, reason, payload: dict,
                     strategy: str = "slab_arb") -> int:
    cur = con.execute(
        "INSERT INTO opportunities(lot_id,created_at,buy_price,expected_sale,"
        "slab_cost,net_profit,roi_pct,confidence,reason,payload,strategy) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (lot_id, time.time(), buy_price, expected_sale, slab_cost, net_profit,
         roi_pct, confidence, reason, json.dumps(payload, ensure_ascii=False),
         strategy))
    con.commit()
    return cur.lastrowid
