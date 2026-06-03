# CLAUDE.md — Projekt-Statusdokument (Pokémon Karten Tracker)

> Zweck: Damit eine **neue Session** sofort weiß, was das Projekt ist, was läuft,
> wie es betrieben/bedient wird und was noch offen ist. Bitte bei größeren
> Änderungen aktuell halten.

**Letzter Stand:** 2026-06-03 · Sprache mit dem User: **Deutsch** (Einsteiger,
nicht-technisch → einfache Schritt-für-Schritt-Erklärungen).

---

## 1. Was ist das?
Telegram-Bot + Web-Dashboard zum Tracken/Sammeln/Bewerten von Pokémon-Karten.
Kernnutzung des Users: **Karte fotografieren → erkennen → Preis sehen → ggf. in
die Sammlung/Watchlist legen.** Fokus: **deutsche Karten, deutsche Preise.**

---

## 2. Aktueller Stand (Ampel)

### 🟢 Funktioniert (live auf dem Server)
- **Telegram-Bot läuft 24/7** (systemd, siehe Deployment).
- **Foto-Workflow:** Foto → Gemini-Erkennung (deutsche + englische Namen,
  Produkttyp) → Preise via **TCGdex** (echte Cardmarket-EUR-Preise, auch für
  deutsche Karten) → exakter Cardmarket-Link → 4 Buttons
  `[✅ Sammlung] [💰 Preis-Check] [🔔 Watchlist] [💼 Scalp-Track]`.
  Die Erkennungs-Info **bleibt nach Button-Klick stehen** (Buttons werden nur
  entfernt, Aktion kommt als neue Nachricht).
- **`/preis <name>`** (TCGdex-Preise), **KI-Chat** (Claude Haiku, Freitext).
- **Sammlung:** Foto → „Sammlung" → Kaufpreis abfragen → Eintrag. `/sammlung`,
  `/wert`, `/gekauft`.
- **Budget** (`/budget`, `/ausgabe`), **Watchlist** (`/add`, `/watchlist`),
  **Releases** (`/releases`, `/release_add`).
- **Web-Dashboard** (Sammlung-Galerie, Wert, Watchlist, Budget, Scalp, Releases).

### 🟡 Eingeschränkt
- **Preise sind EU-weite Cardmarket-Durchschnitte**, NICHT nach deutschen
  Verkäufern gefiltert. Werte sind frisch + für die richtige Karte, aber die
  Anzeige „nur DE-Verkäufer ab X €" kann nur die Cardmarket-API. Der **Link**
  führt aber zu den echten DE-Angeboten.
- **`/wert` / Sammlungswert ist statisch** — die tägliche Neubewertung (02:00)
  nutzt noch Cardmarket → tut nichts. TODO: auf TCGdex umstellen.
- **Versiegelte Produkte** (Displays/ETBs) haben **keine Preise** (TCGdex hat nur
  Einzelkarten) → Profit-Rechner/Scalp-Profit ohne Daten.
- Dashboard läuft nur über **HTTP** (Passwort, aber unverschlüsselt).

### 🔴 Läuft (noch) nicht
- **Automatischer Schnäppchen-Scanner + Alerts** (30-Min-Job) nutzt Cardmarket →
  `403`, keine Alerts. TODO: auf TCGdex umstellen (Preis-Drop-Alerts).
- **Scalping** (retail_monitor/hotstock): Händler-Selektoren sind ungetestete
  Platzhalter, Sealed-Preise fehlen → keine echten Restock-Alerts. Die Jobs
  laufen, tun aber nichts Nützliches.
- **Cardmarket-API**: nicht verbunden (keine Tokens). Sobald die 4 Tokens in der
  `.env` stehen, schaltet der Bot automatisch auf echte DE-Filterung um
  (`config.cardmarket_enabled()`).

---

## 3. Deployment / Infrastruktur

