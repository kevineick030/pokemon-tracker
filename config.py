"""Zentrale Konfiguration. Lädt .env und stellt Einstellungen bereit."""
import os
import logging
from pathlib import Path
from dotenv import load_dotenv

# override=True: Werte aus der .env haben Vorrang vor bereits gesetzten
# Umgebungsvariablen (sonst kann eine leere OS-Variable die .env aushebeln).
load_dotenv(override=True)

BASE_DIR = Path(__file__).resolve().parent

# --- Telegram ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# --- Cardmarket API v2.0 (OAuth 1.0a) ---
MKM_APP_TOKEN = os.getenv("MKM_APP_TOKEN", "")
MKM_APP_SECRET = os.getenv("MKM_APP_SECRET", "")
MKM_ACCESS_TOKEN = os.getenv("MKM_ACCESS_TOKEN", "")
MKM_ACCESS_TOKEN_SECRET = os.getenv("MKM_ACCESS_TOKEN_SECRET", "")
MKM_SANDBOX = os.getenv("MKM_SANDBOX", "false").lower() == "true"

MKM_BASE_URL = (
    "https://sandbox.cardmarket.com/ws/v2.0/output.json"
    if MKM_SANDBOX
    else "https://api.cardmarket.com/ws/v2.0/output.json"
)

# --- Anthropic / Claude ---
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = "claude-haiku-4-5"
CLAUDE_MAX_TOKENS = 500

# --- Google Gemini (Bilderkennung) ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-2.5-flash"
MAX_IMAGE_BYTES = 5 * 1024 * 1024     # max 5 MB pro Foto
MAX_IMAGES_PER_HOUR = 20              # Rate-Limit Bilderkennung

# --- Pokémon TCG API (pokemontcg.io) als kostenlose Preis-Quelle ---
# Funktioniert OHNE Key (begrenzte Rate); mit Key (aus dev.pokemontcg.io)
# höheres Limit. Liefert u.a. Cardmarket-EUR-Preise (low/avg/trend).
POKEMONTCG_API_KEY = os.getenv("POKEMONTCG_API_KEY", "")
POKEMONTCG_BASE_URL = "https://api.pokemontcg.io/v2"

# --- Cardmarket Price Guide (taeglich, kein API-Key noetig) ---
# Oeffentlicher S3-Link (Cardmarket ersetzt die deprecated API dadurch).
# Enthaelt ~75k Pokemon-Produkte mit low/trend/avg/avg7/avg30 (EUR).
CM_PRICE_GUIDE_URL = os.getenv(
    "CM_PRICE_GUIDE_URL",
    "https://downloads.s3.cardmarket.com/productCatalog/priceGuide/price_guide_6.json",
)
CM_PRICE_GUIDE_DOWNLOAD_HOUR = 6   # 06:00 taeglich (Cardmarket aktualisiert morgens)

# --- Datenbank ---
DB_PATH = str(BASE_DIR / "pokemon_tracker.db")

# --- Karten-Fotos ---
CARD_IMAGES_DIR = BASE_DIR / "card_images"
CARD_IMAGES_DIR.mkdir(exist_ok=True)

# --- Scanner / Deal-Logik (Defaults, zur Laufzeit per Command änderbar) ---
SCAN_INTERVAL_MINUTES = 30
BRIEFING_HOUR = 9          # tägliches Briefing 09:00
PORTFOLIO_VALUATION_HOUR = 2  # tägliche Wertaktualisierung 02:00

DEFAULT_SAVINGS_THRESHOLD = 20.0   # % Mindestersparnis für "Top-Deal"-Markierung
DEFAULT_MIN_SCORE = 60             # Alert ab diesem Deal-Score
DEFAULT_WATCHLIST_ALERT_THRESHOLD = 15.0  # Default-Ersparnis (%) beim Foto-Watchlist-Flow
MIN_SELLER_REPUTATION = 98.0       # nur Verkäufer >= 98%
SELLER_COUNTRY = "D"               # nur DE-Verkäufer
MARKET_PRICE_SAMPLE_SIZE = 10      # Median der letzten N DE-Angebote

# --- Scalping-Modul ---
SCALP_LOG_FILE = str(BASE_DIR / "scalp_monitor.log")
RETAILERS_CONFIG_FILE = str(BASE_DIR / "retailers_config.json")

# Scheduler-Intervalle (Sekunden bzw. Stunden)
RETAIL_CHECK_INTERVAL_SECONDS = 120
HOTSTOCK_CHECK_INTERVAL_SECONDS = 60
SEALED_PRICES_UPDATE_HOURS = 6
RELEASE_CHECK_HOUR = 9

# Profit & Alerts
MIN_PROFIT_MARGIN_PCT = float(os.getenv("MIN_PROFIT_MARGIN_PCT", "20"))
RESTOCK_ALERT_DEDUPE_HOURS = int(os.getenv("RESTOCK_ALERT_DEDUPE_HOURS", "6"))

# Gebühren-Annahmen für den Profit-Rechner
CARDMARKET_FEE_PCT = 0.05
PAYPAL_FEE_PCT = 0.0249
PAYPAL_FEE_FIXED = 0.35
SHIPPING_DHL = 6.99
PACKAGING_COST = 1.00
QUICK_SELL_DISCOUNT = 0.92   # realistischer Schnellverkauf = 92 % des avg

