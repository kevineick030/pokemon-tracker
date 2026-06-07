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
    """Cardmarket-Suche gefiltert auf deutsche Verkäufer (sellerCountry=7)."""
    q = urllib.parse.quote_plus(name or "")
    return (f"https://www.cardmarket.com/de/Pokemon/Products/Search"
            f"?searchString={q}&sellerCountry=7")


def _cardmarket_product_url(id_product) -> str | None:
    """Direktlink zur Cardmarket-Produktseite, gefiltert auf 🇩🇪 Verkäufer.

    sellerCountry=7 = Deutschland. /Singles ist die korrekte CM-Unterseite
    fuer Einzelkarten — /Products allein landet auf der falschen Kategorie-Seite.
    """
    if not id_product:
        return None
    return (f"https://www.cardmarket.com/de/Pokemon/Products/Singles"
            f"?idProduct={id_product}&sellerCountry=7")


# Set-Cache (einmal pro Sprache laden) für den direkten Set+Nummer-Pfad
_sets_cache: dict[str, list] = {}


def _get_sets(lang: str) -> list:
    if lang not in _sets_cache:
        try:
            r = requests.get(f"{BASE}/{lang}/sets", timeout=15)
            r.raise_for_status()
            _sets_cache[lang] = r.json() if isinstance(r.json(), list) else []
        except (requests.RequestException, ValueError):
            _sets_cache[lang] = []
    return _sets_cache[lang]


def _resolve_set_id(lang: str, set_name: str | None) -> str | None:
    """Findet die TCGdex-Set-ID zu einem (evtl. ungenauen) Set-Namen."""
    if not set_name:
        return None
    best, best_score = None, 0
    target = set_name.lower()
    twords = set(target.split())
    for s in _get_sets(lang):
        nm = (s.get("name") or "").lower()
        if not nm:
            continue
        if nm == target:
            return s.get("id")
        score = 0
        if nm in target or target in nm:
            score += 3
        score += len(twords & set(nm.split()))
        if score > best_score:
            best, best_score = s.get("id"), score
    return best if best_score >= 2 else None


def _name_base(name: str) -> str:
    """Basis-Pokémonname ohne Suffix (für Plausibilitätscheck)."""
    return re.sub(r"[\s-]+(ex|EX|GX|V|VMAX|VSTAR)\b.*$", "",
                  (name or "")).strip().lower()


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


def _build_result(d: dict, name: str) -> dict:
    """Baut das normierte Rückgabe-Dict aus einem TCGdex-Karten-Detail."""
    cm = (d.get("pricing") or {}).get("cardmarket") or {}
    set_obj = d.get("set") or {}
    link = (_cardmarket_product_url(cm.get("idProduct"))
            or cardmarket_search_url(d.get("name") or name))

    # TCGPlayer (USD) als Fallback wenn keine CM-Preise
    tcgp = (d.get("pricing") or {}).get("tcgplayer") or {}
    # Bevorzugt: holofoil > normal (höherwertiger Marktpreis)
    tcgp_data = tcgp.get("holofoil") or tcgp.get("normal") or tcgp.get("1st Edition") or {}
    tcgp_market = tcgp_data.get("marketPrice")
    tcgp_low = tcgp_data.get("lowPrice")

    return {
        "id": d.get("id"),
        "name": d.get("name"),
        "set_name": set_obj.get("name"),
        "number": d.get("localId"),
        "rarity": d.get("rarity"),
        "image": d.get("image"),
        "url": link,
        "currency": cm.get("unit", "EUR"),
        "idProduct": cm.get("idProduct"),
        "low": cm.get("low"),
        "de_low": None,
        "avg": cm.get("avg"),
        "trend": cm.get("trend"),
        "avg7": cm.get("avg7"),
        "avg30": cm.get("avg30"),
        # TCGPlayer (USD) — Fallback wenn keine CM-Preise vorhanden
        "tcgp_market_usd": tcgp_market,
        "tcgp_low_usd": tcgp_low,
    }