- **Server:** Strato VPS, **IP `87.106.255.195`**, Ubuntu.
  ⚠️ **GETEILTER Server!** Dort laufen weitere Projekte:
  `sk-holzfabrik-bot`, `rookiecard-telegram-bot`, `max` (+ `max-admin`),
  `crypto-tracker`. **NIEMALS globale Befehle** (`pkill`, killall, globale
  Restarts) — nur `/opt/pokemon-tracker` und die eigenen systemd-Units anfassen.
  Mehrere Projekte nutzen eine `main.py` → `pkill -f main.py` hat schon mal
  fremde Bots gekillt (Lektion gelernt).
- **SSH:** `ssh root@87.106.255.195` — **passwortlos** (SSH-Key auf Kevins PC).
  Claude kann Server-Befehle **direkt** ausführen, indem es vom lokalen
  Bash-Tool `ssh root@87.106.255.195 "..."` aufruft (Key greift).
- **Projektpfad auf dem Server:** `/opt/pokemon-tracker`
- **Dienste (systemd):**
  - `pokemon-tracker.service` → der Bot (`venv/bin/python main.py`)
  - `pokemon-dashboard.service` → Dashboard (`gunicorn -w 2 -b 0.0.0.0:8090
    dashboard.app:app`, `Environment=DASHBOARD_HTTPS=false`)
- **Dashboard-URL:** `http://87.106.255.195:8090` · Passwort steht in der
  `.env` (`DASHBOARD_PASSWORD`). Port 8090 gewählt, weil 5000/8081/80/443 belegt
  waren (nginx + andere Projekte) — Port 8090 ist frei + von außen erreichbar.
- **GitHub:** `https://github.com/kevineick030/pokemon-tracker`
  (aktuell **public**; enthält **keine** Secrets — `.env` ist gitignored,
  Historie auf Keys geprüft = sauber). Kann auf privat gestellt werden, dann
  braucht der Server beim `git pull` einen Token/Deploy-Key.

### Update-/Deploy-Workflow (wichtig!)
Es gibt **drei Orte**: lokaler PC → GitHub → Server.
1. Lokal ändern (`C:\Users\Kevin\Desktop\claude projekte\pokemon tracker`)
2. `git commit` + `git push` (Claude kann pushen — Credentials sind im
   Windows-Credential-Manager gecached; sonst pusht der User via GitHub Desktop)
3. Auf dem Server: `cd /opt/pokemon-tracker && git pull && systemctl restart
   pokemon-tracker` (bzw. `pokemon-dashboard` bei Dashboard-Änderungen).
   Claude macht das direkt via `ssh ... "..."`.

### Lokales Testen (Windows)
- Python 3.13 global hat alle Bot-Pakete (durch früheres `pip install`).
- Start: Doppelklick `start_bot.bat` ODER `python main.py`.
- ⚠️ **NICHT** gleichzeitig mit dem Server-Bot laufen lassen → Telegram
  „Conflict" (nur eine Instanz pro Token).

---

## 4. Bedienung

### Telegram
- **Foto schicken** = wichtigster Weg (Erkennung + Preise + Buttons).
- Commands: `/start /watchlist /add /remove /preis /status /threshold /score
  /scan /sammlung /wert /gekauft /budget /ausgabe /briefing /import
  /scalp /scalp_add /scalp_remove /scalp_pause /restocks /profit /releases
  /release_add /retailers`
- **Freitext** = KI-Experte (Claude Haiku).

### Dashboard
- `http://87.106.255.195:8090` → Login mit `DASHBOARD_PASSWORD` aus `.env`.
- Seiten: Übersicht, Sammlung, Karte-Detail, Watchlist, Scalp, Scalp-Detail,
  Releases, Budget.

---

