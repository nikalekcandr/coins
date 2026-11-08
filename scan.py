#!/usr/bin/env python3
"""CLI: сбор -> нормализация -> БД сделок -> аналитика -> отчёты.

    python scan.py harvest-sm              собрать лоты staraya-moneta (магазин)
    python scan.py harvest-sm-auction      аукционы staraya-moneta (EXPERIMENTAL)
    python scan.py harvest-meshok          активные лоты meshok (нужны cookies/прокси)
    python scan.py harvest-meshok --sold   ЗАВЕРШЁННЫЕ лоты meshok -> база сделок
    python scan.py import-comps FILE       справочные цены (CSV)
    python scan.py import-sold FILE        факты продаж: CSV/JSON/HTML архив аукциона
    python scan.py import-raw FILE         JSON-снапшот сбора -> в lots
    python scan.py renormalize             пересчитать признаки новым парсером
    python scan.py analyze                 все стратегии арбитража
    python scan.py index                   ценовой индекс по типам (+CSV)
    python scan.py calibrate               пары грейдеров -> калибровка ngs_discount
    python scan.py stats                   сводка по базе
    python scan.py report                  HTML + CSV/JSON отчёты
    python scan.py all                     полный цикл (seed -> сбор -> аналитика -> отчёт)
"""
from __future__ import annotations
import argparse, asyncio, csv, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from coinarb import analyze, arbitrage, comps_io, fees, index as index_mod, report, store
from coinarb.normalize import parse
from coinarb.sources import meshok as meshok_src
from coinarb.sources import sold as sold_src
from coinarb.sources import staraya_moneta as sm_src

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"

STRATEGY_LABELS = {
    "slab_arb": "Сырая монета → слаб NGS → продажа",
    "cross_source": "Кросс-площадочный (купить дешевле на одной, продать на другой)",
    "slab_flip": "Флип слаба (признанный слаб ниже рынка)",
    "underpriced": "Радар заниженных цен (информационный сигнал)",
}


# ------------------------------------------------------------------ сбор
async def harvest_sm(delay_lo=1.2, delay_hi=2.5) -> list[dict]:
    from coinarb.fetch import Fetcher
    all_items = []
    async with Fetcher(cache=False, delay=(delay_lo, delay_hi)) as f:
        for sect in sm_src.SECTIONS:
            url = sm_src.shop_url(sect)
            try:
                html = await f.get(url, wait=2500)
            except Exception as e:
                print(f"  ! раздел {sect}: {e}")
                continue
            items = sm_src.parse_shop(html, sect)
            print(f"  раздел {sect} ({sm_src.SECTIONS[sect]}): {len(items)} лотов")
            all_items += items
    return all_items


async def harvest_sm_auction() -> list[dict]:
    """EXPERIMENTAL: перебирает кандидаты URL аукционной витрины SM."""
    from coinarb.fetch import Fetcher
    async with Fetcher(cache=False, delay=(1.5, 3.0)) as f:
        for url in sm_src.AUCTION_URLS:
            try:
                html = await f.get(url, wait=2500)
            except Exception as e:
                print(f"  ! {url}: {e}")
                continue
            items = sm_src.parse_auction(html)
            print(f"  {url}: {len(items)} лотов")
            if items:
                return items
    return []


async def harvest_meshok(pages: int, section: str, storage_state: str | None,
                         proxy: str | None, query: str | None, sold: bool) -> list[dict]:
    from coinarb.fetch import Fetcher
    items = []
    async with Fetcher(cache=False, delay=(2.0, 4.0), storage_state=storage_state,
                       proxy=proxy) as f:
        for p in range(1, pages + 1):
            if query:
                url = meshok_src.search_url(query, p, sold=sold)
            else:
                url = meshok_src.page_url(section, p,
                                          extra=meshok_src.SOLD_EXTRA if sold else "")
            try:
                html = await f.get(url, wait=3000)
            except Exception as e:
                print(f"  ! meshok стр.{p}: {e}")
                break
            got = (meshok_src.parse_ended(html, section) if sold
                   else meshok_src.parse_listing(html, section))
            print(f"  meshok стр.{p}: {len(got)} лотов" + (" (завершённые)" if sold else ""))
            if not got:
                break
            items += got
    return items


