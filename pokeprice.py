"""Preis-Quelle über die kostenlose pokemontcg.io-API (v2).

Liefert für Einzelkarten u.a. die Cardmarket-EUR-Preise (lowPrice,
averageSellPrice, trendPrice, avg7, avg30) sowie Set/Rarität/Bild.

Kein API-Key nötig (begrenzte Rate); optional POKEMONTCG_API_KEY für höheres
Limit. Wird als Preis-Quelle genutzt, solange keine Cardmarket-Tokens gesetzt
sind. Hinweis: pokemontcg.io enthält NUR Einzelkarten, keine versiegelten
Produkte (Displays/ETBs).
"""
import logging

import requests

import config

log = logging.getLogger(__name__)


def _headers() -> dict:
    h = {"Accept": "application/json"}
    if config.POKEMONTCG_API_KEY:
        h["X-Api-Key"] = config.POKEMONTCG_API_KEY
    return h


def _resolve_buy_url(url: str | None) -> str | None:
    """Macht aus der pokemontcg.io-Weiterleitung eine direkte, deutsche
    Cardmarket-Produkt-URL (ohne Tracking-Parameter)."""
    if not url:
        return None
    target = url
    if "pokemontcg.io" in url:
        try:
            r = requests.get(url, allow_redirects=False, timeout=10,
                             headers={"User-Agent": "Mozilla/5.0"})
            loc = r.headers.get("Location")
            if loc:
                target = loc
        except requests.RequestException:
            return url  # Fallback: Weiterleitungs-Link (funktioniert im Browser)
    target = target.split("?")[0].replace("/en/", "/de/")
    if target.startswith("https://cardmarket.com"):
        target = target.replace("https://cardmarket.com", "https://www.cardmarket.com")
    return target


def _score_candidate(card: dict, number: str | None, set_name: str | None) -> int:
    """Bewertet, wie gut ein Treffer zu Nummer/Set passt (höher = besser)."""
    score = 0
    if card.get("cardmarket", {}).get("prices"):
        score += 2  # hat Preisdaten -> bevorzugen
    if number and str(card.get("number", "")).lstrip("0") == number.lstrip("0"):
        score += 5
    if set_name:
        cset = (card.get("set", {}).get("name") or "").lower()
        if set_name.lower() in cset or cset in set_name.lower():
            score += 3
    return score


def lookup(name: str, set_name: str | None = None,
           number: str | None = None) -> dict | None:
    """Sucht eine Karte und gibt normalisierte Preis-/Stammdaten zurück.

    `number` darf '223/197' oder '223' sein (es zählt der Teil vor dem '/').
    """
    if not name:
        return None
    num = number.split("/")[0].strip() if number else None

    params = {
        "q": f'name:"{name}"',
        "pageSize": 30,
        "orderBy": "-set.releaseDate",
    }
    try:
        resp = requests.get(
            f"{config.POKEMONTCG_BASE_URL}/cards",
            params=params, headers=_headers(), timeout=20,
        )
        resp.raise_for_status()
        cards = resp.json().get("data", [])
    except (requests.RequestException, ValueError) as exc:
        log.warning("pokemontcg.io-Suche für '%s' fehlgeschlagen: %s", name, exc)
        return None

    if not cards:
        return None

    best = max(cards, key=lambda c: _score_candidate(c, num, set_name))
    cm = best.get("cardmarket", {}) or {}
    prices = cm.get("prices", {}) or {}

    return {
        "id": best.get("id"),
        "name": best.get("name"),
        "set_name": best.get("set", {}).get("name"),
        "number": best.get("number"),
        "rarity": best.get("rarity"),
        "image": best.get("images", {}).get("small"),
        "url": _resolve_buy_url(cm.get("url")),
        "currency": "EUR",
        "low": prices.get("lowPrice"),
        "de_low": prices.get("germanProLow"),   # günstigster DE-Profi-Händler
        "avg": prices.get("averageSellPrice") or prices.get("trendPrice"),
        "trend": prices.get("trendPrice"),
        "avg7": prices.get("avg7"),
        "avg30": prices.get("avg30"),
    }


def trend_from_prices(card: dict) -> dict:
    """Leitet einen Trend (steigend/fallend/stabil) aus avg7 vs avg30 ab."""
    import trend_analyzer  # für die Emoji-Tabelle
    a7, a30 = card.get("avg7"), card.get("avg30")
    if not a7 or not a30:
        return {"trend": "unbekannt", "emoji": trend_analyzer.TREND_EMOJI["unbekannt"],
                "change_pct": 0.0, "recommendation": "egal"}
    change = (a7 - a30) / a30 if a30 else 0.0
    if change > 0.10:
        trend, rec = "steigend", "kaufen"
    elif change < -0.10:
        trend, rec = "fallend", "warten"
    else:
        trend, rec = "stabil", "egal"
    return {
        "trend": trend,
        "emoji": trend_analyzer.TREND_EMOJI[trend],
        "change_pct": round(change * 100, 1),
        "recommendation": rec,
    }
