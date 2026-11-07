"""Адаптер Мешка.

ВАЖНО: meshok.net закрыт Cloudflare Turnstile — из дата-центра запросы не проходят
(проверено: Chromium headless, Chromium full, Firefox, curl_cffi с impersonate chrome).
Поэтому адаптер работает в трёх режимах:

 1. storage_state (рекомендуется) — вы один раз заходите руками на meshok.net в
    своём браузере, сохраняете cookies, и скрапер ходит с ними.
 2. proxy — резидентный/мобильный прокси (RU), тогда Turnstile обычно проходит сам.
 3. paste/mock — вставка сохранённого HTML вручную.

Парсер не зависит от режима: он ест HTML. Если вёрстка изменится — правится
только SELECTORS, а не весь пайплайн.
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

def parse_listing(html: str, section: str = "252") -> list[dict]:
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
        out.append({
            "source": "meshok", "source_id": item_id,
            "url": BASE + href if href.startswith("/") else href,
            "title": title, "price_rub": price,
            "section": section, "seller": _text(seller_el),
        })
    return out

def page_url(section: str = "252", page: int = 1, extra: str = "") -> str:
    if page <= 1:
        return f"{BASE}/good/{section}{extra}"
    return f"{BASE}/good/{section}?page={page}{extra}"
