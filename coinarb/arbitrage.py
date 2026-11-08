"""Движок арбитража: несколько стратегий поверх одной базы данных.

Стратегии:
  slab_arb       «сырая монета → слаб NGS → продажа» — классическая схема
                 проекта (analyze.screen) с comps из сделок, базы и листингов;
  cross_source   одна и та же монета на двух площадках по разным ценам:
                 купить на дешёвой, продать на дорогой (без слабирования);
  slab_flip      монета уже в признанном слабе (NGC/PCGS/ННР), но выставлена
                 ниже медианы сопоставимых слабированных цен — перепродажа как есть;
  underpriced    информационный сигнал: лот заметно ниже медианы типа по базе
                 сделок/листингов («ошибки продавцов» — самый быстрый арбитраж).

Все стратегии возвращают карточки единой формы и складываются в opportunities
с полем strategy — в отчёте видно, ЧТО именно сработало.
"""
from __future__ import annotations
import statistics
from dataclasses import replace

from . import analyze, fees
from .analyze import Comp, grader_tier, is_comparable
from .normalize import Coin


def _common(strategy: str, coin: Coin, buy_price: float, expected_sale: float,
            econ, conf: float, basis: str, lot_id=None, url=None, extra=None) -> dict:
    return {"strategy": strategy, "coin": coin, "buy_price": buy_price,
            "expected_sale": expected_sale, "economics": econ, "confidence": conf,
            "basis": basis, "score": round(econ.net_profit * conf, 2),
            "lot_id": lot_id, "url": url, "extra": extra or {}}


# ---------------------------------------------------------------- slab_arb
def slab_arb(coins, prices, ids, meta, comps_db, deals, terms,
             min_profit, min_roi) -> list[dict]:
    """Сырая монета → NGS → продажа в слабе. Работает на текущих листингах."""
    out = []
    for c in coins:
        if not is_comparable(c) or not c.key():
            continue
        analyze.enrich(c)
        price = prices.get(id(c))
        if not price:
            continue
        comps = list(comps_db.get(c.key(), []))
        comps += analyze.comps_from_deals(c, deals)
        comps += analyze.comps_from_pool(c, coins, prices)
        res = analyze.screen(price, c, comps, terms, min_profit, min_roi)
        if res:
            m = meta.get(id(c), {})
            out.append(_common("slab_arb", c, res["buy_price"], res["expected_sale"],
                               res["economics"], res["confidence"], res["basis"],
                               ids.get(id(c)), m.get("url")))
    return out


# ---------------------------------------------------------------- cross_source
def cross_source(coins, prices, ids, meta, terms, min_profit, min_roi,
                 min_gap_pct: float = 12.0) -> list[dict]:
    """Один тип монеты на разных площадках по разным ценам.

    Считаем честную экономику перепродажи: покупка по ask площадки A, продажа
    по ask площадки B минус комиссия площадки и логистика, без слабирования.
    Требует >=2 источников в базе (SM + Мешок). Пока источник один — молча пусто.
    """
    out = []
    groups: dict[tuple, list] = {}
    for c in coins:
        if not is_comparable(c) or not c.key():
            continue
        p = prices.get(id(c))
        if not p:
            continue
        bucket = c.grade_num // 5 if c.grade_num else -1   # окно грейда ±4
        groups.setdefault((c.key(), bucket, bool(c.is_slabbed)), []).append(c)

    flip_terms = replace(terms, slab_tariff="none")
    for (_key, _b, _s), members in groups.items():
        srcs = {meta[id(c)]["source"] for c in members if id(c) in meta}
        if len(srcs) < 2:
            continue
        members.sort(key=lambda c: prices[id(c)])
        buy_c, sell_c = members[0], members[-1]
        buy_p, sell_p = prices[id(buy_c)], prices[id(sell_c)]
        if buy_p <= 0:
            continue
        gap_pct = 100 * (sell_p - buy_p) / buy_p
        if gap_pct < min_gap_pct:
            continue
        econ = fees.compute(buy_p, sell_p, flip_terms, market_value_hint=sell_p)
        if econ.net_profit < min_profit or econ.roi_pct < min_roi:
            continue
        n = len(members)
        conf = min(0.9, 0.4 + 0.1 * n)
        basis = (f"{meta[id(buy_c)]['source']} {buy_p:,.0f}₽ → "
                 f"{meta[id(sell_c)]['source']} {sell_p:,.0f}₽, разрыв {gap_pct:.0f}%, "
                 f"{n} листингов типа")
        out.append(_common("cross_source", buy_c, buy_p, sell_p, econ, conf, basis,
                           ids.get(id(buy_c)), meta.get(id(buy_c), {}).get("url"),
                           {"sell_lot_id": ids.get(id(sell_c)),
                            "sell_url": meta.get(id(sell_c), {}).get("url"),
                            "sell_title": sell_c.title}))
    return out


