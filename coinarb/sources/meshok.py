"""Адаптер Мешка: активные лоты + ЗАВЕРШЁННЫЕ (база сделок).

ВАЖНО: meshok.net закрыт Cloudflare Turnstile — из дата-центра запросы не проходят
(проверено: Chromium headless, Chromium full, Firefox, curl_cffi с impersonate chrome).
Поэтому адаптер работает в трёх режимах:

 1. storage_state (рекомендуется) — вы один раз заходите руками на meshok.net в
    своём браузере, сохраняете cookies, и скрапер ходит с ними.
 2. proxy — резидентный/мобильный прокси (RU), тогда Turnstile обычно проходит сам.
 3. paste/mock — вставка сохранённого HTML вручную (scan.py import-raw).

Парсер не зависит от режима: он ест HTML. Если вёрстка изменится — правится
только SELECTORS/маркеры, а не весь пайплайн.

Завершённые лоты — золото для базы сделок: на Мешке видна итоговая цена продажи.
Точный URL-параметр фильтра завершённых лотов может отличаться (проверьте в
браузере: поиск → «Завершённые») — он вынесен в константу SOLD_EXTRA.
"""
from __future__ import annotations
import re
from bs4 import BeautifulSoup

BASE = "https://meshok.net"

SELECTORS = {
    "item": ["div.item", "div.lot", "tr.item", "article"],
    "title": ["a.item-title", "a[href*='/item/']", "h3 a", "a"],
    "price": ["div.price", "span.price", "[itemprop=price]", "[class*=price]"],
    "seller": ["a[href*='/user/']", "[class*=seller]"],
}

# Маркеры завершённого/проданного лота в тексте карточки.
SOLD_MARKERS = re.compile(
    r"аукцион\s+заверш|торги\s+заверш|лот\s+заверш|заверш[её]н\w*\s+\d|"
    r"продан|куплен\w*\s+\d|итогов\w+\s+цен|ставок:\s*\d+.*заверш|"
    r"продаж\w+\s+состоял", re.I)
BIDS_RE = re.compile(r"ставок[:\s]*(\d+)|(\d+)\s+ставок", re.I)
END_DATE_RE = re.compile(r"(\d{1,2}[.:]\d{1,2}[.:]\d{2,4}|\d{1,2}\s+\w{3,10}\s+\d{4})")

# Query-параметр «показывать завершённые» в поиске/категории Мешка.
# Проверьте актуальное значение в браузере и поправьте здесь при необходимости.
SOLD_EXTRA = "&sold=1"


def _text(el) -> str:
    return " ".join(el.get_text(" ", strip=True).split()) if el else ""


def parse_price(raw: str) -> float | None:
    if not raw: return None
    cleaned = re.sub(r"[^\d,.]", "", raw).replace(",", ".")
    # "12.500" и "1.234.567" — точки как разделитель тысяч; "12.50" — десятичный
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", cleaned) or cleaned.count(".") > 1:
        cleaned = cleaned.replace(".", "")
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def parse_listing(html: str, section: str = "252", ended_only: bool = False) -> list[dict]:
    """Парсит лоты из HTML выдачи Мешка.

    ended_only=True — брать только завершённые (по SOLD_MARKERS): это источник
    фактов продаж. Для активных лотов sold=False.
    """
    soup = BeautifulSoup(html, "lxml")
    out, seen = [], set()

    # Стратегия: любые ссылки на лот — переживает смену вёрстки карточки
    for a in soup.select("a[href*='/item/']"):
        href = a.get("href", "")
        m = re.search(r"/item/(\d+)", href)
        if not m: continue
        item_id = m.group(1)
        if item_id in seen: continue
        title = _text(a)
        if len(title) < 8:                  # ссылка-картинка / кнопка
            continue
        seen.add(item_id)
        card, price = a, None
        for _ in range(5):
            card = card.parent
            if card is None: break
            pel = card.select_one(", ".join(SELECTORS["price"]))
            if pel:
                price = parse_price(_text(pel)) or parse_price(pel.get("content", ""))
                if price: break
        seller_el = card.select_one(", ".join(SELECTORS["seller"])) if card else None
        card_text = _text(card) if card else ""

        sold = bool(SOLD_MARKERS.search(card_text))
        if ended_only and not sold:
            continue
        bids = BIDS_RE.search(card_text)
        end = END_DATE_RE.search(card_text) if sold else None

        out.append({
            "source": "meshok-ended" if sold else "meshok",
            "source_id": item_id,
            "url": BASE + href if href.startswith("/") else href,
            "title": title, "price_rub": price,
            "section": section, "seller": _text(seller_el),
            "sold": sold,
            "bids": int(bids.group(1) or bids.group(2)) if bids else None,
            "end_date": end.group(1) if end else None,
        })
    return out


def parse_ended(html: str, section: str = "") -> list[dict]:
    """Только завершённые лоты — факты продаж для таблицы deals."""
    return parse_listing(html, section, ended_only=True)


def page_url(section: str = "252", page: int = 1, extra: str = "") -> str:
    if page <= 1:
        return f"{BASE}/good/{section}{extra}"
    return f"{BASE}/good/{section}?page={page}{extra}"


def search_url(query: str, page: int = 1, sold: bool = False) -> str:
    """Поиск по Мешку; sold=True — завершённые лоты (см. SOLD_EXTRA)."""
    q = query.replace(" ", "+")
    extra = SOLD_EXTRA if sold else ""
    if page <= 1:
        return f"{BASE}/search?searchText={q}{extra}"
    return f"{BASE}/search?searchText={q}&page={page}{extra}"
