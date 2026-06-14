# CLAUDE.md — Projekt-Statusdokument (Pokémon Karten Tracker)

## 🚦 Regeln für KI-Sessions (immer befolgen)
Kevin ist Endnutzer mit ADHS und will keine Deployment-Überraschungen. Für JEDE Session (lokal wie Cloud):

1. **Zuerst diese CLAUDE.md + START-HIER.md lesen.**
2. **Direkt auf `main` arbeiten — und die KI veröffentlicht selbst.** Wenn eine Änderung fertig & getestet ist, **committet und pusht die KI direkt nach `main`** (`git add -A && git commit -m "…" && git push`). Kevin muss dafür **keinen Ordner öffnen und keine `.bat` starten**. Push nach `main` = **live/produktiv** → danach in einem Satz sagen, was veröffentlicht wurde. **KEINE Preview-Branches oder Pull Requests**, außer Kevin fragt ausdrücklich danach. ⚠️ Die interaktive `deploy.bat` **nicht** selbst im Terminal ausführen (sie fragt nach Eingaben und bleibt hängen) — die ist nur Kevins Ein-Klick-Variante.
3. **Deployment:** Push nach `main` → Strato-Server `87.106.255.195` zieht automatisch. Daten liegen in SQLite (`pokemon_tracker.db`).
4. **Doku aktuell halten (PFLICHT):** Nach JEDER nennenswerten Code-Änderung diese CLAUDE.md **im selben Commit** mit aktualisieren (neue Features, geänderte Abläufe, Deploy, Config). Veraltete Doku ist schlimmer als keine. Auch was Kevin als dauerhafte Regel sagt, kommt hierher – nicht nur in den Chat.
5. **Einfache Sprache, keine unnötigen Fachbegriffe.**

---

> Zweck: Damit eine **neue Session** sofort weiß, was das Projekt ist, was läuft,
> wie es betrieben/bedient wird und was noch offen ist. Bitte bei größeren
> Änderungen aktuell halten.

**Letzter Stand:** 2026-06-13 · Sprache mit dem User: **Deutsch** (Einsteiger,
nicht-technisch → einfache Schritt-für-Schritt-Erklärungen).

---

## 1. Was ist das?
Telegram-Bot + Web-Dashboard zum Tracken/Sammeln/Bewerten von Pokémon-Karten
**und versiegelten Produkten** (Tins, ETBs, Displays etc.).
Kernnutzung: **Karte/Produkt fotografieren → erkennen → Preis sehen → in
Sammlung/Watchlist legen.** Fokus: **deutsche Karten + Produkte, Cardmarket-Preise.**

---

## 2. Aktueller Stand (Ampel)

### 🟢 Funktioniert (live auf dem Server)

#### Foto-Workflow (Einzelkarten)
- Foto → Gemini-Erkennung (Name DE+EN+JP, Set, Nummer, Seltenheit, Produkttyp)
- Preis-Lookup-Kette:
  1. TCGdex → `idProduct` (Cardmarket-Produkt-ID) ermitteln
  2. **Lokaler Cardmarket Price Guide** (`cm_price_guide` SQLite-Tabelle) → `low`+`trend`+`avg7` (EUR)
  3. Fallback: TCGdex EU-Aggregate (wenn kein CM-Eintrag)
- Anzeige: `Ab: X €` (günstigstes Angebot EU-weit) + Trend + Ø7T + direkter CM-Link
- 4 Buttons: `[✅ Sammlung] [💰 Preis-Check] [🔔 Watchlist] [💼 Scalp-Track]`
  (Scalp-Track nur bei versiegelten Produkten sichtbar)
- Erkennungs-Info bleibt nach Button-Klick stehen (Buttons werden nur entfernt)

#### Cardmarket Price Guide (NEU, 2026-06-04)
- **Datei:** `https://downloads.s3.cardmarket.com/productCatalog/priceGuide/price_guide_6.json`
- **75.099 Pokémon-Produkte** mit low/trend/avg/avg7/avg30 (EUR)
- **Täglich 06:00** automatisch heruntergeladen + in SQLite (`cm_price_guide`) importiert
- Kein API-Key nötig. Lookup: `idProduct` → sofortige lokale DB-Abfrage (kein Rate-Limit)
- Modul: `cm_priceguide.py` (download_and_import, get_price, is_ready)

