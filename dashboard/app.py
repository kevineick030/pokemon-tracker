"""Passwortgeschütztes Web-Dashboard für den Pokémon Tracker.

Flask + Flask-Login, read-only auf dieselbe SQLite-DB. Wiederverwendet die
bestehenden Bot-Module (database, portfolio, trend_analyzer, cardmarket),
statt SQL zu duplizieren.

Start:  python dashboard/app.py      (Dev)
        gunicorn -w 2 -b 127.0.0.1:5000 dashboard.app:app   (Prod, hinter nginx)
"""
import os
import sys
import hmac
import hashlib
import logging
from datetime import datetime, timedelta

from dotenv import load_dotenv

# --- Projekt-Root auf den Pfad legen und .env laden (vor config-Import) ---
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
load_dotenv(os.path.join(ROOT_DIR, ".env"), override=True)

import config  # noqa: E402  (nutzt dieselbe DB_PATH / CARD_IMAGES_DIR)
import database as db  # noqa: E402
import portfolio  # noqa: E402
import trend_analyzer  # noqa: E402
import scalp_targets  # noqa: E402
import profit_calculator  # noqa: E402
import release_calendar  # noqa: E402
from database import get_conn  # noqa: E402

from flask import (  # noqa: E402
    Flask, render_template, request, redirect, url_for, flash,
    send_from_directory, abort, session,
)
from flask_login import (  # noqa: E402
    LoginManager, UserMixin, login_user, logout_user, login_required,
    current_user,
)

log = logging.getLogger("dashboard")

DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")
DASHBOARD_SECRET_KEY = os.getenv("DASHBOARD_SECRET_KEY", "") or os.urandom(32).hex()

app = Flask(__name__)
app.config.update(
    SECRET_KEY=DASHBOARD_SECRET_KEY,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=24),   # Session-Timeout 24h
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    # SESSION_COOKIE_SECURE wird hinter HTTPS gesetzt (siehe ENV)
    SESSION_COOKIE_SECURE=os.getenv("DASHBOARD_HTTPS", "true").lower() == "true",
)

class PrefixMiddleware:
    """Respektiert X-Forwarded-Prefix (z. B. /dashboard), damit url_for hinter
    nginx korrekte Pfade erzeugt."""

    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        prefix = environ.get("HTTP_X_FORWARDED_PREFIX", "").rstrip("/")
        if prefix:
            environ["SCRIPT_NAME"] = prefix
            path = environ.get("PATH_INFO", "")
            if path.startswith(prefix):
                environ["PATH_INFO"] = path[len(prefix):]
        return self.wsgi_app(environ, start_response)


app.wsgi_app = PrefixMiddleware(app.wsgi_app)

login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Bitte einloggen."


# ---------------------------------------------------------------- Auth
class User(UserMixin):
    id = "admin"


_USER = User()


@login_manager.user_loader
def load_user(user_id):
    return _USER if user_id == _USER.id else None


def _password_ok(candidate: str) -> bool:
    if not DASHBOARD_PASSWORD:
        return False
    return hmac.compare_digest(candidate.encode(), DASHBOARD_PASSWORD.encode())


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        password = request.form.get("password", "")
        if _password_ok(password):
            login_user(_USER, remember=True, duration=timedelta(hours=24))
            return redirect(request.args.get("next") or url_for("index"))
        flash("Falsches Passwort.", "error")
    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


# ---------------------------------------------------------------- Security-Header
@app.after_request
def security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    if app.config["SESSION_COOKIE_SECURE"]:
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return resp


# ---------------------------------------------------------------- Profile
def active_profile() -> str:
    """Aktuell im Dashboard gewähltes Sammlungs-Profil (Session)."""
    p = session.get("profile")
    return p if p in config.PROFILES else config.DEFAULT_PROFILE


@app.context_processor
def inject_profiles():
    """Stellt Profil-Liste + aktives Profil allen Templates bereit (Nav-Umschalter)."""
    return {"profiles": config.PROFILES, "active_profile": active_profile()}


@app.route("/profil/<name>")
@login_required
def set_profile(name):
    if name in config.PROFILES:
        session["profile"] = name
    return redirect(request.referrer or url_for("index"))


