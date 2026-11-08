"""Единый слой доступа к сайтам: Playwright + кэш + rate-limit + ретраи.

Playwright импортируется лениво: аналитика, импорт архивов и отчёты работают
без него — он нужен только для живого сбора (harvest-*).
"""
from __future__ import annotations
import asyncio, hashlib, json, random, time
from pathlib import Path

try:  # ленивый импорт: без playwright остальной пайплайн обязан работать
    from playwright.async_api import async_playwright, Browser, BrowserContext
except ImportError:  # pragma: no cover
    async_playwright = None
    Browser = BrowserContext = object

CACHE = Path(__file__).resolve().parent.parent / "cache"
CACHE.mkdir(parents=True, exist_ok=True)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

STEALTH = """
Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});
Object.defineProperty(navigator,'languages',{get:()=>['ru-RU','ru','en-US','en']});
window.chrome={runtime:{}};
"""

def load_storage_state(path: str) -> dict:
    """Accept Playwright storage state or an EditThisCookie JSON export."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            state = json.load(f)
    except (OSError, ValueError) as e:
        raise ValueError(f"Не удалось прочитать файл cookies {path}: {e}") from e
    if isinstance(state, dict) and isinstance(state.get("cookies"), list):
        return state
    if not isinstance(state, list):
        raise ValueError("Файл cookies должен содержать список EditThisCookie или storage state Playwright")
    cookies = []
    for item in state:
        if not isinstance(item, dict) or not all(k in item for k in ("name", "value", "domain")):
            raise ValueError("Неверная запись cookies: нужны name, value и domain")
        cookie = {k: item[k] for k in ("name", "value", "domain", "secure", "httpOnly") if k in item}
        cookie["path"] = item.get("path") or "/"
        expires = item.get("expirationDate", item.get("expires"))
        if isinstance(expires, (int, float)) and not isinstance(expires, bool) and expires > 0:
            cookie["expires"] = expires
        same_site = str(item.get("sameSite", "")).lower()
        if same_site == "no_restriction":
            same_site = "none"
        if same_site in ("strict", "lax", "none"):
            cookie["sameSite"] = same_site.capitalize()
        cookies.append(cookie)
    return {"cookies": cookies, "origins": []}


class Fetcher:
    """Асинхронный фетчер. cache=True -> второй запуск не бьёт по сайту."""

    def __init__(self, headless=True, delay=(1.5, 3.5), cache=True,
                 proxy: str | None = None, storage_state: str | None = None):
        self.headless, self.delay, self.cache = headless, delay, cache
        self.proxy, self.storage_state = proxy, storage_state
        self._b: Browser | None = None
        self._ctx: BrowserContext | None = None
        self._pw = None

    async def __aenter__(self):
        if async_playwright is None:
            raise RuntimeError(
                "playwright не установлен — живой сбор недоступен. "
                "Установка: pip install playwright && python -m playwright install chromium")
        # Validate before launching the browser; never silently ignore a missing file.
        state = load_storage_state(self.storage_state) if self.storage_state else None
        self._pw = await async_playwright().start()
        try:
            kw = {"headless": self.headless,
                  "args": ["--no-sandbox", "--disable-blink-features=AutomationControlled",
                           "--disable-dev-shm-usage"]}
            if self.proxy:
                kw["proxy"] = {"server": self.proxy}
            self._b = await self._pw.chromium.launch(**kw)
            ctx_kw = dict(user_agent=UA, locale="ru-RU", timezone_id="Europe/Moscow",
                          viewport={"width": 1440, "height": 900},
                          extra_http_headers={"Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8"})
            if state is not None:
                ctx_kw["storage_state"] = state
            self._ctx = await self._b.new_context(**ctx_kw)
            await self._ctx.add_init_script(STEALTH)
            return self
        except BaseException:
            await self.__aexit__(None, None, None)
            raise

    async def __aexit__(self, *a):
        if self._ctx: await self._ctx.close()
        if self._b: await self._b.close()
        if self._pw: await self._pw.stop()

    def _cache_path(self, url: str) -> Path:
        return CACHE / (hashlib.sha1(url.encode()).hexdigest() + ".json")

    async def get(self, url: str, wait: int = 2500, retries: int = 3,
                  challenge_check: bool = True) -> str:
        cp = self._cache_path(url)
        if self.cache and cp.exists():
            return json.loads(cp.read_text())["html"]

        last = ""
        for attempt in range(retries):
            await asyncio.sleep(random.uniform(*self.delay) * (attempt + 1))
            page = await self._ctx.new_page()
            try:
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                status = resp.status if resp else 0
                await page.wait_for_timeout(wait)
                html = await page.content()
                title = await page.title()
                blocked = ("Just a moment" in title or "Один момент" in title
                           or "AjaxLoader" in html[:2000] or status == 403)
                if blocked and challenge_check:
                    # дать шанс JS-челленджу пройти
                    for _ in range(6):
                        await page.wait_for_timeout(4000)
                        title = await page.title()
                        if "Один момент" not in title and "Just a moment" not in title:
                            html = await page.content(); blocked = False; break
                if blocked:
                    raise RuntimeError(f"BLOCKED/CHALLENGE not passed: {url} (status {status}, title={title!r})")
                cp.write_text(json.dumps({"url": url, "status": status, "html": html}))
                return html
            except Exception as e:
                last = str(e)
            finally:
                await page.close()
        raise RuntimeError(f"fetch failed after {retries}: {url} :: {last}")
