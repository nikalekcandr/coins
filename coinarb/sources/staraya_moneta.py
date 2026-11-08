"""Адаптер staraya-moneta.ru: магазин (проверено на 463 лотах) и аукционы.

Магазин: разделы /shop/{section}/?SHOWALL_1=1, карточки td[id^=bx_],
цена в span.catalog-price. Этот парсер подтверждён реальным сбором.

Аукционы: EXPERIMENTAL. Разметка аукционной витрины не проверялась живьём
(сбор из дата-центра блокируется), поэтому parse_auction() — надстройка над
магазинным парсером: те же карточки + эвристики по тексту (ставки, таймер,
статус). Перед боевым запуском сравните с браузером и поправьте AUCTION_URLS
и маркеры — остальной пайплайн от этого не зависит.
"""
from __future__ import annotations
import re
from bs4 import BeautifulSoup

BASE = "https://www.staraya-moneta.ru"
SECTIONS = {69: "Царская Россия", 77: "СССР/РСФСР/РФ", 37: "Медали и жетоны",
            133: "Наборы", 123: "Боны", 140: "Антиквариат", 55: "Литература"}

# Кандидаты на адрес аукционной витрины — проверяются по очереди при harvest.
AUCTION_URLS = [
    BASE + "/auction/",
    BASE + "/auctions/",
    BASE + "/aukcii/",
]

BID_RE = re.compile(r"(?:ставок|ставки|ставк\w*)\s*[:\-]?\s*(\d+)", re.I)
BID_PRICE_RE = re.compile(r"(?:текущ\w+\s+ставк\w*|текущ\w+\s+цен\w*|ваша\s+ставк\w*)"
                          r"\s*[:\-]?\s*([\d\s\u00a0]+\d)\s*(?:руб|₽)?", re.I)
END_RE = re.compile(r"(?:заверш\w+|осталось|до\s+окончания|заканчивается)\s*"
                    r"(?:(\d{1,2}[.:]\d{1,2}[.:]\d{1,2})|(\d+\s*(?:дн|час|мин)\w*))", re.I)
SOLD_RE = re.compile(r"аукцион\s+заверш[её]н|торги\s+заверш|лот\s+продан|куплен", re.I)


def shop_url(section: int) -> str:
    return f"{BASE}/shop/{section}/?SHOWALL_1=1"


def parse_shop(html: str, section: int) -> list[dict]:
    """Карточки магазина. Проверено на реальном срезе (463 лота, 7 разделов)."""
    soup = BeautifulSoup(html, "lxml")
    out = []
    for td in soup.select("td[id^=bx_]"):
        a = td.select_one("a.tovar_name")
        if not a:
            continue
        href = a.get("href", "")
        m = re.search(r"/shop/(\d+)/(\d+)/", href)
        if not m:
            continue
        pr = td.select_one("span.catalog-price")
        price = (int(re.sub(r"\D", "", pr.get_text()))
                 if pr and re.search(r"\d", pr.get_text()) else None)
        img = td.select_one("img")
        out.append({"source": "staraya-moneta", "source_id": m.group(2),
                    "url": BASE + href,
                    "title": a.get_text(" ", strip=True), "price_rub": price,
                    "section": SECTIONS.get(int(m.group(1)), str(m.group(1))),
                    "image": img.get("src") if img else None,
                    "seller": "Старая Монета"})
    return out


def parse_auction(html: str) -> list[dict]:
    """EXPERIMENTAL: аукционные лоты SM.

    Стратегия: любые карточки-ссылки на лот + текстовые маркеры ставок/таймера.
    Цена: «текущая ставка» если найдена, иначе обычная цена карточки.
    lot.sold=True, если аукцион завершён — такие лоты уходят в базу сделок.
    """
    soup = BeautifulSoup(html, "lxml")
    out, seen = [], set()
    for a in soup.select("a[href*='/shop/'], a[href*='/auction'], a.tovar_name"):
        href = a.get("href", "")
        m = re.search(r"/(?:shop|auction\w*)/(?:\d+/)?(\d+)", href)
        if not m:
            continue
        item_id = m.group(1)
        title = " ".join(a.get_text(" ", strip=True).split())
        if len(title) < 10 or item_id in seen:
            continue
        seen.add(item_id)
        card = a
        for _ in range(4):
            if card.parent is None:
                break
            card = card.parent
            if card.select_one("[class*=price]") or BID_RE.search(card.get_text(" ", strip=True)):
                break
        text = card.get_text(" ", strip=True) if card else ""

        price = None
        bp = BID_PRICE_RE.search(text)
        if bp:
            price = int(re.sub(r"\D", "", bp.group(1)))
        if price is None:
            pr = card.select_one("[class*=price]") if card else None
            if pr and re.search(r"\d", pr.get_text()):
                price = int(re.sub(r"\D", "", pr.get_text()))

        bids = BID_RE.search(text)
        end = END_RE.search(text)
        out.append({
            "source": "staraya-moneta-auction", "source_id": item_id,
            "url": BASE + href if href.startswith("/") else href,
            "title": title, "price_rub": price, "section": "Аукцион",
            "seller": "Старая Монета (аукцион)",
            "bids": int(bids.group(1)) if bids else None,
            "ends_in": (end.group(2) or end.group(1)) if end else None,
            "sold": bool(SOLD_RE.search(text)),
        })
    return out


# обратная совместимость со старым именем из scan.py
parse_sm = parse_shop
