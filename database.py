"""SQLite-Datenzugriffsschicht. Schema-Initialisierung + alle DB-Operationen.

Verwendet eine einfache Connection-pro-Aufruf-Strategie (SQLite mit
check_same_thread=False), was für die Last dieses Bots (Scan alle 30 Min)
völlig ausreichend ist.
"""
import json
import sqlite3
import logging
from datetime import datetime, timedelta
from contextlib import contextmanager

import config

log = logging.getLogger(__name__)


SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT NOT NULL,
    cardmarket_product_id INTEGER,
    added_at            TEXT NOT NULL,
    language_preference TEXT DEFAULT 'DE',
    alert_threshold     REAL          -- NULL = globale Schwelle nutzen
);

CREATE TABLE IF NOT EXISTS price_history (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id           INTEGER NOT NULL,
    price             REAL NOT NULL,
    seller_country    TEXT,
    seller_reputation REAL,
    condition         TEXT,
    language          TEXT,
    timestamp         TEXT NOT NULL,
    FOREIGN KEY (card_id) REFERENCES cards(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS alerts_sent (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id      INTEGER NOT NULL,
    price        REAL NOT NULL,
    market_price REAL,
    savings_pct  REAL,
    deal_score   INTEGER,
    sent_at      TEXT NOT NULL,
    FOREIGN KEY (card_id) REFERENCES cards(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS portfolio (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    card_name             TEXT NOT NULL,
    cardmarket_product_id INTEGER,
    purchase_price        REAL NOT NULL,
    purchase_date         TEXT NOT NULL,
    condition             TEXT,
    language              TEXT,
    set_name              TEXT,
    card_number           TEXT,
    rarity                TEXT,
    image_path            TEXT,
    notes                 TEXT
);

CREATE TABLE IF NOT EXISTS portfolio_value_history (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio_card_id INTEGER NOT NULL,
    market_value      REAL,
    timestamp         TEXT NOT NULL,
    FOREIGN KEY (portfolio_card_id) REFERENCES portfolio(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS budget_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    amount      REAL NOT NULL,
    description TEXT,
    date        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS image_requests (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id          TEXT,
    timestamp        TEXT NOT NULL,
    card_recognized  TEXT,
    success          INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS scan_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    cards_scanned INTEGER DEFAULT 0,
    alerts_sent   INTEGER DEFAULT 0
);

-- Scalp-Watchlist (versiegelte Produkte).
CREATE TABLE IF NOT EXISTS scalp_targets (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    product_name        TEXT NOT NULL,
    product_type        TEXT,         -- display/etb/bundle/collection/tin/box
    ean                 TEXT,
    set_name            TEXT,
    retail_price_target REAL,
    min_profit_margin   REAL DEFAULT 20,
    image_path          TEXT,
    active              INTEGER DEFAULT 1,
    added_at            TEXT NOT NULL,
    notes               TEXT
);

CREATE TABLE IF NOT EXISTS retailers (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL UNIQUE,
    base_url      TEXT,
    scrape_method TEXT,               -- playwright / requests
    active        INTEGER DEFAULT 1,
    last_check    TEXT,
    success_rate  REAL DEFAULT 1.0,
    last_error    TEXT
);

CREATE TABLE IF NOT EXISTS retail_stock_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scalp_target_id INTEGER NOT NULL,
    retailer_id     INTEGER NOT NULL,
    in_stock        INTEGER DEFAULT 0,
    price           REAL,
    url             TEXT,
    checked_at      TEXT NOT NULL,
    FOREIGN KEY (scalp_target_id) REFERENCES scalp_targets(id) ON DELETE CASCADE,
    FOREIGN KEY (retailer_id) REFERENCES retailers(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS restock_alerts_sent (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scalp_target_id INTEGER NOT NULL,
    retailer_id     INTEGER NOT NULL,
    price           REAL,
    sent_at         TEXT NOT NULL,
    FOREIGN KEY (scalp_target_id) REFERENCES scalp_targets(id) ON DELETE CASCADE,
    FOREIGN KEY (retailer_id) REFERENCES retailers(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS cardmarket_sealed_prices (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    product_name TEXT NOT NULL,
    low_price    REAL,
    avg_price    REAL,
    trend_price  REAL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pokemon_releases (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    set_name            TEXT NOT NULL,
    release_date        TEXT NOT NULL,   -- ISO YYYY-MM-DD
    product_types       TEXT,            -- JSON-Liste
    uvp_prices          TEXT,            -- JSON-Objekt
    pre_order_available INTEGER DEFAULT 0,
    notes               TEXT
);

-- SIR/IR-Karten-Cache (taeglich via TCGdex befuellt).
-- Enthaelt idProduct (Cardmarket-ID) fuer alle Special/Illustration/Hyper Rares.
CREATE TABLE IF NOT EXISTS sir_ir_cards (
    id_product  INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    set_name    TEXT,
    set_id      TEXT,
    number      TEXT,
    rarity      TEXT,
    cm_url      TEXT,
    updated_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_sir_set ON sir_ir_cards(set_id, number);

-- Taeglich aktualisierter Cardmarket Price Guide (kein API-Key noetig).
-- Befuellt von cm_priceguide.download_and_import() um 06:00 Uhr.
CREATE TABLE IF NOT EXISTS cm_price_guide (
    id_product  INTEGER PRIMARY KEY,
    avg         REAL,
    low         REAL,
    trend       REAL,
    avg1        REAL,
    avg7        REAL,
    avg30       REAL,
    avg_holo    REAL,
    low_holo    REAL,
    trend_holo  REAL,
    avg1_holo   REAL,
    avg7_holo   REAL,
    avg30_holo  REAL,
    updated_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_price_history_card ON price_history(card_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_alerts_card ON alerts_sent(card_id, sent_at);
CREATE INDEX IF NOT EXISTS idx_pvh_card ON portfolio_value_history(portfolio_card_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_rsh_target ON retail_stock_history(scalp_target_id, retailer_id, checked_at);
CREATE INDEX IF NOT EXISTS idx_restock_alerts ON restock_alerts_sent(scalp_target_id, retailer_id, sent_at);
CREATE INDEX IF NOT EXISTS idx_sealed_name ON cardmarket_sealed_prices(product_name, updated_at);
CREATE INDEX IF NOT EXISTS idx_releases_date ON pokemon_releases(release_date);
"""


@contextmanager
def get_conn():
    """Context-Manager für eine SQLite-Verbindung mit Row-Factory."""
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Erstellt alle Tabellen, falls noch nicht vorhanden."""
    with get_conn() as conn:
        conn.executescript(SCHEMA)
    _migrate()
    log.info("Datenbank initialisiert: %s", config.DB_PATH)
    _seed_settings()


def _migrate() -> None:
    """Additive Migrationen für bereits bestehende Datenbanken.

    Fügt neue Spalten per ALTER TABLE hinzu, falls sie fehlen (bricht
    bestehende Daten nicht).
    """
    with get_conn() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(cards)")}
        if "alert_threshold" not in cols:
            conn.execute("ALTER TABLE cards ADD COLUMN alert_threshold REAL")
            log.info("Migration: cards.alert_threshold ergänzt.")


def _now() -> str:
    return datetime.utcnow().isoformat()


# ----------------------------------------------------------------------------
# Settings (Laufzeit-Konfiguration: threshold, min_score)
# ----------------------------------------------------------------------------
def _seed_settings() -> None:
    defaults = {
        "savings_threshold": str(config.DEFAULT_SAVINGS_THRESHOLD),
        "min_score": str(config.DEFAULT_MIN_SCORE),
        "weekly_budget": "0",
    }
    with get_conn() as conn:
        for key, val in defaults.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, val),
            )


def get_setting(key: str, default=None):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )


# ----------------------------------------------------------------------------
# Watchlist (cards)
# ----------------------------------------------------------------------------
def add_card(name: str, product_id: int | None = None,
             language_preference: str = "DE") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO cards (name, cardmarket_product_id, added_at, language_preference) "
            "VALUES (?, ?, ?, ?)",
            (name, product_id, _now(), language_preference),
        )
        return cur.lastrowid


def remove_card_by_name(name: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM cards WHERE LOWER(name) = LOWER(?)", (name,)
        )
        return cur.rowcount


def get_card_by_name(name: str):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM cards WHERE LOWER(name) = LOWER(?)", (name,)
        ).fetchone()


def get_watchlist() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM cards ORDER BY name").fetchall()


def set_card_product_id(card_id: int, product_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE cards SET cardmarket_product_id = ? WHERE id = ?",
            (product_id, card_id),
        )


def set_card_alert_threshold(card_id: int, threshold: float | None) -> None:
    """Setzt die individuelle Alert-Ersparnis-Schwelle (%) einer Karte.

    NULL bedeutet: globale Schwelle verwenden.
    """
    with get_conn() as conn:
        conn.execute(
            "UPDATE cards SET alert_threshold = ? WHERE id = ?",
            (threshold, card_id),
        )


# ----------------------------------------------------------------------------
# Scalp-Targets (versiegelte Produkte)
# ----------------------------------------------------------------------------
def add_scalp_target(product_name: str, product_type: str | None = None,
                     retail_price_target: float | None = None,
                     set_name: str | None = None, ean: str | None = None,
                     min_profit_margin: float = 20.0,
                     image_path: str | None = None,
                     notes: str | None = None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO scalp_targets "
            "(product_name, product_type, ean, set_name, retail_price_target, "
            " min_profit_margin, image_path, active, added_at, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
            (product_name, product_type, ean, set_name, retail_price_target,
             min_profit_margin, image_path, _now(), notes),
        )
        return cur.lastrowid


def get_scalp_target_by_name(product_name: str):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM scalp_targets WHERE LOWER(product_name) = LOWER(?)",
            (product_name,),
        ).fetchone()


def get_scalp_targets(active_only: bool = False) -> list[sqlite3.Row]:
    query = "SELECT * FROM scalp_targets"
    if active_only:
        query += " WHERE active = 1"
    query += " ORDER BY product_name"
    with get_conn() as conn:
        return conn.execute(query).fetchall()


def update_scalp_target_price(scalp_id: int, retail_price_target: float) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE scalp_targets SET retail_price_target = ? WHERE id = ?",
            (retail_price_target, scalp_id),
        )


def set_scalp_image_path(scalp_id: int, image_path: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE scalp_targets SET image_path = ? WHERE id = ?",
            (image_path, scalp_id),
        )


def set_scalp_active(scalp_id: int, active: bool) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE scalp_targets SET active = ? WHERE id = ?",
            (1 if active else 0, scalp_id),
        )


def remove_scalp_target_by_name(product_name: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM scalp_targets WHERE LOWER(product_name) = LOWER(?)",
            (product_name,),
        )
        return cur.rowcount


# ----------------------------------------------------------------------------
# Retailers
# ----------------------------------------------------------------------------
def upsert_retailer(name: str, base_url: str, scrape_method: str) -> int:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO retailers (name, base_url, scrape_method, active) "
            "VALUES (?, ?, ?, 1) "
            "ON CONFLICT(name) DO UPDATE SET base_url = excluded.base_url, "
            "scrape_method = excluded.scrape_method",
            (name, base_url, scrape_method),
        )
        row = conn.execute(
            "SELECT id FROM retailers WHERE name = ?", (name,)
        ).fetchone()
        return row["id"]


def get_retailers(active_only: bool = False) -> list[sqlite3.Row]:
    query = "SELECT * FROM retailers"
    if active_only:
        query += " WHERE active = 1"
    query += " ORDER BY name"
    with get_conn() as conn:
        return conn.execute(query).fetchall()


def get_retailer_by_name(name: str):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM retailers WHERE name = ?", (name,)
        ).fetchone()


def update_retailer_check(retailer_id: int, success: bool,
                          error: str | None = None) -> None:
    """Aktualisiert last_check/last_error und gleitende success_rate."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT success_rate FROM retailers WHERE id = ?", (retailer_id,)
        ).fetchone()
        prev = row["success_rate"] if row and row["success_rate"] is not None else 1.0
        # exponentiell geglättet (alpha=0.2)
        new_rate = round(prev * 0.8 + (1.0 if success else 0.0) * 0.2, 3)
        conn.execute(
            "UPDATE retailers SET last_check = ?, success_rate = ?, last_error = ? "
            "WHERE id = ?",
            (_now(), new_rate, error, retailer_id),
        )


def set_retailer_active(retailer_id: int, active: bool) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE retailers SET active = ? WHERE id = ?",
            (1 if active else 0, retailer_id),
        )


# ----------------------------------------------------------------------------
# Retail Stock History
# ----------------------------------------------------------------------------
def add_stock_check(scalp_target_id: int, retailer_id: int, in_stock: bool,
                    price: float | None, url: str | None) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO retail_stock_history "
            "(scalp_target_id, retailer_id, in_stock, price, url, checked_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (scalp_target_id, retailer_id, 1 if in_stock else 0, price, url, _now()),
        )


def get_last_stock(scalp_target_id: int, retailer_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM retail_stock_history "
            "WHERE scalp_target_id = ? AND retailer_id = ? "
            "ORDER BY checked_at DESC LIMIT 1",
            (scalp_target_id, retailer_id),
        ).fetchone()


def get_recent_restocks(hours: int = 24, limit: int = 50) -> list[sqlite3.Row]:
    """In-Stock-Checks der letzten `hours`, neueste zuerst."""
    cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
    with get_conn() as conn:
        return conn.execute(
            "SELECT h.*, s.product_name, r.name AS retailer_name "
            "FROM retail_stock_history h "
            "JOIN scalp_targets s ON s.id = h.scalp_target_id "
            "JOIN retailers r ON r.id = h.retailer_id "
            "WHERE h.in_stock = 1 AND h.checked_at >= ? "
            "ORDER BY h.checked_at DESC LIMIT ?",
            (cutoff, limit),
        ).fetchall()


def get_stock_history_for_target(scalp_target_id: int, days: int = 30
                                 ) -> list[sqlite3.Row]:
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    with get_conn() as conn:
        return conn.execute(
            "SELECT h.*, r.name AS retailer_name FROM retail_stock_history h "
            "JOIN retailers r ON r.id = h.retailer_id "
            "WHERE h.scalp_target_id = ? AND h.checked_at >= ? "
            "ORDER BY h.checked_at ASC",
            (scalp_target_id, cutoff),
        ).fetchall()


# ----------------------------------------------------------------------------
# Restock-Alert-Dedupe
# ----------------------------------------------------------------------------
def restock_alert_recently_sent(scalp_target_id: int, retailer_id: int,
                                hours: int) -> bool:
    cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM restock_alerts_sent "
            "WHERE scalp_target_id = ? AND retailer_id = ? AND sent_at >= ?",
            (scalp_target_id, retailer_id, cutoff),
        ).fetchone()
    return row["c"] > 0


def record_restock_alert(scalp_target_id: int, retailer_id: int,
                         price: float | None) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO restock_alerts_sent "
            "(scalp_target_id, retailer_id, price, sent_at) VALUES (?, ?, ?, ?)",
            (scalp_target_id, retailer_id, price, _now()),
        )


# ----------------------------------------------------------------------------
# Cardmarket Sealed Prices
# ----------------------------------------------------------------------------
def add_sealed_price(product_name: str, low_price: float | None,
                     avg_price: float | None, trend_price: float | None) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO cardmarket_sealed_prices "
            "(product_name, low_price, avg_price, trend_price, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (product_name, low_price, avg_price, trend_price, _now()),
        )


def get_sealed_price(product_name: str):
    """Neuester Sealed-Preis-Eintrag für ein Produkt."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM cardmarket_sealed_prices "
            "WHERE LOWER(product_name) = LOWER(?) ORDER BY updated_at DESC LIMIT 1",
            (product_name,),
        ).fetchone()


def get_sealed_price_history(product_name: str, days: int = 90
                             ) -> list[sqlite3.Row]:
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM cardmarket_sealed_prices "
            "WHERE LOWER(product_name) = LOWER(?) AND updated_at >= ? "
            "ORDER BY updated_at ASC",
            (product_name, cutoff),
        ).fetchall()


# ----------------------------------------------------------------------------
# Pokémon Releases
# ----------------------------------------------------------------------------
def add_release(set_name: str, release_date: str,
                product_types: list | None = None,
                uvp_prices: dict | None = None,
                pre_order_available: bool = False,
                notes: str | None = None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO pokemon_releases "
            "(set_name, release_date, product_types, uvp_prices, "
            " pre_order_available, notes) VALUES (?, ?, ?, ?, ?, ?)",
            (set_name, release_date,
             json.dumps(product_types or []),
             json.dumps(uvp_prices or {}),
             1 if pre_order_available else 0, notes),
        )
        return cur.lastrowid


def get_upcoming_releases(days: int = 60) -> list[dict]:
    today = datetime.utcnow().date().isoformat()
    until = (datetime.utcnow().date() + timedelta(days=days)).isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM pokemon_releases WHERE release_date >= ? AND release_date <= ? "
            "ORDER BY release_date ASC",
            (today, until),
        ).fetchall()
    return [_release_to_dict(r) for r in rows]


def get_releases_on(date_iso: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM pokemon_releases WHERE release_date = ?", (date_iso,)
        ).fetchall()
    return [_release_to_dict(r) for r in rows]


def _release_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    try:
        d["product_types"] = json.loads(d.get("product_types") or "[]")
    except json.JSONDecodeError:
        d["product_types"] = []
    try:
        d["uvp_prices"] = json.loads(d.get("uvp_prices") or "{}")
    except json.JSONDecodeError:
        d["uvp_prices"] = {}
    d["pre_order_available"] = bool(d.get("pre_order_available"))
    return d


# ----------------------------------------------------------------------------
# Price History
# ----------------------------------------------------------------------------
def add_price_point(card_id: int, price: float, seller_country: str,
                    seller_reputation: float, condition: str,
                    language: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO price_history "
            "(card_id, price, seller_country, seller_reputation, condition, language, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (card_id, price, seller_country, seller_reputation,
             condition, language, _now()),
        )


