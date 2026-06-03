"""Cardmarket-Sealed-Preise aktualisieren.

Zieht für jedes aktive Scalp-Target den Preis-Guide über die bestehende
Cardmarket-API (kein Web-Scraping) und speichert low/avg/trend in
cardmarket_sealed_prices. Läuft per Scheduler alle 6 Stunden.
"""
import logging

import database as db
from cardmarket import CardmarketClient, CardmarketError

log = logging.getLogger("scalp.sealed")


def _extract_guide(product: dict) -> dict:
    """Liest LOW/AVG/TREND aus dem Cardmarket-priceGuide heraus."""
    guide = product.get("priceGuide", {}) or {}
    # v2.0-Schlüssel (Groß-/Kleinschreibung variiert je nach Antwort)
    def pick(*keys):
        for k in keys:
            for variant in (k, k.upper(), k.lower()):
                if variant in guide and guide[variant] not in (None, 0):
                    try:
                        return float(guide[variant])
                    except (TypeError, ValueError):
                        pass
        return None

    return {
        "low_price": pick("LOW", "LOWEX"),
        "avg_price": pick("AVG", "SELL", "AVG7", "AVG30"),
        "trend_price": pick("TREND"),
    }


def update_for_target(client: CardmarketClient, product_name: str) -> bool:
    """Aktualisiert den Sealed-Preis eines einzelnen Produkts. True bei Erfolg."""
    try:
        products = client.find_products(product_name, exact=False)
    except CardmarketError as exc:
        log.warning("Produktsuche '%s' fehlgeschlagen: %s", product_name, exc)
        return False
    if not products:
        log.info("Kein Cardmarket-Produkt für '%s'.", product_name)
        return False

    product_id = products[0].get("idProduct")
    try:
        product = client.get_product(product_id)
    except CardmarketError as exc:
        log.warning("Produktdetails '%s' fehlgeschlagen: %s", product_name, exc)
        return False

    guide = _extract_guide(product)
    if guide["avg_price"] is None and guide["low_price"] is None:
        log.info("Kein priceGuide für '%s'.", product_name)
        return False

    db.add_sealed_price(
        product_name, guide["low_price"], guide["avg_price"], guide["trend_price"]
    )
    log.info("Sealed-Preis aktualisiert: %s -> avg=%s low=%s trend=%s",
             product_name, guide["avg_price"], guide["low_price"], guide["trend_price"])
    return True


def update_all(client: CardmarketClient) -> int:
    """Aktualisiert alle aktiven Scalp-Targets. Gibt Anzahl Erfolge zurück."""
    targets = db.get_scalp_targets(active_only=True)
    updated = 0
    for t in targets:
        if update_for_target(client, t["product_name"]):
            updated += 1
    log.info("Sealed-Preis-Update: %d/%d Produkte.", updated, len(targets))
    return updated