@app.route("/api/portfolio-chart")
@login_required
def api_portfolio_chart():
    """Wertentwicklung als JSON für den gewählten Zeitraum + das aktive Profil."""
    days = request.args.get("days", default=30, type=int)
    days = max(1, min(days, 3650))
    return daily_portfolio_values(days, active_profile())


@app.route("/api/card-chart/<int:card_id>")
@login_required
def api_card_chart(card_id):
    """Wertentwicklung einer einzelnen Sammlungskarte als JSON."""
    days = request.args.get("days", default=30, type=int)
    days = max(1, min(days, 3650))
    return card_value_history(card_id, days)


# ---------------------------------------------------------------- Datenhelfer
def _rarity_class(rarity: str | None) -> str:
    r = (rarity or "").lower()
    if "special illustration" in r:
        return "sir"
    if "illustration" in r:
        return "ir"
    if "ultra" in r:
        return "ultra"
    return "other"


def daily_portfolio_values(days: int = 30, owner: str | None = None) -> dict:
    """Tagessummen des Sammlungs-Marktwerts der letzten `days` Tage (pro Profil)."""
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    sql = (
        "SELECT substr(h.timestamp, 1, 10) AS day, SUM(h.market_value) AS total "
        "FROM portfolio_value_history h "
        "JOIN portfolio p ON p.id = h.portfolio_card_id "
        "WHERE h.timestamp >= ?"
    )
    params: list = [cutoff]
    if owner:
        sql += " AND p.owner = ?"
        params.append(owner)
    sql += " GROUP BY day ORDER BY day"
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return {
        "labels": [r["day"] for r in rows],
        "values": [round(r["total"] or 0, 2) for r in rows],
    }


def top_winners(days: int = 7, n: int = 3, owner: str | None = None) -> list[dict]:
    """Top-N Karten nach Wertzuwachs.

    Bevorzugt die 7-Tage-Veränderung; gibt es noch keine 7-Tage-Historie
    (Sammlung/Bewertung erst seit Kurzem), wird auf den Gewinn seit Kauf
    (Marktwert − Kaufpreis) zurückgegriffen, damit die Übersicht sofort
    sinnvolle Werte zeigt.
    """
    winners = []
    for card in db.get_portfolio(owner):
        latest = db.get_latest_portfolio_value(card["id"])
        if not latest or latest["market_value"] is None:
            continue
        now = latest["market_value"]
        past = db.get_portfolio_value_at(card["id"], days)
        if past and past["market_value"] is not None:
            base = past["market_value"]
            gain = now - base
            basis = "7T"
        else:
            base = card["purchase_price"] or 0.0
            gain = now - base
            basis = "seit Kauf"
        gain = round(gain, 2)
        # Nur echte Gewinner zeigen — Karten ohne Bewegung (0,00 €) oder im
        # Minus gehören nicht in die „Top-Gewinner"-Liste.
        if gain <= 0:
            continue
        pct = (gain / base * 100) if base > 0 else 0.0
        winners.append({
            "id": card["id"],
            "name": card["card_name"],
            "gain": gain,
            "pct": round(pct, 1),
            "now": round(now, 2),
            "basis": basis,
        })
    winners.sort(key=lambda w: w["gain"], reverse=True)
    return winners[:n]


def recent_alerts(limit: int = 5) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT a.*, c.name AS card_name FROM alerts_sent a "
            "JOIN cards c ON c.id = a.card_id "
            "ORDER BY a.sent_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def card_value_history(portfolio_card_id: int, days: int = 30) -> dict:
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT substr(timestamp, 1, 10) AS day, market_value "
            "FROM portfolio_value_history "
            "WHERE portfolio_card_id = ? AND timestamp >= ? ORDER BY timestamp",
            (portfolio_card_id, cutoff),
        ).fetchall()
    return {
        "labels": [r["day"] for r in rows],
        "values": [round(r["market_value"] or 0, 2) for r in rows],
    }


