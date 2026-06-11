"""Deal-Scanner: Taeglich SIR/IR-Karten unter Marktwert finden.

Ablauf (taeglich 06:05, nach Price-Guide-Download):
  1. TCGdex: Alle SIR/IR-Karten aus aktuellen Sets cachen (idProduct + Name)
  2. CM Price Guide: low vs. trend vergleichen fuer gecachte Karten
  3. Top-Deals als Telegram-Nachricht senden
  4. Watchlist-Karten pruefen → Alerts wenn Preis unter Schwelle gefallen
"""
import logging
import time
import requests
import cm_priceguide
import database as db

log = logging.getLogger(__name__)

TCGDEX_BASE = "https://api.tcgdex.net/v2"
TIMEOUT = 15
REQUEST_DELAY = 0.15  # Sekunden zwischen TCGdex-Requests

TARGET_RARITIES = {
    "special illustration rare",
    "illustration rare",
    "hyper rare",
    "secret rare",
    "shiny rare",
    "shiny ultra rare",
    "ultra rare",
    "double rare",
}

MIN_TREND_EUR = 8.0
MIN_DISCOUNT_PCT = 15.0


def _is_target_rarity(rarity: str | None) -> bool:
    if not rarity:
        return False
    r = rarity.lower()
    return any(t in r for t in TARGET_RARITIES)


def _get_sets_since(year: int = 2022) -> list[dict]:
    try:
        resp = requests.get(f"{TCGDEX_BASE}/en/sets", timeout=TIMEOUT)
        resp.raise_for_status()
        result = []
        for s in resp.json():
            release = s.get("releaseDate", "")
            y = int(release[:4]) if len(release) >= 4 and release[:4].isdigit() else 0
            if y >= year:
                result.append(s)
        log.info("TCGdex: %d Sets seit %d gefunden.", len(result), year)
        return result
    except Exception:
        log.exception("TCGdex Sets konnten nicht geladen werden")
        return []


def _get_set_cards(set_id: str) -> list[dict]:
    try:
        resp = requests.get(f"{TCGDEX_BASE}/en/sets/{set_id}", timeout=TIMEOUT)
        if resp.status_code != 200:
            return []
        return resp.json().get("cards", [])
    except Exception:
        log.warning("TCGdex Set %s nicht ladbar", set_id)
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
    """Scannt TCGdex auf SIR/IR-Karten und cached idProduct in der DB.

    Bereits bekannte Karten (set_id + number in DB) werden uebersprungen,
    sodass Folgelaeufe nur neue Karten nachladen.
    Gibt Anzahl neu hinzugefuegter Karten zurueck.
    """
    sets = _get_sets_since(year=2022)
    added = 0

    for s in sets:
        set_id = s.get("id")
        if not set_id:
            continue

        cards = _get_set_cards(set_id)
        time.sleep(REQUEST_DELAY)

        for card in cards:
            number = str(card.get("localId") or card.get("number") or "")
            if not number:
                continue

            if db.sir_ir_card_exists(set_id, number):
                continue

            detail = _get_card_detail(set_id, number)
            time.sleep(REQUEST_DELAY)
            if not detail:
                continue

            # Rarität aus dem Detail-Objekt prüfen (set-Endpoint liefert sie oft nicht)
            rarity = detail.get("rarity") or card.get("rarity") or ""
            if not _is_target_rarity(rarity):
                continue

            # idProduct liegt verschachtelt unter pricing.cardmarket
            cm = (detail.get("pricing") or {}).get("cardmarket") or {}
            id_product = cm.get("idProduct")
            if not id_product:
                continue

            db.upsert_sir_ir_card(
                id_product=int(id_product),
                name=detail.get("name") or card.get("name") or "",
                set_name=s.get("name", set_id),
                set_id=set_id,
                number=number,
                rarity=rarity,
                cm_url=cm.get("url"),
            )
            added += 1

    log.info("SIR/IR-Cache: %d neue Karten hinzugefuegt.", added)
    return added


def get_deals(min_discount_pct: float = MIN_DISCOUNT_PCT,
              min_trend_eur: float = MIN_TREND_EUR,
              limit: int = 10) -> list[dict]:
    """Findet Karten mit aktuellem Angebot deutlich unter Marktwert.

    Zuerst aus dem SIR/IR-Cache (mit Namen + Rarität).
    Fallback: direkt aus dem CM Price Guide (nur Preise, kein Name).
    """
    if not cm_priceguide.is_ready():
        log.warning("CM Price Guide noch nicht geladen, keine Deals.")
        return []

    rows = db.get_sir_ir_deals(min_discount_pct, min_trend_eur, limit)
    if rows:
        return [dict(r) for r in rows]

    # Fallback: direkt aus Price Guide (wenn SIR/IR-Cache leer)
    log.info("SIR/IR-Cache leer, nutze direkte Price-Guide-Suche als Fallback.")
    rows = db.get_priceguide_deals(min_discount_pct, min_trend_eur, limit)
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