# Anti-Bot / Scraping
SCRAPE_MAX_CONCURRENT = 3
PLAYWRIGHT_MAX_INSTANCES = 2
SCRAPE_MIN_DELAY = 2.0       # Sekunden
SCRAPE_MAX_DELAY = 8.0
SCRAPE_RETRIES = 3
CIRCUIT_BREAKER_THRESHOLD = 3   # Fehler bis Pause
CIRCUIT_BREAKER_COOLDOWN_MIN = 30
HOTSTOCK_RSS_URL = "https://www.hotstock.de/rss"
FUZZY_MATCH_THRESHOLD = 80   # 0-100

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
]

# Proxy (optional, anti-bot)
PROXY_URL = os.getenv("PROXY_URL", "")
PROXY_USERNAME = os.getenv("PROXY_USERNAME", "")
PROXY_PASSWORD = os.getenv("PROXY_PASSWORD", "")

# Initiale Händler-Liste (Selektoren liegen in retailers_config.json).
# scrape_method: "requests" (leichtgewichtig) oder "playwright" (optional).
RETAILERS = [
    {"name": "mueller",        "base_url": "https://www.mueller.de",            "scrape_method": "playwright"},
    {"name": "smythstoys",     "base_url": "https://www.smythstoys.com/de",     "scrape_method": "playwright"},
    {"name": "mediamarkt",     "base_url": "https://www.mediamarkt.de",         "scrape_method": "playwright"},
    {"name": "saturn",         "base_url": "https://www.saturn.de",             "scrape_method": "playwright"},
    {"name": "galeria",        "base_url": "https://www.galeria.de",            "scrape_method": "requests"},
    {"name": "rossmann",       "base_url": "https://www.rossmann.de",           "scrape_method": "requests"},
    {"name": "thalia",         "base_url": "https://www.thalia.de",             "scrape_method": "requests"},
    {"name": "amazon",         "base_url": "https://www.amazon.de",             "scrape_method": "playwright"},
    {"name": "pokemoncenter",  "base_url": "https://www.pokemoncenter.com",     "scrape_method": "playwright"},
    {"name": "pokemoncards24", "base_url": "https://www.pokemoncards24.com",    "scrape_method": "requests"},
    {"name": "tradingcards",   "base_url": "https://www.tradingcards.de",       "scrape_method": "requests"},
]

# --- Logging ---
LOG_FILE = str(BASE_DIR / "pokemon_tracker.log")
LOG_LEVEL = logging.INFO


def setup_logging() -> logging.Logger:
    """Konfiguriert Logging in Datei + Konsole. Idempotent."""
    logger = logging.getLogger()
    if logger.handlers:
        return logger
    logger.setLevel(LOG_LEVEL)
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)

    # python-telegram-bot / httpx weniger gesprächig
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)

    # Scalp-Monitoring zusätzlich in eigene Datei
    scalp_logger = logging.getLogger("scalp")
    if not any(isinstance(h, logging.FileHandler)
               and getattr(h, "baseFilename", "").endswith("scalp_monitor.log")
               for h in scalp_logger.handlers):
        scalp_handler = logging.FileHandler(SCALP_LOG_FILE, encoding="utf-8")
        scalp_handler.setFormatter(fmt)
        scalp_logger.addHandler(scalp_handler)
    return logger


def validate() -> list[str]:
    """Gibt fehlende PFLICHT-Werte zurück.

    Nur der Telegram-Bot-Token ist zwingend nötig, damit der Bot überhaupt
    startet (Schnellstart). Alles andere ist optional und schaltet einzelne
    Funktionen frei (siehe optional_status()).
    """
    missing = []
    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    return missing


def cardmarket_enabled() -> bool:
    """True, wenn alle 4 Cardmarket-Tokens gesetzt sind."""
    return all([MKM_APP_TOKEN, MKM_APP_SECRET, MKM_ACCESS_TOKEN,
                MKM_ACCESS_TOKEN_SECRET])


def optional_status() -> list[str]:
    """Warnungen zu optionalen, nicht gesetzten Funktionen (Bot startet trotzdem)."""
    msgs = []
    if not TELEGRAM_CHAT_ID:
        msgs.append("TELEGRAM_CHAT_ID fehlt -> der Bot ist fuer JEDEN nutzbar und "
                    "es werden keine automatischen Alerts gesendet.")
    if not all([MKM_APP_TOKEN, MKM_APP_SECRET, MKM_ACCESS_TOKEN,
                MKM_ACCESS_TOKEN_SECRET]):
        msgs.append("Cardmarket-Tokens fehlen -> Einzelkarten-Preise laufen ueber "
                    "pokemontcg.io; Live-Schnaeppchen-Scanner und Sealed-Preise "
                    "(versiegelte Produkte) sind eingeschraenkt.")
    if not ANTHROPIC_API_KEY:
        msgs.append("ANTHROPIC_API_KEY fehlt -> der KI-Freitext-Chat ist deaktiviert.")
    if not GEMINI_API_KEY:
        msgs.append("GEMINI_API_KEY fehlt -> die Foto-Bilderkennung ist deaktiviert.")
    return msgs
