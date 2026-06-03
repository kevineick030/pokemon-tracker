"""Telegram-Bot: Command-Handler + Freitext-Chat.

Die eigentliche Scheduler-/App-Verdrahtung passiert in main.py. Dieses Modul
stellt die Handler-Funktionen und `register_handlers()` bereit.
"""
import os
import time
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters,
)

import config
import database as db
import ai_chat
import portfolio
import trend_analyzer
import deal_scorer
import image_recognition
import scalp_targets
import profit_calculator
import release_calendar
import pokeprice
from cardmarket import (
    CardmarketError, filter_de_offers, market_median, parse_article,
    LANGUAGE_IDS,
)
import scanner

log = logging.getLogger(__name__)

# Sprach-IDs für /preis-Vergleich
LANG_QUERY = {"DE": 3, "EN": 1, "JP": 7}


def _authorized(update: Update) -> bool:
    """Nur der konfigurierte Chat darf den Bot bedienen."""
    if not config.TELEGRAM_CHAT_ID:
        return True
    return str(update.effective_chat.id) == str(config.TELEGRAM_CHAT_ID)


def _mkm(context: ContextTypes.DEFAULT_TYPE):
    return context.application.bot_data["mkm"]


# ---------------------------------------------------------------- Commands
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    await update.message.reply_text(
        "👋 *Pokémon Karten Tracker*\n\n"
        "Ich tracke SIR/IR/Ultra-Rare-Karten auf Cardmarket, erkenne Schnäppchen "
        "und verwalte deine Sammlung.\n\n"
        "*Wichtigste Befehle:*\n"
        "/watchlist – Beobachtungsliste\n"
        "/add <Name> – Karte hinzufügen\n"
        "/preis <Name> – Preis + Trend + Empfehlung\n"
        "/sammlung – Portfolio\n"
        "/wert – Gesamtwert + G/V\n"
        "/budget – Budgetstatus\n"
        "/briefing – Tagesbriefing jetzt\n"
        "/import <id> – Cardmarket-Wunschliste importieren\n"
        "/status – Bot-Status\n\n"
        "📸 *Schick mir ein Foto* einer Karte oder eines versiegelten Produkts — "
        "ich erkenne es und biete dir an:\n"
        "✅ Sammlung · 💰 Preis-Check · 🔔 Watchlist · 💼 Scalp-Track\n\n"
        "Schreib mir einfach eine Frage für den KI-Experten 🤖",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    cards = db.get_watchlist()
    if not cards:
        await update.message.reply_text("📭 Watchlist ist leer. /add <Name>")
        return
    lines = ["👁️ *Watchlist*", ""]
    for c in cards:
        trend = trend_analyzer.analyze(c["id"])
        history = db.get_price_history(c["id"], days=7)
        last_price = history[-1]["price"] if history else None
        price_str = f"{last_price:.2f}€" if last_price else "–"
        lines.append(f"{trend['emoji']} {c['name']} · {price_str}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    name = " ".join(context.args).strip()
    if not name:
        await update.message.reply_text("Nutzung: /add <Kartenname>")
        return
    if db.get_card_by_name(name):
        await update.message.reply_text(f"ℹ️ '{name}' ist bereits auf der Watchlist.")
        return

    product_id = None
    try:
        products = _mkm(context).find_products(name, exact=False)
        if products:
            product_id = products[0].get("idProduct")
    except CardmarketError as exc:
        log.warning("Produktsuche fehlgeschlagen: %s", exc)

    db.add_card(name, product_id)
    suffix = f" (Produkt-ID {product_id})" if product_id else " (Produkt-ID folgt beim Scan)"
    is_sealed = _set_pending_from_command(context, name, product_id)
    keyboard = _build_action_keyboard(["price", "collect", "scalp"], is_sealed)
    await update.message.reply_text(
        f"✅ '{name}' zur Watchlist hinzugefügt{suffix}.\nWeitere Aktionen:",
        reply_markup=keyboard,
    )


async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    name = " ".join(context.args).strip()
    if not name:
        await update.message.reply_text("Nutzung: /remove <Kartenname>")
        return
    n = db.remove_card_by_name(name)
    if n:
        await update.message.reply_text(f"🗑️ '{name}' entfernt.")
    else:
        await update.message.reply_text(f"❓ '{name}' nicht gefunden.")


async def cmd_preis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    name = " ".join(context.args).strip()
    if not name:
        await update.message.reply_text("Nutzung: /preis <Kartenname>")
        return

    # Ohne Cardmarket: Preise über pokemontcg.io
    if not config.cardmarket_enabled():
        import asyncio
        loop = asyncio.get_running_loop()
        text = await loop.run_in_executor(None, _pokeprice_text, name, None, None)
        is_sealed = _set_pending_from_command(context, name, None)
        keyboard = _build_action_keyboard(["collect", "watch", "scalp"], is_sealed)
        await update.message.reply_text(
            text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard
        )
        return

    card = db.get_card_by_name(name)
    product_id = card["cardmarket_product_id"] if card else None
    if not product_id:
        try:
            products = _mkm(context).find_products(name, exact=False)
            if products:
                product_id = products[0].get("idProduct")
        except CardmarketError as exc:
            await update.message.reply_text(f"⚠️ Cardmarket-Fehler: {exc}")
            return
    if not product_id:
        await update.message.reply_text(f"❓ Keine Produkte für '{name}' gefunden.")
        return

    # Preise je Sprache (DE/EN/JP)
    lines = [f"💰 *{name}*", ""]
    for lang_label, lang_id in LANG_QUERY.items():
        try:
            articles = _mkm(context).get_articles(
                product_id, idLanguage=lang_id, maxResults=50
            )
        except CardmarketError:
            articles = []
        offers = filter_de_offers(articles)
        med = market_median(offers)
        if med:
            cheapest = min(o["price"] for o in offers)
            lines.append(f"{lang_label}: ab {cheapest:.2f}€ (Median {med:.2f}€)")
        else:
            lines.append(f"{lang_label}: keine DE-Angebote")

    # Trend + Empfehlung (nur wenn auf Watchlist mit Historie)
    if card:
        trend = trend_analyzer.analyze(card["id"])
        lines.append("")
        lines.append(f"📈 Trend: {trend['emoji']} {trend['trend']} "
                     f"({trend['change_pct']:+.1f}%)")
        lines.append(f"💡 Empfehlung: {trend['recommendation']}")

    # Aktions-Buttons anbieten
    is_sealed = _set_pending_from_command(context, name, product_id)
    keyboard = _build_action_keyboard(["collect", "watch", "scalp"], is_sealed)
    await update.message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard
    )


def _set_pending_from_command(context: ContextTypes.DEFAULT_TYPE, name: str,
                              product_id: int | None, *,
                              market_price: float | None = None) -> bool:
    """Legt einen pending_card-Kontext für command-basierte Flows an (ohne
    Foto). Gibt zurück, ob es sich um ein versiegeltes Produkt handelt."""
    product_type = image_recognition.guess_product_type(name)
    context.user_data["pending_card"] = {
        "recog": {"card_name": name, "product_type": product_type,
                  "set_name": None, "condition_estimate": None,
                  "language": None, "card_number": None, "rarity": None},
        "analysis": {"product_id": product_id, "market_price": market_price},
        "temp_path": None,
    }
    return image_recognition.is_sealed(product_type)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    mkm = _mkm(context)
    online = mkm.ping()
    threshold = db.get_setting("savings_threshold")
    min_score = db.get_setting("min_score")
    scans = db.count_scans_since(days=1)
    await update.message.reply_text(
        "🤖 *Status*\n\n"
        f"Cardmarket-API: {'🟢 online' if online else '🔴 offline'}\n"
        f"Watchlist: {len(db.get_watchlist())} Karten\n"
        f"Sammlung: {len(db.get_portfolio())} Karten\n"
        f"Ersparnis-Schwelle: {threshold}%\n"
        f"Min. Deal-Score: {min_score}\n"
        f"Scans (24h): {scans}",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_threshold(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    if not context.args:
        await update.message.reply_text(
            f"Aktuelle Ersparnis-Schwelle: {db.get_setting('savings_threshold')}%\n"
            "Nutzung: /threshold <zahl>"
        )
        return
    try:
        val = float(context.args[0].replace(",", "."))
    except ValueError:
        await update.message.reply_text("Bitte eine Zahl angeben.")
        return
    db.set_setting("savings_threshold", val)
    await update.message.reply_text(f"✅ Ersparnis-Schwelle auf {val}% gesetzt.")


async def cmd_score(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    if not context.args:
        await update.message.reply_text(
            f"Aktueller Min. Deal-Score: {db.get_setting('min_score')}\n"
            "Nutzung: /score <zahl>"
        )
        return
    try:
        val = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Bitte eine ganze Zahl angeben.")
        return
    db.set_setting("min_score", val)
    await update.message.reply_text(f"✅ Min. Deal-Score auf {val} gesetzt.")


async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    await update.message.reply_text("🔍 Scan läuft …")
    alerts = await run_scan_and_alert(context.application)
    if not alerts:
        await update.message.reply_text("✅ Scan fertig — keine Schnäppchen gefunden.")
    else:
        await update.message.reply_text(f"✅ Scan fertig — {len(alerts)} Alert(s) gesendet.")


async def cmd_sammlung(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    summ = portfolio.summary()
    if summ["count"] == 0:
        await update.message.reply_text("📭 Sammlung ist leer. /gekauft <name> <preis>")
        return
    lines = ["💎 *Sammlung*", ""]
    for it in summ["items"]:
        mv = f"{it['market_value']:.2f}€" if it["market_value"] is not None else "–"
        gain = ""
        if it["gain"] is not None:
            s = "+" if it["gain"] >= 0 else ""
            gain = f" ({s}{it['gain']:.2f}€)"
        cond = it["condition"] or "?"
        lang = it["language"] or "?"
        lines.append(
            f"• {it['name']} [{lang}/{cond}] – "
            f"Kauf {it['purchase_price']:.2f}€ → {mv}{gain}"
        )
    lines.append("")
    lines.append(f"Σ Wert: {summ['total_market']:.2f}€ | "
                 f"Einstand: {summ['total_cost']:.2f}€")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_wert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    summ = portfolio.summary()
    if summ["count"] == 0:
        await update.message.reply_text("📭 Sammlung ist leer.")
        return
    g = summ["profit"]
    gsign = "+" if g >= 0 else ""
    emoji = "📈" if g >= 0 else "📉"
    lines = [
        "💎 *Sammlungswert*", "",
        f"Gesamtwert: {summ['total_market']:.2f}€",
        f"Einstandswert: {summ['total_cost']:.2f}€",
        f"{emoji} G/V: {gsign}{g:.2f}€ ({gsign}{summ['profit_pct']:.1f}%)",
    ]
    top = summ["top_card"]
    if top:
        s = "+" if top["gain"] >= 0 else ""
        lines.append("")
        lines.append(f"🏆 Top-Karte: {top['name']} ({s}{top['gain']:.2f}€)")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_gekauft(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    if len(context.args) < 2:
        await update.message.reply_text("Nutzung: /gekauft <name> <preis>")
        return
    *name_parts, price_str = context.args
    name = " ".join(name_parts)
    try:
        price = float(price_str.replace(",", "."))
    except ValueError:
        await update.message.reply_text("Preis muss eine Zahl sein.")
        return

    product_id = None
    card = db.get_card_by_name(name)
    if card:
        product_id = card["cardmarket_product_id"]
    if not product_id:
        try:
            products = _mkm(context).find_products(name, exact=False)
            if products:
                product_id = products[0].get("idProduct")
        except CardmarketError:
            pass

    db.add_portfolio_card(name, price, product_id=product_id)
    # automatisch als Ausgabe verbuchen
    db.add_expense(price, f"Kauf: {name}")
    await update.message.reply_text(
        f"✅ '{name}' für {price:.2f}€ in die Sammlung aufgenommen "
        "(und als Ausgabe verbucht)."
    )


async def cmd_budget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    # optional: /budget <zahl> setzt das Wochenbudget
    if context.args:
        try:
            val = float(context.args[0].replace(",", "."))
            db.set_setting("weekly_budget", val)
            await update.message.reply_text(f"✅ Wochenbudget auf {val:.2f}€ gesetzt.")
            return
        except ValueError:
            pass

    weekly = float(db.get_setting("weekly_budget", "0"))
    spent_week = db.get_total_spent(days=7)
    spent_total = db.get_total_spent()
    lines = ["💶 *Budget*", ""]
    if weekly > 0:
        remaining = weekly - spent_week
        bar = _budget_bar(spent_week, weekly)
        lines.append(f"Diese Woche: {spent_week:.2f}€ / {weekly:.2f}€")
        lines.append(bar)
        lines.append(f"Verbleibend: {remaining:.2f}€")
    else:
        lines.append(f"Diese Woche: {spent_week:.2f}€ (kein Budget gesetzt)")
        lines.append("Setzen mit: /budget <zahl>")
    lines.append("")
    lines.append(f"Gesamt ausgegeben: {spent_total:.2f}€")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


def _budget_bar(spent: float, total: float, width: int = 10) -> str:
    if total <= 0:
        return ""
    frac = min(spent / total, 1.0)
    filled = int(round(frac * width))
    return "▓" * filled + "░" * (width - filled)


async def cmd_ausgabe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    if len(context.args) < 1:
        await update.message.reply_text("Nutzung: /ausgabe <betrag> <beschreibung>")
        return
    try:
        amount = float(context.args[0].replace(",", "."))
    except ValueError:
        await update.message.reply_text("Betrag muss eine Zahl sein.")
        return
    description = " ".join(context.args[1:]) or "—"
    db.add_expense(amount, description)
    await update.message.reply_text(f"✅ Ausgabe verbucht: {amount:.2f}€ – {description}")


async def cmd_briefing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    import briefing
    await update.message.reply_text(
        briefing.build_briefing(), parse_mode=ParseMode.MARKDOWN
    )


async def cmd_import(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    if not context.args:
        await update.message.reply_text("Nutzung: /import <wunschlisten-id>")
        return
    wantslist_id = context.args[0].strip()
    await update.message.reply_text("📥 Wunschliste wird importiert …")
    try:
        items = _mkm(context).get_wantslist(wantslist_id)
    except CardmarketError as exc:
        await update.message.reply_text(f"⚠️ Cardmarket-Fehler: {exc}")
        return
    if not items:
        await update.message.reply_text("❓ Wunschliste leer oder nicht gefunden.")
        return

    added, skipped = 0, 0
    for item in items:
        if db.get_card_by_name(item["name"]):
            skipped += 1
            continue
        db.add_card(item["name"], item.get("product_id"))
        added += 1
    await update.message.reply_text(
        f"✅ Import fertig: {added} neu hinzugefügt, {skipped} Duplikate übersprungen "
        f"({len(items)} Karten in der Wunschliste)."
    )


# ---------------------------------------------------------------- Scalp-Commands
_STOCK_EMOJI = {True: "✅", False: "❌", None: "❓"}


async def cmd_scalp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    targets = scalp_targets.list_with_status()
    if not targets:
        await update.message.reply_text(
            "📭 Keine Scalp-Targets. /scalp_add <produkt> <ziel_preis>"
        )
        return
    lines = ["💼 *Scalp-Watchlist*", ""]
    for t in targets:
        status = "" if t["active"] else " ⏸️"
        target = f"🎯 {t['retail_price_target']:.2f}€" if t["retail_price_target"] else "🎯 –"
        lines.append(f"*{t['product_name']}* ({t['product_type'] or '?'}) {target}{status}")
        # Händler-Status kompakt
        parts = []
        for r in t["retailers"]:
            if r["in_stock"] is None:
                continue
            parts.append(f"{_STOCK_EMOJI[r['in_stock']]}{r['retailer']}")
        lines.append("  " + (" · ".join(parts) if parts else "noch keine Checks"))
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_scalp_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    if len(context.args) < 2:
        await update.message.reply_text("Nutzung: /scalp_add <produktname> <ziel_preis>")
        return
    *name_parts, price_str = context.args
    name = " ".join(name_parts)
    try:
        target = float(price_str.replace(",", ".").replace("€", ""))
    except ValueError:
        await update.message.reply_text("Ziel-Einkaufspreis muss eine Zahl sein.")
        return
    if db.get_scalp_target_by_name(name):
        await update.message.reply_text(f"ℹ️ '{name}' ist bereits ein Scalp-Target.")
        return
    ptype = image_recognition.guess_product_type(name)
    db.add_scalp_target(name, product_type=ptype, retail_price_target=target)
    await update.message.reply_text(
        f"💼 '{name}' ({ptype}) als Scalp-Target hinzugefügt — Ziel {target:.2f}€."
    )


async def cmd_scalp_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    name = " ".join(context.args).strip()
    if not name:
        await update.message.reply_text("Nutzung: /scalp_remove <produktname>")
        return
    n = db.remove_scalp_target_by_name(name)
    await update.message.reply_text(
        f"🗑️ '{name}' entfernt." if n else f"❓ '{name}' nicht gefunden."
    )


async def cmd_scalp_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    name = " ".join(context.args).strip()
    if not name:
        await update.message.reply_text("Nutzung: /scalp_pause <produktname>")
        return
    target = db.get_scalp_target_by_name(name)
    if not target:
        await update.message.reply_text(f"❓ '{name}' nicht gefunden.")
        return
    new_active = not bool(target["active"])
    db.set_scalp_active(target["id"], new_active)
    await update.message.reply_text(
        f"{'▶️ aktiviert' if new_active else '⏸️ pausiert'}: '{name}'."
    )


async def cmd_restocks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    rows = db.get_recent_restocks(hours=48, limit=10)
    if not rows:
        await update.message.reply_text("📭 Keine Restock-Events in den letzten 48h.")
        return
    lines = ["📦 *Letzte Restocks*", ""]
    for r in rows:
        price = f"{r['price']:.2f}€" if r["price"] is not None else "?"
        lines.append(
            f"• {r['product_name']} @ {r['retailer_name']} – {price} "
            f"({r['checked_at'][:16].replace('T', ' ')})"
        )
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_profit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    if len(context.args) < 2:
        await update.message.reply_text("Nutzung: /profit <produktname> <kaufpreis>")
        return
    *name_parts, price_str = context.args
    name = " ".join(name_parts)
    try:
        buy = float(price_str.replace(",", ".").replace("€", ""))
    except ValueError:
        await update.message.reply_text("Kaufpreis muss eine Zahl sein.")
        return
    c = profit_calculator.calculate_profit(buy, name)
    if not c["sealed_known"]:
        await update.message.reply_text(
            f"💼 *{name}*\nKaufpreis: {buy:.2f}€\n"
            "⚠️ Kein Cardmarket-Sealed-Preis vorhanden. Erst als Scalp-Target "
            "anlegen, dann werden Preise alle 6h aktualisiert.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    await update.message.reply_text(
        f"💼 *Profit-Rechner: {name}*\n\n"
        f"Kaufpreis: {c['buy_price']:.2f}€\n"
        f"Cardmarket avg: {c['cm_avg']:.2f}€\n"
        f"Realistischer Verkauf: {c['sell_realistic']:.2f}€\n"
        f"Gebühren gesamt: {c['total_fees']:.2f}€\n"
        f"  (CM {c['cardmarket_fee']:.2f} · PayPal {c['paypal_fee']:.2f} · "
        f"Versand {c['shipping']:.2f} · Verpackung {c['packaging']:.2f})\n"
        f"➡️ Netto: {c['net_profit']:.2f}€ | Marge: {c['margin_pct']:.1f}%\n"
        f"💡 Empfehlung: *{c['recommendation']}*",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_releases(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    await update.message.reply_text(
        release_calendar.format_upcoming(60), parse_mode=ParseMode.MARKDOWN
    )


async def cmd_release_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    if len(context.args) < 2:
        await update.message.reply_text(
            "Nutzung: /release_add <set-name> <YYYY-MM-DD> [pre]"
        )
        return
    args = list(context.args)
    pre_order = False
    if args[-1].lower() in ("pre", "preorder", "pre-order"):
        pre_order = True
        args = args[:-1]
    date_str = args[-1]
    set_name = " ".join(args[:-1])
    # Datum validieren
    from datetime import date
    try:
        date.fromisoformat(date_str)
    except ValueError:
        await update.message.reply_text("Datum bitte als YYYY-MM-DD angeben.")
        return
    db.add_release(set_name, date_str, pre_order_available=pre_order)
    pre_txt = " (Pre-Order)" if pre_order else ""
    await update.message.reply_text(
        f"📅 Release eingetragen: {set_name} am {date_str}{pre_txt}."
    )


async def cmd_retailers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    retailers = db.get_retailers()
    if not retailers:
        await update.message.reply_text("Keine Händler konfiguriert.")
        return
    lines = ["🏪 *Händler-Status*", ""]
    for r in retailers:
        if not r["active"]:
            state = "⏸️"
        elif r["last_error"]:
            state = "🔴"
        elif r["last_check"]:
            state = "🟢"
        else:
            state = "⚪"
        rate = f"{(r['success_rate'] or 0) * 100:.0f}%"
        last = r["last_check"][:16].replace("T", " ") if r["last_check"] else "nie"
        lines.append(f"{state} {r['name']} ({r['scrape_method']}) · {rate} · {last}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


# ---------------------------------------------------------------- Aktions-Buttons
# Button-Definitionen: key -> (Label, callback_data)
_ACTION_BUTTONS = {
    "collect": ("✅ Sammlung", "pc:collect"),
    "price":   ("💰 Preis-Check", "pc:price"),
    "watch":   ("🔔 Watchlist", "pc:watch"),
    "scalp":   ("💼 Scalp-Track", "pc:scalp"),
}


def _build_action_keyboard(keys: list[str], is_sealed: bool) -> InlineKeyboardMarkup:
    """Baut ein Inline-Keyboard (2 Buttons pro Reihe). Scalp-Track nur,
    wenn ein versiegeltes Produkt erkannt wurde."""
    btns = []
    for k in keys:
        if k == "scalp" and not is_sealed:
            continue
        label, data = _ACTION_BUTTONS[k]
        btns.append(InlineKeyboardButton(label, callback_data=data))
    rows = [btns[i:i + 2] for i in range(0, len(btns), 2)]
    return InlineKeyboardMarkup(rows)


def _price_check_text(context: ContextTypes.DEFAULT_TYPE, product_id: int | None,
                      name: str, card_id: int | None = None) -> str:
    """Erstellt den Preis-Check-Text: Top-5 DE-Angebote, DE/EN/JP-Vergleich,
    7-Tage-Trend, Deal-Score. Speichert nichts dauerhaft."""
    if not product_id:
        return f"💰 *{name}*\n\nKeine Cardmarket-Produkt-ID gefunden."

    lines = [f"💰 *Preis-Check: {name}*", ""]

    # Top-5 günstigste DE-Angebote
    try:
        articles = _mkm(context).get_articles(product_id, maxResults=100)
    except CardmarketError as exc:
        return f"💰 *{name}*\n\n⚠️ Cardmarket-Fehler: {exc}"
    de_offers = sorted(filter_de_offers(articles), key=lambda o: o["price"])
    if de_offers:
        lines.append("🏪 *Top 5 DE-Angebote:*")
        for o in de_offers[:5]:
            lines.append(
                f"• {o['price']:.2f}€ – {o['condition']} / {o['language']} "
                f"({o['seller_reputation']:.0f}%)"
            )
        lines.append("")
    else:
        lines.append("Keine passenden DE-Angebote.")
        lines.append("")

    # DE/EN/JP-Vergleich
    lines.append("🌍 *Sprachvergleich:*")
    for lang_label, lang_id in LANG_QUERY.items():
        try:
            lang_articles = _mkm(context).get_articles(
                product_id, idLanguage=lang_id, maxResults=50
            )
        except CardmarketError:
            lang_articles = []
        lang_offers = filter_de_offers(lang_articles)
        med = market_median(lang_offers)
        if med:
            cheapest = min(o["price"] for o in lang_offers)
            lines.append(f"{lang_label}: ab {cheapest:.2f}€ (Median {med:.2f}€)")
        else:
            lines.append(f"{lang_label}: keine DE-Angebote")
    lines.append("")

    # Trend (nur bei Watchlist-Historie verfügbar)
    if card_id:
        trend = trend_analyzer.analyze(card_id)
    else:
        trend = {"emoji": trend_analyzer.TREND_EMOJI["unbekannt"],
                 "trend": "unbekannt", "change_pct": 0.0, "recommendation": "egal"}
    lines.append(f"📈 Trend: {trend['emoji']} {trend['trend']} "
                 f"({trend['change_pct']:+.1f}%) | 💡 {trend['recommendation']}")

    # Deal-Score (günstigstes Angebot vs. Markt)
    if de_offers:
        market = market_median(de_offers)
        cheapest = de_offers[0]
        if market and cheapest["price"] < market:
            savings = round((market - cheapest["price"]) / market * 100, 1)
        else:
            savings = 0.0
        score_info = deal_scorer.compute_score(
            savings, cheapest["seller_reputation"], cheapest["condition"],
            trend["trend"],
        )
        lines.append(f"🏆 Deal-Score: {score_info['score']}/100")

    return "\n".join(lines)


# ---------------------------------------------------------------- Bilderkennung
def _pokeprice_analysis(recog: dict) -> dict:
    """Marktpreis + Deal-Score über pokemontcg.io (wenn kein Cardmarket)."""
    info = {
        "product_id": None, "min_price": None, "market_price": None,
        "trend": {"emoji": trend_analyzer.TREND_EMOJI["unbekannt"],
                  "trend": "unbekannt", "recommendation": "egal"},
        "score": None, "best_offer": None, "source": "pokemontcg",
    }
    # Englischen Namen bevorzugen (pokemontcg.io kennt nur englische Namen)
    query_name = recog.get("card_name_en") or recog.get("card_name", "")
    # Cardmarket-Suchlink als Fallback (immer verfügbar)
    search_url = pokeprice.cardmarket_search_url(query_name)
    card = pokeprice.lookup(query_name, recog.get("set_name"),
                            recog.get("card_number"))
    if not card:
        log.info("pokeprice: keine Treffer fuer '%s' (Set '%s', Nr '%s')",
                 query_name, recog.get("set_name"), recog.get("card_number"))
        info["url"] = search_url
        return info
    log.info("pokeprice: '%s' -> de_low=%s low=%s avg=%s", query_name,
             card.get("de_low"), card.get("low"), card.get("avg"))
    # DE-Preis bevorzugen (germanProLow), sonst allgemeiner Cardmarket-low
    min_price = card.get("de_low") or card.get("low")
    avg = card.get("avg")
    info["min_price"] = min_price
    info["market_price"] = avg
    # Direkter Produktlink, sonst Suchlink (z.B. bei JP-Karten ohne Preise)
    info["url"] = card.get("url") or search_url
    info["trend"] = pokeprice.trend_from_prices(card)
    # Deal-Score nur, wenn ein Marktpreis vorliegt (sonst nicht aussagekräftig)
    if avg and min_price:
        savings = round((avg - min_price) / avg * 100, 1) if min_price < avg else 0.0
        # Keine Verkäuferbewertung verfügbar -> Reputation neutral (98 = 0 Punkte)
        score_info = deal_scorer.compute_score(
            savings, 98.0, recog.get("condition_estimate") or "NM",
            info["trend"]["trend"],
        )
        info["score"] = score_info["score"]
    return info


def _pokeprice_text(name: str, set_name: str | None = None,
                    number: str | None = None) -> str:
    """Preis-Übersicht über pokemontcg.io (Cardmarket-EUR-Preise)."""
    card = pokeprice.lookup(name, set_name, number)
    if not card:
        return (
            f"💰 *{name}*\n\nKeine Preisdaten gefunden (oft bei japanischen "
            "oder sehr neuen Karten).\n"
            f"🔗 Auf Cardmarket suchen: {pokeprice.cardmarket_search_url(name)}"
        )

    def fmt(v):
        # 0 oder None gilt als "nicht verfügbar"
        return f"{v:.2f}€" if isinstance(v, (int, float)) and v > 0 else "–"

    tr = pokeprice.trend_from_prices(card)
    lines = [f"💰 *{card['name']}*"]
    sub = []
    if card.get("set_name"):
        sub.append(card["set_name"])
    if card.get("number"):
        sub.append(f"Nr. {card['number']}")
    if card.get("rarity"):
        sub.append(card["rarity"])
    if sub:
        lines.append("📦 " + " · ".join(sub))
    lines += ["", "💶 *Cardmarket-Preise (EUR):*"]
    if card.get("de_low"):   # nur zeigen, wenn ein echter DE-Wert vorliegt
        lines.append(f"🇩🇪 Günstigster DE-Händler: {fmt(card.get('de_low'))}")
    lines += [
        f"• Günstigst (Cardmarket): {fmt(card.get('low'))}",
        f"• Durchschnitt: {fmt(card.get('avg'))}",
        f"• Trend: {fmt(card.get('trend'))}",
        "",
        f"📈 Tendenz: {tr['emoji']} {tr['trend']} ({tr['change_pct']:+.1f}%) | "
        f"💡 {tr['recommendation']}",
    ]
    has_prices = any(card.get(k) for k in ("de_low", "low", "avg", "trend"))
    if not has_prices:
        lines.append("⚠️ Keine Preisdaten (oft bei JP/sehr neuen Karten) — "
                     "Preis bitte über den Link prüfen.")
    cm_url = card.get("url") or pokeprice.cardmarket_search_url(name)
    lines.append(f"🔗 Zum Angebot (Cardmarket DE): {cm_url}")
    lines += ["", "_Quelle: pokemontcg.io · Preise in EUR (Cardmarket EU-Markt)_"]
    return "\n".join(lines)


def _analyze_recognized_card(context: ContextTypes.DEFAULT_TYPE,
                             recog: dict) -> dict:
    """Marktpreis + Deal-Score für eine erkannte Karte.

    Nutzt Cardmarket, falls Tokens gesetzt sind, sonst pokemontcg.io.
    """
    if not config.cardmarket_enabled():
        return _pokeprice_analysis(recog)

    info = {
        "product_id": None, "min_price": None, "market_price": None,
        "trend": {"emoji": trend_analyzer.TREND_EMOJI["unbekannt"],
                  "trend": "unbekannt", "recommendation": "egal"},
        "score": None, "best_offer": None,
    }
    name = recog.get("card_name", "")
    if not name:
        return info
    try:
        products = _mkm(context).find_products(name, exact=False)
    except CardmarketError:
        return info
    if not products:
        return info
    product_id = products[0].get("idProduct")
    info["product_id"] = product_id

    try:
        articles = _mkm(context).get_articles(product_id, maxResults=100)
    except CardmarketError:
        return info
    offers = filter_de_offers(articles)
    if not offers:
        return info

    market_price = market_median(offers)
    cheapest = min(offers, key=lambda o: o["price"])
    info["min_price"] = cheapest["price"]
    info["market_price"] = market_price
    info["best_offer"] = cheapest

    if market_price and cheapest["price"] < market_price:
        savings_pct = round((market_price - cheapest["price"]) / market_price * 100, 1)
    else:
        savings_pct = 0.0
    # erkannte Karte ist (noch) nicht auf der Watchlist -> Trend "unbekannt"
    score_info = deal_scorer.compute_score(
        savings_pct, cheapest["seller_reputation"],
        cheapest["condition"], "unbekannt",
    )
    info["score"] = score_info["score"]
    return info


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    chat_id = str(update.effective_chat.id)

    # Rate-Limit: max N Bilder/Stunde
    if db.count_image_requests_since(hours=1) >= config.MAX_IMAGES_PER_HOUR:
        await update.message.reply_text(
            f"⏳ Limit erreicht ({config.MAX_IMAGES_PER_HOUR} Bilder/Stunde). "
            "Bitte später erneut."
        )
        return

    photo = update.message.photo[-1]  # größte Auflösung
    if photo.file_size and photo.file_size > config.MAX_IMAGE_BYTES:
        await update.message.reply_text("⚠️ Foto zu groß (max 5 MB).")
        return

    await context.bot.send_chat_action(chat_id, "typing")
    status = await update.message.reply_text("🔍 Analysiere Karte …")

    # temporär speichern
    temp_path = os.path.join(
        str(config.CARD_IMAGES_DIR), f"_temp_{chat_id}_{int(time.time())}.jpg"
    )
    try:
        tg_file = await photo.get_file()
        await tg_file.download_to_drive(temp_path)
    except Exception:
        log.exception("Foto-Download fehlgeschlagen")
        await status.edit_text("⚠️ Foto konnte nicht geladen werden.")
        return

    # Gemini-Erkennung (blockierend -> Executor)
    import asyncio
    loop = asyncio.get_running_loop()
    recog = await loop.run_in_executor(None, image_recognition.recognize, temp_path)

    if "error" in recog:
        db.record_image_request(chat_id, None, success=False)
        _safe_remove(temp_path)
        await status.edit_text(f"❌ Karte nicht erkannt: {recog['error']}")
        return

    db.record_image_request(chat_id, recog.get("card_name"), success=True)

    # Marktdaten + Score
    analysis = await loop.run_in_executor(
        None, _analyze_recognized_card, context, recog
    )

    # pending-Zustand für Button-Callbacks merken
    context.user_data["pending_card"] = {
        "recog": recog,
        "analysis": analysis,
        "temp_path": temp_path,
    }

    msg = _format_recognition(recog, analysis)
    is_sealed = image_recognition.is_sealed(recog.get("product_type"))
    keyboard = _build_action_keyboard(
        ["collect", "price", "watch", "scalp"], is_sealed
    )
    await status.edit_text(msg, reply_markup=keyboard, disable_web_page_preview=True)


def _format_recognition(recog: dict, analysis: dict) -> str:
    conf = int(round(recog.get("confidence", 0) * 100))
    min_price = analysis.get("min_price")
    market = analysis.get("market_price")
    trend = analysis["trend"]
    score = analysis.get("score")
    sealed_line = ""
    if image_recognition.is_sealed(recog.get("product_type")):
        sealed_line = f"📦 Versiegeltes Produkt: {recog.get('product_type')}\n"
    min_str = f"{min_price:.2f}€" if isinstance(min_price, (int, float)) and min_price > 0 else "–"
    market_str = f"{market:.2f}€" if isinstance(market, (int, float)) and market > 0 else "–"
    url = analysis.get("url")
    link_line = f"\n🔗 Günstigstes Angebot: {url}" if url else ""
    return (
        f"🔍 Erkannt! ({conf}% sicher)\n\n"
        f"🃏 {recog.get('card_name', '?')} | {recog.get('card_number', '?')}\n"
        f"📦 {recog.get('set_name', '?')} | ⭐ {recog.get('rarity', '?')}\n"
        f"🌍 {recog.get('language', '?')} | Zustand ca.: "
        f"{recog.get('condition_estimate', '?')}\n"
        f"{sealed_line}\n"
        f"💰 Günstigster Preis: {min_str}\n"
        f"📊 Marktpreis (Ø): {market_str}\n"
        f"📈 Trend: {trend['emoji']} {trend['trend']}\n"
        f"🏆 Deal-Score: {score if score is not None else '–'}/100"
        f"{link_line}\n\n"
        "Was möchtest du tun?"
    )


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Verarbeitet die Inline-Buttons nach Bilderkennung bzw. /preis und /add."""
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    pending = context.user_data.get("pending_card")
    if not pending:
        await query.edit_message_text("⌛ Diese Karte ist nicht mehr aktiv.")
        return

    recog = pending["recog"]
    analysis = pending["analysis"]
    temp_path = pending.get("temp_path")
    name = recog.get("card_name", "Unbekanntes Produkt")
    product_id = analysis.get("product_id")
    is_sealed = image_recognition.is_sealed(recog.get("product_type"))

    # --- 💰 Preis-Check: nur Info, kein dauerhafter Eintrag, Buttons bleiben ---
    if data == "pc:price":
        import asyncio
        loop = asyncio.get_running_loop()
        if not config.cardmarket_enabled():
            text = await loop.run_in_executor(
                None, _pokeprice_text, recog.get("card_name_en") or name,
                recog.get("set_name"), recog.get("card_number"),
            )
        else:
            card = db.get_card_by_name(name)
            card_id = card["id"] if card else None
            text = await loop.run_in_executor(
                None, _price_check_text, context, product_id, name, card_id
            )
        keyboard = _build_action_keyboard(["collect", "watch", "scalp"], is_sealed)
        await query.edit_message_text(
            text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard,
            disable_web_page_preview=True,
        )
        return

    # --- 🔔 Watchlist: hinzufügen + Alert-Schwelle abfragen ---
    if data == "pc:watch":
        card = db.get_card_by_name(name)
        if card:
            card_id = card["id"]
            await query.edit_message_text(
                f"ℹ️ '{name}' ist bereits auf der Watchlist."
            )
        else:
            card_id = db.add_card(name, product_id)
            await query.edit_message_text(
                f"🔔 '{name}' zur Watchlist hinzugefügt.\n"
                f"Bei welcher Ersparnis alarmieren? (Standard: "
                f"{config.DEFAULT_WATCHLIST_ALERT_THRESHOLD:.0f}%)\n"
                "Antworte mit einer Zahl oder „standard“."
            )
            context.user_data["awaiting_alert_threshold"] = card_id
        _safe_remove(temp_path)
        context.user_data.pop("pending_card", None)
        return

    # --- 💼 Scalp-Track: nur für versiegelte Produkte ---
    if data == "pc:scalp":
        if not is_sealed:
            await query.edit_message_text(
                "⚠️ Scalp-Tracking ist nur für versiegelte Produkte verfügbar."
            )
            return
        existing = db.get_scalp_target_by_name(name)
        if existing:
            scalp_id = existing["id"]
            db.set_scalp_active(scalp_id, True)
        else:
            scalp_id = db.add_scalp_target(
                product_name=name,
                product_type=recog.get("product_type"),
                set_name=recog.get("set_name"),
            )
        # Foto dauerhaft speichern, falls eines vorliegt
        if temp_path:
            final_path = os.path.join(
                str(config.CARD_IMAGES_DIR), f"scalp_{scalp_id}_{int(time.time())}.jpg"
            )
            try:
                os.replace(temp_path, final_path)
                db.set_scalp_image_path(scalp_id, final_path)
            except OSError:
                log.warning("Scalp-Foto konnte nicht gespeichert werden.")
        context.user_data["awaiting_scalp_target"] = scalp_id
        context.user_data.pop("pending_card", None)
        await query.edit_message_text(
            f"💼 '{name}' für Scalp-Tracking vorgemerkt.\n"
            "🎯 Ziel-Einkaufspreis? (€)"
        )
        return

    # --- ✅ Sammlung: Portfolio-Eintrag + Kaufpreis abfragen ---
    if data == "pc:collect":
        card_id = db.add_portfolio_card(
            card_name=name,
            purchase_price=0.0,
            product_id=product_id,
            condition=recog.get("condition_estimate"),
            language=recog.get("language"),
            set_name=recog.get("set_name"),
            card_number=recog.get("card_number"),
            rarity=recog.get("rarity"),
        )
        if temp_path:
            final_path = os.path.join(
                str(config.CARD_IMAGES_DIR), f"{card_id}_{int(time.time())}.jpg"
            )
            try:
                os.replace(temp_path, final_path)
                db.set_portfolio_image_path(card_id, final_path)
            except OSError:
                log.warning("Foto konnte nicht dauerhaft gespeichert werden.")

        if analysis.get("market_price") is not None:
            db.add_portfolio_value(card_id, analysis["market_price"])

        context.user_data["awaiting_price"] = card_id
        context.user_data.pop("pending_card", None)
        await query.edit_message_text(
            f"✅ '{name}' in die Sammlung aufgenommen.\n"
            "💶 Wie viel hast du bezahlt? (Zahl in €)"
        )
        return


def _safe_remove(path: str) -> None:
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


# ---------------------------------------------------------------- Freitext
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    text = update.message.text.strip()
    if not text:
        return

    # Follow-up-Eingaben haben Vorrang vor dem KI-Chat

    # 1) Kaufpreis nach "Sammlung"
    pending_price_id = context.user_data.get("awaiting_price")
    if pending_price_id:
        try:
            price = float(text.replace(",", ".").replace("€", "").strip())
        except ValueError:
            await update.message.reply_text(
                "Bitte den Kaufpreis als Zahl angeben (z. B. 49.90)."
            )
            return
        db.update_portfolio_purchase_price(pending_price_id, price)
        db.add_expense(price, f"Kauf: {_portfolio_name(pending_price_id)}")
        context.user_data.pop("awaiting_price", None)
        await update.message.reply_text(
            f"✅ Kaufpreis {price:.2f}€ gespeichert. Karte ist vollständig in der "
            "Sammlung (und als Ausgabe verbucht)."
        )
        return

    # 2) Alert-Schwelle nach "Watchlist"
    pending_alert_card = context.user_data.get("awaiting_alert_threshold")
    if pending_alert_card:
        default = config.DEFAULT_WATCHLIST_ALERT_THRESHOLD
        if text.lower() in ("standard", "default", "-", "ok"):
            threshold = default
        else:
            try:
                threshold = float(text.replace(",", ".").replace("%", "").strip())
            except ValueError:
                await update.message.reply_text(
                    f"Bitte eine Prozentzahl angeben oder „standard“ "
                    f"(Standard: {default:.0f}%)."
                )
                return
        db.set_card_alert_threshold(pending_alert_card, threshold)
        context.user_data.pop("awaiting_alert_threshold", None)
        await update.message.reply_text(
            f"🔔 Alarmiere bei ≥ {threshold:.0f}% Ersparnis. "
            "Automatische Scans sind aktiv."
        )
        return

    # 3) Ziel-Einkaufspreis nach "Scalp-Track"
    pending_scalp = context.user_data.get("awaiting_scalp_target")
    if pending_scalp:
        try:
            target = float(text.replace(",", ".").replace("€", "").strip())
        except ValueError:
            await update.message.reply_text(
                "Bitte den Ziel-Einkaufspreis als Zahl angeben (z. B. 149.90)."
            )
            return
        db.update_scalp_target_price(pending_scalp, target)
        context.user_data.pop("awaiting_scalp_target", None)
        await update.message.reply_text(
            f"💼 Ziel-Einkaufspreis {target:.2f}€ gespeichert. "
            "Retail-Monitoring wird aktiviert, sobald das Scalping-Modul läuft."
        )
        return

    await context.bot.send_chat_action(update.effective_chat.id, "typing")
    answer = ai_chat.ask(text)
    await update.message.reply_text(answer)


def _portfolio_name(portfolio_card_id: int) -> str:
    row = db.get_portfolio_card(portfolio_card_id)
    return row["card_name"] if row else "Karte"


# ---------------------------------------------------------------- Scan-Helper
async def run_scan_and_alert(application: Application) -> list[dict]:
    """Führt einen Scan aus und sendet alle Alerts an den Haupt-Chat.

    Wird sowohl vom /scan-Command als auch vom Scheduler aufgerufen.
    Der blockierende Cardmarket-Teil läuft in einem Thread-Executor.
    """
    import asyncio
    mkm = application.bot_data["mkm"]
    loop = asyncio.get_running_loop()
    alerts = await loop.run_in_executor(None, scanner.run_scan, mkm)

    chat_id = config.TELEGRAM_CHAT_ID
    if chat_id:
        for alert in alerts:
            try:
                await application.bot.send_message(
                    chat_id=chat_id,
                    text=alert["message"],
                    disable_web_page_preview=False,
                )
            except Exception:
                log.exception("Alert-Versand fehlgeschlagen")
    return alerts


# ---------------------------------------------------------------- Registry
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.error("Unbehandelter Bot-Fehler", exc_info=context.error)


def register_handlers(application: Application) -> None:
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("watchlist", cmd_watchlist))
    application.add_handler(CommandHandler("add", cmd_add))
    application.add_handler(CommandHandler("remove", cmd_remove))
    application.add_handler(CommandHandler("preis", cmd_preis))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CommandHandler("threshold", cmd_threshold))
    application.add_handler(CommandHandler("score", cmd_score))
    application.add_handler(CommandHandler("scan", cmd_scan))
    application.add_handler(CommandHandler("sammlung", cmd_sammlung))
    application.add_handler(CommandHandler("wert", cmd_wert))
    application.add_handler(CommandHandler("gekauft", cmd_gekauft))
    application.add_handler(CommandHandler("budget", cmd_budget))
    application.add_handler(CommandHandler("ausgabe", cmd_ausgabe))
    application.add_handler(CommandHandler("briefing", cmd_briefing))
    application.add_handler(CommandHandler("import", cmd_import))
    # Scalping-Commands
    application.add_handler(CommandHandler("scalp", cmd_scalp))
    application.add_handler(CommandHandler("scalp_add", cmd_scalp_add))
    application.add_handler(CommandHandler("scalp_remove", cmd_scalp_remove))
    application.add_handler(CommandHandler("scalp_pause", cmd_scalp_pause))
    application.add_handler(CommandHandler("restocks", cmd_restocks))
    application.add_handler(CommandHandler("profit", cmd_profit))
    application.add_handler(CommandHandler("releases", cmd_releases))
    application.add_handler(CommandHandler("release_add", cmd_release_add))
    application.add_handler(CommandHandler("retailers", cmd_retailers))
    application.add_handler(CallbackQueryHandler(on_callback, pattern=r"^pc:"))
    application.add_handler(MessageHandler(filters.PHOTO, on_photo))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, on_text)
    )
    application.add_error_handler(on_error)
    log.info("Telegram-Handler registriert.")
