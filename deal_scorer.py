"""Deal-Scoring 0-100.

Punkteverteilung (max 100):
  - Preis-Ersparnis:    40 Punkte (20 %+ Ersparnis = max)
  - Verkäufer-Bewertung: 25 Punkte (100 % = max, 98 % = 0)
  - Zustand:            20 Punkte (NM=20, EX=12, GD=5)
  - Trend-Bonus:        15 Punkte (fallend = +15, steigend = -10, stabil = 0)
"""
import logging

log = logging.getLogger(__name__)

# Cardmarket-Zustandscodes -> Punkte
CONDITION_POINTS = {
    "MT": 20,   # Mint
    "NM": 20,   # Near Mint
    "EX": 12,   # Excellent
    "GD": 5,    # Good
    "LP": 5,    # Light Played (~Good)
    "PL": 2,    # Played
    "PO": 0,    # Poor
}

CONDITION_LABELS = {
    "MT": "Mint", "NM": "Near Mint", "EX": "Excellent",
    "GD": "Good", "LP": "Light Played", "PL": "Played", "PO": "Poor",
}


def savings_points(savings_pct: float) -> float:
    """40 Punkte bei >=20 % Ersparnis, linear darunter, 0 bei <=0 %."""
    if savings_pct <= 0:
        return 0.0
    return round(min(savings_pct / 20.0, 1.0) * 40.0, 1)


def reputation_points(reputation_pct: float) -> float:
    """25 Punkte bei 100 %, 0 Punkte bei 98 %, linear dazwischen."""
    if reputation_pct <= 98.0:
        return 0.0
    frac = min((reputation_pct - 98.0) / 2.0, 1.0)  # 98->0, 100->1
    return round(frac * 25.0, 1)


def condition_points(condition: str) -> float:
    return float(CONDITION_POINTS.get((condition or "").upper(), 0))


def trend_points(trend: str) -> float:
    """Trend-Bonus: fallend +15, steigend -10, stabil 0."""
    return {"fallend": 15.0, "steigend": -10.0, "stabil": 0.0}.get(trend, 0.0)


def compute_score(savings_pct: float, reputation_pct: float,
                  condition: str, trend: str) -> dict:
    """Berechnet den Gesamtscore und die Teilkomponenten.

    Rückgabe: dict mit 'score' (int, 0-100) und Aufschlüsselung.
    """
    parts = {
        "savings": savings_points(savings_pct),
        "reputation": reputation_points(reputation_pct),
        "condition": condition_points(condition),
        "trend": trend_points(trend),
    }
    raw = sum(parts.values())
    score = int(round(max(0.0, min(100.0, raw))))
    return {"score": score, "parts": parts}