def watchlist_rows() -> list[dict]:
    """Watchlist mit Marktpreis, Trend, letztem Alert, Deal-Score, Sparkline."""
    result = []
    for card in db.get_watchlist():
        history = db.get_price_history(card["id"], days=7)
        prices = [h["price"] for h in history if h["price"]]
        latest_price = prices[-1] if prices else None
        trend = trend_analyzer.analyze(card["id"])
        with get_conn() as conn:
            alert = conn.execute(
                "SELECT price, deal_score, sent_at FROM alerts_sent "
                "WHERE card_id = ? ORDER BY sent_at DESC LIMIT 1",
                (card["id"],),
            ).fetchone()
        result.append({
            "id": card["id"],
            "name": card["name"],
            "market_price": latest_price,
            "trend": trend,
            "last_alert": dict(alert) if alert else None,
            "sparkline": prices[-14:],
        })
    return result


def monthly_expenses() -> dict:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT substr(date, 1, 7) AS month, SUM(amount) AS total "
            "FROM budget_log GROUP BY month ORDER BY month"
        ).fetchall()
    return {
        "labels": [r["month"] for r in rows],
        "values": [round(r["total"] or 0, 2) for r in rows],
    }


def all_transactions() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM budget_log ORDER BY date DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def scalp_profit_pool() -> dict:
    """Geschätzter Profit-Pool: Summe potenzieller Netto-Gewinne über alle
    aktiven Scalp-Targets (auf Basis Ziel-Einkaufspreis + Sealed-Avg)."""
    total = 0.0
    counted = 0
    for t in db.get_scalp_targets(active_only=True):
        buy = t["retail_price_target"]
        if not buy:
            continue
        calc = profit_calculator.calculate_profit(buy, t["product_name"])
        if calc["sealed_known"] and calc["net_profit"] is not None:
            total += calc["net_profit"]
            counted += 1
    return {"total": round(total, 2), "counted": counted}


def sealed_value_history(product_name: str, days: int = 90) -> dict:
    rows = db.get_sealed_price_history(product_name, days)
    return {
        "labels": [r["updated_at"][:10] for r in rows],
        "values": [round(r["avg_price"] or 0, 2) for r in rows],
    }


def price_guide_info(card) -> dict | None:
    """Aktuelle Cardmarket-Price-Guide-Preise für eine Sammlungskarte.

    Nutzt die gespeicherte Produkt-ID (schnell, kein Netzaufruf); fehlt sie,
    wird einmalig über TCGdex aufgelöst. Liefert low/trend/avg7/avg30 + Link.
    """
    import cm_priceguide
    import tcgdex

    # Immer über TCGdex auflösen — liefert denselben geprüften Direktlink
    # wie der Telegram-Workflow (z.B. /Singles/Paldean-Fates/Charmander-V2-PAF109)
    # statt einer Namenssuche, die auf Cardmarket oft ins Leere führt.
    try:
        found = tcgdex.lookup(
            card["card_name"], set_name=card["set_name"],
            number=card["card_number"], rarity=card["rarity"],
        )
    except Exception:
        found = None

    cm_url = found.get("url") if found else None
    product_id = card["cardmarket_product_id"] or (found.get("idProduct") if found else None)

    # Fallback-Link nur wenn TCGdex gar nichts lieferte (Nummer ohne /-Suffix)
    if not cm_url:
        num = (card["card_number"] or "").split("/")[0].strip() or None
        cm_url = tcgdex.cardmarket_search_url(card["card_name"] or "", number=num)

    cm = cm_priceguide.get_price(product_id) if product_id else None
    if not cm:
        return {"low": None, "trend": None, "avg7": None, "avg30": None, "url": cm_url}
    return {
        "low": cm.get("low"),
        "trend": cm.get("trend") or cm.get("avg"),
        "avg7": cm.get("avg7"),
        "avg30": cm.get("avg30"),
        "url": cm_url,
    }


# ---------------------------------------------------------------- Routes
@app.route("/")
@login_required
def index():
    prof = active_profile()
    summ = portfolio.summary(prof)
    change = portfolio.value_change_vs(days_ago=7, owner=prof)
    chart = daily_portfolio_values(30, prof)
    return render_template(
        "index.html",
        summary=summ,
        change=change,
        chart_labels=chart["labels"],
        chart_values=chart["values"],
        winners=top_winners(7, 3, prof),
        alerts=recent_alerts(5),
        scalp_active=db.get_scalp_targets(active_only=True),
        recent_restocks=db.get_recent_restocks(hours=48, limit=5),
        profit_pool=scalp_profit_pool(),
        realized=db.realized_profit(prof),
        active="index",
    )


