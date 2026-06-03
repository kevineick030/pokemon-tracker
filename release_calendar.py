"""Pokémon-Release-Kalender.

- Release-Daten werden manuell via /release_add gepflegt (Quelle der Wahrheit).
- Optionales Scraping von pokemon.com/de ist als Hook vorgesehen (noch No-Op).
- 14 und 1 Tag(e) vor Release: Pre-Order-Reminder.
- Am Release-Tag: Signal für Scan-Boost (60s) an den Scheduler.
"""
import logging
from datetime import datetime, date

import database as db

log = logging.getLogger("scalp.releases")


def _days_until(release_date: str) -> int | None:
    try:
        d = date.fromisoformat(release_date[:10])
    except ValueError:
        return None
    return (d - datetime.utcnow().date()).days


def format_upcoming(days: int = 60) -> str:
    """Formatierte Liste der kommenden Releases (für /releases)."""
    releases = db.get_upcoming_releases(days)
    if not releases:
        return f"📅 Keine Releases in den nächsten {days} Tagen eingetragen.\n" \
               "Hinzufügen mit: /release_add <set> <YYYY-MM-DD>"
    lines = [f"📅 *Kommende Releases ({days} Tage)*", ""]
    for r in releases:
        du = _days_until(r["release_date"])
        when = f"in {du} Tagen" if du and du > 0 else "heute" if du == 0 else r["release_date"]
        pre = " 🛒 Pre-Order" if r["pre_order_available"] else ""
        types = ", ".join(r["product_types"]) if r["product_types"] else ""
        lines.append(f"• *{r['set_name']}* — {r['release_date']} ({when}){pre}")
        if types:
            lines.append(f"  {types}")
    return "\n".join(lines)


def check_upcoming() -> dict:
    """Täglicher Check. Liefert Reminder-Texte + Release-Day-Boost-Signal.

    Rückgabe: {"reminders": [str, ...], "boost": bool, "today": [set_name, ...]}
    """
    reminders = []
    today_sets = []
    boost = False

    for r in db.get_upcoming_releases(days=30):
        du = _days_until(r["release_date"])
        if du is None:
            continue
        if du == 0:
            boost = True
            today_sets.append(r["set_name"])
            reminders.append(
                f"🎉 *Release heute:* {r['set_name']}! "
                "Retail-Scans werden für 24h beschleunigt."
            )
        elif du in (14, 1) and r["pre_order_available"]:
            reminders.append(
                f"🛒 *Pre-Order-Reminder:* {r['set_name']} erscheint in {du} "
                f"Tag(en) ({r['release_date']})."
            )
    return {"reminders": reminders, "boost": boost, "today": today_sets}


def scrape_pokemon_de() -> list[dict]:
    """Platzhalter für optionales Scraping offizieller Termine von pokemon.com/de.

    Aktuell No-Op (manuelle Pflege via /release_add). Kann später ergänzt werden.
    """
    return []
