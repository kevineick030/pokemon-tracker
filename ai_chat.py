"""Claude-Haiku-Chat als Pokémon-Karten-Experte (nur Freitext).

Wirtschaftlichkeit: max_tokens=500, kein Streaming, nur für Freitext.
"""
import logging

import anthropic

import config

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "Du bist ein Experte für Pokémon-Sammelkarten und hilfst beim Tracking, "
    "Bewerten und Sammeln von SIR-, IR- und Ultra-Rare-Karten auf Cardmarket. "
    "Du kennst dich mit Sets, Seltenheiten (Rarities), Kartenzuständen (NM/EX/GD), "
    "Sprachen (DE/EN/JP) und Marktpreisen aus. Antworte präzise, freundlich und "
    "auf Deutsch. Halte dich kurz (max. ~150 Wörter), da die Antwort in Telegram "
    "angezeigt wird. Gib keine Finanzberatung als Garantie, sondern Einschätzungen."
)

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


def ask(message: str) -> str:
    """Schickt eine Freitext-Frage an Claude Haiku und gibt die Antwort zurück."""
    if not config.ANTHROPIC_API_KEY:
        return "⚠️ Kein ANTHROPIC_API_KEY konfiguriert."
    try:
        client = _get_client()
        resp = client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=config.CLAUDE_MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": message}],
        )
        parts = [block.text for block in resp.content if block.type == "text"]
        return "\n".join(parts).strip() or "🤔 (keine Antwort)"
    except Exception as exc:
        log.exception("Claude-Anfrage fehlgeschlagen")
        return f"⚠️ Fehler bei der KI-Anfrage: {exc}"