@app.route("/sammlung")
@login_required
def sammlung():
    prof = active_profile()
    summ = portfolio.summary(prof)
    cards = []
    for card in db.get_portfolio(prof):
        item = next((i for i in summ["items"] if i["id"] == card["id"]), {})
        image_name = os.path.basename(card["image_path"]) if card["image_path"] else None
        cards.append({
            "id": card["id"],
            "name": card["card_name"],
            "set_name": card["set_name"],
            "rarity": card["rarity"],
            "rarity_class": _rarity_class(card["rarity"]),
            "language": card["language"],
            "condition": card["condition"],
            "purchase_price": card["purchase_price"],
            "purchase_date": (card["purchase_date"] or "")[:10],
            "market_value": item.get("market_value"),
            "gain": item.get("gain"),
            "image": image_name,
        })
    return render_template("sammlung.html", cards=cards, active="sammlung")


@app.route("/sammlung/<int:card_id>/delete", methods=["POST"])
@login_required
def sammlung_delete(card_id):
    """Entfernt eine Karte endgültig (verloren / verschenkt / Fehleingabe)."""
    card = db.get_portfolio_card(card_id)
    if card and db.remove_portfolio_card(card_id):
        flash(f"'{card['card_name']}' aus der Sammlung entfernt.", "success")
    else:
        flash("Karte nicht gefunden.", "error")
    return redirect(url_for("sammlung"))


@app.route("/verkauft")
@login_required
def verkauft():
    """Verkaufte Karten: Kauf-/Verkaufspreis, realisierter Gewinn."""
    prof = active_profile()
    cards = []
    for c in db.get_sold_cards(prof):
        cost = c["purchase_price"] or 0.0
        sale = c["sale_price"]
        profit = (sale - cost) if sale is not None else None
        cards.append({
            "id": c["id"],
            "name": c["card_name"],
            "set_name": c["set_name"],
            "purchase_price": cost,
            "sale_price": sale,
            "sale_date": (c["sale_date"] or "")[:10],
            "profit": round(profit, 2) if profit is not None else None,
        })
    return render_template(
        "verkauft.html", cards=cards, summary=db.realized_profit(prof),
        active="verkauft",
    )


@app.route("/verkauft/<int:card_id>/preis", methods=["POST"])
@login_required
def verkauft_preis(card_id):
    """Verkaufspreis nachträglich korrigieren."""
    try:
        price = float(request.form.get("sale_price", "").replace(",", "."))
        if price < 0:
            raise ValueError
    except ValueError:
        flash("Bitte einen gültigen Verkaufspreis eingeben.", "error")
        return redirect(url_for("verkauft"))
    db.update_portfolio_sale_price(card_id, price)
    flash("Verkaufspreis aktualisiert.", "success")
    return redirect(url_for("verkauft"))


@app.route("/verkauft/<int:card_id>/zurueck", methods=["POST"])
@login_required
def verkauft_zurueck(card_id):
    """Verkauf rückgängig — Karte zurück in die Sammlung."""
    db.unmark_portfolio_sold(card_id)
    flash("Karte zurück in der Sammlung.", "success")
    return redirect(url_for("verkauft"))


@app.route("/karte/<int:card_id>")
@login_required
def karte(card_id):
    card = db.get_portfolio_card(card_id)
    if not card:
        abort(404)
    latest = db.get_latest_portfolio_value(card_id)
    market_value = latest["market_value"] if latest else None
    gain = (market_value - card["purchase_price"]) if market_value is not None else None
    hist = card_value_history(card_id, 30)
    guide = price_guide_info(card)
    image_name = os.path.basename(card["image_path"]) if card["image_path"] else None
    return render_template(
        "karte.html",
        card=dict(card),
        image=image_name,
        market_value=market_value,
        gain=gain,
        chart_labels=hist["labels"],
        chart_values=hist["values"],
        guide=guide,
        today=datetime.utcnow().strftime("%Y-%m-%d"),
        active="sammlung",
    )


