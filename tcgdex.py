"""Preis-Quelle über die kostenlose, MEHRSPRACHIGE TCGdex-API (api.tcgdex.net).

Vorteil gegenüber pokemontcg.io: deutsche Karten-/Set-Namen (z.B. „Mauzi-ex",
„Optimale Ordnung") UND tagesaktuelle Cardmarket-EUR-Preise inkl. der
Cardmarket-Produkt-ID (für einen exakten Produkt-Link).

Gleiche Schnittstelle wie pokeprice (lookup / trend_from_prices /
cardmarket_search_url), damit der Bot sie 1:1 nutzen kann.

Hinweis: Die Preise sind Cardmarket-EUR-Aggregate (EU-weit), NICHT nach
deutschen Verkäufern gefiltert — das bleibt der Cardmarket-API vorbehalten.
"""
import re
import logging
import urllib.parse

import requests

log = logging.getLogger(__name__)

BASE = "https://api.tcgdex.net/v2"

# Suffixe, die TCGdex mit Bindestrich schreibt ("Mauzi-ex", "Pikachu-V")
_SUFFIXES = ["ex", "EX", "GX", "V", "VMAX", "VSTAR", "V-UNION"]


def _name_variants(name: str) -> list[str]:
    """Erzeugt Such-Varianten: Bindestrich-Suffix, Original, Basisname.

    Gemini liefert oft 'Mauzi ex' (Leerzeichen), TCGdex hat 'Mauzi-ex'.
    """
    n = (name or "").strip()
    variants = []
    hy = re.sub(r"\s+(ex|EX|GX|V|VMAX|VSTAR)\b",
                lambda m: "-" + m.group(1), n)
    for cand in (hy, n):
        if cand and cand not in variants:
            variants.append(cand)
    base = re.sub(r"[\s-]+(ex|EX|GX|V|VMAX|VSTAR)\b.*$", "", n).strip()
    if base and base not in variants:
        variants.append(base)
    return variants


def _rarity_tier(s: str | None) -> str:
    """Normalisiert eine Seltenheit (DE/EN) auf eine grobe Wertstufe."""
    s = (s or "").lower()
    if "besondere illustration" in s or "special illustration" in s:
        return "sir"
    if "illustration" in s:
        return "ir"
    if any(k in s for k in ("hyper", "secret", "rainbow", "gold", "geheim")):
        return "secret"
    if "ultra" in s:
        return "ultra"
    if "doppel" in s or "double" in s:
        return "double"
    return "other"


def _set_match(a: str | None, b: str | None) -> bool:
    a, b = (a or "").lower(), (b or "").lower()
    if not a or not b:
        return False
    return a in b or b in a or bool(set(a.split()) & set(b.split()) - {"in", "und", "der", "die"})


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


def _detail(lang: str, card_id: str) -> dict:
    try:
        r = requests.get(f"{BASE}/{lang}/cards/{card_id}", timeout=15)
        r.raise_for_status()
        return r.json()
    except (requests.RequestException, ValueError):
        return {}


def lookup(name: str, set_name: str | None = None, number: str | None = None,
           rarity: str | None = None) -> dict | None:
    """Robuste Karten-Suche → normalisierte Preis-/Stammdaten.

    Nutzt Namens-Varianten (Leerzeichen/Bindestrich) und bewertet Kandidaten
    nach **Seltenheit** (wichtig für den Wert!), Nummer und Set — damit auch
    bei verlesenem Set/Nummer die richtige Karte gefunden wird.
    """
    if not name:
        return None
    num = number.split("/")[0].strip() if number else None
    want_tier = _rarity_tier(rarity)

    # Kandidaten holen: deutsche, dann englische Namens-Varianten
    candidates, lang = [], "de"
    for candidate_lang in ("de", "en"):
        for variant in _name_variants(name):
            res = _search(candidate_lang, variant)
            if res:
                candidates, lang = res, candidate_lang
                break
        if candidates:
            break
    if not candidates:
        return None

    # Zu viele? Erst per Nummer eingrenzen, sonst auf die ersten begrenzen
    if len(candidates) > 12 and num:
        narrowed = [c for c in candidates
                    if str(c.get("localId", "")).lstrip("0") == num.lstrip("0")]
        if narrowed:
            candidates = narrowed
    candidates = candidates[:12]

    # Details laden + bewerten
    best_d, best_score = None, -1
    for c in candidates:
        d = _detail(lang, c["id"]) or c
        cm = (d.get("pricing") or {}).get("cardmarket") or {}
        score = 0.0
        if num and str(d.get("localId", "")).lstrip("0") == num.lstrip("0"):
            score += 3
        if _set_match(set_name, (d.get("set") or {}).get("name")):
            score += 2
        if want_tier != "other" and _rarity_tier(d.get("rarity")) == want_tier:
            score += 4
        if cm.get("trend") or cm.get("avg"):
            score += 1
        if score > best_score:
            best_d, best_score = d, score

    d = best_d or {}
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