#### TCGdex-Lookup (zwei-Pfad-Strategie, NEU 2026-06-04)
- **Pfad 1 (bevorzugt):** Set + Kartennummer → direkter TCGdex-Endpunkt `/{lang}/cards/{set_id}-{num}`
  → immer exakt, kein Scoring-Fehler möglich. Plausibilitätscheck: Basis-Pokémon-Name.
- **Pfad 2 (Fallback):** Namenssuche → Nummer dominiert Scoring (+10 Treffer / -20 Mismatch).
  Wenn kein Kandidat die Nummer trifft → `None` statt falscher Karte.
- **Prinzip: lieber kein Preis als ein Preis von der falschen Karte.**

#### Sammlung (Einzelkarten + versiegelte Produkte)
- Foto → ✅ Sammlung → Kaufpreis eingeben → Eintrag in `portfolio`-Tabelle
- Funktioniert für Einzelkarten UND Tins/ETBs/Displays
- **Tägliche Neubewertung (02:00):** `portfolio.update_all_values()` → TCGdex→CM-Price-Guide
- `/wert`, `/sammlung`, `/gekauft`

#### Japanische Karten
- Gemini-Prompt verlangt `card_name_en` als **PFLICHT** (auch bei JP-Karten, mit Beispielen)
- TCGdex EN-Lookup liefert `idProduct` → CM Price Guide-Lookup funktioniert
- Fallback: Namens-Suchlink wenn idProduct nicht gefunden

#### Weitere funktionsfähige Features
- **`/preis <name>`** (TCGdex-Preise), **KI-Chat** (Claude Haiku, Freitext)
- **Budget** (`/budget`, `/ausgabe`), **Watchlist** (`/add`, `/watchlist`)
- **Releases** (`/releases`, `/release_add`)
- **Web-Dashboard** (Sammlung-Galerie, Wert, Watchlist, Budget, Scalp, Releases)
  URL: `http://87.106.255.195:8090`

### 🟡 Eingeschränkt
- **Preise sind EU-weite Cardmarket-Durchschnitte** (kein DE-Filter): `low` = günstigstes
  EU-Angebot, nicht nur Deutschland. Der Link führt zu allen Cardmarket-Angeboten.
- **Versiegelte Produkte** (Tins/ETBs/Displays) haben **keine Preise** im CM Price Guide
  (der enthält nur Einzelkarten) → Preis zeigt `–`, manueller Kaufpreis beim Sammlung-Eintrag.
- Dashboard läuft nur über **HTTP** (Passwort, aber unverschlüsselt).
- (Watchlist-Alerts laufen inzwischen über den neuen Deal-Scanner, siehe 🟢 unten — nicht mehr über die blockierte Cardmarket-API.)

### 🟢 Schnäppchen-Scanner (NEU 2026-06-13 — neu gebaut, OHNE Cardmarket)
- **`deal_scanner.py`** (täglich 06:05, nach Price-Guide-Download): cacht SIR/IR-Karten per
  TCGdex-Rarity-Endpunkt, vergleicht `low` vs. `trend` aus dem CM Price Guide und schickt die
  Top-Deals als Telegram-Nachricht. Deal-Bewertung 0–100 via `deal_scorer.py`.
- **Watchlist-Alerts** laufen jetzt ebenfalls über `deal_scanner.check_watchlist_alerts`
  (nicht mehr über die blockierte Cardmarket-API → 403-Problem umgangen).

### 🔴 Läuft nicht / kaputt
- **Scalping** (retail_monitor/hotstock): Händler-Selektoren ungetestete Platzhalter,
  Sealed-Preise fehlen → keine echten Restock-Alerts. Jobs laufen, tun nichts Nützliches.
- **Cardmarket-API**: nicht verbunden (keine Tokens). Sobald 4 Tokens in `.env`,
  schaltet Bot auf echte DE-Filterung um (`config.cardmarket_enabled()`).
