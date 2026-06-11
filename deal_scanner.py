"""Deal-Scanner: Taeglich SIR/IR-Karten unter Marktwert finden.

Ablauf (taeglich 06:05, nach Price-Guide-Download):
  1. TCGdex: SIR/IR-Karten direkt per Rarity-Endpunkt cachen (idProduct + Name)
  2. CM Price Guide: low vs. trend vergleichen fuer gecachte Karten
  3. Top-Deals als Telegram-Nachricht senden
  4. Watchlist-Karten pruefen → Alerts wenn Preis unter Schwelle gefallen
"""
import logging
import time
import urllib.parse
import requests
import cm_priceguide
import database as db

log = logging.getLogger(__name__)

TCGDEX_BASE = "https://api.tcgdex.net/v2"
TIMEOUT = 20
REQUEST_DELAY = 0.2  # Sekunden zwischen TCGdex-Requests

# Exakte Rarity-Namen wie TCGdex sie nennt (case-insensitive match beim Lookup)
TARGET_RARITIES_TCGDEX = [
    "Special Illustration Rare",
    "Illustration Rare",
    "Hyper Rare",
    "Secret Rare",
    "Shiny Rare",
    "Shiny Ultra Rare",
    "Ultra Rare",
    "Double Rare",
]

MIN_TREND_EUR = 8.0
MIN_DISCOUNT_PCT = 15.0


def _get_cards_by_rarity(rarity: str) -> list[dict]:
    """Holt alle Karten einer Rarity direkt vom TCGdex-Rarity-Endpunkt."""
    encoded = urllib.parse.quote(rarity, safe="")
    try:
        resp = requests.get(f"{TCGDEX_BASE}/en/rarities/{encoded}", timeout=TIMEOUT)
        if resp.status_code != 200:
            log.warning("TCGdex Rarity '%s' → HTTP %d", rarity, resp.status_code)
            return []
        data = resp.json()
        if not isinstance(data, list):
            log.warning("TCGdex Rarity '%s' → unerwartetes Format", rarity)
            return []
        return data
    except Exception:
        log.exception("TCGdex Rarity '%s' nicht ladbar", rarity)
        return []


def _get_card_detail(set_id: str, number: str) -> dict | None:
    try:
        resp = requests.get(
            f"{TCGDEX_BASE}/en/cards/{set_id}-{number}", timeout=TIMEOUT
        )
        if resp.status_code != 200:
            return None
        return resp.json()
    except Exception:
        return None


def refresh_sir_ir_cache() -> int:
    """Cached SIR/IR-Karten per TCGdex-Rarity-Endpunkte.

    Effizienter als Set-Scan: nur die Karten werden geladen die tatsaechlich
    die Ziel-Rarities haben. Ueberspringt bereits gecachte Karten.
    Gibt Anzahl neu hinzugefuegter Karten zurueck.
    """
    added = 0
    total_found = 0

    for rarity in TARGET_RARITIES_TCGDEX:
        cards = _get_cards_by_rarity(rarity)
        log.info("TCGdex Rarity '%s' → %d Karten gefunden", rarity, len(cards))
        total_found += len(cards)
        time.sleep(REQUEST_DELAY)

        for card in cards:
            set_info = card.get("set") or {}
            set_id = set_info.get("id") or ""
            set_name = set_info.get("name") or set_id
            number = str(card.get("localId") or "")

            if not set_id or not number:
                continue
            if db.sir_ir_card_exists(set_id, number):
                continue

            detail = _get_card_detail(set_id, number)
            time.sleep(REQUEST_DELAY)
            if not detail:
                continue

            cm = (detail.get("pricing") or {}).get("cardmarket") or {}
            id_product = cm.get("idProduct")
            if not id_product:
                continue

            db.upsert_sir_ir_card(
                id_product=int(id_product),
                name=detail.get("name") or card.get("name") or "",
                set_name=set_name,
                set_id=set_id,
                number=number,
                rarity=rarity,
                cm_url=cm.get("url"),
            )
            added += 1

    log.info(
        "SIR/IR-Cache: %d Karten geprueft, %d neu hinzugefuegt.", total_found, added
    )
    return added


