"""Тесты парсера Мешка: активные лоты, завершённые аукционы (источник сделок)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coinarb.sources import meshok

LISTING_HTML = """
<html><body>
<div class="item">
  <a href="/item/1001">10 рублей 1899 (АГ), золото, в слабе ННР MS62</a>
  <div class="price">52 000</div>
  <a href="/user/55">продавец1</a>
</div>
<div class="item">
  <a href="/item/1002">Рубль 1913 (ВС) 300 лет дома Романовых, ННР MS62</a>
  <div class="price">44 000</div>
  <div class="status">Аукцион завершен 12.03.2024, итоговая цена. ставок: 14</div>
  <a href="/user/77">продавец2</a>
</div>
<div class="item">
  <a href="/item/1003">5 рублей 1898 АГ золото</a>
  <span class="price-current">34 500</span>
</div>
</body></html>
"""


def test_parse_listing():
    items = meshok.parse_listing(LISTING_HTML, section="252")
    assert len(items) == 3, f"ожидали 3 лота, получили {len(items)}"
    by_id = {it["source_id"]: it for it in items}

    a = by_id["1001"]
    assert a["price_rub"] == 52000 and not a["sold"] and a["seller"] == "продавец1"
    assert a["url"] == "https://meshok.net/item/1001"

    b = by_id["1002"]
    assert b["sold"], "завершённый аукцион обязан иметь sold=True"
    assert b["bids"] == 14, f"ставки не распознаны: {b['bids']}"
    assert b["end_date"] == "12.03.2024"
    assert b["source"] == "meshok-ended"

    c = by_id["1003"]
    assert c["price_rub"] == 34500, "ценник с другим классом не найден"


def test_parse_ended():
    ended = meshok.parse_ended(LISTING_HTML)
    assert len(ended) == 1
    assert ended[0]["source_id"] == "1002"
    assert ended[0]["price_rub"] == 44000


def test_price_formats():
    assert meshok.parse_price("1.234.567") == 1234567
    assert meshok.parse_price("12.50") == 12.5
    assert meshok.parse_price("52 000") == 52000
    assert meshok.parse_price("44\u00a0000\u00a0руб.") == 44000
    assert meshok.parse_price("нет ставки") is None


def test_urls():
    assert meshok.page_url("252", 1) == "https://meshok.net/good/252"
    assert "page=2" in meshok.page_url("252", 2)
    assert "searchText=рубль+1913" in meshok.search_url("рубль 1913")
    assert meshok.SOLD_EXTRA in meshok.search_url("рубль 1913", sold=True)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print(f"ok  {name}")
    print("meshok: all tests passed")
