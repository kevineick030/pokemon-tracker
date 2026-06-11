"""Einstiegspunkt: Bot starten, Scheduler einrichten, Polling laufen lassen."""
import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from telegram.ext import Application
from telegram.constants import ParseMode

import config
import database as db
import bot
import portfolio
import briefing
import cm_priceguide
import deal_scanner
import release_calendar

config.setup_logging()
log = logging.getLogger("main")


# ---------------------------------------------------------------- Jobs
async def job_briefing(application: Application) -> None:
    log.info("Scheduler: tägliches Briefing.")
    if not config.TELEGRAM_CHAT_ID:
        return
    try:
        text = briefing.build_briefing()
        await application.bot.send_message(
            chat_id=config.TELEGRAM_CHAT_ID, text=text, parse_mode=ParseMode.MARKDOWN
        )
    except Exception:
        log.exception("Briefing-Job fehlgeschlagen")


async def job_priceguide(application: Application) -> None:
    log.info("Scheduler: Cardmarket Price Guide Download.")
    loop = asyncio.get_running_loop()
    try:
        count = await loop.run_in_executor(None, cm_priceguide.download_and_import)
        log.info("Price Guide: %d Produkte importiert.", count)
    except Exception:
        log.exception("Price-Guide-Job fehlgeschlagen")


async def job_deal_scan(application: Application) -> None:
    """Laeuft um 06:05 (nach Price-Guide-Download um 06:00).

    1. SIR/IR-Cache aktualisieren (neue Karten aus TCGdex laden)
    2. Deals berechnen → an Telegram senden
    3. Watchlist-Karten prüfen → Alerts bei Preisrückgang senden
    """
    log.info("Scheduler: SIR/IR Deal-Scanner startet.")
    loop = asyncio.get_running_loop()
    chat_id = config.TELEGRAM_CHAT_ID
    if not chat_id:
        return

    try:
        # Schritt 1: SIR/IR-Cache aktualisieren
        added = await loop.run_in_executor(None, deal_scanner.refresh_sir_ir_cache)
        log.info("Deal-Scanner: %d neue SIR/IR-Karten gecacht.", added)

        # Schritt 2: Deals berechnen und senden
        deals = await loop.run_in_executor(None, deal_scanner.get_deals)
        if deals:
            msg = deal_scanner.format_deals_message(deals)
            await application.bot.send_message(
                chat_id=chat_id, text=msg,
                parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True,
            )
            log.info("Deal-Scanner: %d Deals gesendet.", len(deals))

        # Schritt 3: Watchlist-Alerts prüfen
        alert_texts = await loop.run_in_executor(None, deal_scanner.check_watchlist_alerts)
        for alert_text in alert_texts:
            await application.bot.send_message(
                chat_id=chat_id, text=alert_text, parse_mode=ParseMode.MARKDOWN,
            )
        if alert_texts:
            log.info("Deal-Scanner: %d Watchlist-Alerts gesendet.", len(alert_texts))

    except Exception:
        log.exception("Deal-Scanner-Job fehlgeschlagen")


async def job_portfolio_valuation(application: Application) -> None:
    log.info("Scheduler: Portfolio-Wertaktualisierung.")
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, portfolio.update_all_values)
    except Exception:
        log.exception("Portfolio-Bewertungs-Job fehlgeschlagen")


async def job_release_check(application: Application) -> None:
    log.info("Scheduler: Release-Kalender prüfen.")
    try:
        data = release_calendar.check_upcoming()
        if config.TELEGRAM_CHAT_ID:
            for msg in data["reminders"]:
                await application.bot.send_message(
                    chat_id=config.TELEGRAM_CHAT_ID, text=msg,
                    parse_mode=ParseMode.MARKDOWN,
                )
    except Exception:
        log.exception("Release-Check-Job fehlgeschlagen")


def setup_scheduler(application: Application) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="Europe/Berlin")

    scheduler.add_job(
        job_briefing, CronTrigger(hour=config.BRIEFING_HOUR, minute=0),
        args=[application], id="briefing", name="Taegliches Briefing",
    )
    scheduler.add_job(
        job_priceguide,
        CronTrigger(hour=config.CM_PRICE_GUIDE_DOWNLOAD_HOUR, minute=0),
        args=[application], id="priceguide", name="CM Price Guide Download",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        job_portfolio_valuation,
        CronTrigger(hour=config.PORTFOLIO_VALUATION_HOUR, minute=0),
        args=[application], id="portfolio_valuation",
        name="Portfolio-Wertaktualisierung",
    )
    scheduler.add_job(
        job_deal_scan,
        CronTrigger(hour=config.CM_PRICE_GUIDE_DOWNLOAD_HOUR, minute=5),
        args=[application], id="deal_scan", name="SIR/IR Deal-Scanner + Watchlist",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        job_release_check,
        CronTrigger(hour=config.RELEASE_CHECK_HOUR, minute=5),
        args=[application], id="release_check", name="Release-Kalender",
    )
    return scheduler


# ---------------------------------------------------------------- Lifecycle
async def _post_init(application: Application) -> None:
    scheduler = setup_scheduler(application)
    scheduler.start()
    application.bot_data["scheduler"] = scheduler
    log.info(
        "Scheduler gestartet (Briefing %02d:00, Price Guide %02d:00, "
        "Deal-Scanner %02d:05, Portfolio %02d:00).",
        config.BRIEFING_HOUR,
        config.CM_PRICE_GUIDE_DOWNLOAD_HOUR,
        config.CM_PRICE_GUIDE_DOWNLOAD_HOUR,
        config.PORTFOLIO_VALUATION_HOUR,
    )


async def _post_shutdown(application: Application) -> None:
    scheduler = application.bot_data.get("scheduler")
    if scheduler:
        scheduler.shutdown(wait=False)
        log.info("Scheduler gestoppt.")


def main() -> None:
    missing = config.validate()
    if missing:
        log.error("Pflicht-Konfiguration fehlt in .env: %s", ", ".join(missing))
        log.error("Mindestens TELEGRAM_BOT_TOKEN muss gesetzt sein. Abbruch.")
        return

    for warn in config.optional_status():
        log.warning("[optional] %s", warn)

    db.init_db()

    application = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )

    bot.register_handlers(application)

    log.info("Bot startet (Polling) …")
    application.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
