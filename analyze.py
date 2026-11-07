"""Аналитика: comps, оценка потенциала, ранжирование возможностей.

Ключевой принцип: цена "в слабе именитого грейдера" != цена "в слабе NGS".
Ожидаемая цена продажи = медиана comps * ngs_discount, где ngs_discount
не выдуман, а настраивается и калибруется по реальным данным.
"""
from __future__ import annotations
import statistics
from dataclasses import dataclass

from . import fees, reference
from .normalize import Coin

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
    origin: str = "pool"        # pool | db

def enrich(coin: Coin) -> Coin:
    """Дополняет металл/вес из справочника, если продавец их не указал."""
    if not coin.metal or not coin.weight_g:
        metal, weight = reference.infer(coin.title, coin.year, coin.denomination)
        coin.metal = coin.metal or metal
        coin.weight_g = coin.weight_g or weight
    return coin

def comps_from_pool(target: Coin, pool: list[Coin], prices: dict[int, float],
                    grade_window: int = 4) -> list[Comp]:
    """Сопоставимые лоты из текущей выдачи: тот же год+номинал+металл, близкий грейд."""
    out = []
    if not target.year or not target.denomination: return []
    for c in pool:
        if c is target: continue
        if c.item_type != "coin" or c.is_set: continue
        if not c.year or not c.denomination: continue
        if c.year != target.year or c.denomination != target.denomination: continue
        if target.metal and c.metal and c.metal != target.metal: continue
        if target.grade_num and c.grade_num and abs(c.grade_num - target.grade_num) > grade_window:
            continue
        p = prices.get(id(c))
        if p: out.append(Comp(p, c.grader, c.grade_num, c.is_slabbed, "pool"))
    return out

def robust_price(comps: list[Comp], prefer_slabbed: bool = True) -> tuple[float | None, int]:
    if not comps: return None, 0
    vals = [c.price for c in comps]
    if prefer_slabbed:
        slab = [c.price for c in comps if c.is_slabbed]
        if len(slab) >= 2: vals = slab
    return statistics.median(vals), len(vals)

def estimate_sale_price(comps: list[Comp], target: Coin,
                        terms: fees.DealTerms) -> tuple[float | None, str, float]:
    """Ожидаемая цена продажи слабированной монеты + текстовое обоснование + уверенность."""
    med, n = robust_price(comps)
    if med is None:
        return None, "нет comps", 0.0

    tiers = [grader_tier(c.grader) for c in comps if c.is_slabbed]
    tier_mode = statistics.mode(tiers) if tiers else 0
    db_share = sum(1 for c in comps if c.origin == "db") / len(comps)

    # comps в топовых/признанных слабах -> наш слаб NGS котируется ниже
    discount = terms.ngs_discount if tier_mode in (1, 2) else 1.0
    est = med * (1 + terms.grade_uplift) * discount

    conf = min(1.0, 0.25 + 0.15 * n)
    conf *= 0.75 + 0.25 * db_share          # реальные проданные цены достовернее
    if tier_mode == 0: conf *= 0.8          # сравнение с «сырыми» — грубее
    if target.metal is None: conf *= 0.85
    basis = (f"{n} comps, tier {tier_mode}, скидка NGS {discount:.2f}, "
             f"из базы {db_share:.0%}")
    return round(est, 2), basis, round(conf, 3)

def screen(raw_price: float, coin: Coin, comps: list[Comp], terms: fees.DealTerms,
           min_profit: float = 5000, min_roi: float = 25,
           min_confidence: float = 0.3) -> dict | None:
    """Проверяет лот на арбитраж. Возвращает карточку возможности или None."""
    enrich(coin)
    if coin.item_type != "coin" or coin.is_set: return None
    if not coin.year or not coin.denomination: return None   # нечем сопоставлять
    if not raw_price or raw_price <= 0: return None
    # уже в признанном слабе — не наш вход (нет прироста от слабирования)
    if coin.is_slabbed and grader_tier(coin.grader) in (1, 2): return None

    est, basis, conf = estimate_sale_price(comps, coin, terms)
    if not est or conf < min_confidence: return None
    econ = fees.compute(raw_price, est, terms, market_value_hint=est)
    if econ.net_profit < min_profit or econ.roi_pct < min_roi: return None
    return {"coin": coin, "buy_price": raw_price, "expected_sale": est, "basis": basis,
            "confidence": conf, "economics": econ,
            "score": round(econ.net_profit * conf, 2)}

def watchlist(coins: list[Coin], prices: dict[int, float], ids: dict[int, int],
              db_by_key: dict[str, list[Comp]], terms: fees.DealTerms) -> list[dict]:
    """Все лоты, для которых есть аналоги — даже если порог прибыли не пройден.

    Нужен, чтобы видеть картину рынка целиком, а не только «прошло/не прошло».
    """
    out = []
    for c in coins:
        key = c.key()
        if not key: continue
        enrich(c)
        if c.item_type != "coin" or c.is_set: continue
        price = prices.get(id(c))
        if not price: continue
        comps = list(db_by_key.get(key, [])) + comps_from_pool(c, coins, prices)
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
