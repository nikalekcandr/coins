#!/usr/bin/env python3
"""CLI: сбор -> нормализация -> БД -> аналитика -> отчёты.

    python scan.py harvest-sm          # собрать лоты staraya-moneta
    python scan.py harvest-meshok      # собрать лоты meshok (нужны cookies/прокси)
    python scan.py import-comps FILE   # загрузить базу сопоставимых цен
    python scan.py analyze             # посчитать возможности
    python scan.py report              # собрать HTML + CSV
    python scan.py all                 # полный цикл
"""
from __future__ import annotations
import argparse, asyncio, csv, json, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from coinarb import analyze, comps_io, fees, report, store
from coinarb.fetch import Fetcher
from coinarb.normalize import parse
from coinarb.sources import meshok as meshok_src

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
SM_SECTIONS = {69: "Царская Россия", 77: "СССР/РСФСР/РФ", 37: "Медали и жетоны",
               133: "Наборы", 123: "Боны", 140: "Антиквариат", 55: "Литература"}


def parse_sm(html: str, section: int) -> list[dict]:
    import re
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    out = []
    for td in soup.select("td[id^=bx_]"):
        a = td.select_one("a.tovar_name")
        if not a: continue
        href = a.get("href", "")
        m = re.search(r"/shop/(\d+)/(\d+)/", href)
        if not m: continue
        pr = td.select_one("span.catalog-price")
        price = int(re.sub(r"\D", "", pr.get_text())) if pr and re.search(r"\d", pr.get_text()) else None
        img = td.select_one("img")
        out.append({"source": "staraya-moneta", "source_id": m.group(2),
                    "url": "https://www.staraya-moneta.ru" + href,
                    "title": a.get_text(" ", strip=True), "price_rub": price,
                    "section": SM_SECTIONS.get(int(m.group(1)), str(m.group(1))),
                    "image": img.get("src") if img else None, "seller": "Старая Монета"})
    return out


async def harvest_sm(delay_lo=1.2, delay_hi=2.5) -> list[dict]:
    all_items = []
    async with Fetcher(cache=False, delay=(delay_lo, delay_hi)) as f:
        for sect in SM_SECTIONS:
            url = f"https://www.staraya-moneta.ru/shop/{sect}/?SHOWALL_1=1"
            try:
                html = await f.get(url, wait=2500)
            except Exception as e:
                print(f"  ! раздел {sect}: {e}")
                continue
            items = parse_sm(html, sect)
            print(f"  раздел {sect} ({SM_SECTIONS[sect]}): {len(items)} лотов")
            all_items += items
    return all_items


async def harvest_meshok(pages: int, section: str, storage_state: str | None,
                         proxy: str | None) -> list[dict]:
    items = []
    async with Fetcher(cache=False, delay=(2.0, 4.0), storage_state=storage_state,
                       proxy=proxy) as f:
        for p in range(1, pages + 1):
            url = meshok_src.page_url(section, p)
            try:
                html = await f.get(url, wait=3000)
            except Exception as e:
                print(f"  ! meshok стр.{p}: {e}")
                break
            got = meshok_src.parse_listing(html, section)
            print(f"  meshok стр.{p}: {len(got)} лотов")
            if not got: break
            items += got
    return items


def persist(items: list[dict]) -> dict:
    con = store.connect()
    seen: dict[str, set] = {}
    for it in items:
        if not it.get("price_rub"):
            continue
        lot_id = store.upsert_lot(con, source=it["source"], source_id=it["source_id"],
                                 url=it["url"], title=it["title"], price=it["price_rub"],
                                 section=it.get("section", ""), seller=it.get("seller", ""))
        coin = analyze.enrich(parse(it["title"]))
        store.save_features(con, lot_id, coin)
        seen.setdefault(it["source"], set()).add(str(it["source_id"]))
    gone = {s: store.mark_missing_inactive(con, s, ids) for s, ids in seen.items()}
    return {"saved": len(items), "marked_inactive": gone}


def build_ctx(terms: fees.DealTerms, results: list[dict]) -> dict:
    """Собирает контекст для HTML-отчёта: статистика, покрытие, лист наблюдения, юнит-экономика."""
    con = store.connect()
    coins, prices, ids = comps_io.load_pool(con)

    db_by_key: dict[str, list] = {}
    for row in con.execute("SELECT * FROM comps"):
        db_by_key.setdefault(row["comp_key"], []).append(
            analyze.Comp(row["price_rub"], row["grader"], row["grade_num"],
                         bool(row["is_slabbed"]), "db"))

    watch = analyze.watchlist(coins, prices, ids, db_by_key, terms)

    lots = con.execute("SELECT COUNT(*) c FROM lots").fetchone()["c"]
    coins_n = con.execute("SELECT COUNT(*) c FROM features WHERE item_type='coin'").fetchone()["c"]
    comps_n = con.execute("SELECT COUNT(*) c FROM comps").fetchone()["c"]

    # покрытие признаков среди монет
    total = coins_n or 1
    def cov(expr):
        n = con.execute(f"SELECT COUNT(*) c FROM features WHERE item_type='coin' AND {expr}").fetchone()["c"]
        return {"n": n, "total": coins_n, "pct": 100 * n / total}
    coverage = {
        "Год": cov("year IS NOT NULL"),
        "Номинал": cov("denomination IS NOT NULL AND denomination != ''"),
        "Металл": cov("metal IS NOT NULL"),
        "Грейд (число)": cov("grade_num IS NOT NULL"),
        "В слабе": cov("is_slabbed = 1"),
    }

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
                  "comps": comps_n, "opportunities": len(results)},
        "watch": watch, "terms": terms.to_dict(),
        "tariffs": fees.NGS_TARIFFS, "coverage": coverage, "flagship": flagship,
    }


