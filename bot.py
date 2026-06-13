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
import tcgdex as pokeprice   # TCGdex: Name→idProduct-Mapper + Fallback-Preise
import cm_priceguide          # Lokaler Cardmarket Price Guide (taeglich, ~75k Produkte)

log = logging.getLogger(__name__)


def _authorized(update: Update) -> bool:
    """Nur der konfigurierte Chat darf den Bot bedienen."""
    if not config.TELEGRAM_CHAT_ID:
        return True
    return str(update.effective_chat.id) == str(config.TELEGRAM_CHAT_ID)


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
        "/deals – Beste SIR/IR-Deals heute 🔥\n"
        "/karte <Set> <Nr> – Karte direkt suchen (z.B. /karte sv06 10)\n"
        "/preis <Name> – Preis + Trend + Empfehlung\n"
        "/sammlung – Portfolio\n"
        "/wert – Gesamtwert + G/V\n"
        "/budget – Budgetstatus\n"
        "/briefing – Tagesbriefing jetzt\n"
        "/import <id> – Cardmarket-Wunschliste importieren\n"
        "/status – Bot-Status\n\n"
        "📸 *Schick mir ein Foto* einer Karte oder eines versiegelten Produkts — "
        "ich erkenne es, zeige dir Preis + Trend, und du wählst per Button:\n"
        "✅ Bestätigen → ➕ Sammlung · 🔔 Watchlist · 💼 Scalp-Track\n\n"
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
        card = pokeprice.lookup(name)
        if card:
            product_id = card.get("idProduct")
    except Exception as exc:
        log.warning("TCGdex-Produktsuche fehlgeschlagen: %s", exc)

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

    # Preise über TCGdex + lokalen CM Price Guide
    import asyncio
    loop = asyncio.get_running_loop()
    text = await loop.run_in_executor(None, _pokeprice_text, name, None, None)
    is_sealed = _set_pending_from_command(context, name, None)
    keyboard = _build_action_keyboard(["collect", "watch", "scalp"], is_sealed)
    await update.message.reply_text(
        text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard
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
    threshold = db.get_setting("savings_threshold")
    min_score = db.get_setting("min_score")
    scans = db.count_scans_since(days=1)
    pg_count = db.cm_price_guide_count()
    pg_status = f"🟢 {pg_count:,} Produkte" if pg_count else "🔴 noch nicht geladen"
    sir_count = db.sir_ir_cache_count()
    await update.message.reply_text(
        "🤖 *Status*\n\n"
        f"CM Price Guide: {pg_status}\n"
        f"SIR/IR-Cache: {sir_count} Karten\n"
        f"Watchlist: {len(db.get_watchlist())} Karten\n"
        f"Sammlung: {len(db.get_portfolio())} Karten\n"
        f"Ersparnis-Schwelle: {threshold}%\n"
        f"Min. Deal-Score: {min_score}\n"
        f"Scans (24h): {scans}",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Startet den Bot-Dienst neu (nur für autorisierten Chat)."""
    if not _authorized(update):
        return
    import asyncio, subprocess
    await update.message.reply_text("🔄 Bot wird neu gestartet … bin gleich wieder da!")
    await asyncio.sleep(1)          # Nachricht noch senden bevor Prozess stirbt
    subprocess.Popen(["systemctl", "restart", "pokemon-tracker"])


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
            found = pokeprice.lookup(name)
            if found:
                product_id = found.get("idProduct")
        except Exception:
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


async def cmd_deals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Zeigt aktuelle SIR/IR-Deals: Karten deutlich unter Cardmarket-Marktwert."""
    if not _authorized(update):
        return
    import asyncio, deal_scanner
    loop = asyncio.get_running_loop()
    cache_count = db.sir_ir_cache_count()
    hint = f"{cache_count} SIR/IR-Karten" if cache_count > 0 else "Cache wird geprüft"
    msg_loading = await update.message.reply_text(f"🔍 Suche Deals in {hint} …")
    deals = await loop.run_in_executor(None, deal_scanner.get_deals)
    text = deal_scanner.format_deals_message(deals)
    await msg_loading.edit_text(text, parse_mode=ParseMode.MARKDOWN,
                                disable_web_page_preview=True)


async def cmd_deals_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Baut SIR/IR-Cache manuell auf und zeigt danach sofort Deals."""
    if not _authorized(update):
        return
    import asyncio, deal_scanner
    loop = asyncio.get_running_loop()
    cache_before = db.sir_ir_cache_count()
    status = await update.message.reply_text(
        "🔄 SIR/IR-Datenbank wird aufgebaut …\n"
        "_(Lädt SIR/IR/Hyper Rare direkt per Rarity-Endpunkt — dauert ca. 2-5 Min)_",
        parse_mode=ParseMode.MARKDOWN,
    )
    added = await loop.run_in_executor(None, deal_scanner.refresh_sir_ir_cache)
    cache_after = db.sir_ir_cache_count()
    await status.edit_text(
        f"✅ Cache aktualisiert: {cache_after} Karten ({added} neu hinzugefügt)\n"
        "🔍 Suche jetzt nach Deals …",
        parse_mode=ParseMode.MARKDOWN,
    )
    deals = await loop.run_in_executor(None, deal_scanner.get_deals)
    text = deal_scanner.format_deals_message(deals)
    await status.edit_text(text, parse_mode=ParseMode.MARKDOWN,
                           disable_web_page_preview=True)


async def cmd_deals_debug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Testet den kompletten Refresh-Pfad fuer 3 SIR-Karten."""
    if not _authorized(update):
        return
    import asyncio, requests as req, urllib.parse, time as _time

    BASE = "https://api.tcgdex.net/v2"
    # SIR direkt testen (wissen wir, dass es existiert)
    TEST_RARITY = "Special illustration rare"

    def _debug():
        lines = []

        # 1. Karten der Rarity laden
        encoded = urllib.parse.quote(TEST_RARITY, safe="")
        try:
            r = req.get(f"{BASE}/en/rarities/{encoded}", timeout=15)
            lines.append(f"Rarity HTTP: {r.status_code}")
            data = r.json()
            cards = data.get("cards", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
            lines.append(f"Karten gefunden: {len(cards)}")
        except Exception as e:
            lines.append(f"Rarity-Fehler: {e}")
            return "\n".join(lines)

        if not cards:
            lines.append("Keine Karten → Abbruch")
            return "\n".join(lines)

        # 2. Erste 3 Karten: Detail + DB-Insert testen
        inserted = 0
        for card in cards[:3]:
            card_id = card.get("id", "")
            number = str(card.get("localId") or "")
            name = card.get("name", "")
            lines.append(f"\n--- {card_id} ({name}) ---")

            # set_id ableiten
            set_id = card_id[:-(len(number)+1)] if number and card_id.endswith(f"-{number}") else ""
            lines.append(f"set_id={set_id!r}, number={number!r}")

            try:
                r2 = req.get(f"{BASE}/en/cards/{card_id}", timeout=15)
                lines.append(f"Detail HTTP: {r2.status_code}")
                if r2.status_code != 200:
                    lines.append(f"Body: {r2.text[:100]}")
                    continue
                detail = r2.json()
                cm = (detail.get("pricing") or {}).get("cardmarket") or {}
                id_product = cm.get("idProduct")
                cm_url = cm.get("url")
                lines.append(f"idProduct={id_product}, url={str(cm_url)[:60]}")

                if id_product and set_id:
                    db.upsert_sir_ir_card(
                        id_product=int(id_product),
                        name=detail.get("name") or name,
                        set_name=(detail.get("set") or {}).get("name") or set_id,
                        set_id=set_id,
                        number=number,
                        rarity=TEST_RARITY,
                        cm_url=cm_url,
                    )
                    inserted += 1
                    lines.append("→ DB-Insert OK")
                else:
                    lines.append(f"→ Übersprungen (id_product={id_product}, set_id={set_id!r})")
            except Exception as e:
                lines.append(f"→ FEHLER: {e}")
            _time.sleep(0.3)

        cache = db.sir_ir_cache_count()
        lines.append(f"\nInserted: {inserted}, Cache gesamt: {cache}")
        return "\n".join(lines)

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _debug)
    await update.message.reply_text(f"```\n{result}\n```", parse_mode=ParseMode.MARKDOWN)


async def cmd_karte(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direkte Kartensuche per Set-Kürzel + Nummer. Beispiel: /karte sv06 10/198"""
    if not _authorized(update):
        return
    import asyncio

    args = context.args
    if len(args) < 2:
        await update.message.reply_text(
            "📋 *Karten-Direktsuche*\n\n"
            "Nutzung: `/karte <Set-Kürzel> <Nummer>`\n\n"
            "Beispiele:\n"
            "• `/karte sv06 10`\n"
            "• `/karte sv06 10/198`\n"
            "• `/karte 151 10`\n"
            "• `/karte par 200`\n\n"
            "Set-Kürzel findest du unten rechts auf der Karte "
            "(z.B. `sv06`, `par`, `twm`, `151`).",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    set_code = args[0].strip()
    number_raw = args[1].strip()

    status_msg = await update.message.reply_text(
        f"🔍 Suche Karte {set_code.upper()} #{number_raw} …"
    )

    loop = asyncio.get_running_loop()
    card = await loop.run_in_executor(
        None, pokeprice.lookup_by_set_and_number, set_code, number_raw
    )

    if not card:
        await status_msg.edit_text(
            f"❌ Karte *{set_code.upper()} #{number_raw}* nicht gefunden.\n\n"
            "Tipps:\n"
            "• Set-Kürzel prüfen (z.B. `sv06`, `par`, `151`)\n"
            "• Nummer ohne führende Nullen versuchen\n"
            "• Nur die Zahl vor dem `/` angeben (z.B. `10` statt `10/198`)",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # Preis-Infos aus CM Price Guide (idProduct vorhanden?)
    cm_data = {}
    id_product = card.get("idProduct")
    if id_product:
        cm_data = cm_priceguide.get_price(int(id_product)) or {}

    low = cm_data.get("low") or card.get("low")
    trend = cm_data.get("trend") or card.get("trend")
    avg7 = cm_data.get("avg7") or card.get("avg7")

    # Preis-Zeile aufbauen
    if low and trend:
        price_line = f"Ab: *{low:.2f} €*  |  Markt: {trend:.2f} €"
        if avg7:
            price_line += f"  |  Ø7T: {avg7:.2f} €"
    elif low:
        price_line = f"Ab: *{low:.2f} €*"
    elif trend:
        price_line = f"Markt: *{trend:.2f} €*"
    else:
        price_line = "Kein Preis verfügbar"

    name = card.get("name") or "Unbekannte Karte"
    set_name = card.get("set_name") or set_code.upper()
    number = card.get("number") or number_raw
    rarity = card.get("rarity") or ""
    cm_url = card.get("url") or pokeprice.cardmarket_search_url(name)

    caption = (
        f"🃏 *{name}*\n"
        f"📦 {set_name}  |  #{number}"
        + (f"  |  {rarity}" if rarity else "")
        + f"\n\n{price_line}\n"
        f"[🔗 Cardmarket]({cm_url})"
    )

    # pending_card für Folge-Aktionen (Sammlung, Watchlist)
    pending = {
        "name": name,
        "set_name": set_name,
        "number": number,
        "rarity": rarity,
        "image": card.get("image"),
        "url": cm_url,
        "low": low,
        "trend": trend,
        "avg7": avg7,
        "idProduct": id_product,
        "currency": "EUR",
    }
    context.user_data["pending_card"] = pending

    keyboard = _build_confirmation_keyboard()

    image_url = card.get("image")
    if image_url:
        photo_url = image_url.rstrip("/") + "/high.jpg"
        try:
            await status_msg.delete()
            await update.message.reply_photo(
                photo=photo_url,
                caption=caption,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=keyboard,
            )
            return
        except Exception:
            pass  # Fallback auf Text wenn Bild nicht ladbar

    await status_msg.edit_text(
        caption,
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=False,
        reply_markup=keyboard,
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
    "buy":     ("💡 Kauf-Check", "pc:buy"),
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


def _build_confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Stimmt so", callback_data="pc:confirm"),
        InlineKeyboardButton("❌ Falsch erkannt", callback_data="pc:wrong"),
    ]])


def _build_price_keyboard(is_sealed: bool) -> InlineKeyboardMarkup:
    if is_sealed:
        presets = [("20€", "pc:pr_20"), ("35€", "pc:pr_35"),
                   ("50€", "pc:pr_50"), ("100€", "pc:pr_100")]
    else:
        presets = [("2€", "pc:pr_2"), ("5€", "pc:pr_5"),
                   ("10€", "pc:pr_10"), ("20€", "pc:pr_20")]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(l, callback_data=d) for l, d in presets[:2]],
        [InlineKeyboardButton(l, callback_data=d) for l, d in presets[2:]],
        [InlineKeyboardButton("💬 Anderen Preis eingeben", callback_data="pc:pr_other")],
    ])


def _build_profile_keyboard() -> InlineKeyboardMarkup:
    """Profil-Auswahl beim Sammlung-Hinzufügen (Kevin/Magnus …)."""
    btns = [InlineKeyboardButton(f"👤 {p}", callback_data=f"pc:prof:{p}")
            for p in config.PROFILES]
    rows = [btns[i:i + 2] for i in range(0, len(btns), 2)]
    return InlineKeyboardMarkup(rows)


def _build_condition_keyboard() -> InlineKeyboardMarkup:
    conds = [("NM", "pc:cd_NM"), ("LP", "pc:cd_LP"),
             ("MP", "pc:cd_MP"), ("HP", "pc:cd_HP")]
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(l, callback_data=d) for l, d in conds
    ]])


def _build_threshold_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("−10%", callback_data="pc:th_10"),
         InlineKeyboardButton("−15%", callback_data="pc:th_15")],
        [InlineKeyboardButton("−20%", callback_data="pc:th_20"),
         InlineKeyboardButton("−25%", callback_data="pc:th_25")],
        [InlineKeyboardButton("💬 Anderen Wert eingeben", callback_data="pc:th_other")],
    ])


# ---------------------------------------------------------------- Bilderkennung
def _cardmarket_url_for_card(name: str, set_name: str | None = None,
                             number: str | None = None, language: str | None = None,
                             url_from_card: str | None = None) -> str:
    """Baut den bestmöglichen Cardmarket-Link für eine erkannte Karte.

    JP-Karten bekommen einen gefilterten Suchlink (language=7 = Japanisch),
    damit keine falsche EN-Version angezeigt wird.
    """
    if url_from_card:
        return url_from_card

    lang = (language or "").upper()
    if lang == "JP":
        return pokeprice.cardmarket_search_url(name or "", language_id=7)
    if lang == "EN":
        return pokeprice.cardmarket_search_url(name or "", language_id=1)
    return pokeprice.cardmarket_search_url(name or "", language_id=3)  # DE default


def _best_lookup(names: list, set_name=None, number=None, rarity=None, language=None):
    """Probiert mehrere Namen (z.B. Original + englisch) und bevorzugt einen
    Treffer MIT Preisdaten. Wichtig für JP-Karten: Originalname ist japanisch
    (in TCGdex de/en nicht suchbar) → englischer Name greift."""
    best = None
    seen = set()
    for qn in names:
        if not qn or qn in seen:
            continue
        seen.add(qn)
        card = pokeprice.lookup(qn, set_name, number, rarity, language=language)
        if card:
            if card.get("trend") or card.get("avg") or card.get("low"):
                return card           # Treffer mit Preis → sofort nehmen
            best = best or card       # Treffer ohne Preis → merken
    return best


def _pokeprice_analysis(recog: dict) -> dict:
    """Marktpreis-Analyse: erst lokaler CM Price Guide, dann TCGdex als Fallback.

    Lookup-Kette:
      1. TCGdex → idProduct ermitteln (Name/Set/Nummer/Seltenheit)
      2. idProduct → lokaler Cardmarket Price Guide (tagesaktuell, kein API-Limit)
      3. Fallback: TCGdex-Preise direkt (EU-weit, wenn kein lokaler Eintrag)
    """
    info = {
        "product_id": None, "min_price": None, "market_price": None,
        "trend": {"emoji": trend_analyzer.TREND_EMOJI["unbekannt"],
                  "trend": "unbekannt", "recommendation": "egal"},
        "score": None, "best_offer": None, "source": "pokemontcg",
    }
    names = [recog.get("card_name"), recog.get("card_name_en")]
    lang = recog.get("language")
    card = _best_lookup(names, recog.get("set_name"),
                        recog.get("card_number"), recog.get("rarity"),
                        language=lang)
    fallback_name = recog.get("card_name_en") or recog.get("card_name") or ""
    search_url = _cardmarket_url_for_card(
        name=(card or {}).get("name") or fallback_name,
        set_name=recog.get("set_name"),
        number=recog.get("card_number"),
        language=lang,
        url_from_card=(card or {}).get("url"),
    )
    if not card:
        log.info("pokeprice: kein TCGdex-Treffer fuer %s (Set '%s', Nr '%s') — versuche pokemontcg.io",
                 names, recog.get("set_name"), recog.get("card_number"))
        # Direkter pokemontcg.io Versuch wenn TCGdex nichts findet
        import pokeprice as pokemontcg_io
        en_name = recog.get("card_name_en") or recog.get("card_name") or ""
        po_card = pokemontcg_io.lookup(
            en_name,
            set_name=recog.get("set_name"),
            number=recog.get("card_number"),
        )
        if po_card and (po_card.get("trend") or po_card.get("avg") or po_card.get("low")):
            log.info("pokemontcg.io Direkt-Treffer '%s': trend=%s",
                     po_card.get("name"), po_card.get("trend"))
            info["market_price"] = po_card.get("trend") or po_card.get("avg")
            info["min_price"] = po_card.get("low")
            info["avg7"] = po_card.get("avg7")
            info["avg30"] = po_card.get("avg30")
            info["source"] = "pokemontcg"
            info["trend"] = pokeprice.trend_from_prices(po_card)
            info["url"] = po_card.get("url") or search_url
            info["tcgdex_name"] = po_card.get("name")
            info["tcgdex_set"] = po_card.get("set_name")
            info["tcgdex_number"] = po_card.get("number")
            return info
        info["url"] = search_url
        info["language"] = lang
        return info

    product_id = card.get("idProduct")
    info["product_id"] = product_id
    info["url"] = card.get("url") or search_url
    # TCGdex-Match-Info für Anzeige in der Erkennungs-Nachricht
    info["tcgdex_name"] = card.get("name")
    info["tcgdex_set"] = card.get("set_name")
    info["tcgdex_number"] = card.get("number")

    # 1) Lokaler CM Price Guide (bevorzugt: tagesaktuell, 75k Produkte)
    cm = cm_priceguide.get_price(product_id) if product_id else None
    if cm and (cm.get("low") or cm.get("trend")):
        log.info("CM-Local '%s' (id=%s): low=%s trend=%s avg7=%s",
                 card.get("name"), product_id,
                 cm.get("low"), cm.get("trend"), cm.get("avg7"))
        info["market_price"] = cm.get("trend") or cm.get("avg")
        info["min_price"] = cm.get("low")
        info["avg7"] = cm.get("avg7")
        info["avg30"] = cm.get("avg30")
        info["source"] = "cardmarket_local"
        # Trend aus CM avg7 vs avg30 berechnen (gleiche Logik wie TCGdex)
        info["trend"] = pokeprice.trend_from_prices({
            "avg7": cm.get("avg7"), "avg30": cm.get("avg30"),
        })
        return info

    # 2) Fallback: TCGdex EU-Preise (wenn kein CM-Eintrag)
    log.info("TCGdex-Fallback '%s' (id=%s): trend=%s avg7=%s avg30=%s low=%s",
             card.get("name"), product_id,
             card.get("trend"), card.get("avg7"), card.get("avg30"), card.get("low"))
    tcgdex_market = card.get("trend") or card.get("avg")
    if tcgdex_market:
        info["market_price"] = tcgdex_market
        info["avg7"] = card.get("avg7")
        info["min_price"] = card.get("low")
        info["trend"] = pokeprice.trend_from_prices(card)
        return info

    # 3) pokemontcg.io Fallback: CM-EUR-Preise direkt (kein idProduct nötig)
    import pokeprice as pokemontcg_io
    po_card = pokemontcg_io.lookup(
        recog.get("card_name_en") or card.get("name") or "",
        set_name=recog.get("set_name"),
        number=recog.get("card_number"),
    )
    if po_card and (po_card.get("trend") or po_card.get("avg") or po_card.get("low")):
        log.info("pokemontcg.io Fallback '%s': trend=%s low=%s",
                 po_card.get("name"), po_card.get("trend"), po_card.get("low"))
        info["market_price"] = po_card.get("trend") or po_card.get("avg")
        info["min_price"] = po_card.get("low")
        info["avg7"] = po_card.get("avg7")
        info["avg30"] = po_card.get("avg30")
        info["source"] = "pokemontcg"
        info["trend"] = pokeprice.trend_from_prices(po_card)
        # pokemontcg.io-URLs sind direkte CM-Slugs → oft falsch, search_url ist zuverlässiger
        return info

    # 4) Letzter Fallback: TCGPlayer (USD)
    tcgp = card.get("tcgp_market_usd") or card.get("tcgp_low_usd")
    if tcgp:
        log.info("TCGPlayer-Fallback '%s': market_usd=%s", card.get("name"), tcgp)
        info["market_price"] = tcgp
        info["min_price"] = card.get("tcgp_low_usd")
        info["source"] = "tcgplayer_usd"
    return info


def _pokeprice_text(name: str, set_name: str | None = None,
                    number: str | None = None, rarity: str | None = None,
                    alt_name: str | None = None) -> str:
    """Preis-Übersicht über die Preis-Quelle (TCGdex)."""
    card = _best_lookup([name, alt_name], set_name, number, rarity)
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
    lines += ["", "💶 *Cardmarket-Wert (EUR, EU-weit):*"]
    lines += [
        f"• Trend (aktuell): {fmt(card.get('trend'))}",
        f"• Ø 7 Tage: {fmt(card.get('avg7'))}",
        f"• Ø 30 Tage: {fmt(card.get('avg30'))}",
        "",
        f"📈 Tendenz: {tr['emoji']} {tr['trend']} ({tr['change_pct']:+.1f}%) | "
        f"💡 {tr['recommendation']}",
    ]
    has_prices = any(card.get(k) for k in ("trend", "avg7", "avg30", "avg"))
    if not has_prices:
        lines.append("⚠️ Keine Preisdaten (oft bei JP/sehr neuen Karten) — "
                     "Preis bitte über den Link prüfen.")
    cm_url = card.get("url") or pokeprice.cardmarket_search_url(name)
    lines += [
        "",
        "ℹ️ EU-weite Werte (nicht nach DE-Verkäufern gefiltert).",
        f"🔗 Auf Cardmarket suchen (DE): {cm_url}",
        "",
        "_Quelle: TCGdex · Cardmarket-Preise in EUR_",
    ]
    return "\n".join(lines)


def _analyze_recognized_card(context: ContextTypes.DEFAULT_TYPE,
                             recog: dict) -> dict:
    """Marktpreis + Deal-Score für eine erkannte Karte (TCGdex + CM Price Guide)."""
    return _pokeprice_analysis(recog)


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
    keyboard = _build_confirmation_keyboard()
    await status.edit_text(msg, reply_markup=keyboard, disable_web_page_preview=True)


def _format_recognition(recog: dict, analysis: dict) -> str:
    conf = int(round(recog.get("confidence", 0) * 100))
    market = analysis.get("market_price")
    trend = analysis["trend"]

    def fmt(v):
        return f"{v:.2f}€" if isinstance(v, (int, float)) and v > 0 else "–"

    sealed_line = ""
    if image_recognition.is_sealed(recog.get("product_type")):
        sealed_line = f"📦 Versiegeltes Produkt: {recog.get('product_type')}\n"

    # Direkten CM-Slug-URLs (/Singles/...) → in sichere Suche umwandeln
    raw_url = analysis.get("url")
    fallback_name = analysis.get("tcgdex_name") or recog.get("card_name_en") or recog.get("card_name")
    card_number = (analysis.get("tcgdex_number") or recog.get("card_number") or "").split("/")[0].strip()
    url = pokeprice._safe_cm_url(raw_url, fallback_name=fallback_name, number=card_number or None)

    # Warnung wenn Konfidenz niedrig
    conf_warn = " ⚠️ unsicher" if conf < 70 else ""

    # TCGdex-Match anzeigen: was wurde auf Cardmarket WIRKLICH gefunden?
    tcgdex_name = analysis.get("tcgdex_name")
    tcgdex_set = analysis.get("tcgdex_set")
    tcgdex_number = analysis.get("tcgdex_number")
    if tcgdex_name:
        if tcgdex_number:
            cm_found = f"🔗 CM-Treffer: {tcgdex_name} | {tcgdex_number}"
        else:
            cm_found = f"🔗 CM-Treffer: {tcgdex_name}"
        if tcgdex_set:
            cm_found += f" | {tcgdex_set}"
        # Warnung nur wenn BEIDE Namen (DE + EN) nicht im TCGdex-Treffer sind
        # verhindert False-Positive bei DE/EN-Übersetzungen (Glurak≠Charizard ist OK)
        import re as _re
        def _base(n):
            return _re.sub(r'[\s-]+(ex|EX|GX|V|VMAX|VSTAR)\b.*$', '', (n or '')).strip().lower()
        g_de = _base(recog.get("card_name", ""))
        g_en = _base(recog.get("card_name_en", ""))
        tcg_low = tcgdex_name.lower()
        de_mismatch = g_de and g_de not in tcg_low
        en_mismatch = (not g_en) or g_en not in tcg_low
        if de_mismatch and en_mismatch:
            cm_found += " ⚠️ Prüfen!"
        cm_line = f"\n{cm_found}\n"
    else:
        is_jp_lang = recog.get("language", "").upper() == "JP"
        if is_jp_lang:
            cm_line = "\n🇯🇵 JP-exklusives Set — nicht in EU-Datenbank.\n"
        else:
            cm_line = "\n⚠️ Kein CM-Treffer — Preis und Link könnten ungenau sein.\n"

    head = (
        f"🔍 Erkannt! ({conf}%{conf_warn})\n\n"
        f"🃏 {recog.get('card_name', '?')} | {recog.get('card_number', '?')}\n"
        f"📦 {recog.get('set_name', '?')} | ⭐ {recog.get('rarity', '?')}\n"
        f"🌍 {recog.get('language', '?')} | Zustand ca.: "
        f"{recog.get('condition_estimate', '?')}\n"
        f"{sealed_line}{cm_line}"
    )

    is_sealed_product = image_recognition.is_sealed(recog.get("product_type"))
    source = analysis.get("source", "pokemontcg")
    is_jp = recog.get("language", "").upper() == "JP"

    # JP-Karten ohne Preis: klare Meldung + JP-gefilterter CM-Link
    if is_jp and not market and not analysis.get("min_price"):
        body = (
            "💡 *Japanische Karte* — kein Preis in der EU-Datenbank gefunden.\n\n"
            "JP-exklusive Sets (wie M4, S-Serien) sind auf Cardmarket unter "
            "'Japanische Singles' gelistet — aber nicht im tägl. Price Guide.\n\n"
            f"🔗 [Auf Cardmarket JP-Karten suchen]({url})"
        )
        return head + body + "\n\nWas möchtest du tun?"

    # Versiegelte Produkte (Tins, ETBs, Displays) haben keine Einzelkarten-Preise
    if is_sealed_product and not market and not analysis.get("min_price"):
        body = (
            "💡 Versiegeltes Produkt — Preise sind auf Cardmarket verfügbar.\n"
            "🔗 Auf Cardmarket suchen: "
            f"{pokeprice.cardmarket_search_url(recog.get('card_name', ''))}\n\n"
            "✅ Du kannst das Produkt trotzdem in deine Sammlung aufnehmen\n"
            "   und den Kaufpreis manuell eintragen."
        )
        return head + body + "\n\nWas möchtest du tun?"

    if source == "cardmarket_local":
        # Lokaler Cardmarket Price Guide: tagesaktuell, Link filtert auf DE-Verkäufer
        body = f"📊 Trend: {fmt(market)}\n"
        if analysis.get("avg7"):
            body += f"📈 Ø 7T: {fmt(analysis.get('avg7'))}\n"
        body += f"🌍 Niedrigst EU: {fmt(analysis.get('min_price'))}  (nicht nur 🇩🇪)\n"
        body += f"{trend['emoji']} {trend['trend'].capitalize()}\n"
        if url:
            body += f"🔗 Cardmarket 🇩🇪 Verkäufer: {url}"
    elif source == "tcgplayer_usd":
        # Letzter Fallback: TCGPlayer USA — Karte kaum auf Cardmarket DE verfügbar
        def fmt_usd(v):
            return f"{v:.2f} $" if isinstance(v, (int, float)) and v > 0 else "–"
        body = f"💵 TCGPlayer (USA): {fmt_usd(market)}\n"
        if analysis.get("min_price"):
            body += f"   Ab: {fmt_usd(analysis.get('min_price'))}\n"
        body += "ℹ️ Karte nicht/kaum auf Cardmarket DE — US-Preis als Richtwert.\n"
        if url:
            body += f"🔗 Auf Cardmarket suchen: {url}"
    elif source == "pokemontcg":
        # TCGdex Fallback: EU-weite Aggregate, kein direkter CM-Eintrag
        body = f"💶 Cardmarket (ca.): {fmt(market)}\n"
        if analysis.get("avg7"):
            body += f"📊 O 7 Tage: {fmt(analysis.get('avg7'))}\n"
        body += f"📈 Trend: {trend['emoji']} {trend['trend']}\n"
        if url:
            body += f"🔗 Auf Cardmarket (🇩🇪): {url}"
    else:
        # Cardmarket-API: echte DE-Angebote + Verkäuferbewertung
        score = analysis.get("score")
        body = (
            f"Guenstigstes DE-Angebot: {fmt(analysis.get('min_price'))}\n"
            f"Marktpreis: {fmt(market)}\n"
            f"Trend: {trend['emoji']} {trend['trend']}\n"
            f"Deal-Score: {score if score is not None else '-'}/100"
        )
        if url:
            body += f"\n🔗 {url}"

    return head + body + "\n\nWas möchtest du tun?"


async def _disable_buttons(query) -> None:
    """Entfernt die Inline-Buttons, lässt aber den Infotext stehen."""
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Verarbeitet alle Inline-Buttons nach Bilderkennung, Sammlung, Watchlist und Scalp."""

    query = update.callback_query
    await query.answer()
    data = query.data or ""

    # --- 💶 Preis-Buttons (nach Sammlung-Klick) ---
    if data.startswith("pc:pr_"):
        price_key = data[6:]
        card_id = context.user_data.get("awaiting_price")
        if not card_id:
            await _disable_buttons(query)
            await query.message.reply_text("⌛ Session abgelaufen — schick das Foto bitte neu.")
            return
        if price_key == "other":
            await _disable_buttons(query)
            await query.message.reply_text("💶 Kaufpreis eingeben (z.B. 12.50):")
            return
        try:
            price = float(price_key.replace(",", "."))
        except ValueError:
            return
        context.user_data["chosen_price"] = price
        row = db.get_portfolio_card(card_id)
        gemini_cond = row["condition"] if row else None
        cond_hint = f"\nGemini-Schätzung: {gemini_cond}" if gemini_cond else ""
        await _disable_buttons(query)
        await query.message.reply_text(
            f"✅ Preis: {price:.2f}€{cond_hint}\n\n📋 Zustand der Karte?",
            reply_markup=_build_condition_keyboard(),
        )
        return

    # --- 📋 Zustand-Buttons (nach Preis-Auswahl) ---
    if data.startswith("pc:cd_"):
        condition = data[6:]
        card_id = context.user_data.get("awaiting_price")
        chosen_price = context.user_data.get("chosen_price")
        if not card_id or chosen_price is None:
            await _disable_buttons(query)
            await query.message.reply_text("⌛ Session abgelaufen — schick das Foto bitte neu.")
            return
        db.update_portfolio_purchase_price(card_id, chosen_price)
        db.update_portfolio_condition(card_id, condition)
        db.add_expense(chosen_price, f"Kauf: {_portfolio_name(card_id)}")
        card_name = _portfolio_name(card_id)
        val_row = db.get_latest_portfolio_value(card_id)
        market_val = val_row["market_value"] if val_row else None
        market_txt = f" | Markt: {market_val:.2f}€" if market_val else ""
        profit_txt = ""
        if market_val and chosen_price > 0:
            diff_pct = (market_val - chosen_price) / chosen_price * 100
            s = "+" if diff_pct >= 0 else ""
            profit_txt = f" ({s}{diff_pct:.0f}%)"
        context.user_data.pop("awaiting_price", None)
        context.user_data.pop("chosen_price", None)
        context.user_data.pop("pending_is_sealed", None)
        await _disable_buttons(query)
        await query.message.reply_text(
            f"✅ {card_name} in der Sammlung!\n"
            f"💶 Gekauft: {chosen_price:.2f}€{market_txt}{profit_txt}\n"
            f"📋 Zustand: {condition}"
        )
        return

    # --- 🔔 Schwellen-Buttons (nach Watchlist-Klick) ---
    if data.startswith("pc:th_"):
        th_key = data[6:]
        card_id = context.user_data.get("awaiting_alert_threshold")
        if not card_id:
            await _disable_buttons(query)
            return
        if th_key == "other":
            await _disable_buttons(query)
            await query.message.reply_text("Prozentzahl eingeben (z.B. 18 für −18%):")
            return
        try:
            threshold = float(th_key)
        except ValueError:
            return
        db.set_card_alert_threshold(card_id, threshold)
        context.user_data.pop("awaiting_alert_threshold", None)
        await _disable_buttons(query)
        await query.message.reply_text(
            f"🔔 Alarmiere bei ≥ {threshold:.0f}% Ersparnis. Automatische Scans sind aktiv."
        )
        return

    # --- Ab hier: pending_card wird benötigt ---
    pending = context.user_data.get("pending_card")
    if not pending:
        await query.message.reply_text(
            "⌛ Diese Karte ist nicht mehr aktiv — schick das Foto bitte neu."
        )
        await _disable_buttons(query)
        return

    recog = pending["recog"]
    analysis = pending["analysis"]
    temp_path = pending.get("temp_path")
    name = recog.get("card_name", "Unbekanntes Produkt")
    product_id = analysis.get("product_id")
    is_sealed = image_recognition.is_sealed(recog.get("product_type"))

    # --- ✅ Bestätigung: Erkennung korrekt → Aktions-Buttons zeigen ---
    if data == "pc:confirm":
        keyboard = _build_action_keyboard(["collect", "watch", "buy", "scalp"], is_sealed)
        await query.edit_message_reply_markup(reply_markup=keyboard)
        return

    # --- ❌ Falsch erkannt: Korrektur-Eingabe anfordern ---
    if data == "pc:wrong":
        context.user_data["awaiting_correction"] = True
        await _disable_buttons(query)
        await query.message.reply_text(
            "❌ Wie heißt die Karte oder das Produkt?\n"
            "Gib den Namen ein (z.B. Pikachu ex oder Scarlet & Violet ETB):"
        )
        return

    # --- 💡 Kauf-Check: User gibt Preis ein → Bot empfiehlt kaufen/skip ---
    if data == "pc:buy":
        context.user_data["awaiting_buy_check"] = True
        await _disable_buttons(query)
        trend = analysis.get("trend") or 0
        trend_txt = f" (Marktpreis: {trend:.0f}€)" if trend > 0 else ""
        await query.message.reply_text(
            f"💡 *Kauf-Check*{trend_txt}\n\n"
            "Für wieviel € würdest du die Karte kaufen?\n"
            "Einfach den Preis eingeben, z.B. `12.50`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # --- 💰 Preis-Check: Details als NEUE Nachricht (für /preis und /add) ---
    if data == "pc:price":
        import asyncio
        loop = asyncio.get_running_loop()
        text = await loop.run_in_executor(
            None, _pokeprice_text, recog.get("card_name") or name,
            recog.get("set_name"), recog.get("card_number"),
            recog.get("rarity"), recog.get("card_name_en"),
        )
        await query.message.reply_text(
            text, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True,
        )
        return

    # --- 🔔 Watchlist: hinzufügen + Schwelle per Button ---
    if data == "pc:watch":
        card = db.get_card_by_name(name)
        if card:
            await _disable_buttons(query)
            await query.message.reply_text(f"ℹ️ '{name}' ist bereits auf der Watchlist.")
            _safe_remove(temp_path)
            context.user_data.pop("pending_card", None)
            return
        card_id = db.add_card(name, product_id)
        context.user_data["awaiting_alert_threshold"] = card_id
        context.user_data.pop("pending_card", None)
        _safe_remove(temp_path)
        await _disable_buttons(query)
        await query.message.reply_text(
            f"🔔 {name} zur Watchlist hinzugefügt.\n\n"
            "Ab welchem Preisrückgang soll ich dich alarmieren?",
            reply_markup=_build_threshold_keyboard(),
        )
        return

    # --- 💼 Scalp-Track: nur für versiegelte Produkte ---
    if data == "pc:scalp":
        if not is_sealed:
            await query.message.reply_text(
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
        await _disable_buttons(query)
        await query.message.reply_text(
            f"💼 '{name}' für Scalp-Tracking vorgemerkt.\n"
            "🎯 Ziel-Einkaufspreis? (€)"
        )
        return

    # --- ➕ Sammlung: erst Profil fragen (Kevin/Magnus), dann Karte anlegen ---
    if data == "pc:collect":
        if len(config.PROFILES) <= 1:
            await _finish_collect(query, context, config.DEFAULT_PROFILE)
        else:
            await query.message.reply_text(
                f"👤 In wessen Sammlung soll *{name}* rein?",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=_build_profile_keyboard(),
            )
        return

    # --- 👤 Profil gewählt → Karte in dessen Sammlung anlegen ---
    if data.startswith("pc:prof:"):
        owner = data.split(":", 2)[2]
        if owner not in config.PROFILES:
            await query.message.reply_text("⚠️ Unbekanntes Profil.")
            return
        await _finish_collect(query, context, owner)
        return


async def _finish_collect(query, context: ContextTypes.DEFAULT_TYPE, owner: str) -> None:
    """Legt die vorgemerkte Karte in der Sammlung des Profils `owner` an
    und fragt anschließend nach dem Kaufpreis."""
    pending = context.user_data.get("pending_card")
    if not pending:
        await query.message.reply_text(
            "⌛ Diese Karte ist nicht mehr aktiv — schick das Foto bitte neu."
        )
        return
    recog = pending["recog"]
    analysis = pending["analysis"]
    temp_path = pending.get("temp_path")
    name = recog.get("card_name", "Unbekanntes Produkt")
    product_id = analysis.get("product_id")
    is_sealed = image_recognition.is_sealed(recog.get("product_type"))

    existing_count = db.count_portfolio_by_name(name, owner=owner)
    card_id = db.add_portfolio_card(
        card_name=name,
        purchase_price=0.0,
        product_id=product_id,
        condition=recog.get("condition_estimate"),
        language=recog.get("language"),
        set_name=recog.get("set_name"),
        card_number=recog.get("card_number"),
        rarity=recog.get("rarity"),
        owner=owner,
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
    context.user_data["pending_is_sealed"] = is_sealed
    context.user_data.pop("pending_card", None)
    await _disable_buttons(query)
    market = analysis.get("market_price")
    dup_line = f"\n⚠️ {owner} hat diese Karte bereits {existing_count}x!" if existing_count > 0 else ""
    market_line = f"\n💰 Marktwert: {market:.2f}€" if market else ""
    await query.message.reply_text(
        f"✅ {name} → Sammlung *{owner}*.{dup_line}{market_line}\n\n💶 Was hast du bezahlt?",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_build_price_keyboard(is_sealed),
    )


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

    import asyncio
    loop = asyncio.get_running_loop()

    # 0) Korrektur nach "Falsch erkannt"
    if context.user_data.get("awaiting_correction"):
        context.user_data.pop("awaiting_correction", None)
        pending = context.user_data.get("pending_card")
        if not pending:
            await update.message.reply_text("⌛ Session abgelaufen — schick das Foto bitte neu.")
            return
        pending["recog"]["card_name"] = text
        pending["recog"]["card_name_en"] = text
        status_msg = await update.message.reply_text(f"🔍 Suche nach \"{text}\" …")
        analysis = await loop.run_in_executor(
            None, _analyze_recognized_card, context, pending["recog"]
        )
        pending["analysis"] = analysis
        context.user_data["pending_card"] = pending
        msg = _format_recognition(pending["recog"], analysis)
        await status_msg.edit_text(
            msg, reply_markup=_build_confirmation_keyboard(), disable_web_page_preview=True
        )
        return

    # 1) Kauf-Check: Preis eingeben → Empfehlung
    if context.user_data.get("awaiting_buy_check"):
        context.user_data.pop("awaiting_buy_check", None)
        try:
            pay = float(text.replace(",", ".").replace("€", "").strip())
            if pay <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Bitte einen gültigen Preis eingeben, z.B. `12.50`.")
            context.user_data["awaiting_buy_check"] = True
            return
        pending = context.user_data.get("pending_card") or {}
        analysis_data = pending.get("analysis") or {}
        trend = analysis_data.get("trend") or 0
        low = analysis_data.get("market_price") or analysis_data.get("low") or 0
        if trend > 0:
            savings_pct = (trend - pay) / trend * 100
            if pay > trend * 1.05:
                verdict = "❌ *SKIP* — Zu teuer!"
                detail = f"Du zahlst {abs(savings_pct):.0f}% *über* Marktpreis."
            elif pay > trend:
                verdict = "⚠️ *EHER SKIP* — leicht über Markt."
                detail = f"Nur {abs(savings_pct):.0f}% über Marktpreis, aber kein Schnäppchen."
            elif savings_pct < 5:
                verdict = "🤔 *GRENZWERTIG* — ungefähr Marktpreis."
                detail = "Kein Schnäppchen, aber auch nicht zu teuer."
            elif savings_pct < 15:
                verdict = "✅ *OKAY* — leicht unter Markt."
                detail = f"Du sparst {savings_pct:.0f}% gegenüber dem Marktpreis."
            elif savings_pct < 30:
                verdict = "✅ *KAUFEN* — guter Deal!"
                detail = f"Du sparst {savings_pct:.0f}% — deutlich unter Marktpreis."
            else:
                verdict = "🔥 *KAUFEN* — sehr guter Deal!"
                detail = f"Du sparst {savings_pct:.0f}% — deutlich unter Marktpreis!"
            low_txt = f"\nGünstigstes EU-Angebot: {low:.2f}€" if low > 0 else ""
            await update.message.reply_text(
                f"💡 *Kauf-Check*\n\n"
                f"Dein Preis: *{pay:.2f}€*\n"
                f"Marktpreis (Trend): *{trend:.2f}€*{low_txt}\n\n"
                f"{verdict}\n{detail}",
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            await update.message.reply_text(
                f"💡 Dein Preis: {pay:.2f}€\n"
                "Kein Marktpreis verfügbar — Vergleich nicht möglich."
            )
        return

    # 2) Kaufpreis nach "Sammlung" — Texteingabe (wenn "Anderen Preis eingeben" gewählt)
    pending_price_id = context.user_data.get("awaiting_price")
    if pending_price_id and context.user_data.get("chosen_price") is None:
        try:
            price = float(text.replace(",", ".").replace("€", "").strip())
            if price < 0:
                raise ValueError("negativ")
        except ValueError:
            await update.message.reply_text(
                "Bitte den Kaufpreis als Zahl angeben (z. B. 49.90)."
            )
            return
        context.user_data["chosen_price"] = price
        row = db.get_portfolio_card(pending_price_id)
        gemini_cond = row["condition"] if row else None
        cond_hint = f"\nGemini-Schätzung: {gemini_cond}" if gemini_cond else ""
        await update.message.reply_text(
            f"✅ Preis: {price:.2f}€{cond_hint}\n\n📋 Zustand der Karte?",
            reply_markup=_build_condition_keyboard(),
        )
        return

    # 2) Alert-Schwelle nach "Watchlist" — Texteingabe (wenn "Anderen Wert" gewählt)
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
                    f"Bitte eine Prozentzahl angeben oder 'standard' "
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
    application.add_handler(CommandHandler("restart", cmd_restart))
    application.add_handler(CommandHandler("threshold", cmd_threshold))
    application.add_handler(CommandHandler("score", cmd_score))
    application.add_handler(CommandHandler("sammlung", cmd_sammlung))
    application.add_handler(CommandHandler("wert", cmd_wert))
    application.add_handler(CommandHandler("gekauft", cmd_gekauft))
    application.add_handler(CommandHandler("budget", cmd_budget))
    application.add_handler(CommandHandler("ausgabe", cmd_ausgabe))
    application.add_handler(CommandHandler("briefing", cmd_briefing))
    # Scalping-Commands
    application.add_handler(CommandHandler("scalp", cmd_scalp))
    application.add_handler(CommandHandler("scalp_add", cmd_scalp_add))
    application.add_handler(CommandHandler("scalp_remove", cmd_scalp_remove))
    application.add_handler(CommandHandler("scalp_pause", cmd_scalp_pause))
    application.add_handler(CommandHandler("restocks", cmd_restocks))
    application.add_handler(CommandHandler("profit", cmd_profit))
    application.add_handler(CommandHandler("releases", cmd_releases))
    application.add_handler(CommandHandler("release_add", cmd_release_add))
    application.add_handler(CommandHandler("deals", cmd_deals))
    application.add_handler(CommandHandler("deals_refresh", cmd_deals_refresh))
    application.add_handler(CommandHandler("deals_debug", cmd_deals_debug))
    application.add_handler(CommandHandler("karte", cmd_karte))
    application.add_handler(CommandHandler("retailers", cmd_retailers))
    application.add_handler(CallbackQueryHandler(on_callback, pattern=r"^pc:"))
    application.add_handler(MessageHandler(filters.PHOTO, on_photo))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, on_text)
    )
    application.add_error_handler(on_error)
    log.info("Telegram-Handler registriert.")
