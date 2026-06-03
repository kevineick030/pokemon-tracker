"""Trend-Analyse der Preishistorie (7 Tage).

Klassifiziert den Preisverlauf als steigend / fallend / stabil
(±10 % Schwelle) und leitet eine Empfehlung ab (kaufen / warten / egal).
"""
import logging
from statistics import mean

import database as db

log = logging.getLogger(__name__)

TREND_THRESHOLD = 0.10  # ±10 %

TREND_EMOJI = {"steigend": "↑", "fallend": "↓", "stabil": "→", "unbekannt": "·"}


def analyze(card_id: int, days: int = 7) -> dict:
    """Analysiert die letzten `days` Tage der Preishistorie.

    Vergleicht den Durchschnitt der ersten Hälfte des Zeitraums mit dem
    der zweiten Hälfte, um Rauschen einzelner Angebote zu glätten.

    Rückgabe: dict(trend, change_pct, recommendation, emoji, samples)
    """
    history = db.get_price_history(card_id, days=days)
    prices = [row["price"] for row in history if row["price"] and row["price"] > 0]

    if len(prices) < 2:
        return {
            "trend": "unbekannt",
            "change_pct": 0.0,
            "recommendation": "egal",
            "emoji": TREND_EMOJI["unbekannt"],
            "samples": len(prices),
        }

    half = max(1, len(prices) // 2)
    first_avg = mean(prices[:half])
    second_avg = mean(prices[half:])

    if first_avg == 0:
        change = 0.0
    else:
        change = (second_avg - first_avg) / first_avg

    if change > TREND_THRESHOLD:
        trend = "steigend"
        recommendation = "kaufen"   # Preise steigen -> jetzt zugreifen
    elif change < -TREND_THRESHOLD:
        trend = "fallend"
        recommendation = "warten"   # Preise fallen -> evtl. noch günstiger
    else:
        trend = "stabil"
        recommendation = "egal"

    return {
        "trend": trend,
        "change_pct": round(change * 100, 1),
        "recommendation": recommendation,
        "emoji": TREND_EMOJI[trend],
        "samples": len(prices),
    }