def get_price_history(card_id: int, days: int = 7) -> list[sqlite3.Row]:
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM price_history WHERE card_id = ? AND timestamp >= ? "
            "ORDER BY timestamp ASC",
            (card_id, cutoff),
        ).fetchall()


# ----------------------------------------------------------------------------
# Alerts
# ----------------------------------------------------------------------------
def record_alert(card_id: int, price: float, market_price: float,
                 savings_pct: float, deal_score: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO alerts_sent "
            "(card_id, price, market_price, savings_pct, deal_score, sent_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (card_id, price, market_price, savings_pct, deal_score, _now()),
        )


def alert_recently_sent(card_id: int, price: float, hours: int = 24) -> bool:
    """Prüft, ob für (card_id, ~price) zuletzt schon ein Alert raus ging
    (Doppel-Alert-Schutz). Preis-Match mit 1-Cent-Toleranz."""
    cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM alerts_sent "
            "WHERE card_id = ? AND ABS(price - ?) < 0.01 AND sent_at >= ?",
            (card_id, price, cutoff),
        ).fetchone()
    return row["c"] > 0


def get_recent_alerts(hours: int = 24) -> list[sqlite3.Row]:
    cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
    with get_conn() as conn:
        return conn.execute(
            "SELECT a.*, c.name AS card_name FROM alerts_sent a "
            "JOIN cards c ON c.id = a.card_id "
            "WHERE a.sent_at >= ? ORDER BY a.deal_score DESC",
            (cutoff,),
        ).fetchall()


