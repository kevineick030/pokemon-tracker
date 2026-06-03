"""Retail-Monitoring (Scraping-Engine) für deutsche Händler.

Hybrid-Ansatz:
  - "requests"-Händler: leichtgewichtig via requests + BeautifulSoup (im Executor).
  - "playwright"-Händler: optional über Playwright (async). Fehlt Playwright,
    werden diese Händler übersprungen (Hybrid-Degradation).

Resilienz:
  - Retry mit exponential Backoff (SCRAPE_RETRIES)
  - Circuit Breaker pro Händler (CIRCUIT_BREAKER_THRESHOLD Fehler -> Cooldown)
  - 429/403 -> sofortiger Cooldown
  - Rate-Limiting: zufällige Delays, max. SCRAPE_MAX_CONCURRENT parallel
  - Rotating User-Agents, optionaler Proxy
  - Captcha-Erkennung -> Skip + Logging + Admin-Warnung
"""
import re
import json
import random
import logging
import asyncio
import urllib.parse
from datetime import datetime, timedelta

import config
import database as db

log = logging.getLogger("scalp.retail")

_CAPTCHA_MARKERS = [
    "captcha", "are you a robot", "bist du ein roboter", "zugriff verweigert",
    "access denied", "unusual traffic", "ungewöhnliche aktivität",
    "verify you are human", "cf-challenge",
]


# ---------------------------------------------------------------- Parsing-Helfer
def detect_captcha(html: str) -> bool:
    low = (html or "").lower()
    return any(m in low for m in _CAPTCHA_MARKERS)


def parse_html(html: str, cfg: dict) -> tuple[bool | None, float | None]:
    """Bestimmt (in_stock, price) aus dem HTML anhand der Selektor-Konfig.

    in_stock: True/False/None (None = unbestimmt). Nutzt BeautifulSoup, wenn
    verfügbar; sonst Textsuche.
    """
    if not html:
        return None, None

    text = html
    price = None
    try:
        from bs4 import BeautifulSoup  # type: ignore
        soup = BeautifulSoup(html, "html.parser")
        # Stock-Bereich gezielt lesen, sonst gesamten Text
        stock_el = None
        if cfg.get("stock_selector"):
            stock_el = soup.select_one(cfg["stock_selector"].split(",")[0].strip())
        text = (stock_el.get_text(" ", strip=True) if stock_el
                else soup.get_text(" ", strip=True))
        if cfg.get("price_selector"):
            for sel in cfg["price_selector"].split(","):
                price_el = soup.select_one(sel.strip())
                if price_el:
                    price = _parse_price(price_el.get_text(" ", strip=True))
                    if price is not None:
                        break
    except ImportError:
        log.debug("bs4 nicht installiert — Textsuche ohne Preis-Selektor.")
        price = _parse_price(html)

    low = text.lower()
    out_kw = [k.lower() for k in cfg.get("stock_out_keywords", [])]
    in_kw = [k.lower() for k in cfg.get("stock_in_keywords", [])]
    if any(k in low for k in out_kw):
        return False, price
    if any(k in low for k in in_kw):
        return True, price
    return None, price


_PRICE_RE = re.compile(r"(\d{1,4}(?:[.\s]\d{3})*[.,]\d{2})\s*€?")


def _parse_price(text: str) -> float | None:
    if not text:
        return None
    m = _PRICE_RE.search(text)
    if not m:
        return None
    raw = m.group(1).replace(" ", "").replace(".", "").replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


