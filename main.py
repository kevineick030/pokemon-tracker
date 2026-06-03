"""Einstiegspunkt: Bot starten, Scheduler einrichten, Polling laufen lassen.

Scheduler-Jobs (APScheduler, AsyncIOScheduler):
  - Watchlist-Scan alle 30 Min
  - tägliches Briefing um 09:00
  - Portfolio-Wertaktualisierung um 02:00
"""
import asyncio
import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from telegram.ext import Application
from telegram.constants import ParseMode

import config
import database as db
import bot
import portfolio
import briefing
import sealed_prices
import release_calendar
import restock_alerts
from retail_monitor import RetailMonitor
from hotstock_monitor import HotStockMonitor
from cardmarket import CardmarketClient

config.setup_logging()
log = logging.getLogger("main")


# ---------------------------------------------------------------- Jobs
async def job_scan(application: Application) -> None:
    log.info("Scheduler: Watchlist-Scan startet.")
    try:
        await bot.run_scan_and_alert(application)
    except Exception:
        log.exception("Scan-Job fehlgeschlagen")


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


async def job_portfolio_valuation(application: Application) -> None:
    log.info("Scheduler: Portfolio-Wertaktualisierung.")
    mkm = application.bot_data["mkm"]
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, portfolio.update_all_values, mkm)
    except Exception:
        log.exception("Portfolio-Bewertungs-Job fehlgeschlagen")


# ---------------------------------------------------------------- Scalp-Jobs
async def _send_restock_events(application: Application, events: list[dict]) -> None:
    """Versendet Restock-Events (mit Dedupe) an den Haupt-Chat."""
    chat_id = config.TELEGRAM_CHAT_ID
    if not chat_id:
        return
    for ev in events:
        if not restock_alerts.should_send(ev["scalp_target_id"], ev["retailer_id"]):
            continue
        try:
            await application.bot.send_message(
                chat_id=chat_id, text=restock_alerts.format_alert(ev),
                disable_web_page_preview=False,
            )
            restock_alerts.record(ev["scalp_target_id"], ev["retailer_id"],
                                  ev.get("price"))
        except Exception:
            log.exception("Restock-Alert-Versand fehlgeschlagen")


async def job_retail_monitor(application: Application) -> None:
    monitor: RetailMonitor = application.bot_data["retail_monitor"]
    try:
        events = await monitor.check_all_active_targets()
        await _send_restock_events(application, events)
        # Captcha-Warnungen an Admin
        if monitor.captcha_warnings and config.TELEGRAM_CHAT_ID:
            haendler = ", ".join(sorted(set(monitor.captcha_warnings)))
            await application.bot.send_message(
                chat_id=config.TELEGRAM_CHAT_ID,
                text=f"⚠️ Captcha/Block erkannt bei: {haendler}. "
                     "Händler pausiert (Cooldown).",
            )
    except Exception:
        log.exception("Retail-Monitor-Job fehlgeschlagen")


async def job_hotstock(application: Application) -> None:
    monitor: HotStockMonitor = application.bot_data["hotstock_monitor"]
    try:
        events = await monitor.fetch_feed()
        await _send_restock_events(application, events)
    except Exception:
        log.exception("HotStock-Job fehlgeschlagen")


async def job_sealed_prices(application: Application) -> None:
    log.info("Scheduler: Cardmarket-Sealed-Preise aktualisieren.")
    mkm = application.bot_data["mkm"]
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, sealed_prices.update_all, mkm)
    except Exception:
        log.exception("Sealed-Preis-Job fehlgeschlagen")


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
        if data["boost"]:
            _apply_release_boost(application)
    except Exception:
        log.exception("Release-Check-Job fehlgeschlagen")


def _apply_release_boost(application: Application) -> None:
    """Beschleunigt den Retail-Scan für 24h auf 60s (Release-Tag-Boost)."""
    scheduler = application.bot_data.get("scheduler")
    if not scheduler:
        return
    try:
        scheduler.reschedule_job("retail_monitor", trigger=IntervalTrigger(seconds=60))
        scheduler.add_job(
            _revert_release_boost,
            trigger=DateTrigger(run_date=datetime.now() + timedelta(hours=24)),
            id="retail_boost_revert", replace_existing=True, args=[application],
        )
        log.info("Release-Boost aktiv: Retail-Scan alle 60s für 24h.")
    except Exception:
        log.exception("Release-Boost konnte nicht aktiviert werden")


def _revert_release_boost(application: Application) -> None:
    scheduler = application.bot_data.get("scheduler")
    if not scheduler:
        return
    try:
        scheduler.reschedule_job(
            "retail_monitor",
            trigger=IntervalTrigger(seconds=config.RETAIL_CHECK_INTERVAL_SECONDS),
        )
        log.info("Release-Boost beendet: Retail-Scan zurück auf Normalintervall.")
    except Exception:
        log.exception("Release-Boost-Revert fehlgeschlagen")


def setup_scheduler(application: Application) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="Europe/Berlin")

    scheduler.add_job(
        job_scan, IntervalTrigger(minutes=config.SCAN_INTERVAL_MINUTES),
        args=[application], id="scan", name="Watchlist-Scan",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        job_briefing, CronTrigger(hour=config.BRIEFING_HOUR, minute=0),
        args=[application], id="briefing", name="Tägliches Briefing",
    )
    scheduler.add_job(
        job_portfolio_valuation,
        CronTrigger(hour=config.PORTFOLIO_VALUATION_HOUR, minute=0),
        args=[application], id="portfolio_valuation",
        name="Portfolio-Wertaktualisierung",
    )

    # --- Scalping-Jobs ---
    scheduler.add_job(
        job_retail_monitor,
        IntervalTrigger(seconds=config.RETAIL_CHECK_INTERVAL_SECONDS),
        args=[application], id="retail_monitor", name="Retail-Monitor",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        job_hotstock,
        IntervalTrigger(seconds=config.HOTSTOCK_CHECK_INTERVAL_SECONDS),
        args=[application], id="hotstock", name="HotStock-Monitor",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        job_sealed_prices,
        IntervalTrigger(hours=config.SEALED_PRICES_UPDATE_HOURS),
        args=[application], id="sealed_prices", name="Sealed-Preise",
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
    """Wird nach dem App-Start aufgerufen: Scheduler starten, Ping."""
    mkm: CardmarketClient = application.bot_data["mkm"]
    online = await asyncio.get_running_loop().run_in_executor(None, mkm.ping)
    log.info("Cardmarket-API erreichbar: %s", online)

    # Scalp-Monitore initialisieren
    application.bot_data["retail_monitor"] = RetailMonitor()
    application.bot_data["hotstock_monitor"] = HotStockMonitor()

    scheduler = setup_scheduler(application)
    scheduler.start()
    application.bot_data["scheduler"] = scheduler
    log.info("Scheduler gestartet (Scan alle %d Min, Briefing %02d:00, "
             "Bewertung %02d:00).",
             config.SCAN_INTERVAL_MINUTES, config.BRIEFING_HOUR,
             config.PORTFOLIO_VALUATION_HOUR)


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

    # Optionale, nicht gesetzte Funktionen als Warnung anzeigen (kein Abbruch)
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
    application.bot_data["mkm"] = CardmarketClient()

    bot.register_handlers(application)

    log.info("Bot startet (Polling) …")
    application.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
