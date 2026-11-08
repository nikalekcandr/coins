"""Аналитика: comps, оценка потенциала, ранжирование возможностей.

Ключевой принцип: цена "в слабе именитого грейдера" != цена "в слабе NGS".
Ожидаемая цена продажи = медиана comps * ngs_discount, где ngs_discount
не выдуман, а настраивается и калибруется по реальным данным.

Иерархия достоверности comps (учтена в robust_price и confidence):
  deal  — факт продажи (архив аукциона, завершённый лот Мешка, vanished-proxy);
  db    — справочная цена из comps_seed / ручной загрузки (может быть DEMO);
  pool  — текущий ask другой площадки: цена «хотелка», а не сделка.
"""
from __future__ import annotations
import statistics
from dataclasses import dataclass

from . import fees, reference
from .normalize import Coin, NON_COMPARABLE_VARIETIES

TIER1 = {"NGC", "PCGS"}                                    # международные
TIER2 = {"ННР", "NGS"}                                     # российские признанные
TIER3 = {"ANACS", "RNGA", "NRG", "CPRC", "Знакъ", "PMG", "ZG", "PG"}  # прочие

def grader_tier(g: str | None) -> int:
    if g in TIER1: return 1
    if g in TIER2: return 2
    if g: return 3
    return 0

@dataclass
class Comp:
    price: float
    grader: str | None = None
    grade_num: int | None = None
    is_slabbed: bool = False
    origin: str = "pool"        # deal | db | pool

ORIGIN_RANK = {"deal": 0, "db": 1, "pool": 2}

def enrich(coin: Coin) -> Coin:
    """Дополняет металл/вес из справочника, если продавец их не указал.

    Только для монет: банкноте «50 рублей 1997» справочник иначе припишет Ni.
    """
    if coin.item_type != "coin":
        return coin
    if not coin.metal or not coin.weight_g:
        metal, weight = reference.infer(coin.title, coin.year, coin.denomination,
                                        coin.weight_g)
        coin.metal = coin.metal or metal
        coin.weight_g = coin.weight_g or weight
    return coin

def is_comparable(coin: Coin) -> bool:
    """Монета, пригодная для сопоставления цен: не книга, не набор, не брак/заготовка."""
    if coin.item_type != "coin" or coin.is_set: return False
    if not coin.year or not coin.denomination: return False
    if coin.variety and any(v in coin.variety for v in NON_COMPARABLE_VARIETIES):
        return False
    return True

def comps_from_pool(target: Coin, pool: list[Coin], prices: dict[int, float],
                    grade_window: int = 4) -> list[Comp]:
    """Сопоставимые лоты из текущей выдачи: тот же год+номинал+металл, близкий грейд."""
    out = []
    if not is_comparable(target): return []
    for c in pool:
        if c is target: continue
        if not is_comparable(c): continue
        if c.year != target.year or c.denomination != target.denomination: continue
        if target.metal and c.metal and c.metal != target.metal: continue
        if target.grade_num and c.grade_num and abs(c.grade_num - target.grade_num) > grade_window:
            # словесный грейд сырой монеты (XF≈42) — грубая оценка; резать по нему
            # слабовые comps нельзя: для сырой цели именно слабовые цены и нужны
            if not (not target.is_slabbed and c.is_slabbed):
                continue
        p = prices.get(id(c))
        if p: out.append(Comp(p, c.grader, c.grade_num, c.is_slabbed, "pool"))
    return out

def comps_from_deals(target: Coin, deals_by_key: dict[str, list[dict]],
                     grade_window: int = 4) -> list[Comp]:
    """Факты продаж того же типа из базы сделок."""
    out = []
    key = target.key()
    if not key: return []
    for d in deals_by_key.get(key, []):
        if target.metal and d.get("metal") and d["metal"] != target.metal: continue
        gn = d.get("grade_num")
        # окно грейда: у сырой монеты грейд — словесная оценка продавца (XF≈42),
        # поэтому слабовые сделки не отсекаем: для сырой цели это цены ПОСЛЕ слаба.
        # Для грейдированной цели — только близкие грейды, чтобы не смешивать MS62 и VF30.
        if target.grade_num and gn and abs(gn - target.grade_num) > grade_window:
            if not (not target.is_slabbed and d.get("is_slabbed")):
                continue
        out.append(Comp(d["price_rub"], d.get("grader"), gn,
                        bool(d.get("is_slabbed")), "deal"))
    return out

