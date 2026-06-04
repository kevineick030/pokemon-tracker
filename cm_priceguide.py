"""Taeglich aktualisierter Cardmarket Price Guide als lokale Preis-Quelle.

Cardmarket stellt unter CM_PRICE_GUIDE_URL (S3-Link, kein API-Key noetig)
eine JSON-Datei mit ~75k Pokemon-Produkten bereit, die taeglich um ca. 02-06 Uhr
aktualisiert wird. Sie enthaelt pro Produkt (Werte in EUR):
  low, trend, avg, avg1, avg7, avg30 + Holo-Varianten.

Workflow:
  1. job_priceguide (main.py) ruft download_and_import() taeglich um 06:00 auf.
  2. Bot ruft get_price(product_id) auf → sofortiger lokaler Lookup, keine API.
  3. Fallback auf TCGdex-Preise wenn product_id unbekannt.
"""
import logging

import requests

import config
import database as db

log = logging.getLogger(__name__)


def download_and_import() -> int:
    """Laedt den Cardmarket Price Guide und importiert alle Eintraege in SQLite.

    Gibt die Anzahl importierter Produkte zurueck (0 bei Fehler).
    """
    url = config.CM_PRICE_GUIDE_URL
    if not url:
        log.warning("CM_PRICE_GUIDE_URL nicht konfiguriert - kein Download.")
        return 0

    try:
        log.info("Lade Cardmarket Price Guide von %s ...", url)
        r = requests.get(url, timeout=120)
        r.raise_for_status()
        data = r.json()
    except Exception:
        log.exception("Cardmarket Price Guide Download fehlgeschlagen")
        return 0

    guides = data.get("priceGuides") or []
    if not guides:
        log.warning("Price Guide leer oder unerwartetes Datenformat.")
        return 0

    updated_at = data.get("createdAt", "")
    rows = []
    for g in guides:
        pid = g.get("idProduct")
        if not pid:
            continue
        rows.append((
            pid,
            g.get("avg"),
            g.get("low"),
            g.get("trend"),
            g.get("avg1"),
            g.get("avg7"),
            g.get("avg30"),
            g.get("avg-holo"),
            g.get("low-holo"),
            g.get("trend-holo"),
            g.get("avg1-holo"),
            g.get("avg7-holo"),
            g.get("avg30-holo"),
            updated_at,
        ))

    db.import_cm_price_guide(rows)
    log.info("Cardmarket Price Guide importiert: %d Produkte (Stand: %s).",
             len(rows), updated_at)
    return len(rows)


def get_price(product_id: int) -> dict | None:
    """Liefert Preisdaten fuer eine Cardmarket-Produkt-ID aus der lokalen DB.

    Felder (alle in EUR, koennen None sein wenn kein Handel stattfand):
      low, trend, avg, avg1, avg7, avg30
      low_holo, trend_holo, avg_holo, avg7_holo, avg30_holo

    Gibt None zurueck wenn die ID nicht in der DB ist (noch kein Download
    oder Produkt nicht in der Pokemon-Kategorie).
    """
    if not product_id:
        return None
    row = db.get_cm_price(int(product_id))
    if not row:
        return None
    return dict(row)


def is_ready() -> bool:
    """True wenn der lokale Price Guide mindestens einen Eintrag enthaelt."""
    return db.cm_price_guide_count() > 0
