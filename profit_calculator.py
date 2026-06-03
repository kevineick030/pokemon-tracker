"""Profit-Rechner für versiegelte Produkte.

Berechnet den realistischen Netto-Gewinn nach allen Gebühren (Cardmarket,
PayPal, Versand, Verpackung) auf Basis des aktuellen Cardmarket-Sealed-Preises.
"""
import logging

import config
import database as db

log = logging.getLogger("scalp.profit")


def get_cardmarket_sealed_price(product_name: str) -> dict | None:
    """Aktueller Sealed-Preis (low/avg/trend) aus der DB, oder None."""
    row = db.get_sealed_price(product_name)
    if not row:
        return None
    return {
        "low_price": row["low_price"],
        "avg_price": row["avg_price"],
        "trend_price": row["trend_price"],
    }


def _recommendation(margin_pct: float) -> str:
    if margin_pct >= config.MIN_PROFIT_MARGIN_PCT:
        return "KAUFEN"
    if margin_pct >= config.MIN_PROFIT_MARGIN_PCT / 2:
        return "GRENZWERTIG"
    return "SKIP"


def calculate_profit(buy_price: float, product_name: str) -> dict:
    """Berechnet realistischen Profit nach allen Gebühren.

    Wenn kein Sealed-Preis vorliegt, sind die Verkaufs-/Profit-Felder None und
    die Empfehlung ist 'KEINE DATEN'.
    """
    sealed = get_cardmarket_sealed_price(product_name)

    if not sealed or not sealed.get("avg_price"):
        return {
            "buy_price": buy_price,
            "product_name": product_name,
            "sealed_known": False,
            "sell_realistic": None,
            "sell_optimistic": None,
            "total_fees": None,
            "net_profit": None,
            "margin_pct": None,
            "recommendation": "KEINE DATEN",
        }

    avg = sealed["avg_price"]
    sell_realistic = round(avg * config.QUICK_SELL_DISCOUNT, 2)
    sell_optimistic = round(avg, 2)

    cardmarket_fee = sell_realistic * config.CARDMARKET_FEE_PCT
    paypal_fee = (sell_realistic * config.PAYPAL_FEE_PCT) + config.PAYPAL_FEE_FIXED
    shipping = config.SHIPPING_DHL
    packaging = config.PACKAGING_COST

    total_fees = round(cardmarket_fee + paypal_fee + shipping + packaging, 2)
    net_profit = round(sell_realistic - buy_price - total_fees, 2)
    margin = round((net_profit / buy_price) * 100, 1) if buy_price > 0 else 0.0

    return {
        "buy_price": round(buy_price, 2),
        "product_name": product_name,
        "sealed_known": True,
        "cm_avg": round(avg, 2),
        "cm_low": sealed.get("low_price"),
        "cm_trend": sealed.get("trend_price"),
        "sell_realistic": sell_realistic,
        "sell_optimistic": sell_optimistic,
        "cardmarket_fee": round(cardmarket_fee, 2),
        "paypal_fee": round(paypal_fee, 2),
        "shipping": shipping,
        "packaging": packaging,
        "total_fees": total_fees,
        "net_profit": net_profit,
        "margin_pct": margin,
        "recommendation": _recommendation(margin),
    }