# ------------------------------------------------------------------ запись
def persist(items: list[dict], mark_missing: bool = True) -> dict:
    """Лоты -> lots/features. Завершённые/проданные -> сразу в deals.

    mark_missing=False — для импорта снапшотов, которые могут быть неполными
    (иначе лоты вне снапшота будут ложно помечены «продано»).
    """
    con = store.connect()
    seen: dict[str, set] = {}
    n_deals = 0
    for it in items:
        # совместимость форматов: harvest отдаёт source_id/section, старые
        # снапшоты — item_id/section_name
        it = {**it,
              "source_id": str(it.get("source_id") or it.get("item_id") or ""),
              "section": it.get("section_name") or str(it.get("section") or "")}
        if not it["source_id"]:
            continue
        if it.get("sold"):                      # завершённый аукцион = факт продажи
            if not it.get("price_rub"):
                continue
            coin = analyze.enrich(parse(it["title"], it.get("section")))
            sold_at = it.get("sold_at")
            if not sold_at and it.get("end_date"):
                sold_at = sold_src.parse_date(it["end_date"])
            ok = store.record_deal(
                con, title=it["title"], price_rub=it["price_rub"],
                source=it.get("source", "meshok-ended"), method="ended-listing",
                comp_key=coin.key(), url=it.get("url", ""), grader=coin.grader,
                grade_num=coin.grade_num, grade_bucket=coin.grade_bucket,
                is_slabbed=coin.is_slabbed, item_type=coin.item_type,
                sold_at=sold_at, seller=it.get("seller", ""),
                note=f"bids={it.get('bids')}" if it.get("bids") else "")
            n_deals += 1 if ok else 0
            continue
        if not it.get("price_rub"):
            continue
        lot_id = store.upsert_lot(con, source=it["source"], source_id=it["source_id"],
                                  url=it["url"], title=it["title"], price=it["price_rub"],
                                  section=it.get("section", ""), seller=it.get("seller", ""))
        coin = analyze.enrich(parse(it["title"], it.get("section")))
        store.save_features(con, lot_id, coin)
        seen.setdefault(it["source"], set()).add(str(it["source_id"]))
    gone = ({s: store.mark_missing_inactive(con, s, ids) for s, ids in seen.items()}
            if mark_missing else {})
    return {"saved": len(items), "deals_from_ended": n_deals, "marked_inactive": gone}


