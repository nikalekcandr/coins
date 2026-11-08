"""Тесты универсального импорта фактов продаж в базу сделок."""
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coinarb import store
from coinarb.sources import sold

CONROS_LIKE = """
<html><body>
<h2>Аукцион №153 от 15.03.2024</h2>
<table>
<tr><th>Лот</th><th>Старт</th><th>Итог</th></tr>
<tr><td>10 рублей 1899 (АГ) золото, в слабе NGC MS62</td><td>40000</td><td>52 000</td></tr>
<tr><td>Рубль 1913 (ВС) 300 лет дома Романовых, ННР MS62</td><td>30 000</td><td>44000</td></tr>
<tr><td>Фото группы чиновников, XIX век</td><td>1000</td><td>2500</td></tr>
<tr><td>15 копеек 1903 СПБ АР</td><td></td><td>1 850</td></tr>
</table>
</body></html>
"""

WOLMAR_LIKE = """
<html><body>
<div><table class="lots">
<tr><td>07.03.2024</td><td>5 рублей 1898 АГ, золото</td><td>31 200</td></tr>
<tr><td>07.03.2024</td><td>Полтина 1840 СПБ НГ серебро</td><td>41 000</td></tr>
</table></div>
</body></html>
"""


def test_parse_date():
    assert sold.parse_date("2024-03-15") is not None
    assert sold.parse_date("15.03.2024") is not None
    assert sold.parse_date("15 марта 2024") is not None
    assert sold.parse_date(time.time()) is not None
    assert sold.parse_date("") is None
    assert sold.parse_date("не дата") is None


def test_parse_generic_archive_conros():
    rows = sold.parse_generic_archive(CONROS_LIKE, source="conros")
    # строка «Фото…» без года/номинала не должна попасть в сделки
    titles = [r["title"] for r in rows]
    assert len(rows) == 3, f"ожидали 3 сделки, получили {len(rows)}: {titles}"
    r0 = next(r for r in rows if "10 рублей 1899" in r["title"])
    assert r0["price_rub"] == 52000, "должна браться ИТОГОВАЯ цена (последняя числовая ячейка)"
    assert r0["grade_num"] == 62
    assert r0["sold_at"] is not None, "дата аукциона из заголовка не подтянулась"
    r1 = next(r for r in rows if "Рубль 1913" in r["title"])
    assert r1["price_rub"] == 44000


def test_parse_generic_archive_wolmar():
    rows = sold.parse_generic_archive(WOLMAR_LIKE, source="wolmar")
    assert len(rows) == 2
    prices = {r["price_rub"] for r in rows}
    assert prices == {31200, 41000}
    for r in rows:
        assert r["sold_at"] is not None, "дата в строке не распознана"


def test_import_rows_dedup():
    con = store.connect(":memory:")
    rows = [
        {"title": "10 рублей 1899 (АГ) золото NGC MS62", "price_rub": "52 000",
         "sold_at": "2024-03-15", "source": "conros"},
        {"title": "10 рублей 1899 (АГ) золото NGC MS62", "price_rub": 52000,
         "sold_at": "2024-03-15", "source": "conros"},           # дубль
        {"title": "Рубль 1913 ВС ННР MS62", "price_rub": 44000},  # без даты
        {"title": "5000 рублей 1922 PMG64 дензнак", "price_rub": 9000},  # банкнота -> skip
        {"title": "", "price_rub": 100},                          # мусор -> skip
    ]
    res = sold.import_rows(con, rows, source="test")
    assert res["added"] == 2, res
    assert res["skipped"] == 2, res
    n = con.execute("SELECT COUNT(*) FROM deals").fetchone()[0]
    assert n == 2
    # повторный импорт тех же строк не создаёт дублей
    res2 = sold.import_rows(con, rows, source="test")
    assert res2["added"] == 0
    n2 = con.execute("SELECT COUNT(*) FROM deals").fetchone()[0]
    assert n2 == 2, "дедупликация не сработала"
    # comp_key проставлен из нормализации
    keys = [r["comp_key"] for r in con.execute("SELECT comp_key FROM deals")]
    assert "1899|10 руб|Au" in keys, keys
    assert "1913|рубль|Ag" in keys, keys


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print(f"ok  {name}")
    print("sold: all tests passed")
