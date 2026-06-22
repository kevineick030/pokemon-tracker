# 🟩 START HIER — Pokémon Tracker (Bot)

> Gesamtübersicht aller Projekte: `Desktop/claude projekte/00_START-HIER.md`

## Was ist das?
Telegram-Bot, der Pokémon-Karten auf Cardmarket trackt: Schnäppchen-Scanner,
Portfolio, Budget, tägliches Briefing, Foto-Erkennung. (Details in `README.md`.)

## Wo liegt was?
| | |
|---|---|
| **Lokaler Ordner** | `claude projekte/pokemon-tracker` (früher „pokemon tracker" mit Leerzeichen) |
| **GitHub** | `kevineick030/pokemon-tracker` |
| **Läuft auf** | Strato-Server `87.106.255.195` (Python-Bot) |
| **Deployment-Muster** | 🟩 Bot — Push zu `main` → Server zieht automatisch |

## Wie ändere ich etwas & bringe es live?
1. Code ändern.
2. Mit **GitHub Desktop** committen + pushen (Branch **`main`**).
3. Server aktualisiert sich automatisch.

## Wo ändere ich Einstellungen?
- Zugänge/Keys: **`.env`** (Vorlage: `.env.example`). Hier stehen Telegram-Token,
  Cardmarket-API, Anthropic- und Gemini-Keys.
- Daten liegen lokal/Server in **`pokemon_tracker.db`** (SQLite-Datenbank).

## Häufige Probleme
- **Bot meldet sich nicht** → Läuft der Dienst auf dem Server? Per SSH prüfen.
- **Keine Deals** → API-Keys in `.env` korrekt? Watchlist gefüllt?