- **Tote Scheduler-Jobs**: Sealed-Preis-Job (Cardmarket 403), Watchlist-Scanner (403).
  Aufräumen steht noch aus.

---

## 3. Deployment / Infrastruktur

- **Server:** Strato VPS, **IP `87.106.255.195`**, Ubuntu.
  ⚠️ **GETEILTER Server!** Dort laufen weitere Projekte:
  `sk-holzfabrik-bot`, `rookiecard-telegram-bot`, `max` (+ `max-admin`),
  `crypto-tracker`. **NIEMALS globale Befehle** (`pkill`, killall, globale
  Restarts) — nur `/opt/pokemon-tracker` und die eigenen systemd-Units anfassen.
- **SSH:** `ssh root@87.106.255.195` — passwortlos (SSH-Key auf Kevins PC).
  Claude kann direkt via `ssh root@87.106.255.195 "..."` Befehle ausführen.
- **Projektpfad auf dem Server:** `/opt/pokemon-tracker`
- **Dienste (systemd):**
  - `pokemon-tracker.service` → Bot (`venv/bin/python main.py`)
  - `pokemon-dashboard.service` → Dashboard (`gunicorn -w 2 -b 0.0.0.0:8090 dashboard.app:app`)
- **Dashboard-URL:** `http://87.106.255.195:8090` · Passwort: `.env` → `DASHBOARD_PASSWORD`

### Update-/Deploy-Workflow
1. Lokal ändern (`C:\Users\Kevin\Desktop\claude projekte\pokemon tracker`)
2. `git commit` + `git push`
3. Server: `cd /opt/pokemon-tracker && git pull && systemctl restart pokemon-tracker`
   Claude macht Schritte 2+3 direkt.

---

## 4. Architektur / wichtige Dateien

```
main.py              Einstieg: Bot + APScheduler-Jobs
bot.py               Telegram-Handler, Foto-Workflow, Buttons (on_callback)
config.py            .env laden (override=True!), Konstanten, cardmarket_enabled()
                     CM_PRICE_GUIDE_URL, CM_PRICE_GUIDE_DOWNLOAD_HOUR=6
database.py          SQLite-Schema + Zugriff (pokemon_tracker.db)
                     Tabelle cm_price_guide (NEU): 75k CM-Produktpreise
image_recognition.py Gemini (gemini-2.5-flash): card_name + card_name_en (PFLICHT)
                     + set_name + card_number + rarity + language + product_type
tcgdex.py            Name→idProduct-Mapper + Fallback-Preise
                     Zwei-Pfad-Suche: Set+Nr direkt (Pfad1) / Namenssuche strikt (Pfad2)
cm_priceguide.py     NEU: Cardmarket Price Guide Download + lokaler Lookup
                     download_and_import() / get_price(product_id) / is_ready()
pokeprice.py         ALT (pokemontcg.io) — nicht mehr genutzt, bleibt liegen
cardmarket.py        Cardmarket-API (nur aktiv wenn Tokens gesetzt, derzeit leer)
deal_scanner.py      NEU (06-13): täglicher Schnäppchen-Scanner (TCGdex+CM Price Guide, 06:05) + Watchlist-Alerts
deal_scorer.py       NEU: Deal-Bewertung 0–100 (Ersparnis 40 / Verkäufer 25 / Zustand 20 / Trend)
briefing.py          Baut die Texte fürs tägliche Briefing (09:00)
ai_chat.py           KI-Chat (Claude Haiku, Freitext-Fragen)
trend_analyzer.py    Preis-Trend-Analyse (steigend / fallend / stabil)
portfolio.py         Sammlungswert: TCGdex→CM-Price-Guide, tägl. 02:00
profit_calculator.py / scalp_targets.py /
retail_monitor.py / hotstock_monitor.py / restock_alerts.py /
release_calendar.py  → Scalping-Modul (teils nicht funktional)
dashboard/           Flask-Dashboard (app.py + templates/ + static/)
```

