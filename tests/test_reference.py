"""Тесты справочника: ошибка в металле обнуляет всю аналитику, поэтому проверяем явно."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from coinarb.normalize import parse
from coinarb import reference

CASES = [
    # (заголовок, ожидаемый металл, ожидаемый вес или None)
    ("5 рублей 1889 г. (АГ), золото", "Au", 4.30),
    ("10 рублей 1899 г. (АГ), золото, царский чекан", "Au", 8.60),
    ("Рубль 1913 г. (ВС) 300 лет дома Романовых", "Ag", None),
    ("Полтина 1840 СПБ НГ, серебро", "Ag", None),
    ("Копейка 1714 г. НД, UNC медь", "Cu", None),
    ("20 копеек 1913 СПБ ВС", "Ag", None),
    ("10 рублей 2022 ММД, Рыльск", "Ni", None),      # РФ, не золото
    ("10 рублей 2026 г. С.П.Б., коронационные", "Ni", None),
    ("150 рублей 1990, Бот Св.Гавриил, платина", "Pt", 15.55),
    ("100 рублей 1988, Златник Владимира, золото", "Au", 17.45),
    ("25 рублей 1991, Отмена крепостного права", "Pd", 31.10),
    ("3 рубля 1989, Московский Кремль", "Ag", 33.93),
    ("1 рубль 1969, Федорин VI № 20", "Ni", None),
    ("50 копеек 1997 СП", "Ni", None),
    ("5 рублей 1988, Памятник «Тысячелетие России»", "Ni", None),
    ("15 копеек 1898 СПБ АГ", "Ag", None),
    ("Билет Банка России 50000 рублей 1994 в слабе PMG67", None, None),
]

def run():
    bad = 0
    for title, want_metal, want_weight in CASES:
        c = parse(title)
        metal, weight = reference.infer(title, c.year, c.denomination)
        ok = metal == want_metal and (want_weight is None or weight == want_weight)
        if not ok:
            bad += 1
            print(f"FAIL  {title[:60]:<62} got={metal}/{weight} want={want_metal}/{want_weight}")
    print(f"{len(CASES)-bad}/{len(CASES)} тестов прошло")
    return bad

if __name__ == "__main__":
    sys.exit(1 if run() else 0)