# ----------------------------------------------------------------------------
# Portfolio
# ----------------------------------------------------------------------------
def add_portfolio_card(card_name: str, purchase_price: float,
                       product_id: int | None = None,
                       condition: str | None = None, language: str | None = None,
                       set_name: str | None = None, card_number: str | None = None,
                       rarity: str | None = None, image_path: str | None = None,
                       notes: str | None = None,
                       purchase_date: str | None = None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO portfolio "
            "(card_name, cardmarket_product_id, purchase_price, purchase_date, "
            " condition, language, set_name, card_number, rarity, image_path, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (card_name, product_id, purchase_price,
             purchase_date or _now(), condition, language, set_name,
             card_number, rarity, image_path, notes),
        )
        return cur.lastrowid


def get_portfolio() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM portfolio ORDER BY card_name"
        ).fetchall()


def get_portfolio_product_ids() -> set[int]:
    """Product-IDs der Sammlung — Scanner überspringt diese."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT cardmarket_product_id FROM portfolio "
            "WHERE cardmarket_product_id IS NOT NULL"
        ).fetchall()
    return {r["cardmarket_product_id"] for r in rows}


def update_portfolio_purchase_price(portfolio_card_id: int, price: float) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE portfolio SET purchase_price = ? WHERE id = ?",
            (price, portfolio_card_id),
        )


def update_portfolio_condition(portfolio_card_id: int, condition: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE portfolio SET condition = ? WHERE id = ?",
            (condition, portfolio_card_id),
        )


def count_portfolio_by_name(name: str) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM portfolio WHERE LOWER(card_name) = LOWER(?)",
            (name,),
        ).fetchone()
    return row["c"] if row else 0


def set_portfolio_image_path(portfolio_card_id: int, image_path: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE portfolio SET image_path = ? WHERE id = ?",
            (image_path, portfolio_card_id),
        )


def get_portfolio_card(portfolio_card_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM portfolio WHERE id = ?", (portfolio_card_id,)
        ).fetchone()


def add_portfolio_value(portfolio_card_id: int, market_value: float) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO portfolio_value_history "
            "(portfolio_card_id, market_value, timestamp) VALUES (?, ?, ?)",
            (portfolio_card_id, market_value, _now()),
        )


def get_latest_portfolio_value(portfolio_card_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT market_value, timestamp FROM portfolio_value_history "
            "WHERE portfolio_card_id = ? ORDER BY timestamp DESC LIMIT 1",
            (portfolio_card_id,),
        ).fetchone()


def get_portfolio_value_at(portfolio_card_id: int, days_ago: int):
    """Wert am nächstgelegenen Zeitpunkt um vor `days_ago` Tagen herum."""
    target = (datetime.utcnow() - timedelta(days=days_ago)).isoformat()
    with get_conn() as conn:
        return conn.execute(
            "SELECT market_value, timestamp FROM portfolio_value_history "
            "WHERE portfolio_card_id = ? AND timestamp <= ? "
            "ORDER BY timestamp DESC LIMIT 1",
            (portfolio_card_id, target),
        ).fetchone()


# ----------------------------------------------------------------------------
# Budget
# ----------------------------------------------------------------------------
def add_expense(amount: float, description: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO budget_log (amount, description, date) VALUES (?, ?, ?)",
            (amount, description, _now()),
        )


def get_expenses_since(days: int = 7) -> list[sqlite3.Row]:
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM budget_log WHERE date >= ? ORDER BY date DESC",
            (cutoff,),
        ).fetchall()


def get_total_spent(days: int | None = None) -> float:
    with get_conn() as conn:
        if days is None:
            row = conn.execute(
                "SELECT COALESCE(SUM(amount), 0) AS total FROM budget_log"
            ).fetchone()
        else:
            cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
            row = conn.execute(
                "SELECT COALESCE(SUM(amount), 0) AS total FROM budget_log WHERE date >= ?",
                (cutoff,),
            ).fetchone()
    return row["total"]


# ----------------------------------------------------------------------------
# Cardmarket Price Guide (taeglich heruntergeladen, lokal gecacht)
# ----------------------------------------------------------------------------
def import_cm_price_guide(rows: list[tuple]) -> None:
    """Schreibt alle Price-Guide-Eintraege in die DB (INSERT OR REPLACE).

    rows: Liste von Tupeln in der Reihenfolge:
    (id_product, avg, low, trend, avg1, avg7, avg30,
     avg_holo, low_holo, trend_holo, avg1_holo, avg7_holo, avg30_holo, updated_at)
    """
    with get_conn() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO cm_price_guide "
            "(id_product, avg, low, trend, avg1, avg7, avg30, "
            " avg_holo, low_holo, trend_holo, avg1_holo, avg7_holo, avg30_holo, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )


def get_cm_price(product_id: int):
    """Preis fuer eine Cardmarket-Produkt-ID aus dem lokalen Price Guide."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM cm_price_guide WHERE id_product = ?",
            (product_id,),
        ).fetchone()


