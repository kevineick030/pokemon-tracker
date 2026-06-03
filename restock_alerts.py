"""Restock-Alert-Logik: Dedupe + Formatierung.

Entkoppelt von Telegram — die Monitore erzeugen Event-Dicts, der Scheduler-Job
in main.py verschickt sie. Dedupe: gleicher Produkt+Händler max. 1× pro
RESTOCK_ALERT_DEDUPE_HOURS.
"""
import logging
from datetime import datetime

import config
import database as db
import profit_calculator

log = logging.getLogger("scalp.alerts")


def should_send(scalp_target_id: int, retailer_id: int) -> bool:
    """True, wenn für (Produkt, Händler) im Dedupe-Fenster noch kein Alert war."""
    return not db.restock_alert_recently_sent(
        scalp_target_id, retailer_id, config.RESTOCK_ALERT_DEDUPE_HOURS
    )


def record(scalp_target_id: int, retailer_id: int, price: float | None) -> None:
    db.record_restock_alert(scalp_target_id, retailer_id, price)


def format_alert(event: dict) -> str:
    """Baut die Restock-Alarm-Nachricht inkl. Profit-Analyse (Spec-Format)."""
    product = event.get("product_name", "?")
    retailer = event.get("retailer_name", "?")
    price = event.get("price")
    uvp = event.get("uvp")
    url = event.get("url", "")
    ts = event.get("timestamp") or datetime.now().strftime("%Y-%m-%d %H:%M")

    price_str = f"{price:.2f}€" if price is not None else "?"
    uvp_str = f" (UVP: {uvp:.2f}€)" if uvp else ""

    lines = [
        "🚨 RESTOCK ALARM 🚨", "",
        f"📦 {product}",
        f"🏪 {retailer}",
        f"💰 Preis: {price_str}{uvp_str}", "",
    ]

    # Profit-Analyse (nur sinnvoll mit bekanntem Kaufpreis)
    if price is not None:
        calc = profit_calculator.calculate_profit(price, product)
        lines.append("💼 Profit-Analyse:")
        if calc["sealed_known"]:
            lines.extend([
                f"- Cardmarket avg: {calc['cm_avg']:.2f}€",
                f"- Realistischer Verkauf: {calc['sell_realistic']:.2f}€",
                f"- Netto nach Gebühren: {calc['net_profit']:.2f}€",
                f"- Marge: {calc['margin_pct']:.1f}%",
                f"- Empfehlung: {calc['recommendation']}",
            ])
        else:
            lines.append("- Kein Cardmarket-Sealed-Preis vorhanden (Empfehlung n/a)")
        lines.append("")

    lines.append(f"⏰ Entdeckt: {ts}")
    if url:
        lines.append(f"🔗 {url}")
    if event.get("source") == "hotstock":
        lines.append("⚡ Quelle: HotStock (war schneller!)")

    return "\n".join(lines)
