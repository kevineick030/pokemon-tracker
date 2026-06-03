"""HotStock.de als Reliability-Fallback.

HotStock.de aggregiert deutsche Händler-Restocks. Wir lesen den Feed, gleichen
Einträge per Fuzzy-Match mit unseren Scalp-Targets ab und erzeugen Events.
feedparser ist optional (Fallback: requests + XML-Parsing).

Läuft alle 60 Sekunden parallel zum retail_monitor.
"""
import re
import logging
import asyncio
from datetime import datetime

import config
import database as db
import scalp_targets

log = logging.getLogger("scalp.hotstock")

_PRICE_RE = re.compile(r"(\d{1,4}[.,]\d{2})\s*€")


def _parse_price(text: str) -> float | None:
    if not text:
        return None
    m = _PRICE_RE.search(text)
    if not m:
        return None
    try:
        return float(m.group(1).replace(".", "").replace(",", "."))
    except ValueError:
        return None


class HotStockMonitor:
    def __init__(self):
        # Pseudo-Händler für Dedupe/History-Zuordnung
        self.retailer_id = db.upsert_retailer(
            "hotstock", "https://www.hotstock.de", "requests"
        )

    # ------------------------------------------------------------------ Feed
    def _fetch_entries(self) -> list[dict]:
        """Lädt die Feed-Einträge. Gibt [{title, link, summary, published}]."""
        # 1) feedparser, falls verfügbar
        try:
            import feedparser  # type: ignore
            feed = feedparser.parse(config.HOTSTOCK_RSS_URL)
            entries = []
            for e in feed.entries:
                entries.append({
                    "title": e.get("title", ""),
                    "link": e.get("link", ""),
                    "summary": e.get("summary", ""),
                    "published": e.get("published", ""),
                })
            if entries:
                return entries
        except ImportError:
            log.debug("feedparser nicht installiert — nutze requests-Fallback.")
        except Exception as exc:
            log.warning("feedparser-Fehler: %s", exc)

        # 2) Fallback: requests + XML
        try:
            import requests
            import xml.etree.ElementTree as ET
            headers = {"User-Agent": config.USER_AGENTS[0]}
            resp = requests.get(config.HOTSTOCK_RSS_URL, headers=headers, timeout=20)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            entries = []
            for item in root.iter("item"):
                entries.append({
                    "title": (item.findtext("title") or ""),
                    "link": (item.findtext("link") or ""),
                    "summary": (item.findtext("description") or ""),
                    "published": (item.findtext("pubDate") or ""),
                })
            return entries
        except Exception as exc:
            log.warning("HotStock-Feed nicht abrufbar: %s", exc)
            return []

    async def fetch_feed(self) -> list[dict]:
        """Holt den Feed (im Executor) und matcht gegen aktive Scalp-Targets.

        Rückgabe: Liste von Event-Dicts (Dedupe übernimmt der Aufrufer).
        """
        targets = db.get_scalp_targets(active_only=True)
        if not targets:
            return []

        loop = asyncio.get_running_loop()
        entries = await loop.run_in_executor(None, self._fetch_entries)
        if not entries:
            return []

        events = []
        for entry in entries:
            text = f"{entry['title']} {entry['summary']}"
            match, score = scalp_targets.best_match(entry["title"])
            if not match:
                continue
            price = _parse_price(text)
            # Stock-Check protokollieren (HotStock-Einträge = Restock-Signal)
            db.add_stock_check(match["id"], self.retailer_id, True, price, entry["link"])
            events.append({
                "scalp_target_id": match["id"],
                "product_name": match["product_name"],
                "retailer_id": self.retailer_id,
                "retailer_name": "HotStock",
                "price": price,
                "url": entry["link"],
                "uvp": None,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "source": "hotstock",
                "match_score": score,
            })
            log.info("HotStock-Match: '%s' (Score %d) -> %s",
                     entry["title"], score, match["product_name"])
        return events
