"""Единый слой доступа к сайтам: Playwright + кэш + rate-limit + ретраи."""
from __future__ import annotations
import asyncio, hashlib, json, os, random, time
from pathlib import Path
from playwright.async_api import async_playwright, Browser, BrowserContext

CACHE = Path(__file__).resolve().parent.parent / "cache"
CACHE.mkdir(exist_ok=True)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

STEALTH = """
Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});
Object.defineProperty(navigator,'languages',{get:()=>['ru-RU','ru','en-US','en']});
window.chrome={runtime:{}};
"""

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
        self._pw = await async_playwright().start()
        kw = {"headless": self.headless,
              "args": ["--no-sandbox", "--disable-blink-features=AutomationControlled",
                       "--disable-dev-shm-usage"]}
        if self.proxy:
            kw["proxy"] = {"server": self.proxy}
        self._b = await self._pw.chromium.launch(**kw)
        ctx_kw = dict(user_agent=UA, locale="ru-RU", timezone_id="Europe/Moscow",
                      viewport={"width": 1440, "height": 900},
                      extra_http_headers={"Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8"})
        if self.storage_state and os.path.exists(self.storage_state):
            ctx_kw["storage_state"] = self.storage_state
        self._ctx = await self._b.new_context(**ctx_kw)
        await self._ctx.add_init_script(STEALTH)
        return self

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