def robust_price(comps: list[Comp], prefer_slabbed: bool = True) -> tuple[float | None, int]:
    """Медиана по наиболее достоверному слою comps: deal > db > pool, slab > raw."""
    if not comps: return None, 0
    best = sorted(comps, key=lambda c: ORIGIN_RANK.get(c.origin, 9))
    top_rank = ORIGIN_RANK.get(best[0].origin, 9)
    vals_all = [c for c in best if ORIGIN_RANK.get(c.origin, 9) == top_rank]
    vals = [c.price for c in vals_all]
    if prefer_slabbed:
        slab = [c.price for c in vals_all if c.is_slabbed]
        if len(slab) >= 2: vals = slab
    if not vals:
        vals = [c.price for c in comps]
    return statistics.median(vals), len(vals)

def estimate_sale_price(comps: list[Comp], target: Coin,
                        terms: fees.DealTerms) -> tuple[float | None, str, float]:
    """Ожидаемая цена продажи слабированной монеты + текстовое обоснование + уверенность."""
    med, n = robust_price(comps)
    if med is None:
        return None, "нет comps", 0.0

    tiers = [grader_tier(c.grader) for c in comps if c.is_slabbed]
    tier_mode = statistics.mode(tiers) if tiers else 0
    n_deal = sum(1 for c in comps if c.origin == "deal")
    n_db = sum(1 for c in comps if c.origin == "db")
    deal_share = n_deal / len(comps)
    db_share = n_db / len(comps)

    # comps в топовых/признанных слабах -> наш слаб NGS котируется ниже
    discount = terms.ngs_discount if tier_mode in (1, 2) else 1.0
    est = med * (1 + terms.grade_uplift) * discount

    conf = min(1.0, 0.25 + 0.15 * n)
    conf *= 0.7 + 0.3 * deal_share + 0.15 * db_share   # факты продаж достовернее asks
    conf = min(conf, 1.0)
    if tier_mode == 0: conf *= 0.8          # сравнение с «сырыми» — грубее
    if target.metal is None: conf *= 0.85
    basis = (f"{n} comps ({n_deal} сделок, {n_db} из базы), tier {tier_mode}, "
             f"скидка NGS {discount:.2f}")
    return round(est, 2), basis, round(conf, 3)

def screen(raw_price: float, coin: Coin, comps: list[Comp], terms: fees.DealTerms,
           min_profit: float = 5000, min_roi: float = 25,
           min_confidence: float = 0.3) -> dict | None:
    """Проверяет лот на арбитраж «сырая → слаб NGS». Карточка возможности или None."""
    enrich(coin)
    if not is_comparable(coin): return None
    if not raw_price or raw_price <= 0: return None
    # уже в признанном слабе — не наш вход (нет прироста от слабирования)
    if coin.is_slabbed and grader_tier(coin.grader) in (1, 2): return None

    est, basis, conf = estimate_sale_price(comps, coin, terms)
    if not est or conf < min_confidence: return None
    econ = fees.compute(raw_price, est, terms, market_value_hint=est)
    if econ.net_profit < min_profit or econ.roi_pct < min_roi: return None
    return {"coin": coin, "buy_price": raw_price, "expected_sale": est, "basis": basis,
            "confidence": conf, "economics": econ, "strategy": "slab_arb",
            "score": round(econ.net_profit * conf, 2)}

def watchlist(coins: list[Coin], prices: dict[int, float], ids: dict[int, int],
              db_by_key: dict[str, list[Comp]], terms: fees.DealTerms,
              deals_by_key: dict[str, list[dict]] | None = None) -> list[dict]:
    """Все лоты, для которых есть аналоги — даже если порог прибыли не пройден.

    Нужен, чтобы видеть картину рынка целиком, а не только «прошло/не прошло».
    """
    out = []
    for c in coins:
        key = c.key()
        if not key: continue
        enrich(c)
        if not is_comparable(c): continue
        price = prices.get(id(c))
        if not price: continue
        comps = list(db_by_key.get(key, []))
        if deals_by_key:
            comps += comps_from_deals(c, deals_by_key)
        comps += comps_from_pool(c, coins, prices)
        if not comps: continue
        est, basis, conf = estimate_sale_price(comps, c, terms)
        if not est: continue
        econ = fees.compute(price, est, terms, market_value_hint=est)
        out.append({"coin": c, "buy_price": price, "expected_sale": est,
                    "economics": econ, "confidence": conf, "basis": basis,
                    "lot_id": ids.get(id(c)), "comps": comps,
                    "already_slabbed": c.is_slabbed and grader_tier(c.grader) in (1, 2)})
    out.sort(key=lambda r: -r["economics"].net_profit)
    return out


