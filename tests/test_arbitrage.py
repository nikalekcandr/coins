"""Тесты стратегий арбитража на синтетической базе (in-memory SQLite).

Проверяем, что каждая стратегия срабатывает на данных, где она ДОЛЖНА
сработать, и молчит там, где прибыли нет.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coinarb import analyze, arbitrage, fees, index as index_mod, store
from coinarb.normalize import parse


def _db():
    return store.connect(":memory:")


def _add_lot(con, source, sid, title, price, section=""):
    lot_id = store.upsert_lot(con, source=source, source_id=sid,
                              url=f"https://example/{source}/{sid}",
                              title=title, price=price, section=section,
                              seller="test")
    coin = analyze.enrich(parse(title, section))
    store.save_features(con, lot_id, coin)
    return lot_id


def _add_comp(con, key, price, grader=None, grade_num=None, slabbed=True, source="TEST"):
    store.add_comp(con, comp_key=key, source=source, title="t", url="",
                   price_rub=price, grader=grader, grade_num=grade_num,
                   grade_bucket=("high" if (grade_num or 0) >= 58 else "mid"),
                   is_slabbed=slabbed)


def test_slab_arb_fires_on_raw_vs_slab_comps():
    con = _db()
    _add_lot(con, "staraya-moneta", "1",
             "Рубль 1913 г. (ВС) 300 лет дома Романовых, выпуклый чекан", 12000)
    _add_comp(con, "1913|рубль|Ag", 62000, "NGC", 62)
    _add_comp(con, "1913|рубль|Ag", 44000, "ННР", 62)
    res = arbitrage.find_all(con, fees.DealTerms(), min_profit=5000, min_roi=25)
    slab = [r for r in res if r["strategy"] == "slab_arb"]
    assert slab, "slab_arb не сработал на очевидной паре raw vs NGC/ННР comps"
    e = slab[0]["economics"]
    assert e.net_profit > 5000 and e.roi_pct > 25
    assert e.slab_cost == 1200          # Стандарт: рыночная стоимость до 50к
    assert e.tariff_name == "Стандарт"


def test_slab_arb_silent_when_entry_too_expensive():
    con = _db()
    _add_lot(con, "staraya-moneta", "1",
             "Рубль 1913 г. (ВС) 300 лет дома Романовых", 60000)
    _add_comp(con, "1913|рубль|Ag", 62000, "NGC", 62)
    _add_comp(con, "1913|рубль|Ag", 44000, "ННР", 62)
    res = arbitrage.find_all(con, fees.DealTerms(), min_profit=5000, min_roi=25)
    assert not [r for r in res if r["strategy"] == "slab_arb"], \
        "при входе 60к арбитража быть не должно"


def test_cross_source_needs_two_sources():
    con = _db()
    t = "10 рублей 1899 г. (АГ), золото, в слабе ННР MS62"
    _add_lot(con, "staraya-moneta", "1", t, 55000)
    _add_lot(con, "meshok", "2", "10 рублей 1899 (АГ) золото ННР MS62", 75000)
    res = arbitrage.find_all(con, fees.DealTerms(), min_profit=5000, min_roi=10)
    xs = [r for r in res if r["strategy"] == "cross_source"]
    assert xs, "кросс-площадочный арбитраж не найден"
    r = xs[0]
    assert r["buy_price"] == 55000 and r["expected_sale"] == 75000
    assert r["economics"].slab_cost == 0, "флип без слабирования: slab_cost обязан быть 0"
    assert r["extra"]["sell_lot_id"] == 2

    # одна площадка — стратегии нет
    con2 = _db()
    _add_lot(con2, "staraya-moneta", "1", t, 55000)
    _add_lot(con2, "staraya-moneta", "2", t, 75000)
    res2 = arbitrage.find_all(con2, fees.DealTerms(), min_profit=5000, min_roi=10)
    assert not [r for r in res2 if r["strategy"] == "cross_source"]


def test_slab_flip_underpriced_slab():
    con = _db()
    _add_lot(con, "meshok", "9",
             "10 рублей 1899 г. (АГ), золото, в слабе NGC MS62", 40000)
    _add_comp(con, "1899|10 руб|Au", 62000, "NGC", 62)
    _add_comp(con, "1899|10 руб|Au", 58000, "NGC", 62)
    res = arbitrage.find_all(con, fees.DealTerms(), min_profit=5000, min_roi=25)
    flips = [r for r in res if r["strategy"] == "slab_flip"]
    assert flips, "slab_flip не сработал на слабe NGC за 40к при медиане 60к"
    assert flips[0]["economics"].slab_cost == 0


def test_underpriced_radar():
    con = _db()
    _add_lot(con, "meshok", "5", "15 копеек 1903 СПБ АР", 900)
    for p in (1800, 2200, 2600):
        _add_comp(con, "1903|15 коп|Ag", p, slabbed=False, source="TEST")
    res = arbitrage.find_all(con, fees.DealTerms(), min_profit=5000, min_roi=25)
    ups = [r for r in res if r["strategy"] == "underpriced"]
    assert ups, "радар заниженных цен не сработал"
    assert ups[0]["extra"]["gap_pct"] >= 20


def test_deals_boost_confidence_and_price():
    """Факты продаж (deals) приоритетнее справочных comps и повышают уверенность."""
    con = _db()
    _add_lot(con, "staraya-moneta", "1", "5 рублей 1898 г. АГ, золото", 20000)
    _add_comp(con, "1898|5 руб|Au", 34000, "NGC", 62)
    _add_comp(con, "1898|5 руб|Au", 36000, "ННР", 62)
    store.record_deal(con, title="5 рублей 1898 АГ золото NGC MS62", price_rub=50000,
                      source="conros", method="auction-archive",
                      comp_key="1898|5 руб|Au", grader="NGC", grade_num=62,
                      is_slabbed=True, item_type="coin")
    store.record_deal(con, title="5 рублей 1898 АГ золото NGC MS62", price_rub=54000,
                      source="wolmar", method="auction-archive",
                      comp_key="1898|5 руб|Au", grader="NGC", grade_num=62,
                      is_slabbed=True, item_type="coin")
    res = arbitrage.find_all(con, fees.DealTerms(), min_profit=5000, min_roi=25)
    slab = [r for r in res if r["strategy"] == "slab_arb"]
    assert slab
    # медиана сделок (52000) выигрывает у справочных (35000)
    assert slab[0]["expected_sale"] > 0.7 * 50000
    assert slab[0]["confidence"] > 0.45


def test_index_slab_premium():
    con = _db()
    _add_lot(con, "staraya-moneta", "1", "Рубль 1913 г. (ВС) 300 лет дома Романовых", 13000)
    _add_lot(con, "staraya-moneta", "2",
             "Рубль 1913 г. (ВС) 300 лет дома Романовых, в слабе ННР MS62", 52000)
    idx = index_mod.build_index(con)
    row = next(r for r in idx if r["comp_key"] == "1913|рубль|Ag")
    assert row["n_raw"] == 1 and row["n_slab"] == 1
    assert row["slab_premium"] == 4.0, row["slab_premium"]


def test_vanished_proxy_records_deal():
    con = _db()
    _add_lot(con, "staraya-moneta", "1", "15 копеек 1903 СПБ АР", 1500)
    _add_lot(con, "staraya-moneta", "2", "15 копеек 1904 СПБ АР", 1600)
    # второй прогон: лот 1 исчез из выдачи
    gone = store.mark_missing_inactive(con, "staraya-moneta", {"2"})
    assert gone == 1
    deals = con.execute("SELECT * FROM deals").fetchall()
    assert len(deals) == 1
    d = deals[0]
    assert d["method"] == "vanished-proxy" and d["price_rub"] == 1500
    assert d["comp_key"] == "1903|15 коп|Ag"


def test_fees_none_tariff():
    econ = fees.compute(10000, 15000, fees.DealTerms(slab_tariff="none"))
    assert econ.slab_cost == 0 and econ.tariff_name == "—"
    econ2 = fees.compute(10000, 15000, fees.DealTerms())
    assert econ2.slab_cost == 1200        # Стандарт до 50к


def test_calibration_ngs_is_measured_side():
    """В паре NGS/ННР одного тира измеряемой стороной обязан быть NGS."""
    con = _db()
    for title, price, grader in [
            ("10 рублей 1899 (АГ) золото в слабе NGS MS62", 34000, "NGS"),
            ("10 рублей 1899 (АГ) золото в слабе ННР MS62", 53000, "ННР"),
            ("10 рублей 1899 (АГ) золото в слабе NGC MS62", 51000, "NGC")]:
        store.record_deal(con, title=title, price_rub=price, source="test",
                          method="auction-archive", comp_key="1899|10 руб|Au",
                          grader=grader, grade_num=62, is_slabbed=True,
                          item_type="coin", sold_at=1700000000.0)
    pairs = analyze.grader_pairs_from_db(con)
    ngs = [p for p in pairs if p["grader_a"] == "NGS"]
    assert len(ngs) == 2, pairs
    ratios = {p["grader_b"]: p["ratio"] for p in ngs}
    assert ratios["NGC"] == round(34000 / 51000, 3)
    assert ratios["ННР"] == round(34000 / 53000, 3)
    calib = analyze.calibrate_ngs_discount([(p["price_a"], p["price_b"]) for p in ngs])
    assert calib["n"] == 2 and 0.6 < calib["discount"] < 0.7
    # DEMO-comps в калибровке не участвуют
    _add_comp(con, "1899|10 руб|Au", 99000, "NGS", 62, source="DEMO")
    pairs2 = analyze.grader_pairs_from_db(con)
    assert all(not (p["grader_a"] == "NGS" and p["price_a"] == 99000) for p in pairs2)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print(f"ok  {name}")
    print("arbitrage: all tests passed")