# ---------------------------------------------------------------- slab_flip
def slab_flip(coins, prices, ids, meta, comps_db, deals, terms,
              min_profit, min_roi, min_margin_pct: float = 10.0) -> list[dict]:
    """Признанный слаб ниже рынка: купить и перепродать без переоформления.

    Вход: NGC/PCGS/ННР-слаб, чей ask ниже медианы сделок/comps того же типа
    и грейда на min_margin_pct и больше. Продажа — через Мешок (комиссия 3%).
    """
    out = []
    flip_terms = replace(terms, slab_tariff="none")
    for c in coins:
        if not is_comparable(c) or not c.key():
            continue
        if not (c.is_slabbed and grader_tier(c.grader) in (1, 2)):
            continue
        price = prices.get(id(c))
        if not price:
            continue
        comps = [x for x in comps_db.get(c.key(), []) if x.is_slabbed]
        comps += analyze.comps_from_deals(c, deals)
        comps = [x for x in comps if x.is_slabbed]
        if c.grade_num:
            near = [x for x in comps if x.grade_num and abs(x.grade_num - c.grade_num) <= 2]
            comps = near or comps
        if len(comps) < 2:
            continue
        med = statistics.median([x.price for x in comps])
        if med <= 0 or (med - price) / med * 100 < min_margin_pct:
            continue
        econ = fees.compute(price, med, flip_terms, market_value_hint=med)
        if econ.net_profit < min_profit or econ.roi_pct < min_roi:
            continue
        n_deal = sum(1 for x in comps if x.origin == "deal")
        conf = min(0.85, 0.3 + 0.12 * len(comps) + 0.1 * n_deal)
        basis = (f"ask {price:,.0f}₽ против медианы {med:,.0f}₽ "
                 f"({len(comps)} comps, {n_deal} сделок) того же грейдера/грейда")
        out.append(_common("slab_flip", c, price, med, econ, conf, basis,
                           ids.get(id(c)), meta.get(id(c), {}).get("url")))
    return out


# ---------------------------------------------------------------- underpriced
def underpriced(coins, prices, ids, meta, comps_db, deals,
                min_gap_pct: float = 20.0, min_comps: int = 3) -> list[dict]:
    """Информационный сигнал: лот ниже медианы рынка типа на min_gap_pct и более.

    Это НЕ готовая сделка (не считаем экономику без сценария продажи), а «радар
    ошибок продавца»: такие лоты разбирают вручную за минуты. Карточка попадает
    в отчёт отдельной секцией и в opportunities с net_profit=0.
    """
    out = []
    for c in coins:
        if not is_comparable(c) or not c.key():
            continue
        price = prices.get(id(c))
        if not price:
            continue
        comps = list(comps_db.get(c.key(), [])) + analyze.comps_from_deals(c, deals)
        if c.is_slabbed:
            comps = [x for x in comps if x.is_slabbed]
        else:
            comps = [x for x in comps if not x.is_slabbed] or comps
        if len(comps) < min_comps:
            continue
        med = statistics.median([x.price for x in comps])
        if med <= 0 or (med - price) / med * 100 < min_gap_pct:
            continue
        n_deal = sum(1 for x in comps if x.origin == "deal")
        conf = min(0.8, 0.25 + 0.1 * len(comps) + 0.1 * n_deal)
        basis = f"ask {price:,.0f}₽ vs медиана рынка {med:,.0f}₽ ({len(comps)} comps)"
        econ = fees.DealEconomics(buy_price=price, slab_cost=0, tariff_name="—",
                                  ship_cost=0, net_sale_price=med, gross_profit=med - price,
                                  net_profit=0.0, margin_pct=0.0, roi_pct=0.0,
                                  breakeven_sale_price=price)
        out.append(_common("underpriced", c, price, med, econ, conf, basis,
                           ids.get(id(c)), meta.get(id(c), {}).get("url"),
                           {"gap_pct": round((med - price) / med * 100, 1)}))
    out.sort(key=lambda r: -r["extra"].get("gap_pct", 0))
    return out


# ---------------------------------------------------------------- свод
def find_all(con, terms: fees.DealTerms, min_profit: float = 5000,
             min_roi: float = 25) -> list[dict]:
    """Прогоняет все стратегии по текущей базе и возвращает общий список."""
    from . import comps_io
    coins, prices, ids, meta = comps_io.load_pool(con)
    for c in coins:
        analyze.enrich(c)
    comps_db = comps_io.load_comps_by_key(con)
    deals = comps_io.load_deals_by_key(con)

    results = []
    results += slab_arb(coins, prices, ids, meta, comps_db, deals, terms,
                        min_profit, min_roi)
    results += cross_source(coins, prices, ids, meta, terms, min_profit, min_roi)
    results += slab_flip(coins, prices, ids, meta, comps_db, deals, terms,
                         min_profit, min_roi)
    results += underpriced(coins, prices, ids, meta, comps_db, deals)
    results.sort(key=lambda r: -r["score"])
    return results