def run_analyze(terms: fees.DealTerms, min_profit: float, min_roi: float) -> list[dict]:
    con = store.connect()
    coins, prices, ids = comps_io.load_pool(con)

    # 1) comps из базы (проданные цены) + 2) comps из текущей выдачи
    db_by_key: dict[str, list[analyze.Comp]] = {}
    for row in con.execute("SELECT * FROM comps"):
        db_by_key.setdefault(row["comp_key"], []).append(
            analyze.Comp(row["price_rub"], row["grader"], row["grade_num"],
                         bool(row["is_slabbed"]), "db"))

    results = []
    for c in coins:
        key = c.key()
        if not key:
            continue
        comps = list(db_by_key.get(key, []))
        comps += analyze.comps_from_pool(c, coins, prices)
        res = analyze.screen(prices[id(c)], c, comps, terms, min_profit, min_roi)
        if res:
            res["lot_id"], res["url"] = ids[id(c)], None
            results.append(res)

    results.sort(key=lambda r: -r["score"])
    for r in results:
        row = con.execute("SELECT url FROM lots WHERE id=?", (r["lot_id"],)).fetchone()
        r["url"] = row["url"] if row else None
        e = r["economics"]
        store.save_opportunity(
            con, r["lot_id"], r["buy_price"], r["expected_sale"], e.slab_cost,
            e.net_profit, e.roi_pct, r["confidence"],
            f"{e.tariff_name}: {r['basis']}",
            {"coin": r["coin"].to_dict(), "economics": e.__dict__, "basis": r["basis"]})
    return results


def write_reports(results: list[dict], terms: fees.DealTerms) -> dict:
    DATA.mkdir(exist_ok=True)
    csv_path, json_path = DATA / "opportunities.csv", DATA / "opportunities.json"
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["score", "title", "год", "номинал", "металл", "грейд", "грейдер",
                    "цена_покупки", "ожид_продажа", "тариф_NGS", "слаб_руб", "чистая_прибыль",
                    "ROI_%", "уверенность", "обоснование", "url"])
        for r in results:
            c, e = r["coin"], r["economics"]
            w.writerow([r["score"], c.title, c.year, c.denomination, c.metal,
                        c.grade_label or "raw", c.grader or "—", r["buy_price"],
                        r["expected_sale"], e.tariff_name, e.slab_cost, e.net_profit,
                        e.roi_pct, r["confidence"], r["basis"], r["url"]])
    json_path.write_text(json.dumps(
        [{"score": r["score"], "title": r["coin"].title, "buy": r["buy_price"],
          "expected": r["expected_sale"], "net_profit": r["economics"].net_profit,
          "roi": r["economics"].roi_pct, "conf": r["confidence"],
          "basis": r["basis"], "url": r["url"],
          "coin": r["coin"].to_dict(), "econ": r["economics"].__dict__}
         for r in results], ensure_ascii=False, indent=1), encoding="utf-8")
    return {"csv": str(csv_path), "json": str(json_path)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["harvest-sm", "harvest-meshok", "import-comps",
                                    "analyze", "report", "all"])
    ap.add_argument("file", nargs="?")
    ap.add_argument("--pages", type=int, default=3)
    ap.add_argument("--section", default="252")
    ap.add_argument("--storage-state")
    ap.add_argument("--proxy")
    ap.add_argument("--terms", help="JSON с переопределением DealTerms")
    ap.add_argument("--min-profit", type=float, default=5000)
    ap.add_argument("--min-roi", type=float, default=25)
    args = ap.parse_args()

    terms = fees.DealTerms()
    if args.terms:
        for k, v in json.loads(args.terms).items():
            setattr(terms, k, v)

    # полный цикл должен сам подхватить базу comps, иначе аналитика считается "на пустоте"
    if args.cmd == "all":
        con0 = store.connect()
        empty = con0.execute("SELECT COUNT(*) c FROM comps").fetchone()["c"] == 0
        seed = DATA / "comps_seed.csv"
        if empty and seed.exists():
            print("→ comps пусты, загружаю", seed.name,
                  ":", comps_io.import_csv(con0, seed), "записей")

    if args.cmd in ("harvest-sm", "all"):
        print("→ Сбор staraya-moneta")
        print(persist(asyncio.run(harvest_sm())))
    if args.cmd in ("harvest-meshok", "all"):
        print("→ Сбор meshok")
        got = asyncio.run(harvest_meshok(args.pages, args.section, args.storage_state, args.proxy))
        print(persist(got) if got else "  meshok: 0 лотов (Cloudflare / нужны cookies)")
    if args.cmd == "import-comps":
        if not args.file: sys.exit("укажите CSV: scan.py import-comps data/comps_seed.csv")
        print("comps загружено:", comps_io.import_csv(store.connect(), args.file))
    if args.cmd in ("analyze", "all", "report"):
        res = run_analyze(terms, args.min_profit, args.min_roi)
        print(f"→ Возможностей: {len(res)}")
        for r in res[:12]:
            e = r["economics"]
            print(f"  {r['score']:>9.0f} | net {e.net_profit:>8.0f} | ROI {e.roi_pct:>6.1f}% "
                  f"| {r['coin'].title[:58]}")
        if args.cmd in ("report", "all"):
            print("→ Файлы:", write_reports(res, terms))
            ctx = build_ctx(terms, res)
            out = DATA / "report.html"
            out.write_text(report.build(ctx), encoding="utf-8")
            print("→ HTML:", out, f"({len(ctx['watch'])} лотов с аналогами)")


if __name__ == "__main__":
    main()
