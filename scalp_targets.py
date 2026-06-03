"""Verwaltung der Scalp-Watchlist + Fuzzy-Matching.

Dünner Service-Layer über database.py. Stellt außerdem das Fuzzy-Matching
bereit (für HotStock-Feed-Abgleich), mit difflib-Fallback falls fuzzywuzzy /
rapidfuzz nicht installiert sind.
"""
import logging

import config
import database as db

log = logging.getLogger("scalp.targets")


# ---------------------------------------------------------------- Fuzzy-Match
def _ratio_impl():
    """Wählt die beste verfügbare Ratio-Funktion (0-100)."""
    try:
        from rapidfuzz import fuzz  # type: ignore
        return lambda a, b: fuzz.token_set_ratio(a, b)
    except ImportError:
        pass
    try:
        from fuzzywuzzy import fuzz  # type: ignore
        return lambda a, b: fuzz.token_set_ratio(a, b)
    except ImportError:
        pass
    # Fallback: difflib mit token-set-Logik (ahmt fuzzywuzzy.token_set_ratio nach,
    # robust gegen Wortreihenfolge und Teilmengen — ohne externe Abhängigkeit).
    import re as _re
    from difflib import SequenceMatcher

    def _tokens(s):
        return set(_re.findall(r"\w+", (s or "").lower()))

    def _sm(a, b):
        return SequenceMatcher(None, a, b).ratio()

    def _difflib_ratio(a, b):
        ta, tb = _tokens(a), _tokens(b)
        if not ta or not tb:
            return 0
        inter = ta & tb
        t0 = " ".join(sorted(inter))
        t1 = " ".join(sorted(inter) + sorted(ta - tb))
        t2 = " ".join(sorted(inter) + sorted(tb - ta))
        best = max(_sm(t0, t1), _sm(t0, t2), _sm(t1, t2))
        return int(best * 100)

    return _difflib_ratio


_ratio = _ratio_impl()


def ratio(a: str, b: str) -> int:
    return _ratio(a or "", b or "")


def best_match(query: str, threshold: int | None = None):
    """Findet das am besten passende aktive Scalp-Target für einen Freitext.

    Gibt (target_row, score) zurück oder (None, 0), wenn unter Schwelle.
    """
    threshold = threshold if threshold is not None else config.FUZZY_MATCH_THRESHOLD
    best, best_score = None, 0
    for t in db.get_scalp_targets(active_only=True):
        score = ratio(query, t["product_name"])
        # Set-Name zusätzlich berücksichtigen
        if t["set_name"]:
            score = max(score, ratio(query, f"{t['product_name']} {t['set_name']}"))
        if score > best_score:
            best, best_score = t, score
    if best and best_score >= threshold:
        return best, best_score
    return None, 0


# ---------------------------------------------------------------- Übersicht
def list_with_status() -> list[dict]:
    """Liefert alle Scalp-Targets mit dem letzten bekannten Stock-Status je
    Händler (für /scalp-Command und Dashboard)."""
    retailers = db.get_retailers()
    result = []
    for t in db.get_scalp_targets():
        per_retailer = []
        for r in retailers:
            last = db.get_last_stock(t["id"], r["id"])
            per_retailer.append({
                "retailer": r["name"],
                "in_stock": bool(last["in_stock"]) if last else None,
                "price": last["price"] if last else None,
                "checked_at": last["checked_at"] if last else None,
            })
        result.append({
            "id": t["id"],
            "product_name": t["product_name"],
            "product_type": t["product_type"],
            "set_name": t["set_name"],
            "retail_price_target": t["retail_price_target"],
            "active": bool(t["active"]),
            "image_path": t["image_path"],
            "retailers": per_retailer,
        })
    return result