# ----------------------------------------------------------------------------
# SIR/IR-Karten-Cache (TCGdex → idProduct-Mapping)
# ----------------------------------------------------------------------------
def upsert_sir_ir_card(id_product: int, name: str, set_name: str, set_id: str,
                       number: str, rarity: str, cm_url: str | None = None) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO sir_ir_cards "
            "(id_product, name, set_name, set_id, number, rarity, cm_url, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (id_product, name, set_name, set_id, number, rarity, cm_url, _now()),
        )


def sir_ir_card_exists(set_id: str, number: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM sir_ir_cards WHERE set_id = ? AND number = ?",
            (set_id, number),
        ).fetchone()
    return row is not None


def get_sir_ir_cards() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM sir_ir_cards ORDER BY set_name, number").fetchall()


def get_sir_ir_deals(min_discount_pct: float, min_trend_eur: float,
                     limit: int) -> list[sqlite3.Row]:
    """JOIN sir_ir_cards x cm_price_guide → Karten mit low deutlich unter trend."""
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT s.name, s.set_name, s.set_id, s.number, s.rarity,
                   s.id_product, s.cm_url,
                   c.trend, c.low, c.avg7, c.avg30,
                   ROUND((c.trend - c.low) / c.trend * 100, 1) AS discount_pct
            FROM sir_ir_cards s
            JOIN cm_price_guide c ON c.id_product = s.id_product
            WHERE c.trend >= ?
              AND c.low > 0
              AND c.trend > c.low
              AND (c.trend - c.low) / c.trend * 100 >= ?
            ORDER BY discount_pct DESC
            LIMIT ?
            """,
            (min_trend_eur, min_discount_pct, limit),
        ).fetchall()


def sir_ir_cache_count() -> int:
    with get_conn() as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM sir_ir_cards").fetchone()
    return row["c"] if row else 0


def cm_price_guide_count() -> int:
    """Anzahl Eintraege im lokalen Price Guide (0 = noch nicht heruntergeladen)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM cm_price_guide"
        ).fetchone()
    return row["c"] if row else 0


# ----------------------------------------------------------------------------
# Image-Requests & Scan-Log
# ----------------------------------------------------------------------------
def record_image_request(chat_id: str, card_recognized: str | None,
                         success: bool) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO image_requests (chat_id, timestamp, card_recognized, success) "
            "VALUES (?, ?, ?, ?)",
            (chat_id, _now(), card_recognized, 1 if success else 0),
        )


def count_image_requests_since(hours: int = 1) -> int:
    """Anzahl Bilderkennungs-Anfragen im Zeitfenster (für Rate-Limiting)."""
    cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM image_requests WHERE timestamp >= ?",
            (cutoff,),
        ).fetchone()
    return row["c"]


def record_scan(cards_scanned: int, alerts_sent: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO scan_log (timestamp, cards_scanned, alerts_sent) "
            "VALUES (?, ?, ?)",
            (_now(), cards_scanned, alerts_sent),
        )


def count_scans_since(days: int = 1) -> int:
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM scan_log WHERE timestamp >= ?", (cutoff,)
        ).fetchone()
    return row["c"]