@app.route("/karte/<int:card_id>/preis", methods=["POST"])
@login_required
def karte_preis(card_id):
    """Kaufpreis einer Sammlungskarte korrigieren."""
    card = db.get_portfolio_card(card_id)
    if not card:
        abort(404)
    try:
        price = float(request.form.get("purchase_price", "").replace(",", "."))
        if price < 0:
            raise ValueError
    except ValueError:
        flash("Bitte einen gültigen Kaufpreis eingeben.", "error")
        return redirect(url_for("karte", card_id=card_id))
    db.update_portfolio_purchase_price(card_id, price)
    flash(f"Kaufpreis auf {price:.2f} € gesetzt.", "success")
    return redirect(url_for("karte", card_id=card_id))


@app.route("/karte/<int:card_id>/verkauft", methods=["POST"])
@login_required
def karte_verkauft(card_id):
    """Karte als verkauft markieren (mit Verkaufspreis + Datum)."""
    card = db.get_portfolio_card(card_id)
    if not card:
        abort(404)
    try:
        price = float(request.form.get("sale_price", "").replace(",", "."))
        if price < 0:
            raise ValueError
    except ValueError:
        flash("Bitte einen gültigen Verkaufspreis eingeben.", "error")
        return redirect(url_for("karte", card_id=card_id))
    sale_date = request.form.get("sale_date") or None
    db.mark_portfolio_sold(card_id, price, sale_date)
    flash(f"'{card['card_name']}' als verkauft markiert ({price:.2f} €).", "success")
    return redirect(url_for("verkauft"))


@app.route("/watchlist")
@login_required
def watchlist():
    return render_template(
        "watchlist.html", rows=watchlist_rows(), active="watchlist"
    )


@app.route("/budget")
@login_required
def budget():
    chart = monthly_expenses()
    weekly_budget = float(db.get_setting("weekly_budget", "0"))
    return render_template(
        "budget.html",
        spent_week=db.get_total_spent(7),
        spent_month=db.get_total_spent(30),
        spent_total=db.get_total_spent(),
        weekly_budget=weekly_budget,
        chart_labels=chart["labels"],
        chart_values=chart["values"],
        transactions=all_transactions(),
        active="budget",
    )


@app.route("/scalp")
@login_required
def scalp():
    return render_template(
        "scalp.html",
        targets=scalp_targets.list_with_status(),
        retailers=db.get_retailers(),
        restocks=db.get_recent_restocks(hours=24, limit=30),
        active="scalp",
    )


@app.route("/scalp/<int:scalp_id>")
@login_required
def scalp_detail(scalp_id):
    target = None
    for t in db.get_scalp_targets():
        if t["id"] == scalp_id:
            target = dict(t)
            break
    if not target:
        abort(404)

    # Preisverlauf je Händler aus retail_stock_history
    hist = db.get_stock_history_for_target(scalp_id, days=30)
    by_retailer: dict[str, dict] = {}
    for h in hist:
        name = h["retailer_name"]
        by_retailer.setdefault(name, {"labels": [], "values": []})
        if h["price"] is not None:
            by_retailer[name]["labels"].append(h["checked_at"][:16].replace("T", " "))
            by_retailer[name]["values"].append(round(h["price"], 2))

    sealed_hist = sealed_value_history(target["product_name"], 90)
    profit = None
    if target["retail_price_target"]:
        profit = profit_calculator.calculate_profit(
            target["retail_price_target"], target["product_name"]
        )
    image_name = os.path.basename(target["image_path"]) if target["image_path"] else None
    return render_template(
        "scalp_detail.html",
        target=target,
        image=image_name,
        price_series=by_retailer,
        sealed_labels=sealed_hist["labels"],
        sealed_values=sealed_hist["values"],
        profit=profit,
        events=[dict(h) for h in hist if h["in_stock"]],
        active="scalp",
    )


@app.route("/releases")
@login_required
def releases():
    return render_template(
        "releases.html",
        releases=db.get_upcoming_releases(90),
        active="releases",
    )


@app.route("/card_image/<path:filename>")
@login_required
def card_image(filename):
    # nur Dateinamen erlauben (kein Pfad-Traversal)
    safe = os.path.basename(filename)
    return send_from_directory(str(config.CARD_IMAGES_DIR), safe)


@app.errorhandler(404)
def not_found(_):
    return render_template("base.html", active=""), 404


if __name__ == "__main__":
    if not DASHBOARD_PASSWORD:
        print("⚠️  DASHBOARD_PASSWORD ist nicht gesetzt — Login nicht möglich.")
    app.run(host="0.0.0.0", port=5000, debug=False)
