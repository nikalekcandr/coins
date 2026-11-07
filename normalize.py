"""Нормализация русскоязычных нумизматических заголовков в структурированные атрибуты.

Ключевая задача: превратить "Рубль 1913 г. (ВС) 300 лет дома Романовых,
выпуклый чекан, в слабе ННР MS 62" в объект, по которому можно считать comps.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field, asdict

METALS = [
    ("Pt", r"платин"), ("Pd", r"паллади"), ("Au", r"золот"),
    ("Ag", r"серебр|серебрян"), ("Cu", r"медь|медн|бронз"),
    ("Ni", r"никел"), ("Bil", r"билон"), ("Br", r"латун"),
]
GRADERS = [("NGC", r"\bNGC\b"), ("PCGS", r"\bPCGS\b"), ("ННР", r"\bННР\b"),
           ("NGS", r"\bNGS\b"), ("ANACS", r"\bANACS\b"), ("RNGA", r"\bRNGA\b"),
           ("NRG", r"\bNRG\b"), ("CPRC", r"\bCPRC\b"),        # встречаются опечатки/мелкие конторы
           ("PMG", r"\bPMG\b"), ("Знакъ", r"Знакъ"), ("ZG", r"\bZG\b"), ("PG", r"\bPG\s?\d")]
GRADE_RE = re.compile(r"\b(PF|PR|PROOF|MS|AU|XF|EF|UNC|BU|VF|F|VG|AG|G)\s*[-\s]?\s*(\d{1,2})\b", re.I)
WORD_GRADE = {"UNC": 62, "BU": 64, "PROOF": 63, "PR": 63, "XF": 42, "EF": 42,
              "AU": 53, "VF": 30, "F": 15, "VG": 10, "AG": 3, "G": 6}
YEAR_RE = re.compile(r"\b(1[5-9]\d{2}|20[0-2]\d)\b")
WEIGHT_RE = re.compile(r"(\d+[.,]\d{1,2})\s*г\b")
DENOM_RE = re.compile(
    r"^\s*(?:(\d+)\s*(рубл\w*|р\b|копе\w*|коп\b)"
    r"|(полтина|полтинник|рубль|руб\b|копейка|полушка|деньга|алтын|"
    r"гривенник|пятак|двугривенн\w*|пятнадцатикопеечник|червонец|"
    r"империал|полуимпериал|десятирублёвик))\b", re.I)
NOTE_RE = re.compile(r"билет|банкнот|бона|бон\b|кредитн|казначейск|червонец 19\d\d|"
                     r"приватизационн|чек\b|марк[аи]\b|расчетн|расчётн", re.I)
MEDAL_RE = re.compile(r"медал|жетон|знак\b|орден|награ", re.I)
SET_RE = re.compile(r"набор|комплект|лот из|годовой набор|полный комплект|"
                    r"коллекци\w* (?:альбом|набор)|в альбоме", re.I)
NONCOIN_RE = re.compile(r"книг|каталог|литератур|мюнцкабинет|холдер|альбом для|футляр|бокс", re.I)
VARIETY_RE = re.compile(r"(выпукл\w* чекан|вогнут\w* чекан|плоский чекан|новодел|"
                        r"перепутк\w*|надчекан\w*|брак|рестрайк|фальш\w*)", re.I)

@dataclass
class Coin:
    title: str
    item_type: str = "unknown"     # coin | note | medal | set | other
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

    def to_dict(self):
        return asdict(self)

def _bucket(n: int | None) -> str:
    if n is None: return "raw"
    if n >= 65: return "gem"
    if n >= 58: return "high"
    if n >= 40: return "mid"
    return "low"

def classify_type(t: str, low: str) -> str:
    """Монета — только если есть номинал или прямое слово 'монета'.

    Иначе фотографии, открытки, книги и знаки попадают в монеты и портят comps.
    """
    # PMG — грейдер банкнот: надёжный признак того, что перед нами не монета
    if re.search(r"\bPMG\b", t): return "note"
    if NONCOIN_RE.search(low): return "other"
    if SET_RE.search(low): return "set"
    if NOTE_RE.search(low) and "монет" not in low: return "note"
    if MEDAL_RE.search(low) and "монет" not in low: return "medal"
    has_denom = bool(DENOM_RE.search(low))
    has_coin_word = bool(re.search(r"монет", low))
    if has_denom or has_coin_word: return "coin"
    return "other"

def parse(title: str) -> Coin:
    t = " ".join(title.split())
    low = t.lower()
    c = Coin(title=t)
    c.item_type = classify_type(t, low)
    c.is_set = bool(SET_RE.search(low))

    m = YEAR_RE.search(t)
    if m: c.year = int(m.group(1))

    dm = DENOM_RE.search(low)
    if dm:
        if dm.group(1):
            c.denomination = f"{dm.group(1)} {('коп' if dm.group(2).startswith('коп') else 'руб')}"
        else:
            c.denomination = dm.group(3).strip()

    for code, pat in METALS:
        if re.search(pat, low):
            c.metal = code; break

    wm = WEIGHT_RE.search(low)
    if wm:
        try: c.weight_g = float(wm.group(1).replace(",", "."))
        except ValueError: pass

    mm = re.search(r"\b(СПБ|СПМД|ММД|ЛМД)\b\s*[-–]?\s*([А-ЯЁ]{2})?\b", t)
    if mm: c.mintmaster = " ".join(x for x in mm.groups() if x)

    for name, pat in GRADERS:
        if re.search(pat, t):
            c.grader = name; break

    gm = GRADE_RE.search(t)
    if gm:
        pre = gm.group(1).upper()
        if pre in ("PROOF", "PR"): pre = "PF"
        if pre == "UNC": pre = "MS"
        num = int(gm.group(2))
        if 1 <= num <= 70:
            # PROOF 66 / MS 62 — валидно; отсечь "MS 12" как случайность
            c.grade_num, c.grade_label = num, f"{pre}{num}"
    if c.grade_num is None:
        for w, n in WORD_GRADE.items():
            if re.search(rf"\b{w}\b", low):
                c.grade_num, c.grade_label = n, w; break

    c.grade_bucket = _bucket(c.grade_num)
    c.is_slabbed = bool(c.grader) or bool(re.search(r"слаб|капсул", low))
    c.is_romanov = bool(re.search(r"романов|300\s*лет\s*дома", low))
    vm = VARIETY_RE.search(low)
    if vm: c.variety = vm.group(1)
    return c
