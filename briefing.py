"""Tägliches Briefing (09:00 Uhr).

Inhalt:
  - Top 3 Deals der letzten 24h
  - Sammlung: Gesamtwert + Wertveränderung zur Vorwoche
  - Wochenbudget-Status
  - Anzahl Scans gestern
"""
import logging

import config
import database as db
import portfolio

log = logging.getLogger(__name__)


def build_briefing() -> str:
    """Erstellt den formatierten Briefing-Text."""
    lines = ["☀️ *Tägliches Briefing*", ""]

    # --- Top 3 Deals letzte 24h ---
    alerts = db.get_recent_alerts(hours=24)[:3]
    lines.append("🏆 *Top Deals (24h)*")
    if alerts:
        for a in alerts:
            lines.append(
                f"• {a['card_name']}: {a['price']:.2f}€ "
                f"(-{a['savings_pct']:.0f}%, Score {a['deal_score']})"
            )
    else:
        lines.append("• Keine Deals in den letzten 24h.")
    lines.append("")

    # --- Sammlung ---
    summ = portfolio.summary()
    change = portfolio.value_change_vs(days_ago=7)
    sign = "+" if change["change"] >= 0 else ""
    lines.append("💎 *Sammlung*")
    lines.append(
        f"• Gesamtwert: {summ['total_market']:.2f}€ "
        f"({summ['count']} Karten)"
    )
    lines.append(
        f"• Δ zur Vorwoche: {sign}{change['change']:.2f}€ "
        f"({sign}{change['change_pct']:.1f}%)"
    )
    g = summ["profit"]
    gsign = "+" if g >= 0 else ""
    lines.append(f"• G/V gesamt: {gsign}{g:.2f}€ ({gsign}{summ['profit_pct']:.1f}%)")
    lines.append("")

    # --- Budget ---
    weekly_budget = float(db.get_setting("weekly_budget", "0"))
    spent_week = db.get_total_spent(days=7)
    lines.append("💶 *Wochenbudget*")
    if weekly_budget > 0:
        remaining = weekly_budget - spent_week
        lines.append(
            f"• {spent_week:.2f}€ / {weekly_budget:.2f}€ ausgegeben "
            f"(noch {remaining:.2f}€)"
        )
    else:
        lines.append(f"• {spent_week:.2f}€ ausgegeben (kein Budget gesetzt)")
    lines.append("")

    # --- Scans gestern ---
    scans = db.count_scans_since(days=1)
    lines.append(f"🔍 Scans (24h): {scans}")

    return "\n".join(lines)
