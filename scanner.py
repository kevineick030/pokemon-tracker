"""Scanner-Logik.

Alle 30 Min:
  1. Watchlist scannen
  2. Karten, die bereits im Portfolio sind, überspringen (kein Alert)
  3. Marktpreis = Median der letzten 10 DE-Angebote (>=98 % Reputation)
  4. Deal-Score berechnen + Trend analysieren
  5. Bei Score >= min_score: Alert senden
"""
import logging

import config
import database as db
import deal_scorer
import trend_analyzer
from cardmarket import (
    CardmarketClient, CardmarketError,
    filter_de_offers, market_median, parse_article,
)

log = logging.getLogger(__name__)


def _build_alert(card_name: str, offer: dict, market_price: float,
                 savings_pct: float, score_info: dict, trend_info: dict,
                 product_id: int | None) -> str:
    """Baut die formatierte Alert-Nachricht (siehe Spezifikation)."""
    emoji = trend_info["emoji"]
    url = (
        f"https://www.cardmarket.com/de/Pokemon/Products/Singles?idProduct={product_id}"
        if product_id else "https://www.cardmarket.com/de/Pokemon"
    )
    condition_label = deal_scorer.CONDITION_LABELS.get(
        (offer["condition"] or "").upper(), offer["condition"]
    )
    return (
        f"🚨 SCHNÄPPCHEN! Score: {score_info['score']}/100\n\n"
        f"🃏 {card_name} ({offer['language']}) | {_set_hint(offer)}\n"
        f"💰 {offer['price']:.2f}€  →  Markt: {market_price:.2f}€  "
        f"(-{savings_pct:.0f}%)\n"
        f"📈 Trend: {emoji} {trend_info['trend']} | 💡 {trend_info['recommendation']}\n"
        f"⭐ {condition_label} | 👤 {offer['seller_reputation']:.0f}% Bewertung\n"
        f"🔗 {url}"
    )


def _set_hint(offer: dict) -> str:
    """Set-Info ist auf Article-Ebene nicht immer vorhanden -> Platzhalter."""
    return offer.get("set_name") or "—"


def scan_card(client: CardmarketClient, card, min_score: int,
              portfolio_ids: set[int]) -> dict | None:
    """Scannt eine einzelne Watchlist-Karte.

    Rückgabe: Alert-dict (für Versand) oder None.
    """
    product_id = card["cardmarket_product_id"]

    # Produkt-ID ggf. per Namenssuche auflösen und cachen
    if not product_id:
        try:
            products = client.find_products(card["name"], exact=False)
        except CardmarketError as exc:
            log.warning("Produktsuche für '%s' fehlgeschlagen: %s", card["name"], exc)
            return None
        if not products:
            log.info("Keine Produkte für '%s' gefunden.", card["name"])
            return None
        product_id = products[0].get("idProduct")
        if product_id:
            db.set_card_product_id(card["id"], product_id)

    # Portfolio-Karten überspringen
    if product_id in portfolio_ids:
        log.debug("Überspringe '%s' (im Portfolio).", card["name"])
        return None

    # Angebote abrufen — direkt auf DE + Mindest-Reputation filtern lassen
    try:
        articles = client.get_articles(
            product_id,
            minUserScore=3,          # Cardmarket: 3 ~ "good", filtern wir feiner nach
            idLanguage=3,            # Deutsch bevorzugt; Markt aber breiter bewerten
            maxResults=100,
        )
    except CardmarketError as exc:
        log.warning("Article-Abruf für '%s' fehlgeschlagen: %s", card["name"], exc)
        return None

    de_offers = filter_de_offers(articles)
    if not de_offers:
        log.info("Keine passenden DE-Angebote für '%s'.", card["name"])
        return None

    market_price = market_median(de_offers)
    if not market_price:
        return None

    # Preishistorie schreiben (für Trend) — günstigstes Angebot als Referenz
    cheapest = min(de_offers, key=lambda o: o["price"])
    db.add_price_point(
        card["id"], cheapest["price"], cheapest["seller_country"],
        cheapest["seller_reputation"], cheapest["condition"], cheapest["language"],
    )

    # Trend analysieren
    trend_info = trend_analyzer.analyze(card["id"])

    # Bestes Angebot bewerten: günstigstes DE-Angebot
    offer = cheapest
    if offer["price"] >= market_price:
        return None  # kein Schnäppchen
    savings_pct = round((market_price - offer["price"]) / market_price * 100, 1)

    # Individuelle Alert-Schwelle der Karte (falls gesetzt) zusätzlich anwenden.
    # NULL/fehlt -> bisheriges Verhalten unverändert.
    card_threshold = None
    try:
        card_threshold = card["alert_threshold"]
    except (KeyError, IndexError):
        card_threshold = None
    if card_threshold is not None and savings_pct < card_threshold:
        log.debug("'%s': Ersparnis %.1f%% unter Kartenschwelle %.1f%% — kein Alert.",
                  card["name"], savings_pct, card_threshold)
        return None

    score_info = deal_scorer.compute_score(
        savings_pct, offer["seller_reputation"], offer["condition"],
        trend_info["trend"],
    )

    if score_info["score"] < min_score:
        return None

    # Doppel-Alert-Schutz
    if db.alert_recently_sent(card["id"], offer["price"]):
        log.debug("Alert für '%s' @ %.2f schon kürzlich gesendet.",
                  card["name"], offer["price"])
        return None

    return {
        "card_id": card["id"],
        "card_name": card["name"],
        "product_id": product_id,
        "offer": offer,
        "market_price": market_price,
        "savings_pct": savings_pct,
        "score_info": score_info,
        "trend_info": trend_info,
        "message": _build_alert(
            card["name"], offer, market_price, savings_pct,
            score_info, trend_info, product_id,
        ),
    }


def run_scan(client: CardmarketClient) -> list[dict]:
    """Scannt die komplette Watchlist und gibt alle Alerts zurück."""
    min_score = int(float(db.get_setting("min_score", config.DEFAULT_MIN_SCORE)))
    watchlist = db.get_watchlist()
    portfolio_ids = db.get_portfolio_product_ids()

    alerts: list[dict] = []
    for card in watchlist:
        try:
            alert = scan_card(client, card, min_score, portfolio_ids)
        except Exception:  # einzelne Karte darf den Scan nicht abbrechen
            log.exception("Fehler beim Scan von '%s'", card["name"])
            continue
        if alert:
            db.record_alert(
                alert["card_id"], alert["offer"]["price"],
                alert["market_price"], alert["savings_pct"],
                alert["score_info"]["score"],
            )
            alerts.append(alert)

    db.record_scan(cards_scanned=len(watchlist), alerts_sent=len(alerts))
    log.info("Scan abgeschlossen: %d Karten, %d Alerts.", len(watchlist), len(alerts))
    return alerts