def calibrate_ngs_discount(pairs: list[tuple[float, float]]) -> dict:
    """Скидка NGS к старшим грейдерам по реальным парам (цена_NGS, цена_эталона)."""
    ratios = [a / b for a, b in pairs if b > 0]
    if not ratios: return {"n": 0, "discount": None}
    return {"n": len(ratios), "discount": round(statistics.median(ratios), 3),
            "min": round(min(ratios), 3), "max": round(max(ratios), 3)}


def grader_pairs_from_db(con, include_demo: bool = False) -> list[dict]:
    """Ищет в БД пары «та же монета, близкий грейд, разные грейдеры» — сырьё для
    калибровки ngs_discount. NGS против NGC/ННР — цель; прочие пары — контекст.

    ratio = цена младшего грейдера / цена старшего — это и есть «скидка» одного
    слаба к другому. DEMO-comps по умолчанию исключаются: калибровать гипотезу
    по иллюстративным данным — самообман.
    """
    demo_filter = "" if include_demo else " AND source != 'DEMO'"
    rows = con.execute(f"""
        SELECT comp_key, grader, grade_num, AVG(price_rub) avg_price, COUNT(*) n
        FROM (
            SELECT comp_key, grader, grade_num, price_rub FROM deals
            WHERE grader IS NOT NULL AND grade_num IS NOT NULL AND is_slabbed=1
            UNION ALL
            SELECT comp_key, grader, grade_num, price_rub FROM comps
            WHERE grader IS NOT NULL AND grade_num IS NOT NULL AND is_slabbed=1{demo_filter}
            UNION ALL
            SELECT f.comp_key, f.grader, f.grade_num, l.price_rub
            FROM features f JOIN lots l ON l.id = f.lot_id
            WHERE f.is_slabbed=1 AND f.grader IS NOT NULL AND f.grade_num IS NOT NULL
              AND l.is_active=1 AND f.item_type='coin'
        )
        WHERE comp_key IS NOT NULL AND comp_key != ''
        GROUP BY comp_key, grader, grade_num
    """).fetchall()

    by_key: dict[str, dict[tuple, dict]] = {}
    for r in rows:
        by_key.setdefault(r["comp_key"], {})[(r["grader"], r["grade_num"])] = {
            "avg": r["avg_price"], "n": r["n"]}

    pairs = []
    for key, graders in by_key.items():
        items = list(graders.items())
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                (g1, gr1), v1 = items[i]
                (g2, gr2), v2 = items[j]
                if g1 == g2 or abs(gr1 - gr2) > 2:
                    continue       # близкие грейды, иначе сравнение неверно
                # старшинство: меньший tier = эталон; внутри одного tier NGS всегда
                # «измеряемая» сторона (ради неё калибровка и затеяна), поэтому
                # сортируется ПОСЛЕ прочих грейдеров своего tier
                def _sen(g): return (grader_tier(g), 1 if g == "NGS" else 0, g)
                if _sen(g1) <= _sen(g2):
                    (gh, grh), vh = (g1, gr1), v1
                    (gl, grl), vl = (g2, gr2), v2
                else:
                    (gh, grh), vh = (g2, gr2), v2
                    (gl, grl), vl = (g1, gr1), v1
                if not vh["avg"]:
                    continue
                pairs.append({
                    "comp_key": key, "grade": f"{grh}/{grl}",
                    "grader_a": gl, "price_a": vl["avg"],   # младший
                    "grader_b": gh, "price_b": vh["avg"],   # старший (эталон)
                    "ratio": round(vl["avg"] / vh["avg"], 3),
                    "n": vl["n"] + vh["n"]})
    pairs.sort(key=lambda p: -p["n"])
    return pairs