def get_deals(min_discount_pct: float = MIN_DISCOUNT_PCT,
              min_trend_eur: float = MIN_TREND_EUR,
              limit: int = 10) -> list[dict]:
    """Findet SIR/IR-Karten mit aktuellem Angebot deutlich unter Marktwert."""
    if not cm_priceguide.is_ready():
        log.warning("CM Price Guide noch nicht geladen, keine Deals.")
        return []

    rows = db.get_sir_ir_deals(min_discount_pct, min_trend_eur, limit)
    return [dict(r) for r in rows]


def check_watchlist_alerts() -> list[str]:
    """Prueft Watchlist-Karten gegen CM Price Guide.

    Gibt Alert-Texte fuer Karten zurueck, deren guenstigstes Angebot
    die individuelle (oder globale) Alarm-Schwelle unterschreitet.
    Benutzt alert_recently_sent() als Duplikat-Schutz (24h).
    """
    import config
    cards = db.get_watchlist()
    alerts = []

    for card in cards:
        product_id = card["cardmarket_product_id"]
        if not product_id:
            continue

        cm = cm_priceguide.get_price(product_id)
        if not cm:
            continue

        trend = cm.get("trend") or cm.get("avg")
        low = cm.get("low")
        if not trend or not low or low <= 0 or trend <= 0:
            continue

        raw_threshold = card["alert_threshold"]
        threshold = float(raw_threshold) if raw_threshold is not None \
            else float(db.get_setting("savings_threshold") or config.DEFAULT_SAVINGS_THRESHOLD)

        discount_pct = (trend - low) / trend * 100
        if discount_pct < threshold:
            continue

        if db.alert_recently_sent(card["id"], low, hours=24):
            continue

        db.record_alert(card["id"], low, trend, round(discount_pct, 1), deal_score=0)

        avg7 = cm.get("avg7")
        avg7_txt = f" | Ø7T: {avg7:.0f}€" if avg7 else ""
        alerts.append(
            f"🔔 *Watchlist-Alert: {card['name']}*\n"
            f"Markt: {trend:.0f}€{avg7_txt} | Ab: {low:.0f}€\n"
            f"Ersparnis: -{discount_pct:.0f}% (Schwelle: -{threshold:.0f}%)\n"
            f"⚠️ Guenstigstes EU-Angebot — Verkaeuferbewertung auf CM pruefen."
        )

    return alerts


def format_deals_message(deals: list[dict]) -> str:
    if not deals:
        cache = db.sir_ir_cache_count()
        if cache == 0:
            return (
                "📭 *Keine SIR/IR-Karten im Cache*\n\n"
                "Nutze /deals_refresh um den Cache jetzt aufzubauen (dauert ca. 5 Min).\n"
                "Danach zeigt /deals echte Deals mit Namen und Links."
            )
        return (
            "📊 *Keine SIR/IR-Deals heute*\n\n"
            "Alle Karten liegen nahe am Marktwert. Morgen wieder pruefen!"
        )

    lines = [
        f"🔥 *Beste Deals — SIR/IR/Hyper Rare*\n"
        f"_{len(deals)} Karten deutlich unter Marktwert (EU-weit)_\n"
    ]

    for i, d in enumerate(deals, 1):
        pct = d["discount_pct"]
        fire = "🔥🔥🔥" if pct >= 35 else "🔥🔥" if pct >= 25 else "🔥"
        avg7_txt = f"Ø7T: {d['avg7']:.0f}€  |  " if d.get("avg7") else ""
        name = d.get("name") or f"Produkt #{d.get('id_product', '?')}"
        set_txt = f" — {d['set_name']}" if d.get("set_name") else ""
        url_part = f"\n   [Cardmarket]({d['cm_url']})" if d.get("cm_url") else ""
        lines.append(
            f"*{i}. {name}*{set_txt}\n"
            f"   {avg7_txt}Markt: *{d['trend']:.0f}€*  |  "
            f"Ab: *{d['low']:.0f}€*  |  -{pct:.0f}% {fire}{url_part}\n"
        )

    lines.append("_Cardmarket Price Guide (EU-weit) · taeglich 06:00_")
    return "\n".join(lines)
