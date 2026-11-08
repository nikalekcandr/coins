"""Экономика сделки: тарифы NGS, комиссии площадок, доставка, налоги.

Значения по умолчанию взяты из публичных тарифов (см. docs/PLAYBOOK.md).
ВСЁ настраивается через параметры — не хардкодьте в логике.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict

# Тарифы NGS (грейдинг + слабирование), руб. Официальная таблица ngs.vc/tarify/tarify
NGS_TARIFFS = [
    # name, price_rub, max_market_value_rub, days
    ("Прегрейд",       500,  float("inf"), 0),
    ("Базовый",        750,  10_000,  7),
    ("Стандарт",      1200,  50_000,  7),
    ("Классик",       1500, 100_000,  7),
    ("Оптимум",       2500, 200_000,  5),
    ("ТОП",           3500, 500_000,  3),
    ("Элит",          4500, float("inf"), 2),
]
NGS_EXTRAS = {          # доплаты
    "return_without_slab": 250,
    "oversize_capsule": 250,
    "proof_ultra_cameo": 200,
}
NGS_DISCOUNTS = [(100, 0.10), (50, 0.05)]   # от 100 монет -10%, от 50 -5%

# "Прегрейд" — отдельная срочная услуга (1-2 часа), в автоподборе не участвует
_GRADING_LADDER = [t for t in NGS_TARIFFS if t[0] != "Прегрейд"]

def ngs_tariff(market_value_rub: float) -> tuple[str, int]:
    """Минимальный стандартный тариф, покрывающий монету данной рыночной стоимости."""
    for name, price, cap, _days in _GRADING_LADDER:
        if market_value_rub <= cap:
            return name, price
    return _GRADING_LADDER[-1][0], _GRADING_LADDER[-1][1]

# Комиссия Мешка (офиц. база знаний): 3% от стоимости лота, потолок 10 000 ₽ за единицу.
MESHOK_RATE = 0.03
MESHOK_CAP = 10_000.0

def meshok_fee(price: float) -> float:
    return min(price * MESHOK_RATE, MESHOK_CAP)

@dataclass
class DealTerms:
    """Все допущения сделки в одном месте, чтобы отчёт был воспроизводим."""
    slab_tariff: str = "auto"        # auto | имя тарифа
    ship_in_rub: float = 400.0       # доставка монеты в NGS и обратно
    ship_out_rub: float = 400.0      # доставка покупателю
    marketplace: str = "meshok"
    selling_fee_rate: float | None = None   # None -> по тарифу площадки
    # Скидка на слаб: во сколько раз цена в слабе NGS ниже, чем в NGC/PCGS/ННР.
    # 1.0 = паритет. Это КЛЮЧЕВОЙ параметр, который нужно измерить на данных, а не выдумать.
    ngs_discount: float = 0.70
    # Ожидаемый прирост цены от "повышения грейда" при слабировании
    grade_uplift: float = 0.0        # дополнительный множитель, 0 = не закладываем
    tax_rate: float = 0.0            # НДФЛ/УСН, 0 если не учитываем
    risk_haircut: float = 0.15       # резерв на возвраты/неликвид/подделки
    cushion_rub: float = 0.0         # запас на торг

    def to_dict(self): return asdict(self)

@dataclass
class DealEconomics:
    buy_price: float
    slab_cost: float
    tariff_name: str
    ship_cost: float
    net_sale_price: float
    gross_profit: float
    net_profit: float
    margin_pct: float
    roi_pct: float
    breakeven_sale_price: float

def compute(buy_price: float, expected_sale_price: float, terms: DealTerms,
            market_value_hint: float | None = None) -> DealEconomics:
    mv = market_value_hint if market_value_hint else expected_sale_price
    if terms.slab_tariff == "auto":
        tariff_name, slab = ngs_tariff(mv)
    elif terms.slab_tariff == "none":
        # перепродажа как есть (кросс-площадочный арбитраж, флип слаба) — без NGS
        tariff_name, slab = "—", 0
    else:
        match = [t for t in NGS_TARIFFS if t[0].lower() == terms.slab_tariff.lower()]
        tariff_name, slab = (match[0][0], match[0][1]) if match else ngs_tariff(mv)

    ship = terms.ship_in_rub + terms.ship_out_rub
    fee_rate = terms.selling_fee_rate
    sell_fee = (expected_sale_price * fee_rate) if fee_rate is not None \
        else (meshok_fee(expected_sale_price) if terms.marketplace == "meshok" else 0.0)
    net_sale = expected_sale_price - sell_fee
    tax = max(0.0, net_sale - buy_price - slab - ship) * terms.tax_rate
    gross = expected_sale_price - buy_price - slab - ship
    risk = gross * terms.risk_haircut
    net = gross - sell_fee - tax - risk - terms.cushion_rub
    cost = buy_price + slab + ship
    return DealEconomics(
        buy_price=buy_price, slab_cost=slab, tariff_name=tariff_name, ship_cost=ship,
        net_sale_price=round(net_sale, 2), gross_profit=round(gross, 2),
        net_profit=round(net, 2),
        margin_pct=round(100 * net / expected_sale_price, 1) if expected_sale_price else 0.0,
        roi_pct=round(100 * net / cost, 1) if cost else 0.0,
        breakeven_sale_price=round(cost + sell_fee + risk + tax + terms.cushion_rub, 2),
    )