def lookup(name: str, set_name: str | None = None, number: str | None = None,
           rarity: str | None = None) -> dict | None:
    """Karten-Suche → normalisierte Preis-/Stammdaten.

    Zwei-Pfad-Strategie:

    PFAD 1 (bevorzugt): Direktsuche Set + Nummer
      Wenn Gemini Set-Name UND Kartennummer erkannt hat, wird der exakte
      TCGdex-Endpunkt /{lang}/cards/{set_id}-{num} abgefragt. Das ist immer
      korrekt — Nummer luegt nicht. Plausibilitaetscheck: Basis-Pokémon-Name
      muss im Ergebnis enthalten sein (verhindert komplett falsche Sets).

    PFAD 2 (Fallback): Namenssuche mit strikter Nummer-Filterung
      Wenn Pfad 1 scheitert (Set nicht aufloesbar, Nummer fehlt).
      Kandidaten werden nach Nummernuebereinstimmung gefiltert. Im Scoring
      dominiert die Nummer (+10 Bonus / -20 Strafe) ueber Seltenheit (+4).
      Hat kein Kandidat die richtige Nummer, wird None zurueckgegeben statt
      einer falschen Karte (lieber kein Preis als falscher Preis).
    """
    if not name:
        return None
    num = number.split("/")[0].strip() if number else None
    want_tier = _rarity_tier(rarity)
    base = _name_base(name)

    # ── PFAD 1: Direkte Set+Nummer-Suche ─────────────────────────────────────
    if num and set_name:
        for lang in ("de", "en"):
            set_id = _resolve_set_id(lang, set_name)
            if not set_id:
                continue
            for n in (num, num.zfill(3)):
                dd = _detail(lang, f"{set_id}-{n}")
                if not dd or not dd.get("name"):
                    continue
                # Plausibilitaetscheck: Basis-Pokémon-Name muss stimmen
                # (bei JP-Karten ist base evtl. leer → dann akzeptieren)
                if base and base not in dd["name"].lower():
                    log.debug("Pfad1: Set+Nr Treffer '%s' verworfen (Name-Mismatch, "
                              "erwartet '%s')", dd["name"], base)
                    continue
                log.info("Pfad1 (Set+Nr): '%s' Nr.%s aus Set '%s' (%s)",
                         dd["name"], n, set_id, lang)
                return _build_result(dd, name)

    # ── PFAD 2: Namenssuche mit strikter Nummer-Filterung ────────────────────
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

    # Nummer-Filter: wenn bekannt, nur passende Kandidaten weiterverarbeiten
    if num:
        narrowed = [c for c in candidates
                    if str(c.get("localId", "")).lstrip("0") == num.lstrip("0")]
        if narrowed:
            candidates = narrowed
        # Kein Nummer-Treffer → trotzdem alle behalten, aber im Scoring bestrafen

    details = [(_detail(lang, c["id"]) or c) for c in candidates[:12]]
    if not details:
        return None

    # Scoring: Nummer dominiert (lieber kein Treffer als falscher)
    best_d, best_score = None, -999.0
    for d in details:
        cm = (d.get("pricing") or {}).get("cardmarket") or {}
        candidate_num = str(d.get("localId", "")).lstrip("0")
        score = 0.0

        if num:
            if candidate_num == num.lstrip("0"):
                score += 10   # starker Bonus: Nummer stimmt exakt
            else:
                score -= 20   # starke Strafe: Nummer stimmt NICHT

        if _set_match(set_name, (d.get("set") or {}).get("name")):
            score += 2
        if want_tier != "other" and _rarity_tier(d.get("rarity")) == want_tier:
            score += 4
        if cm.get("trend") or cm.get("avg"):
            score += 1

        if score > best_score:
            best_d, best_score = d, score

    # Wenn kein Kandidat die Nummer trifft: lieber None als falsche Karte
    if num and best_score < -10:
        log.info("Pfad2: kein Kandidat mit Nr.%s fuer '%s' → kein Ergebnis", num, name)
        return None

    log.info("Pfad2 (Name): '%s' score=%.0f num=%s",
             (best_d or {}).get("name"), best_score, num)
    return _build_result(best_d or {}, name)


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
