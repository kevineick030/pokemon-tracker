"""Preis-Quelle über die kostenlose, MEHRSPRACHIGE TCGdex-API (api.tcgdex.net).

Vorteil gegenüber pokemontcg.io: deutsche Karten-/Set-Namen (z.B. „Mauzi-ex",
„Optimale Ordnung") UND tagesaktuelle Cardmarket-EUR-Preise inkl. der
Cardmarket-Produkt-ID (für einen exakten Produkt-Link).

Gleiche Schnittstelle wie pokeprice (lookup / trend_from_prices /
cardmarket_search_url), damit der Bot sie 1:1 nutzen kann.

Hinweis: Die Preise sind Cardmarket-EUR-Aggregate (EU-weit), NICHT nach
deutschen Verkäufern gefiltert — das bleibt der Cardmarket-API vorbehalten.
"""
import logging
import urllib.parse

import requests

log = logging.getLogger(__name__)

BASE = "https://api.tcgdex.net/v2"


def cardmarket_search_url(name: str) -> str:
    q = urllib.parse.quote_plus(name or "")
    return f"https://www.cardmarket.com/de/Pokemon/Products/Search?searchString={q}"


def _cardmarket_product_url(id_product) -> str | None:
    if not id_product:
        return None
    return (f"https://www.cardmarket.com/de/Pokemon/Products/Singles"
            f"?idProduct={id_product}")


def _search(lang: str, name: str) -> list:
    try:
        r = requests.get(f"{BASE}/{lang}/cards",
                         params={"name": name}, timeout=15)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []
    except (requests.RequestException, ValueError):
        return []


def _score(card_brief: dict, number: str | None) -> int:
    s = 0
    if number and str(card_brief.get("localId", "")).lstrip("0") == number.lstrip("0"):
        s += 5
    return s


def lookup(name: str, set_name: str | None = None,
           number: str | None = None) -> dict | None:
    """Sucht eine Karte (deutsch bevorzugt, sonst englisch) und gibt
    normalisierte Preis-/Stammdaten zurück."""
    if not name:
        return None
    num = number.split("/")[0].strip() if number else None

    # Deutsch zuerst (passt zu deutschen Karten), dann Englisch als Fallback
    results, lang = [], "de"
    for candidate_lang in ("de", "en"):
        results = _search(candidate_lang, name)
        if results:
            lang = candidate_lang
            break
    if not results:
        return None

    best = max(results, key=lambda c: _score(c, num)) if num else results[0]

    # Detail laden (enthält pricing)
    try:
        r = requests.get(f"{BASE}/{lang}/cards/{best['id']}", timeout=15)
        r.raise_for_status()
        d = r.json()
    except (requests.RequestException, ValueError, KeyError):
        d = best

    cm = (d.get("pricing") or {}).get("cardmarket") or {}
    id_product = cm.get("idProduct")
    set_obj = d.get("set") or {}

    return {
        "id": d.get("id"),
        "name": d.get("name"),
        "set_name": set_obj.get("name"),
        "number": d.get("localId"),
        "rarity": d.get("rarity"),
        "image": d.get("image"),
        "url": _cardmarket_product_url(id_product) or cardmarket_search_url(name),
        "currency": cm.get("unit", "EUR"),
        "low": cm.get("low"),
        "de_low": None,             # TCGdex hat keinen DE-Verkäufer-Preis
        "avg": cm.get("avg"),
        "trend": cm.get("trend"),
        "avg7": cm.get("avg7"),
        "avg30": cm.get("avg30"),
    }


def trend_from_prices(card: dict) -> dict:
    """Leitet einen Trend (steigend/fallend/stabil) aus avg7 vs avg30 ab."""
    import trend_analyzer
    a7, a30 = card.get("avg7"), card.get("avg30")
    if not a7 or not a30:
        return {"trend": "unbekannt",
                "emoji": trend_analyzer.TREND_EMOJI["unbekannt"],
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