### Schlüssel-Entscheidungen
- **Preis-Architektur (2026-06-04):**
  - TCGdex: kostenlose API, liefert `idProduct` (Cardmarket-ID) + Fallback-Preise
  - CM Price Guide JSON: tägl. Download ohne API-Key, 75k Produkte, `low`+`trend`+`avg7`
  - Lookup-Kette: TCGdex→idProduct → lokale CM-DB → Fallback TCGdex direkt
- **TCGdex-Matching:** Kartennummer hat absoluten Vorrang. Falscher Preis ist schlimmer
  als kein Preis. Pfad1 (Set+Nr) schlägt immer Pfad2 (Name).
- **JP-Karten:** Gemini extrahiert immer englischen Namen → TCGdex EN-Lookup → idProduct OK.
- **`config.load_dotenv(override=True)`** — verhindert leere OS-Umgebungsvariable
  die `.env` überschreibt (Bug bei ANTHROPIC_API_KEY gehabt).
- **Gemini-Modell `gemini-2.5-flash`** (altes `gemini-2.0-flash-exp` → 404, abgeschaltet).
- **`.env`** enthält: Telegram-Token+ChatID, Anthropic-Key, Gemini-Key,
  Dashboard-Passwort+Secret, `CM_PRICE_GUIDE_URL`. Cardmarket-Tokens leer.
- **Geteilter Server** → niemals `pkill -f main.py` o.ä. — killt fremde Bots!

### Scheduler-Jobs (main.py)
| Job | Wann | Status |
|-----|------|--------|
| CM Price Guide Download | 06:00 | 🟢 |
| Deal-Scanner + Watchlist-Alerts | 06:05 | 🟢 NEU (TCGdex+CM Price Guide) |
| Tägliches Briefing | 09:00 | 🟢 |
| Portfolio-Bewertung | 02:00 | 🟢 TCGdex+CM-Price-Guide |
| Retail-Monitor | alle 120s | 🔴 Platzhalter-Selektoren |
| HotStock-Monitor | alle 60s | 🔴 |
| Sealed-Preise (CM) | alle 6h | 🔴 CM 403 |
| Release-Kalender | 09:05 | 🟡 |

---

## 5. Offene TODOs / nächste Schritte

1. **Kauf-Berater (Deal-Check):** Foto → Kaufpreis eingeben → „Markt X €, du zahlst
   Y € → -Z % → KAUFEN/SKIP". Echtzeit-Einkaufsberater.
2. ✅ **Sammlung lebt (ERLEDIGT):** Tägliche Bewertung via TCGdex+CM-Price-Guide.
3. ✅ **CM Price Guide (ERLEDIGT):** Tägl. Download, 75k Produkte, idProduct-Lookup.
4. ✅ **Strikteres Matching (ERLEDIGT):** Nummer dominiert, kein falscher Preis mehr.
5. **Sealed-Produkt-Sammlung:** Tins/ETBs/Displays per Foto in Sammlung aufnehmen
   (Scan funktioniert, ✅ Sammlung-Button vorhanden → testen ob es nun geht nach Bugfix).
6. **Sammlung-Extras:** Doppelte-Warnung beim Scannen, Set-Fortschritt (`8/18 SIR`),
   CSV/Excel-Export, Quick-Sell-Schätzung nach Gebühren.
7. ✅ **Watchlist-Scanner auf TCGdex/CM umgestellt (ERLEDIGT, `deal_scanner.py`)** → Schnäppchen- + Watchlist-Alerts wieder aktiv.
8. **Tote Scalping-Jobs aufräumen**: retail_monitor / hotstock_monitor / restock_alerts (Platzhalter-Selektoren).

---

## 6. Sicherheit / Gotchas
- **Secrets nur in `.env`** (gitignored). Niemals in `.py`/Templates.
- **Geteilter Server** → keine globalen Befehle (§3).
- **Telegram:** nur EINE Bot-Instanz pro Token gleichzeitig.
- Beim systemd-Neustart erscheinen „failed"-Meldungen wenn vorher Hintergrundinstanz
  per taskkill/pkill beendet wurde — das ist normal, kein Absturz.
- `CM_PRICE_GUIDE_URL` in `.env` falls Cardmarket den S3-Link ändert.