## 5. Architektur / wichtige Dateien
```
main.py            Einstieg: Bot + APScheduler-Jobs
bot.py             Telegram-Handler, Foto-Workflow, Buttons (on_callback)
config.py          .env laden (override=True!), Konstanten, cardmarket_enabled()
database.py        SQLite-Schema + Zugriff (pokemon_tracker.db)
image_recognition.py  Gemini (Modell: gemini-2.5-flash), liefert card_name +
                      card_name_en + product_type
tcgdex.py          AKTUELLE Preis-Quelle (in bot.py als `pokeprice` aliasiert!)
pokeprice.py       ALTE Quelle (pokemontcg.io) — nicht mehr genutzt, bleibt liegen
cardmarket.py      Cardmarket-API (nur aktiv, wenn Tokens gesetzt)
scanner.py         Watchlist-Scan (Cardmarket → derzeit tot, TODO: TCGdex)
portfolio.py       Sammlungswert (Cardmarket → tägl. Job tot, TODO: TCGdex)
profit_calculator.py / sealed_prices.py / scalp_targets.py /
retail_monitor.py / hotstock_monitor.py / restock_alerts.py /
release_calendar.py   → Scalping-Modul (teils nicht funktional, siehe oben)
dashboard/app.py + templates/ + static/   Flask-Dashboard
retailers_config.json  Händler-CSS-Selektoren (Platzhalter, ungetestet)
```

### Schlüssel-Entscheidungen
- **Preis-Quelle = TCGdex** (`api.tcgdex.net`): mehrsprachig (deutsche Namen!) +
  tagesaktuelle Cardmarket-EUR-Preise + Produkt-ID für exakten Link. Ersetzt
  pokemontcg.io, das an deutschen/japanischen Karten scheiterte. In `bot.py`
  via `import tcgdex as pokeprice` eingebunden → gleiche Schnittstelle
  (`lookup`, `trend_from_prices`, `cardmarket_search_url`).
- **`config.load_dotenv(override=True)`** — sonst überschreibt eine leere
  OS-Umgebungsvariable die `.env` (Bug bei ANTHROPIC_API_KEY gehabt).
- **Gemini-Modell `gemini-2.5-flash`** (das alte `gemini-2.0-flash-exp` ist
  abgeschaltet → 404). Paket `google.generativeai` ist deprecated, funktioniert
  aber noch.
- **`.env`** enthält: Telegram-Token+ChatID, Anthropic-Key, Gemini-Key,
  Dashboard-Passwort+Secret. **Cardmarket-Tokens leer.** `POKEMONTCG_API_KEY`
  leer (TCGdex braucht eh keinen Key).

---

## 6. Offene TODOs / nächste Schritte
Vom User priorisiert (zuletzt gewählt):
1. **Kauf-Berater (Deal-Check):** beim Foto Kaufpreis eingeben → „Markt X €, du
   zahlst Y € → -Z % → KAUFEN/SKIP". Echtzeit-Einkaufsberater.
2. **Sammlung lebt:** Sammlungswert täglich über TCGdex aktualisieren → `/wert`
   + Dashboard-Chart bewegen sich; toten Cardmarket-Bewertungs-Job ersetzen.
3. **Sammlung-Extras:** Doppelte-Warnung beim Scannen, Set-Fortschritt
   (`8/18 SIR`), CSV/Excel-Export, Quick-Sell-Schätzung nach Gebühren.

Weitere offene Punkte:
- Watchlist-Scanner auf TCGdex umstellen (Preis-Drop-Alerts) — ersetzt toten
  Cardmarket-Scan.
- Tote/sinnlose Scheduler-Jobs aufräumen (Scalping, Cardmarket-Scan, Bewertung).
- Für echte **DE-Verkäufer-Filterung**: Cardmarket-API beantragen (kostenpflichtig
  + Freischaltung; Code schaltet automatisch um).
- Dashboard optional auf **HTTPS** (über vorhandenen nginx + Domain + certbot).
- Scalping: Händler-Selektoren an echte Seiten anpassen ODER Scalping pausieren.

---

## 7. Sicherheit / Gotchas
- **Secrets nur in `.env`** (gitignored). Niemals in `.py`/Templates schreiben.
  (Hinweis: Telegram-Token, Anthropic- & Gemini-Key standen früher im Chat →
  dem User wurde Rotation empfohlen.)
- **Geteilter Server** → keine globalen Befehle (siehe §3).
- **Telegram:** nur EINE Bot-Instanz pro Token gleichzeitig.
- Beim Neustart über systemd erscheinen „failed"-Meldungen, wenn vorher eine
  Hintergrund-Instanz mit `taskkill`/`pkill` beendet wurde — das ist erwartet,
  kein Absturz.