# ---------------------------------------------------------------- Monitor
class RetailMonitor:
    def __init__(self):
        self.cfg = self._load_config()
        self._ensure_retailers()
        self.circuit: dict[str, dict] = {}
        self._sema = asyncio.Semaphore(config.SCRAPE_MAX_CONCURRENT)
        self._pw_sema = asyncio.Semaphore(config.PLAYWRIGHT_MAX_INSTANCES)
        self.playwright_available = self._check_playwright()
        self.captcha_warnings: list[str] = []
        if not self.playwright_available:
            log.info("Playwright nicht installiert — Browser-Händler werden "
                     "übersprungen (Hybrid-Modus).")

    @staticmethod
    def _check_playwright() -> bool:
        try:
            import playwright.async_api  # noqa: F401
            return True
        except ImportError:
            return False

    @staticmethod
    def _load_config() -> dict:
        try:
            with open(config.RETAILERS_CONFIG_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            log.error("retailers_config.json nicht lesbar: %s", exc)
            return {}

    @staticmethod
    def _ensure_retailers() -> None:
        for r in config.RETAILERS:
            db.upsert_retailer(r["name"], r["base_url"], r["scrape_method"])

    # ----------------------------------------------------- Circuit Breaker
    def _circuit_open(self, name: str) -> bool:
        state = self.circuit.get(name)
        if not state:
            return False
        until = state.get("until")
        if until and datetime.utcnow() < until:
            return True
        return False

    def _open_circuit(self, name: str, minutes: int | None = None) -> None:
        mins = minutes if minutes is not None else config.CIRCUIT_BREAKER_COOLDOWN_MIN
        self.circuit.setdefault(name, {})["until"] = (
            datetime.utcnow() + timedelta(minutes=mins)
        )
        self.circuit[name]["failures"] = 0
        log.warning("Circuit Breaker: '%s' pausiert für %d Min.", name, mins)

    def _record_failure(self, name: str) -> None:
        state = self.circuit.setdefault(name, {"failures": 0})
        state["failures"] = state.get("failures", 0) + 1
        if state["failures"] >= config.CIRCUIT_BREAKER_THRESHOLD:
            self._open_circuit(name)

    def _record_success(self, name: str) -> None:
        self.circuit[name] = {"failures": 0, "until": None}

    # ----------------------------------------------------- Fetching
    def _fetch_requests(self, url: str, cfg: dict) -> tuple[int, str]:
        """Blockierender Requests-Fetch (im Executor aufrufen)."""
        import requests
        headers = {
            "User-Agent": random.choice(config.USER_AGENTS),
            "Accept-Language": "de-DE,de;q=0.9",
        }
        proxies = None
        if config.PROXY_URL:
            proxies = {"http": config.PROXY_URL, "https": config.PROXY_URL}
        resp = requests.get(url, headers=headers, proxies=proxies, timeout=25)
        return resp.status_code, resp.text

    async def _fetch_playwright(self, url: str, cfg: dict) -> tuple[int, str]:
        """Rendert die Seite per Playwright (Browser). Nur wenn verfügbar."""
        from playwright.async_api import async_playwright
        async with self._pw_sema:
            async with async_playwright() as p:
                launch_kwargs = {"headless": True}
                if config.PROXY_URL:
                    proxy = {"server": config.PROXY_URL}
                    if config.PROXY_USERNAME:
                        proxy["username"] = config.PROXY_USERNAME
                        proxy["password"] = config.PROXY_PASSWORD
                    launch_kwargs["proxy"] = proxy
                browser = await p.chromium.launch(**launch_kwargs)
                try:
                    context = await browser.new_context(
                        user_agent=random.choice(config.USER_AGENTS),
                        locale="de-DE",
                    )
                    page = await context.new_page()
                    try:
                        from playwright_stealth import stealth_async  # type: ignore
                        await stealth_async(page)
                    except ImportError:
                        pass
                    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    if cfg.get("wait_for"):
                        try:
                            await page.wait_for_selector(cfg["wait_for"], timeout=8000)
                        except Exception:
                            pass
                    html = await page.content()
                    return 200, html
                finally:
                    await browser.close()

    async def _get_html(self, url: str, cfg: dict, name: str) -> tuple[int, str]:
        """Holt HTML mit Retry + exponential Backoff."""
        method = cfg.get("scrape_method", "requests")
        last_exc = None
        for attempt in range(config.SCRAPE_RETRIES):
            try:
                if method == "playwright":
                    return await self._fetch_playwright(url, cfg)
                loop = asyncio.get_running_loop()
                return await loop.run_in_executor(
                    None, self._fetch_requests, url, cfg
                )
            except Exception as exc:
                last_exc = exc
                backoff = (2 ** attempt) + random.uniform(0, 1)
                log.debug("Fetch-Fehler '%s' (Versuch %d): %s — backoff %.1fs",
                          name, attempt + 1, exc, backoff)
                await asyncio.sleep(backoff)
        raise last_exc if last_exc else RuntimeError("Fetch fehlgeschlagen")

    # ----------------------------------------------------- Check-Logik
    async def check_product(self, scalp_target, retailer) -> dict | None:
        """Prüft ein Produkt bei einem Händler. Gibt ein Event zurück, wenn ein
        Restock (OUT->IN) oder ein Preis <= Ziel-Einkaufspreis erkannt wird."""
        name = retailer["name"]
        cfg = self.cfg.get(name)
        if not cfg:
            return None
        if self._circuit_open(name):
            log.debug("'%s' im Cooldown — übersprungen.", name)
            return None
        if cfg.get("scrape_method") == "playwright" and not self.playwright_available:
            return None

        query = urllib.parse.quote_plus(scalp_target["product_name"])
        url = cfg["search_url"].format(query=query)

        async with self._sema:
            # Rate-Limiting: realistische Verzögerung
            await asyncio.sleep(random.uniform(config.SCRAPE_MIN_DELAY,
                                               config.SCRAPE_MAX_DELAY))
            try:
                status, html = await self._get_html(url, cfg, name)
            except Exception as exc:
                self._record_failure(name)
                db.update_retailer_check(retailer["id"], False, str(exc)[:200])
                return None

        # 429/403 -> sofortiger Cooldown
        if status in (429, 403):
            self._open_circuit(name)
            db.update_retailer_check(retailer["id"], False, f"HTTP {status}")
            return None

        # Captcha?
        if detect_captcha(html):
            self.captcha_warnings.append(name)
            self._open_circuit(name)
            db.update_retailer_check(retailer["id"], False, "Captcha erkannt")
            log.warning("Captcha bei '%s' — Cooldown + Admin-Warnung.", name)
            return None

        in_stock, price = parse_html(html, cfg)
        self._record_success(name)
        db.update_retailer_check(retailer["id"], True, None)

        # Vorherigen Status holen, dann aktuellen speichern
        last = db.get_last_stock(scalp_target["id"], retailer["id"])
        was_in_stock = bool(last["in_stock"]) if last else False
        db.add_stock_check(scalp_target["id"], retailer["id"],
                           bool(in_stock), price, url)

        if not in_stock:
            return None

        target_price = scalp_target["retail_price_target"]
        is_restock = not was_in_stock                       # OUT -> IN
        is_price_hit = (price is not None and target_price
                        and price <= target_price)

        if is_restock or is_price_hit:
            return {
                "scalp_target_id": scalp_target["id"],
                "product_name": scalp_target["product_name"],
                "retailer_id": retailer["id"],
                "retailer_name": name,
                "price": price,
                "url": url,
                "uvp": None,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "source": "retail",
                "reason": "restock" if is_restock else "price",
            }
        return None

    async def check_all_active_targets(self) -> list[dict]:
        """Iteriert Scalp-Targets × Händler, parallel (begrenzt). Gibt Events."""
        self.captcha_warnings = []
        targets = db.get_scalp_targets(active_only=True)
        retailers = db.get_retailers(active_only=True)
        if not targets or not retailers:
            return []

        tasks = [
            self.check_product(t, r)
            for t in targets for r in retailers
            if r["name"] != "hotstock"     # HotStock läuft separat
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        events = []
        for res in results:
            if isinstance(res, Exception):
                log.debug("Check-Exception: %s", res)
            elif res:
                events.append(res)
        log.info("Retail-Scan: %d Targets × %d Händler -> %d Events.",
                 len(targets), len(retailers), len(events))
        return events
