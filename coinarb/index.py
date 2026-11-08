"""Ценовой индекс по типам монет: сырьё для поиска арбитража и оценки рынка.

Для каждого comp_key (год|номинал|металл) собирает:
  - листинги: сколько активных, min/медиана/max ask, отдельно сырые и слабы;
  - сделки: min/медиана/max фактических цен продажи (из таблицы deals);
  - slab_premium: во сколько раз слабированный ask выше сырого — это и есть
    «приз» схемы «сырая монета → слаб», измеренный рынком, а не допущением;
  - ликвидность: сколько дней лоты типа висят в листинге (прокси скорости продаж);
  - тренд: изменение цены между первым и последним наблюдением;
  - grader_spread: разброс цен между грейдером X и грейдером Y на том же грейте
    (сырьё для калибровки ngs_discount).

Один прогон даёт срез; регулярные прогоны превращают его в историю.
"""
from __future__ import annotations
import csv
import statistics
from pathlib import Path

from . import comps_io
from .analyze import is_comparable

DAY = 86400.0


def _median(xs):
    return round(statistics.median(xs), 2) if xs else None


def build_index(con, min_listings: int = 1) -> list[dict]:
    coins, prices, ids, meta = comps_io.load_pool(con)
    deals = comps_io.load_deals_by_key(con)

    groups: dict[str, list] = {}
    for c in coins:
        if not is_comparable(c):     # только монеты, которые реально сопоставимы
            continue
        key = c.key()
        if not key:
            continue
        p = prices.get(id(c))
        if not p:
            continue
        groups.setdefault(key, []).append((c, p, meta[id(c)]))

    out = []
    for key, members in groups.items():
        year, denom, metal = key.split("|")
        raw = [p for c, p, _ in members if not c.is_slabbed]
        slab = [p for c, p, _ in members if c.is_slabbed]
        asks = [p for _, p, _ in members]
        kd = [d["price_rub"] for d in deals.get(key, [])]

        days = []
        for _, _, m in members:
            if m.get("first_seen") and m.get("last_seen"):
                days.append((m["last_seen"] - m["first_seen"]) / DAY)

        graders = {}
        for c, p, _ in members:
            if c.is_slabbed and c.grader and c.grade_num:
                graders.setdefault((c.grader, c.grade_num), []).append(p)

        slab_premium = None
        if raw and slab:
            slab_premium = round(_median(slab) / _median(raw), 2)

        spread_pct = None
        if len(asks) >= 2 and _median(asks):
            spread_pct = round(100 * (max(asks) - min(asks)) / _median(asks), 1)

        out.append({
            "comp_key": key, "year": year, "denomination": denom,
            "metal": metal if metal != "None" else "",
            "n_listings": len(members), "n_raw": len(raw), "n_slab": len(slab),
            "ask_min": min(asks), "ask_med": _median(asks), "ask_max": max(asks),
            "raw_min": min(raw) if raw else None, "raw_med": _median(raw),
            "slab_min": min(slab) if slab else None, "slab_med": _median(slab),
            "slab_premium": slab_premium, "spread_pct": spread_pct,
            "n_deals": len(kd), "deal_min": min(kd) if kd else None,
            "deal_med": _median(kd), "deal_max": max(kd) if kd else None,
            "days_listed_med": round(_median(days), 1) if days else None,
            "graders": {f"{g} {n}": _median(ps) for (g, n), ps in sorted(graders.items())},
            "titles": [c.title for c, _, _ in members[:3]],
            "sources": sorted({m["source"] for _, _, m in members}),
        })

    out = [r for r in out if r["n_listings"] >= min_listings or r["n_deals"] > 0]
    out.sort(key=lambda r: -(r["slab_premium"] or 0) * 1000 - r["n_listings"])
    return out


def export_csv(index: list[dict], path: str | Path) -> int:
    if not index:
        return 0
    cols = [k for k in index[0] if k not in ("graders", "titles", "sources")]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(cols + ["graders", "sources"])
        for r in index:
            w.writerow([r[c] for c in cols] +
                       [", ".join(f"{g}:{v:.0f}" for g, v in r["graders"].items()),
                        ",".join(r["sources"])])
    return len(index)


def format_table(index: list[dict], limit: int = 25) -> str:
    """Консольная таблица: топ типов по slab_premium (где деньги лежат)."""
    lines = [f"{'тип':<24}{'лист':>5}{'сыр':>4}{'слаб':>5}{'сыр.мед':>10}"
             f"{'слаб.мед':>10}{'премия':>8}{'сделок':>7}{'дн.лист':>8}"]
    shown = 0
    for r in index:
        if r["n_raw"] == 0 and r["n_slab"] == 0:
            continue
        premium = f"{r['slab_premium']}x" if r["slab_premium"] else "—"
        raw_s = f"{r['raw_med']:.0f}" if r["raw_med"] else "—"
        slab_s = f"{r['slab_med']:.0f}" if r["slab_med"] else "—"
        days_s = f"{r['days_listed_med']:.0f}" if r["days_listed_med"] is not None else "—"
        typ = (r["year"] + " " + r["denomination"] + " " + r["metal"])[:23]
        lines.append(
            f"{typ:<24}{r['n_listings']:>5}{r['n_raw']:>4}{r['n_slab']:>5}"
            f"{raw_s:>10}{slab_s:>10}{premium:>8}{r['n_deals']:>7}{days_s:>8}")
        shown += 1
        if shown >= limit:
            break
    if shown == 0:
        lines.append("  (нет типов с листингами — соберите данные: scan.py harvest-sm)")
    return "\n".join(lines)
