"""Нормализация русскоязычных нумизматических заголовков в структурированные атрибуты.

Ключевая задача: превратить "Рубль 1913 г. (ВС) 300 лет дома Романовых,
выпуклый чекан, в слабе ННР MS 62" в объект, по которому можно считать comps.

Уроки, зашитые в эту версию (найдены на реальном срезе 463 лотов):
  1. Книги из раздела «Литература» ("Корпус советских монет…") не должны
     становиться монетами только из-за слова «монет» в названии.
  2. «150 рублей 1988 Слово о полку Игореве … из серии 1000-летие Древнерусской
     литературы» — это МОНЕТА, слово «литература» в серии не делает её книгой.
  3. Грейдеры банкнот пишутся слитно с грейдом: "PMG65", "ZG1062" — regex
     обязан это ловить, иначе банкноты уезжают в медали.
  4. «Денежный знак»/«дензнак» — банкнота, а не медаль/знак.
  5. Номиналы-синонимы (гривенник, пятак, полтинник, червонец…) приводятся
     к канонической форме, иначе comp_key не сходится между лотами и comps.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, asdict

METALS = [
    ("Pt", r"платин"), ("Pd", r"паллади"), ("Au", r"золот"),
    ("Ag", r"серебр|серебрян"), ("Cu", r"медь|медн|бронз"),
    ("Ni", r"никел"), ("Bil", r"билон"), ("Br", r"латун"),
]
# Порядок важен: PMG/ZG пишутся слитно с номером ("PMG65", "ZG1062").
GRADERS = [("NGC", r"\bNGC\b"), ("PCGS", r"\bPCGS\b"), ("ННР", r"\bННР\b"),
           ("NGS", r"\bNGS\b"), ("ANACS", r"\bANACS\b"), ("RNGA", r"\bRNGA\b"),
           ("NRG", r"\bNRG\b"), ("CPRC", r"\bCPRC\b"),
           ("PMG", r"\bPMG\s?\d"), ("Знакъ", r"Знакъ"), ("ZG", r"\bZG\s?\d"),
           ("PG", r"\bPG\s?\d")]
# Грейдеры, которые работают только с банкнотами — сильный признак type=note.
NOTE_GRADERS = {"PMG", "ZG", "Знакъ"}

GRADE_RE = re.compile(r"\b(PF|PR|PROOF|MS|AU|AUNC|XF|EF|UNC|BU|VF|F|VG|AG|G)\s*[-\s]?\s*(\d{1,2})\b", re.I)
# Слово без числа: порядок важен — aUNC до UNC, XF/EF до F.
WORD_GRADE = [("aUNC", 58), ("AUNC", 58), ("UNC", 62), ("BU", 64), ("PROOF", 63),
              ("XF", 42), ("EF", 42), ("VF", 30), ("F", 15), ("VG", 10),
              ("AG", 3), ("G", 6)]
YEAR_RE = re.compile(r"\b(1[5-9]\d{2}|20[0-2]\d)\b")
WEIGHT_RE = re.compile(r"(\d+[.,]\d{1,2})\s*г\b")
DENOM_RE = re.compile(
    r"^\s*(?:(\d+)\s*(рубл\w*|р\b|копе\w*|коп\b)"
    r"|(полтинник|полтина|рубль|руб\b|копейка|полушка|деньга|денежка|алтын|"
    r"гривенник|гривенный|двугривенн\w*|пятнадцатикопеечник|пятак|пятачок|"
    r"червонец|империал|полуимпериал|целковый))\b", re.I)
# Любой номинал в середине заголовка (для «Жетон … 3 рубля …» и т.п.)
DENOM_ANY_RE = re.compile(
    r"\b(?:(\d+)\s*(рубл\w*|копе\w*|коп\b)"
    r"|(полтинник|полтина|рубль|копейка|полушка|деньга|денежка|алтын|гривенник|"
    r"двугривенн\w*|пятак|червонец|империал|полуимпериал))\b", re.I)

# Канонизация номиналов-синонимов: comp_key должен совпадать между источниками.
CANON_DENOM = {
    "полтинник": "полтина", "гривенник": "10 коп", "гривенный": "10 коп",
    "двугривенный": "20 коп", "двугривенник": "20 коп", "пятак": "5 коп",
    "пятачок": "5 коп", "алтын": "3 коп", "червонец": "10 руб",
    "империал": "10 руб", "полуимпериал": "5 руб", "целковый": "рубль",
    "деньга": "денежка",
}

# Книга/печать. «издание», «том», «автограф» и т.п. — надёжные маркеры книги,
# в отличие от слова «литература», которое встречается в названиях монетных серий.
BOOK_RE = re.compile(
    r"книг|книжк|издани[еяю]|\bизд\b|издательств|перепл[её]т|страниц|\bтом\b|томах|"
    r"автограф|журнал\b|сборник|монографи|брошюр|буклет|каталог|руководство|"
    r"очерк|воспоминан|\bстатей\b|учебник|раскраск|альбом с фото|в \d+ томах|"
    r"подписн|нумизматик\w+ (?:бюллетен|хроник)", re.I)
NONCOIN_RE = re.compile(r"холдер|альбом для|футляр|бокс|капсул(?:ы|ой)? (?:для|под)|"
                        r"фото\b|фотограф|открытк|почтов|паспорт|объявлени|заявлени|"
                        r"лента корреспондента|вексел|облигаци|акци", re.I)
NOTE_RE = re.compile(r"билет|банкнот|бона\b|бон\b|кредитн|казначейск|дензнак|"
                     r"денежный знак|расчетн|расчётн|лотерейн|червонец 19\d\d|"
                     r"приватизационн|чек\b|знак\s*денежн", re.I)
MEDAL_RE = re.compile(r"медал|жетон|знак\b|орден|награ|плакет|медальон", re.I)
MEDAL_FIRST_RE = re.compile(r"^\s*(жетон|медаль|медали|медальон|знак|знаки|орден|награда|плакет)", re.I)
SET_RE = re.compile(r"набор|комплект|лот из|годовой набор|полный комплект|"
                    r"коллекци\w* (?:альбом|набор)|в альбоме", re.I)
VARIETY_RE = re.compile(r"(выпукл\w* чекан|вогнут\w* чекан|плоский чекан|новодел|"
                        r"перепутк\w*|надчекан\w*|брак\b|рестрайк|фальш\w*|"
                        r"заготовк\w*|неотчеканен\w*|пробник|эфимок|с признаком)", re.I)
ROMANOV_RE = re.compile(r"романов|300\s*лет\s*дома|коронаци", re.I)
# Секция площадки как подсказка: используется, только когда заголовок неоднозначен.
SECTION_HINTS = {"Литература": "book", "Боны": "note", "Медали и жетоны": "medal",
                 "Наборы": "set"}
# Разновидности, которые нельзя сопоставлять с обычной монетой того же типа
# (заготовки/браки/перепутки торгуются как свои собственные типы).
NON_COMPARABLE_VARIETIES = {"заготовк", "неотчеканен", "пробник", "брак",
                            "перепутк", "новодел", "рестрайк", "фальш", "надчекан"}


@dataclass
class Coin:
    title: str
    item_type: str = "unknown"     # coin | note | medal | set | book | other
    year: int | None = None
    denomination: str | None = None
    metal: str | None = None
    weight_g: float | None = None
    mintmaster: str | None = None  # СПБ АГ / ВС / ММД / ЛМД
    grader: str | None = None
    grade_label: str | None = None
    grade_num: int | None = None
    grade_bucket: str | None = None
    is_slabbed: bool = False
    is_romanov: bool = False
    is_set: bool = False
    variety: str | None = None

    def key(self) -> str | None:
        """Ключ группировки. None, если монета неопознаваема (нельзя сравнивать)."""
        if not self.year or not self.denomination: return None
        return f"{self.year}|{self.denomination}|{self.metal}"

    def loose_key(self) -> str | None:
        """Ключ без металла — для сопоставления, когда металл не определён."""
        if not self.year or not self.denomination: return None
        return f"{self.year}|{self.denomination}|"

    def to_dict(self):
        return asdict(self)


def _bucket(n: int | None) -> str:
    if n is None: return "raw"
    if n >= 65: return "gem"
    if n >= 58: return "high"
    if n >= 40: return "mid"
    return "low"


def _canon_denom(raw: str) -> str:
    d = raw.strip().lower()
    d = CANON_DENOM.get(d, d)
    if d.startswith("руб") and d != "рубль": d = "руб"   # «рублей», «рубля» -> руб
    return d


def _extract_denom(low: str, strict_start: bool = True) -> str | None:
    """Номинал из заголовка. strict_start — только начало строки (карточки SM
    начинаются с номинала), иначе ищем в любом месте."""
    rx = DENOM_RE if strict_start else DENOM_ANY_RE
    m = rx.search(low)
    if not m: return None
    if m.group(1):
        unit = "коп" if m.group(2).startswith("коп") else "руб"
        return f"{m.group(1)} {unit}"
    return _canon_denom(m.group(3))


def classify_type(t: str, low: str, section: str | None = None) -> str:
    """Тип лота. Порядок проверок = порядок надёжности признаков.

    Секция площадки — лишь подсказка для неоднозначных случаев: на Мешке
    продавцы кладут монеты в любые категории, а книга в «Бонах» всё равно книга.
    """
    hint = SECTION_HINTS.get(section or "")

    # 1. Однозначная печать: «Издание 2018 г.», «в 3-х томах», «с автографом».
    if BOOK_RE.search(low) and not _starts_with_denomination(low):
        return "book"
    # 2. Грейдер банкнот — железный признак (PMG65, ZG1062, Знакъ).
    for name, pat in GRADERS:
        if name in NOTE_GRADERS and re.search(pat, t):
            return "note"
    # 3. Денежные знаки/банкноты без слова «монета».
    if NOTE_RE.search(low) and "монет" not in low:
        return "note"
    if NONCOIN_RE.search(low) and not _starts_with_denomination(low):
        return "other"
    # 4. Жетон/медаль в начале заголовка — даже если дальше есть слово «монеты»
    #    («Жетон «Монеты и медали»» — это жетон).
    if MEDAL_FIRST_RE.search(low):
        return "medal"
    if SET_RE.search(low):
        return "set"
    if MEDAL_RE.search(low) and "монет" not in low:
        return "medal"
    # 5. Монета: номинал в начале или прямое слово «монета».
    if _starts_with_denomination(low) or _extract_denom(low, strict_start=False):
        if "монет" in low or _starts_with_denomination(low) or hint is None:
            return "coin"
    if re.search(r"монет", low):
        return "coin" if hint != "book" else "book"
    # 6. Ничего не опознали — доверяем секции, иначе other.
    if hint == "note": return "note"
    if hint == "medal": return "medal"
    if hint == "book": return "book"
    return "other"


def _starts_with_denomination(low: str) -> bool:
    return bool(DENOM_RE.match(low))


def parse(title: str, section: str | None = None) -> Coin:
    t = " ".join(title.split())
    low = t.lower()
    c = Coin(title=t)
    c.item_type = classify_type(t, low, section)
    c.is_set = bool(SET_RE.search(low))

    m = YEAR_RE.search(t)
    if m: c.year = int(m.group(1))

    # Номинал: сначала строго в начале (формат карточек «15 копеек 1903 …»),
    # затем в любом месте — но только для монет/банкнот, не для книг.
    if c.item_type in ("coin", "note"):
        c.denomination = _extract_denom(low, strict_start=True) or \
                         _extract_denom(low, strict_start=False)

    for code, pat in METALS:
        if re.search(pat, low):
            c.metal = code; break

    wm = WEIGHT_RE.search(low)
    if wm:
        try: c.weight_g = float(wm.group(1).replace(",", "."))
        except ValueError: pass

    mm = re.search(r"\b(СПБ|СПМД|ММД|ЛМД|ЕМ|КМ|ВМ|НМ)\b\s*[-–]?\s*([А-ЯЁ]{2})?\b", t)
    if mm:
        c.mintmaster = " ".join(x for x in mm.groups() if x)
    else:
        ini = re.search(r"\((?:г\.\s*)?([А-ЯЁ]{2})\)", t)   # «(ВС)», «(АГ)» — знаки минцмейстера
        if ini: c.mintmaster = ini.group(1)

    for name, pat in GRADERS:
        if re.search(pat, t):
            c.grader = name; break

    gm = GRADE_RE.search(t)
    if gm:
        pre = gm.group(1).upper()
        if pre in ("PROOF", "PR"): pre = "PF"
        if pre in ("UNC", "AUNC"): pre = "MS"
        num = int(gm.group(2))
        if 1 <= num <= 70:
            c.grade_num, c.grade_label = num, f"{pre}{num}"
    if c.grade_num is None:
        for w, n in WORD_GRADE:
            if re.search(rf"\b{re.escape(w)}\b", t, re.I):
                c.grade_num, c.grade_label = n, w; break

    c.grade_bucket = _bucket(c.grade_num)
    c.is_slabbed = bool(c.grader) or bool(re.search(r"слаб|капсул", low))
    c.is_romanov = bool(ROMANOV_RE.search(low))
    vm = VARIETY_RE.search(low)
    if vm: c.variety = vm.group(1).lower()
    return c
