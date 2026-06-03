# 🃏 Pokémon Karten Tracker Bot

Telegram-Bot zum Tracken von **SIR-, IR- und Ultra-Rare-Pokémon-Karten** auf
[Cardmarket](https://www.cardmarket.com). Erkennt Schnäppchen, verwaltet die
eigene Sammlung, trackt Budget und analysiert Preistrends.

**Filter:** nur DE-Verkäufer mit ≥ 98 % Bewertung.

---

## Features

- 🔍 **Scanner** – durchsucht die Watchlist alle 30 Min nach Schnäppchen
- 🎯 **Deal-Score (0–100)** – aus Ersparnis, Verkäuferbewertung, Zustand & Trend
- 📈 **Trend-Analyse** – 7-Tage-Verlauf: steigend / fallend / stabil + Empfehlung
- 💎 **Portfolio** – Sammlung mit täglicher Wertaktualisierung & G/V-Berechnung
- 💶 **Budget-Tracking** – Wochenbudget & Ausgabenlog
- ☀️ **Tägliches Briefing** – 09:00 Uhr: Top-Deals, Sammlungswert, Budget
- 📸 **Foto-Erkennung** – Karten-Foto schicken → Gemini erkennt sie → in Sammlung/Watchlist
- 📥 **Wunschlisten-Import** – Cardmarket-Wunschliste per ID in die Watchlist holen
- 🤖 **KI-Experte** – Freitext-Fragen an Claude Haiku

---

## Tech Stack

- Python 3.11+
- [python-telegram-bot](https://python-telegram-bot.org/) (async)
- SQLite
- Cardmarket API v2.0 (OAuth 1.0a / HMAC-SHA1)
- APScheduler
- Anthropic Claude `claude-haiku-4-5` (nur Freitext)
- Google Gemini `gemini-2.0-flash-exp` (Bilderkennung)

---

## Installation (lokal & Strato VPS)

```bash
git clone <repo> pokemon-tracker
cd pokemon-tracker

python3.11 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env            # dann .env ausfüllen
python main.py
```

### `.env` ausfüllen

| Variable | Beschreibung |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token vom [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_CHAT_ID` | Deine Chat-ID (z. B. via [@userinfobot](https://t.me/userinfobot)) |
| `MKM_APP_TOKEN` / `MKM_APP_SECRET` | Cardmarket "Dedicated App" |
| `MKM_ACCESS_TOKEN` / `MKM_ACCESS_TOKEN_SECRET` | Cardmarket Access-Tokens |
| `ANTHROPIC_API_KEY` | API-Key von console.anthropic.com |
| `GEMINI_API_KEY` | API-Key von [aistudio.google.com](https://aistudio.google.com/app/apikey) (Bilderkennung) |

> Cardmarket-Tokens: Account → Account-Einstellungen → API → *Neue App erstellen*
> (Typ: **Dedicated**). Erfordert eine kostenpflichtige Cardmarket-Mitgliedschaft
> für den API-Zugang.

---

## Als Dienst auf dem Strato VPS (systemd)

`/etc/systemd/system/pokemon-tracker.service`:

```ini
[Unit]
Description=Pokemon Karten Tracker Bot
After=network-online.target

[Service]
Type=simple
User=pokemon
WorkingDirectory=/home/pokemon/pokemon-tracker
ExecStart=/home/pokemon/pokemon-tracker/venv/bin/python main.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now pokemon-tracker
sudo systemctl status pokemon-tracker
journalctl -u pokemon-tracker -f          # Live-Logs
```

Logs zusätzlich in `pokemon_tracker.log`.

---

## 🖥️ Web-Dashboard (Flask)

Passwortgeschütztes Dashboard mit Sammlungs-Galerie, Wertcharts, Watchlist und
Budget. Läuft auf demselben VPS auf Port 5000, **read-only** auf dieselbe
`pokemon_tracker.db`, hinter nginx unter `/dashboard`.

### Setup

```bash
cd /home/pokemon/pokemon-tracker
source venv/bin/activate
pip install -r dashboard/requirements_dashboard.txt
```

`.env` ergänzen:

| Variable | Beschreibung |
|---|---|
| `DASHBOARD_PASSWORD` | Login-Passwort (ein einziger User, keine Registrierung) |
| `DASHBOARD_SECRET_KEY` | zufälliger Schlüssel für Sessions, z. B. `python -c "import os;print(os.urandom(32).hex())"` |

Lokaler Testlauf:

```bash
python dashboard/app.py            # http://localhost:5000
```

Produktiv (hinter nginx, vom Projekt-Root aus):

```bash
gunicorn -w 2 -b 127.0.0.1:5000 dashboard.app:app
```

### Als systemd-Dienst

`/etc/systemd/system/pokemon-dashboard.service`:

```ini
[Unit]
Description=Pokemon Tracker Dashboard
After=network-online.target

[Service]
Type=simple
User=pokemon
WorkingDirectory=/home/pokemon/pokemon-tracker
ExecStart=/home/pokemon/pokemon-tracker/venv/bin/gunicorn -w 2 -b 127.0.0.1:5000 dashboard.app:app
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now pokemon-dashboard
```

### nginx + SSL

Vorlage: [`dashboard/nginx_pokemon_dashboard.conf`](dashboard/nginx_pokemon_dashboard.conf)

```bash
sudo cp dashboard/nginx_pokemon_dashboard.conf /etc/nginx/sites-available/pokemon-dashboard
# server_name in der Datei auf deine Domain anpassen
sudo ln -s /etc/nginx/sites-available/pokemon-dashboard /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# SSL-Zertifikat (füllt die ssl_certificate-Zeilen automatisch):
sudo certbot --nginx -d deine-domain.de
```

Das Dashboard ist danach unter `https://deine-domain.de/dashboard` erreichbar.
nginx leitet `/dashboard` an `127.0.0.1:5000` weiter und setzt
`X-Forwarded-Prefix`, sodass alle Links korrekt erzeugt werden.

### Passwort ändern

`DASHBOARD_PASSWORD` in `.env` anpassen und Dienst neu starten:

```bash
sudo systemctl restart pokemon-dashboard
```

### Sicherheit

- Flask-Login mit einem einzigen User (keine Registrierung)
- Session-Timeout 24 h, HTTPOnly-/Secure-Cookies
- Security-Header (`HSTS`, `X-Frame-Options`, `X-Content-Type-Options`)
- Dashboard greift nur lesend auf die DB zu

---

## Telegram-Befehle

| Befehl | Funktion |
|---|---|
| `/start` | Begrüßung & Hilfe |
| `/watchlist` | Watchlist mit Trend-Pfeil & Preis |
| `/add <name>` | Karte zur Watchlist hinzufügen |
| `/remove <name>` | Karte entfernen |
| `/preis <name>` | DE/EN/JP-Vergleich + Trend + Empfehlung |
| `/status` | Bot- & API-Status |
| `/threshold <zahl>` | Ersparnis-Schwelle (%) setzen |
| `/score <zahl>` | Mindest-Deal-Score für Alerts setzen |
| `/scan` | Manuellen Scan auslösen |
| `/sammlung` | Portfolio-Übersicht |
| `/wert` | Gesamtwert + G/V + Top-Karte |
| `/gekauft <name> <preis>` | Karte als gekauft markieren |
| `/budget [<zahl>]` | Budgetstatus / Wochenbudget setzen |
| `/ausgabe <betrag> <text>` | Ausgabe verbuchen |
| `/briefing` | Tagesbriefing sofort |
| `/import <id>` | Cardmarket-Wunschliste in die Watchlist importieren |
| `/scalp` | Scalp-Watchlist mit Status pro Händler |
| `/scalp_add <produkt> <ziel>` | Scalp-Target hinzufügen |
| `/scalp_remove <produkt>` | Scalp-Target entfernen |
| `/scalp_pause <produkt>` | Scalp-Target pausieren/aktivieren |
| `/restocks` | Letzte Restock-Events |
| `/profit <produkt> <kaufpreis>` | Profit-Rechner (alle Gebühren) |
| `/releases` | Kommende Releases (60 Tage) |
| `/release_add <set> <YYYY-MM-DD> [pre]` | Release manuell eintragen |
| `/retailers` | Status aller Händler |
| _Foto_ | Karte/Produkt erkennen → Sammlung/Preis-Check/Watchlist/Scalp |
| _Freitext_ | Frage an den KI-Pokémon-Experten |

---

## 📸 Foto-Workflow (Bilderkennung)

1. Schick dem Bot einfach ein **Foto** einer Pokémon-Karte **oder eines
   versiegelten Produkts** (Display, ETB, Tin, Bundle, Collection, Box; max. 5 MB).
2. Das Bild wird temporär in `card_images/` gespeichert und an **Gemini**
   (`gemini-2.0-flash-exp`) geschickt, das Name, Set, Nummer, Rarity, Sprache,
   geschätzten Zustand **und Produkttyp** als JSON zurückgibt.
3. Der Bot sucht das Produkt auf Cardmarket, ermittelt günstigstes DE-Angebot,
   Marktpreis und Deal-Score und zeigt vier Aktions-Buttons:

   `[✅ Sammlung] [💰 Preis-Check]`
   `[🔔 Watchlist] [💼 Scalp-Track]`

   - **✅ Sammlung:** Foto wird dauerhaft als `card_images/{card_id}_{timestamp}.jpg`
     gespeichert, ein Portfolio-Eintrag angelegt und der Bot fragt nach dem
     Kaufpreis. Zahl als Nachricht schicken (z. B. `49.90`) — die Karte wird
     vervollständigt und der Betrag als Ausgabe verbucht.
   - **💰 Preis-Check:** zeigt Top-5-DE-Angebote, DE/EN/JP-Vergleich, 7-Tage-Trend
     und Deal-Score. **Kein** dauerhafter Eintrag — reine Information.
   - **🔔 Watchlist:** Karte wandert in die Beobachtungsliste; der Bot fragt nach
     der Alarm-Schwelle (Standard 15 %) und aktiviert die automatischen Scans.
   - **💼 Scalp-Track:** **nur bei versiegelten Produkten sichtbar.** Der Bot fragt
     nach dem Ziel-Einkaufspreis und merkt das Produkt für das Retail-Monitoring
     vor (Scalping-Modul, folgt).

Auch die Befehle **`/preis`** und **`/add`** bieten dieselben Aktions-Buttons an.

**Rate-Limit:** max. 20 Bilder pro Stunde (in SQLite getrackt).

---

## 📥 Wunschliste importieren

```
/import <wunschlisten-id>
```

Holt alle Karten einer **Cardmarket-Wunschliste** (`GET /wantslist/{id}`) und
fügt sie der Watchlist hinzu. Bereits vorhandene Karten werden übersprungen.
Die Wunschlisten-ID findest du in der Cardmarket-URL deiner Wantslist.

---

## 💼 Scalping-Modul

Überwacht versiegelte Produkte (Displays, ETBs, Tins, Bundles, Collections) bei
deutschen Händlern auf **Restocks** und meldet sie mit einer **Profit-Analyse**.

> ⚖️ **Ethik-Hinweis:** Dieses Modul versendet **nur Benachrichtigungen**. Es
> kauft nichts automatisch, legt nichts in Warenkörbe und umgeht keine
> Kaufbeschränkungen. Es respektiert Rate-Limits und tut nur, was rechtlich
> zulässig ist. Kein Bot-Buying.

### Architektur (Hybrid)

- **HotStock.de-Fallback** (`hotstock_monitor.py`) — liest den HotStock-Feed
  (alle 60 s) und gleicht Einträge per Fuzzy-Match mit deinen Scalp-Targets ab.
  Zuverlässig und leichtgewichtig, da HotStock bereits viele Händler aggregiert.
- **Retail-Monitor** (`retail_monitor.py`, alle 120 s) — scrapt Händler direkt:
  - **requests + BeautifulSoup** für leichte Seiten (Galeria, Rossmann, Thalia,
    pokemoncards24, tradingcards).
  - **Playwright** (optional!) für JS-lastige Shops (Müller, MediaMarkt, Saturn,
    Smyths, Amazon, Pokémon Center). Ist Playwright **nicht** installiert, werden
    diese Händler automatisch übersprungen (Hybrid-Degradation) — der Bot läuft
    trotzdem.

### Playwright installieren (optional)

Nur nötig, wenn du die Browser-basierten Händler nutzen willst:

```bash
pip install playwright playwright-stealth
playwright install chromium
```

Auf kleinen VPS (≤ 2 GB RAM): `PLAYWRIGHT_MAX_INSTANCES` (in `config.py`) niedrig
lassen (Default 2). Bei IP-Sperren optional einen Proxy in `.env` setzen
(`PROXY_URL`, `PROXY_USERNAME`, `PROXY_PASSWORD`).

### Händler-Selektoren anpassen

Die CSS-Selektoren liegen extern in **`retailers_config.json`**. Ändert ein
Händler seine HTML-Struktur, passt du dort `stock_selector`, `price_selector`
und die `stock_in/out_keywords` an — **kein** Code-Deploy nötig. Die mitgelieferten
Selektoren sind Startwerte und sollten gegen die Live-Seiten verifiziert werden.

### Resilienz & Anti-Bot

- Retry mit exponential Backoff (3 Versuche)
- Circuit Breaker pro Händler (3 Fehler → 30 min Pause); 429/403 → sofortiger Cooldown
- Rotierende User-Agents, realistische Delays (2–8 s), max. 3 parallele Requests
- Playwright-Stealth (falls installiert)
- Captcha-Erkennung → Händler-Cooldown + Telegram-Warnung an den Admin
- Eigenes Log: `scalp_monitor.log`

### Profit-Rechner

`/profit <produkt> <kaufpreis>` bzw. automatisch in jedem Restock-Alert.
Rechnet auf Basis des Cardmarket-Sealed-Preises (alle 6 h aktualisiert):

```
Verkauf realistisch = Cardmarket-avg × 0,92  (Schnellverkauf)
Gebühren = Cardmarket 5 % + PayPal 2,49 % + 0,35 € + Versand 6,99 € + Verpackung 1 €
Netto = Verkauf − Kaufpreis − Gebühren
Empfehlung: KAUFEN (Marge ≥ 20 %) / GRENZWERTIG (≥ 10 %) / SKIP
```

### Restock-Alert

Bei einem Übergang **ausverkauft → verfügbar** (oder Preis ≤ Ziel-Einkaufspreis)
kommt ein Alarm inkl. Profit-Analyse und Direkt-Link. **Dedupe:** gleicher
Produkt+Händler max. 1× pro `RESTOCK_ALERT_DEDUPE_HOURS` (Default 6 h).

### Release-Kalender

`/release_add <set> <YYYY-MM-DD> [pre]` pflegt Termine. 14 und 1 Tag(e) vor
einem Pre-Order-Release kommt ein Reminder; am Release-Tag wird der Retail-Scan
für 24 h auf 60 s beschleunigt.

### Scheduler-Übersicht

| Job | Intervall |
|---|---|
| Watchlist-Scan | 30 min |
| Retail-Monitor | 120 s (Release-Tag: 60 s) |
| HotStock-Monitor | 60 s |
| Sealed-Preise (Cardmarket) | 6 h |
| Release-Check | täglich 09:05 |
| Portfolio-Bewertung | täglich 02:00 |
| Tagesbriefing | täglich 09:00 |

---

## Projektstruktur

```
pokemon-tracker/
├── main.py            # Einstieg: Bot + Scheduler
├── bot.py             # Telegram-Handler
├── cardmarket.py      # Cardmarket API v2.0 + OAuth 1.0a
├── scanner.py         # Scan-Logik & Alerts
├── database.py        # SQLite-Schema & Zugriff
├── ai_chat.py         # Claude-Haiku-Chat
├── trend_analyzer.py  # 7-Tage-Trend
├── deal_scorer.py     # Deal-Score 0–100
├── portfolio.py       # Sammlung & Wert-Tracking
├── briefing.py        # Tägliches Briefing
├── image_recognition.py # Gemini-Bilderkennung (Karten + Produkttyp)
├── profit_calculator.py # Profit nach allen Gebühren
├── sealed_prices.py     # Cardmarket-Sealed-Preise (alle 6h)
├── scalp_targets.py     # Scalp-Verwaltung + Fuzzy-Match
├── retail_monitor.py    # Händler-Scraping (requests + optional Playwright)
├── hotstock_monitor.py  # HotStock.de-Fallback
├── restock_alerts.py    # Dedupe + Alert-Format
├── release_calendar.py  # Release-Kalender + Boost
├── config.py          # .env-Konfiguration & Logging
├── retailers_config.json # CSS-Selektoren je Händler (extern)
├── card_images/       # lokale Karten-/Produkt-Fotos
├── dashboard/         # Web-Dashboard (Flask)
│   ├── app.py
│   ├── templates/     # base, login, index, sammlung, karte, watchlist,
│   │                  #   budget, scalp, scalp_detail, releases
│   ├── static/        # style.css, charts.js
│   ├── nginx_pokemon_dashboard.conf
│   └── requirements_dashboard.txt
├── .env / .env.example
├── requirements.txt
└── README.md
```

---

## Deal-Score (0–100)

| Komponente | Max | Logik |
|---|---|---|
| Preis-Ersparnis | 40 | linear bis 20 %+ Ersparnis |
| Verkäufer-Bewertung | 25 | 98 % = 0, 100 % = 25 |
| Zustand | 20 | NM/MT = 20, EX = 12, GD/LP = 5 |
| Trend-Bonus | 15 | fallend +15, steigend −10, stabil 0 |

Alert ab Score ≥ `min_score` (Default 60, via `/score` änderbar).

---

## Roadmap

- **Phase 1 ✅** – Grundgerüst, Cardmarket-OAuth, alle Module, Commands, Scheduler
- **Phase 2 ✅** – Gemini-Bilderkennung, Foto → Sammlung/Watchlist, Wunschlisten-Import
- **Phase 3 ✅** – Passwortgeschütztes Web-Dashboard (Flask): Übersicht, Galerie, Watchlist, Budget
- **Phase 4 ✅** – 4-Button-Foto-Workflow (Sammlung/Preis-Check/Watchlist/Scalp) + Produkttyp-Erkennung
- **Phase 5 ✅** – Scalping-Modul: Retail-Monitoring (Hybrid), HotStock-Fallback, Profit-Rechner, Release-Kalender, Restock-Alerts + Dashboard-Seiten
- **Phase 6** – Live-Tuning der Händler-Selektoren, optionales pokemon.com-Scraping, EAN-basiertes Matching
```
