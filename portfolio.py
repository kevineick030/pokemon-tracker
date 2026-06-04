"""Portfolio-Wert-Tracking.

- Täglich (Job in main.py): Marktpreis jeder Sammlungskarte über TCGdex abrufen
  und in portfolio_value_history speichern → /wert + Dashboard-Chart bewegen sich.
- Gesamtwert  = Summe aktueller Marktpreise
- Einstandswert = Summe purchase_price
- Gewinn/Verlust = Differenz

Preis-Quelle = TCGdex (tagesaktuelle Cardmarket-EUR-Preise, auch für deutsche
Karten). Es wird derselbe Wert genommen wie beim Foto-Scan: Trend, sonst avg.
"""
import logging

import database as db
import tcgdex

log = logging.getLogger(__name__)


def fetch_market_value(card) -> float | None:
    """Aktueller Marktpreis einer Sammlungskarte über TCGdex.

    Sucht über Name + Set + Nummer + Seltenheit (wie der Foto-Scan) und nimmt
    den Cardmarket-Trend, ersatzweise den Durchschnitt (avg).
    """
    name = card["card_name"]
    if not name:
        return None
    result = tcgdex.lookup(
        name,
        set_name=card["set_name"],
        number=card["card_number"],
        rarity=card["rarity"],
    )
    if not result:
        return None
    value = result.get("trend") or result.get("avg")
    return float(value) if value is not None else None


def update_all_values() -> int:
    """Speichert für jede Sammlungskarte den aktuellen Marktwert (TCGdex).

    Rückgabe: Anzahl aktualisierter Karten.
    """
    cards = db.get_portfolio()
    updated = 0
    for card in cards:
        try:
            value = fetch_market_value(card)
        except Exception:
            log.exception("Marktwert für '%s' nicht abrufbar", card["card_name"])
            value = None
        if value is not None:
            db.add_portfolio_value(card["id"], value)
            updated += 1
    log.info("Portfolio-Bewertung (TCGdex): %d/%d Karten aktualisiert.",
             updated, len(cards))
    return updated


def summary() -> dict:
    """Berechnet Gesamtwert, Einstandswert, Gewinn/Verlust und Top-Karte."""
    cards = db.get_portfolio()
    total_market = 0.0
    total_cost = 0.0
    items = []

    for card in cards:
        latest = db.get_latest_portfolio_value(card["id"])
        market_value = latest["market_value"] if latest else None
        cost = card["purchase_price"] or 0.0
        total_cost += cost
        if market_value is not None:
            total_market += market_value
        items.append({
            "id": card["id"],
            "name": card["card_name"],
            "set_name": card["set_name"],
            "language": card["language"],
            "condition": card["condition"],
            "purchase_price": cost,
            "market_value": market_value,
            "gain": (market_value - cost) if market_value is not None else None,
        })

    profit = total_market - total_cost
    profit_pct = (profit / total_cost * 100) if total_cost > 0 else 0.0

    valued = [i for i in items if i["gain"] is not None]
    top_card = max(valued, key=lambda i: i["gain"], default=None)

    return {
        "count": len(cards),
        "total_market": round(total_market, 2),
        "total_cost": round(total_cost, 2),
        "profit": round(profit, 2),
        "profit_pct": round(profit_pct, 1),
        "items": items,
        "top_card": top_card,
    }


def value_change_vs(days_ago: int = 7) -> dict:
    """Gesamtwertveränderung gegenüber vor `days_ago` Tagen."""
    cards = db.get_portfolio()
    now_total = 0.0
    then_total = 0.0
    for card in cards:
        latest = db.get_latest_portfolio_value(card["id"])
        past = db.get_portfolio_value_at(card["id"], days_ago)
        if latest:
            now_total += latest["market_value"]
        if past:
            then_total += past["market_value"]
    change = now_total - then_total
    change_pct = (change / then_total * 100) if then_total > 0 else 0.0
    return {
        "now": round(now_total, 2),
        "then": round(then_total, 2),
        "change": round(change, 2),
        "change_pct": round(change_pct, 1),
    }
