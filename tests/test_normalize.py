"""Тесты нормализации на РЕАЛЬНЫХ заголовках из среза staraya-moneta (463 лота).

Каждый кейс — это баг, который был найден в данных и исправлен. Регрессия здесь
ломает comps и арбитраж молча, поэтому тесты обязательны к прогону перед analyze.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coinarb import reference
from coinarb.normalize import parse

# (заголовок, секция, item_type, denom, metal, grader, grade_num, is_slabbed)
CASES = [
    # --- книги не должны быть монетами (баг: слово «монет» в названии) ---
    ("Корпус советских монет. 1918 - 1992 гг. Сорокин В.Ю. Издание 2024 г., переплет",
     "Литература", "book", None, None, None, None, False),
    ("Медные монеты Петра I. Том I 1700-1708. Дьяков М.Е. Издание 2018 г. с автографом",
     "Литература", "book", None, None, None, None, False),
    ("Стандарты грейдинга. Руководство для начинающих по определению сохранности золотых "
     "и платиновых монет", "Литература", "book", None, None, None, None, False),
    ("Уздеников В.В. Монеты России 1700-1917. 4-е изд., 2011.",
     "Литература", "book", None, None, None, None, False),
    ("Журнал «Нумизматика» № 27, ноябрь 2010 г.", "Литература", "book", None, None, None, None, False),

    # --- монета со словом «литературы» в названии серии НЕ книга (баг v1) ---
    ("150 рублей 1988 \"Слово о полку Игореве\" из серии 1000-летие Древнерусской "
     "литературы, платина 15,55 г", "СССР/РСФСР/РФ", "coin", "150 руб", "Pt", None, None, False),

    # --- банкнотные грейдеры пишутся слитно: PMG65, ZG1062 (баг v1: \bPMG\b) ---
    ("3 рубля 1934 (вариант без подписи) в слабе PMG65 EPQ", "Боны", "note", "3 руб",
     None, "PMG", None, True),
    ("20 копеек 1915 Российской Империи в слабе ZG1062. Казначейский знак.",
     "Боны", "note", "20 коп", None, "ZG", None, True),
    ("5000 рублей 1922 в слабе PMG64 EPQ. Государственный денежный знак.",
     "Боны", "note", "5000 руб", None, "PMG", None, True),
    ("15 рублей 1919 в слабе PMG65 EPQ. Расчетный знак РСФСР, неразрезанный лист",
     "Боны", "note", "15 руб", None, "PMG", None, True),

    # --- «денежный знак» — банкнота, а не медаль/знак (баг v1) ---
    ("3 рубля 1918г в слабе ZG1067. Оренбургское отделение Госбанка. Дензнак.",
     "Боны", "note", "3 руб", None, "ZG", None, True),

    # --- жетон в начале заголовка — медаль, даже со словом «монеты» (баг v1) ---
    ("Жетон «Монеты и медали». Первый советский аукцион 12.12.2015, ММД, 2015.",
     "Медали и жетоны", "medal", None, None, None, None, False),
    ("Знак «Отличник нефтедобывающей промышленности СССР». ММД, 1980-е.",
     "Медали и жетоны", "medal", None, None, None, None, False),

    # --- 50 копеек 1911 — серебро, а не медь (баг v1: catch-all «копе» 1700-1917) ---
    ("50 копеек 1911 (ЭБ), в слабе CPRC MS63.", "Царская Россия", "coin", "50 коп",
     "Ag", "CPRC", 63, True),

    # --- корректные разборы, которые обязаны продолжать работать ---
    ("Рубль 1913 г. (ВС) 300 лет дома Романовых, выпуклый чекан, в слабе ННР MS 62",
     "Царская Россия", "coin", "рубль", "Ag", "ННР", 62, True),
    ("15 копеек 1903 СПБ АР, в слабе NGC PROOF 66 ULTRA CAMEO.",
     "Царская Россия", "coin", "15 коп", "Ag", "NGC", 66, True),
    ("Полтина 1777, дворцовая, золото, Екатерина II, в слабе NGC AU58.",
     "Царская Россия", "coin", "полтина", "Au", "NGC", 58, True),
    ("10 рублей 1911 г. (ЭБ), золото, в слабе ННР MS63", "Царская Россия",
     "coin", "10 руб", "Au", "ННР", 63, True),
    ("5 рублей 1889 г. (АГ), золото, в слабе NGC MS 62", "Царская Россия",
     "coin", "5 руб", "Au", "NGC", 62, True),
    ("Копейка 1714 г. НД,UNC медь", "Царская Россия", "coin", "копейка", "Cu",
     None, 62, False),
    ("2 копейки 1989, перепутка по металлу, Федорин VI № 082-ДМ (Р-4), в слабе NGC MINT ERROR MS61",
     "СССР/РСФСР/РФ", "coin", "2 коп", None, "NGC", 61, True),
    ("1 рубль 1965 «XX лет Победы», ЛМД, в бумажном пакетике.", "СССР/РСФСР/РФ",
     "coin", "1 руб", "Ni", None, None, False),
    ("3 рубля 2023 СПМД, ДНР, ЛНР, Запорожская, Херсонская области.", "СССР/РСФСР/РФ",
     "coin", "3 руб", "Ag", None, None, False),
    ("5 рублей 1992, неотчеканенная заготовка под монету", "СССР/РСФСР/РФ",
     "coin", "5 руб", None, None, None, False),

    # --- наборы и антиквариат ---
    ("Годовой набор монет СССР 1979, ЛМД, в пластике", "Наборы", "set", None, None,
     None, None, False),
    ("Фото. Группа государственных чиновников в интерьере с портретом Александра II.",
     "Антиквариат", "other", None, None, None, None, False),
]

VARIETY_CASES = [
    ("Рубль 1913 г. (ВС) 300 лет дома Романовых, выпуклый чекан", "выпуклый чекан"),
    ("5 рублей 1992, неотчеканенная заготовка под монету", "неотчеканен"),
    ("2 копейки 1989, перепутка по металлу", "перепутка"),
]

ROMANOV_CASES = [
    ("Рубль 1913 г. (ВС) 300 лет дома Романовых", True),
    ("Рубль 1883 ЛШ, коронационный Александра III", True),
    ("15 копеек 1903 СПБ АР", False),
]

MINTMASTER_CASES = [
    ("15 копеек 1903 СПБ АР", "СПБ АР"),
    ("Рубль 1913 г. (ВС) 300 лет дома Романовых", "ВС"),
    ("5 рублей 1889 г. (АГ), золото", "АГ"),
    ("1 рубль 1965 «XX лет Победы», ЛМД", "ЛМД"),
]


def run():
    bad = 0
    for case in CASES:
        title, section, want_type, want_denom, want_metal, want_grader, want_grade, want_slab = case
        c = parse(title, section)
        if want_metal and not c.metal:
            metal, _w = reference.infer(title, c.year, c.denomination)
            c.metal = c.metal or metal
        checks = [
            ("type", c.item_type, want_type),
            ("denom", c.denomination, want_denom),
            ("metal", c.metal, want_metal),
            ("grader", c.grader, want_grader),
            ("grade", c.grade_num, want_grade),
            ("slab", c.is_slabbed, want_slab),
        ]
        fails = [(n, got, want) for n, got, want in checks
                 if want is not None and got != want]
        if fails:
            bad += 1
            print(f"FAIL  {title[:64]}")
            for n, got, want in fails:
                print(f"        {n}: got={got!r} want={want!r}")

    for title, want_var in VARIETY_CASES:
        c = parse(title)
        if not (c.variety and want_var in c.variety):
            bad += 1
            print(f"FAIL variety  {title[:60]}  got={c.variety!r} want~{want_var!r}")

    for title, want in ROMANOV_CASES:
        c = parse(title)
        if c.is_romanov != want:
            bad += 1
            print(f"FAIL romanov  {title[:60]}  got={c.is_romanov} want={want}")

    for title, want in MINTMASTER_CASES:
        c = parse(title)
        if c.mintmaster != want:
            bad += 1
            print(f"FAIL mintmaster  {title[:60]}  got={c.mintmaster!r} want={want!r}")

    total = len(CASES) + len(VARIETY_CASES) + len(ROMANOV_CASES) + len(MINTMASTER_CASES)
    print(f"{total - bad}/{total} тестов прошло")
    return bad


if __name__ == "__main__":
    sys.exit(1 if run() else 0)