def import_raw_json(path: str) -> dict:
    """JSON-снапшот сбора (формат harvest: список карточек) -> БД.

    Снапшот может быть неполным (старый прогон, один раздел), поэтому лоты
    вне снапшота НЕ помечаются проданными.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    items = data if isinstance(data, list) else data.get("items", [])
    return persist(items, mark_missing=False)


# ------------------------------------------------------------------ аналитика
def run_analyze(terms: fees.DealTerms, min_profit: float, min_roi: float,
                con=None) -> list[dict]:
    con = con or store.connect()
    # возможности прошлого прогона со статусом 'new' устаревают; помеченные
    # вручную (taken/rejected/done) сохраняются
    con.execute("DELETE FROM opportunities WHERE status='new'")
    con.commit()
    results = arbitrage.find_all(con, terms, min_profit, min_roi)
    for r in results:
        e = r["economics"]
        r["id"] = store.save_opportunity(
            con, r["lot_id"], r["buy_price"], r["expected_sale"], e.slab_cost,
            e.net_profit, e.roi_pct, r["confidence"],
            f"{e.tariff_name}: {r['basis']}",
            {"coin": r["coin"].to_dict(), "economics": e.__dict__,
             "basis": r["basis"], "extra": r.get("extra", {})},
            strategy=r["strategy"])
    return results


def print_results(results: list[dict]) -> None:
    by_strategy: dict[str, list] = {}
    for r in results:
        by_strategy.setdefault(r["strategy"], []).append(r)
    for strat in ("slab_arb", "cross_source", "slab_flip", "underpriced"):
        rs = by_strategy.get(strat, [])
        print(f"  [{strat}] {len(rs)}")
        for r in rs[:6]:
            e = r["economics"]
            print(f"    #{r.get('id', 0):<4} {r['score']:>9.0f} | net {e.net_profit:>8.0f} "
                  f"| ROI {e.roi_pct:>6.1f}% | {r['coin'].title[:54]}")
    if results:
        print("  Статусы: python scan.py mark <ID> --status taken|rejected|done")


def cmd_mark(opp_id: int, status: str) -> None:
    """Ручной статус возможности: taken (в работе) | rejected | done | new."""
    if status not in ("new", "taken", "rejected", "done"):
        sys.exit("статус: new | taken | rejected | done")
    con = store.connect()
    cur = con.execute("UPDATE opportunities SET status=? WHERE id=?", (status, opp_id))
    con.commit()
    print(f"→ возможность #{opp_id}: {status}" if cur.rowcount else f"→ #{opp_id} не найдена")


def cmd_calc(terms: fees.DealTerms, buy: float, sale: float) -> None:
    """Калькулятор одной сделки: покупка X ₽, продажа Y ₽ — что останется чистыми."""
    e = fees.compute(buy, sale, terms, market_value_hint=sale)
    rows = [
        ("Покупка монеты", -buy),
        (f"Слабирование NGS ({e.tariff_name})", -e.slab_cost),
        ("Логистика (в NGS и покупателю)", -e.ship_cost),
        (f"Продажа на {terms.marketplace}", sale),
        ("Комиссия площадки", -(sale - e.net_sale_price)),
        (f"Резерв на риски ({terms.risk_haircut:.0%})",
         -e.gross_profit * terms.risk_haircut),
    ]
    print("─" * 52)
    for label, v in rows:
        if abs(v) < 1e-9:
            continue
        print(f"  {label:<42}{v:>+10,.0f} ₽".replace(",", " "))
    print("─" * 52)
    print(f"  {'ЧИСТАЯ ПРИБЫЛЬ':<42}{e.net_profit:>+10,.0f} ₽".replace(",", " "))
    print(f"  {'ROI на вложенные деньги':<42}{e.roi_pct:>9.1f} %")
    print(f"  {'Точка безубыточности (цена продажи)':<42}{e.breakeven_sale_price:>10,.0f} ₽"
          .replace(",", " "))


def cmd_demo(terms: fees.DealTerms, min_profit: float, min_roi: float) -> None:
    """Прогон всех стратегий на синтетическом рынке (отдельная БД, DEMO-маркировка)."""
    from coinarb import demo
    DATA.mkdir(exist_ok=True)
    demo_db = DATA / "demo.db"
    if demo_db.exists():
        demo_db.unlink()
    con = demo.build(str(demo_db))
    print(f"→ Демо-БД: {demo_db}")

    results = run_analyze(terms, min_profit, min_roi, con=con)
    print(f"→ Возможностей (демо): {len(results)}")
    print_results(results)

    idx = index_mod.build_index(con)
    print("\n→ Демо-индекс (типы с премией слаба):")
    print(index_mod.format_table(idx, limit=10))

    print("\n→ Демо-калибровка NGS:")
    pairs = analyze.grader_pairs_from_db(con)
    ngs_pairs = [(p["price_a"], p["price_b"]) for p in pairs if p["grader_a"] == "NGS"]
    calib = analyze.calibrate_ngs_discount(ngs_pairs)
    for p in pairs:
        mark = " ← NGS!" if p["grader_a"] == "NGS" else ""
        print(f"    {p['comp_key']:<20} {p['grader_a']:<5}{p['price_a']:>8.0f} / "
              f"{p['grader_b']:<5}{p['price_b']:>8.0f}  ratio {p['ratio']}{mark}")
    if calib["n"]:
        print(f"    измеренная скидка NGS: {calib['discount']} (n={calib['n']})")

    ctx = build_ctx(terms, results, con=con)
    ctx["demo"] = True
    out = DATA / "report_demo.html"
    out.write_text(report.build(ctx), encoding="utf-8")
    print(f"\n→ Демо-отчёт: {out}")
    print("  ВНИМАНИЕ: все цифры в демо — синтетика для демонстрации механики.")


def run_index(min_listings: int) -> list[dict]:
    con = store.connect()
    idx = index_mod.build_index(con, min_listings=min_listings)
    DATA.mkdir(exist_ok=True)
    out = DATA / "price_index.csv"
    index_mod.export_csv(idx, out)
    print(index_mod.format_table(idx))
    print(f"→ {len(idx)} типов, CSV: {out}")
    return idx


def run_calibrate() -> dict:
    con = store.connect()
    pairs = analyze.grader_pairs_from_db(con)          # DEMO-comps исключены
    ngs_pairs = [(p["price_a"], p["price_b"]) for p in pairs if p["grader_a"] == "NGS"]
    calib = analyze.calibrate_ngs_discount(ngs_pairs)
    print(f"→ Пар грейдеров в базе: {len(pairs)} (NGS vs эталон: {len(ngs_pairs)}). "
          f"ratio = цена младшего грейдера / цена старшего")
    for p in pairs[:15]:
        mark = " ← NGS!" if p["grader_a"] == "NGS" else ""
        print(f"    {p['comp_key']:<22} грейд {p['grade']:<8} "
              f"{p['grader_a']:<5} {p['price_a']:>9.0f} / {p['grader_b']:<5} {p['price_b']:>9.0f}"
              f"  ratio {p['ratio']}{mark}")
    if calib["n"]:
        print(f"→ Медианная скидка NGS: {calib['discount']} (n={calib['n']}, "
              f"min {calib['min']}, max {calib['max']})")
        print('  Подставить: python scan.py analyze --terms '
              f'\'{{"ngs_discount":{calib["discount"]}}}\'')
    else:
        print("→ Пар с NGS пока нет: ngs_discount остаётся ГИПОТЕЗОЙ. "
              "Собирайте сделки с NGS-слабами (import-sold / harvest-meshok --sold).")
    return {"pairs": pairs, "ngs": calib}


def run_stats() -> None:
    con = store.connect()
    q = lambda sql, *a: con.execute(sql, a).fetchone()[0]
    print(f"Лоты: всего {q('SELECT COUNT(*) FROM lots')}, "
          f"активных {q('SELECT COUNT(*) FROM lots WHERE is_active=1')}")
    for r in con.execute("SELECT source, COUNT(*) c, SUM(is_active) a FROM lots GROUP BY source"):
        print(f"  {r['source']:<28} {r['c']:>5} лотов ({r['a'] or 0} активных)")
    print("Признаки (item_type):")
    for r in con.execute("SELECT item_type, COUNT(*) c FROM features GROUP BY item_type ORDER BY c DESC"):
        print(f"  {r['item_type'] or '?':<10} {r['c']:>5}")
    demo_n = q("SELECT COUNT(*) FROM comps WHERE source='DEMO'")
    print(f"Comps (справочник): {q('SELECT COUNT(*) FROM comps')} (из них DEMO: {demo_n})")
    print(f"Сделки (deals): {q('SELECT COUNT(*) FROM deals')}")
    for r in con.execute("SELECT method, source, COUNT(*) c FROM deals GROUP BY method, source ORDER BY c DESC"):
        print(f"  {r['method']:<18} {r['source']:<28} {r['c']:>5}")
    print(f"Возможности: {q('SELECT COUNT(*) FROM opportunities')}")
    for r in con.execute("SELECT strategy, COUNT(*) c FROM opportunities GROUP BY strategy"):
        print(f"  {r['strategy']:<15} {r['c']:>5}")


# ------------------------------------------------------------------ отчёты
def write_reports(results: list[dict], terms: fees.DealTerms) -> dict:
    DATA.mkdir(exist_ok=True)
    csv_path, json_path = DATA / "opportunities.csv", DATA / "opportunities.json"
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["id", "strategy", "score", "title", "год", "номинал", "металл", "грейд",
                    "грейдер", "цена_покупки", "ожид_продажа", "тариф_NGS", "слаб_руб",
                    "чистая_прибыль", "ROI_%", "уверенность", "обоснование", "url"])
        for r in results:
            c, e = r["coin"], r["economics"]
            w.writerow([r.get("id", ""), r["strategy"], r["score"], c.title, c.year,
                        c.denomination, c.metal, c.grade_label or "raw", c.grader or "—",
                        r["buy_price"], r["expected_sale"], e.tariff_name, e.slab_cost,
                        e.net_profit, e.roi_pct, r["confidence"], r["basis"], r["url"]])
    json_path.write_text(json.dumps(
        [{"strategy": r["strategy"], "score": r["score"], "title": r["coin"].title,
          "buy": r["buy_price"], "expected": r["expected_sale"],
          "net_profit": r["economics"].net_profit, "roi": r["economics"].roi_pct,
          "conf": r["confidence"], "basis": r["basis"], "url": r["url"],
          "extra": r.get("extra", {}),
          "coin": r["coin"].to_dict(), "econ": r["economics"].__dict__}
         for r in results], ensure_ascii=False, indent=1), encoding="utf-8")
    return {"csv": str(csv_path), "json": str(json_path)}


def build_ctx(terms: fees.DealTerms, results: list[dict], con=None) -> dict:
    """Контекст HTML-отчёта: статистика, индекс, сделки, калибровка, лист наблюдения."""
    con = con or store.connect()
    coins, prices, ids, meta = comps_io.load_pool(con)
    db_by_key = comps_io.load_comps_by_key(con)
    deals_by_key = comps_io.load_deals_by_key(con)

    watch = analyze.watchlist(coins, prices, ids, db_by_key, terms, deals_by_key)

    q = lambda sql: con.execute(sql).fetchone()["c"]
    lots = q("SELECT COUNT(*) c FROM lots")
    coins_n = q("SELECT COUNT(*) c FROM features WHERE item_type='coin'")
    comps_n = q("SELECT COUNT(*) c FROM comps")
    deals_n = q("SELECT COUNT(*) c FROM deals")

    def cov(expr):
        n = con.execute(
            f"SELECT COUNT(*) c FROM features WHERE item_type='coin' AND {expr}").fetchone()["c"]
        return {"n": n, "total": coins_n, "pct": 100 * n / (coins_n or 1)}

    coverage = {
        "Год": cov("year IS NOT NULL"),
        "Номинал": cov("denomination IS NOT NULL AND denomination != ''"),
        "Металл": cov("metal IS NOT NULL"),
        "Грейд (число)": cov("grade_num IS NOT NULL"),
        "В слабе": cov("is_slabbed = 1"),
    }

    idx = index_mod.build_index(con)
    calibration = run_calibrate_quiet(con)

    deals_recent = [dict(r) for r in con.execute(
        "SELECT * FROM deals ORDER BY sold_at DESC LIMIT 12")]
    deals_by_method = [dict(r) for r in con.execute(
        "SELECT method, source, COUNT(*) c FROM deals GROUP BY method, source ORDER BY c DESC")]

    by_strategy: dict[str, list] = {}
    for r in results:
        by_strategy.setdefault(r["strategy"], []).append(r)

    # флагман: Романовский рубль (или первый лот с comps)
    flagship = None
    rmn = [w for w in watch if w["coin"].is_romanov]
    pick = rmn[0] if rmn else (watch[0] if watch else None)
    if pick:
        c, e = pick["coin"], pick["economics"]
        fee = e.net_sale_price - pick["expected_sale"]
        risk = abs(pick["expected_sale"] - e.buy_price - e.slab_cost - e.ship_cost) * terms.risk_haircut
        entry = ("Вход: монета уже в слабе (переслабирование)"
                 if pick["coin"].is_slabbed else "Вход: сырая монета без слаба")
        flagship = {"coin": c, "buy": pick["buy_price"], "entry_label": entry,
                    "tariff": e.tariff_name, "slab": e.slab_cost,
                    "ship": e.ship_cost, "sale": pick["expected_sale"], "fee": abs(fee),
                    "risk": risk, "risk_pct": terms.risk_haircut, "net": e.net_profit,
                    "roi": e.roi_pct, "breakeven": e.breakeven_sale_price,
                    "comps": pick["comps"], "basis": pick["basis"]}

    return {
        "stats": {"lots": lots, "coins": coins_n, "with_comps": len(watch),
                  "comps": comps_n, "deals": deals_n, "index_types": len(idx),
                  "opportunities": len(results),
                  "by_strategy": {k: len(v) for k, v in by_strategy.items()}},
        "watch": watch, "terms": terms.to_dict(),
        "tariffs": fees.NGS_TARIFFS, "coverage": coverage, "flagship": flagship,
        "opportunities": results, "by_strategy": by_strategy,
        "strategy_labels": STRATEGY_LABELS,
        "index_top": idx[:20], "calibration": calibration,
        "deals_recent": deals_recent, "deals_by_method": deals_by_method,
    }


def run_calibrate_quiet(con) -> dict:
    pairs = analyze.grader_pairs_from_db(con)
    ngs_pairs = [(p["price_a"], p["price_b"]) for p in pairs if p["grader_a"] == "NGS"]
    return {"pairs": pairs[:20], "ngs": analyze.calibrate_ngs_discount(ngs_pairs)}


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description="Coinarb: арбитраж монет")
    ap.add_argument("cmd", choices=["harvest-sm", "harvest-sm-auction", "harvest-meshok",
                                    "import-comps", "import-sold", "import-raw",
                                    "renormalize", "analyze", "index", "calibrate",
                                    "stats", "report", "all", "mark", "calc", "demo"])
    ap.add_argument("file", nargs="?")
    ap.add_argument("--status", default="taken", help="для mark: new|taken|rejected|done")
    ap.add_argument("--buy", type=float, help="для calc: цена покупки, ₽")
    ap.add_argument("--sale", type=float, help="для calc: цена продажи, ₽")
    ap.add_argument("--pages", type=int, default=3)
    ap.add_argument("--section", default="252")
    ap.add_argument("--query", help="поиск по Мешку вместо раздела")
    ap.add_argument("--sold", action="store_true", help="завершённые лоты (в базу сделок)")
    ap.add_argument("--source", help="имя источника для import-sold")
    ap.add_argument("--format", help="csv|json|html (иначе по расширению)")
    ap.add_argument("--storage-state")
    ap.add_argument("--proxy")
    ap.add_argument("--min-listings", type=int, default=1)
    ap.add_argument("--terms", help="JSON с переопределением DealTerms")
    ap.add_argument("--min-profit", type=float, default=5000)
    ap.add_argument("--min-roi", type=float, default=25)
    args = ap.parse_args()

    terms = fees.DealTerms()
    if args.terms:
        for k, v in json.loads(args.terms).items():
            if not hasattr(terms, k):
                sys.exit(f"неизвестный параметр DealTerms: {k}")
            setattr(terms, k, v)

    if args.cmd == "all":
        con0 = store.connect()
        empty = con0.execute("SELECT COUNT(*) c FROM comps").fetchone()["c"] == 0
        seed = DATA / "comps_seed.csv"
        if empty and seed.exists():
            print("→ comps пусты, загружаю", seed.name,
                  ":", comps_io.import_csv(con0, seed), "записей")

    def _harvest(coro_fn, label: str, on_fail: str):
        print(label)
        try:
            got = asyncio.run(coro_fn())
        except RuntimeError as e:
            print(f"  ! {on_fail}: {e}")
            return None
        print(persist(got) if got else f"  {on_fail}: 0 лотов")
        return got

    if args.cmd in ("harvest-sm", "all"):
        _harvest(harvest_sm, "→ Сбор staraya-moneta (магазин)", "сбор SM не выполнен")
    if args.cmd == "harvest-sm-auction":
        _harvest(harvest_sm_auction, "→ Сбор staraya-moneta (аукционы, EXPERIMENTAL)",
                 "аукционы (проверьте AUCTION_URLS в sources/staraya_moneta.py)")
    if args.cmd in ("harvest-meshok", "all"):
        _harvest(lambda: harvest_meshok(args.pages, args.section, args.storage_state,
                                        args.proxy, args.query, args.sold),
                 "→ Сбор meshok" + (" (завершённые лоты)" if args.sold else ""),
                 "meshok (Cloudflare / нужны cookies)")
    if args.cmd == "import-comps":
        if not args.file:
            sys.exit("укажите CSV: scan.py import-comps data/comps_seed.csv")
        print("comps загружено:", comps_io.import_csv(store.connect(), args.file))
    if args.cmd == "import-sold":
        if not args.file:
            sys.exit("укажите файл: scan.py import-sold FILE [--source NAME --format FMT]")
        res = sold_src.import_deals_file(store.connect(), args.file, args.source,
                                         args.format)
        print("база сделок:", res)
    if args.cmd == "import-raw":
        if not args.file:
            sys.exit("укажите JSON-снапшот: scan.py import-raw data/raw/sm_raw.json")
        print("снапшот загружен:", import_raw_json(args.file))
    if args.cmd == "renormalize":
        con = store.connect()
        n = store.renormalize(con, lambda t, s: analyze.enrich(parse(t, s)))
        print(f"пересчитано признаков: {n}")
    if args.cmd == "index":
        run_index(args.min_listings)
    if args.cmd == "calibrate":
        run_calibrate()
    if args.cmd == "stats":
        run_stats()
    if args.cmd == "mark":
        if not args.file or not args.file.isdigit():
            sys.exit("укажите ID возможности: scan.py mark 12 --status rejected")
        cmd_mark(int(args.file), args.status)
    if args.cmd == "calc":
        if not args.buy or not args.sale:
            sys.exit("пример: scan.py calc --buy 12000 --sale 43400 [--terms '{...}']")
        cmd_calc(terms, args.buy, args.sale)
    if args.cmd == "demo":
        cmd_demo(terms, args.min_profit, args.min_roi)
    if args.cmd in ("analyze", "all", "report"):
        res = run_analyze(terms, args.min_profit, args.min_roi)
        print(f"→ Возможностей всего: {len(res)}")
        print_results(res)
        if args.cmd in ("report", "all"):
            print("→ Файлы:", write_reports(res, terms))
            ctx = build_ctx(terms, res)
            DATA.mkdir(exist_ok=True)
            out = DATA / "report.html"
            out.write_text(report.build(ctx), encoding="utf-8")
            print("→ HTML:", out, f"({len(ctx['watch'])} лотов с аналогами)")


if __name__ == "__main__":
    main()
