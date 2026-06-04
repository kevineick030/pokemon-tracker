"""Bilderkennung von Pokémon-Karten via Google Gemini.

Modell: gemini-2.0-flash-exp. Nimmt ein Karten-Foto und liefert
strukturierte JSON-Daten (Name, Set, Nummer, Rarity, Sprache, Zustand,
Confidence).
"""
import json
import logging

import config

log = logging.getLogger(__name__)

PROMPT = (
    "Analysiere dieses Pokémon-Bild (Einzelkarte ODER versiegeltes Produkt). "
    "Gib NUR JSON zurück:\n"
    "{\n"
    '  "card_name": "exakter Name wie auf der Karte (deutsch, englisch oder japanisch)",\n'
    '  "card_name_en": "PFLICHT: offizieller englischer Name, IMMER ausfuellen - auch bei JP-Karten! '
    '(Glurak->Charizard, Glurak-ex->Charizard ex, フリーザー->Articuno). Niemals leer lassen.",\n'
    '  "set_name": "Set-Name (englisch bevorzugt, z.B. Obsidian Flames statt Obsidianflammen)",\n'
    '  "card_number": "z.B. 201/165 (leer bei versiegelten Produkten)",\n'
    '  "rarity": "Special Illustration Rare / Illustration Rare / Ultra Rare / Double Rare / andere",\n'
    '  "language": "DE / EN / JP / KO / other",\n'
    '  "condition_estimate": "NM / EX / GD",\n'
    '  "product_type": "single_card / display / etb / bundle / collection / tin / box / other",\n'
    '  "confidence": 0.0-1.0\n'
    "}\n"
    "product_type-Hinweise: 'display' = 36er-Booster-Display, 'etb' = Elite Trainer Box, "
    "'tin' = Metalldose, 'box' = sonstige Box, 'bundle' = Booster-Bundle, "
    "'collection' = Collection/Premium-Box, 'single_card' = einzelne Karte.\n"
    "WICHTIG fuer die Zuordnung — sehr sorgfaeltig lesen:\n"
    "- card_name_en: Bei japanischen Karten ist der englische Name IMMER bekannt "
    "(alle JP-Pokemon haben offizielle EN-Namen). Aus dem Artwork/Typ ableiten, "
    "nicht leer lassen. Beispiele: フシギダネ->Bulbasaur, ピカチュウ->Pikachu, "
    "リザードンex->Charizard ex.\n"
    "- card_number: die Sammler-Nummer steht meist UNTEN (z.B. '121/165', "
    "'201/091', 'TG12/TG30'). Genau abtippen, nicht raten.\n"
    "- set_name: am Set-Symbol/Logo erkennen; englischen Set-Namen bevorzugen "
    "fuer bessere Datenbanksuche; wenn unsicher, leer lassen statt zu raten.\n"
    "- rarity: anhand des Stils bestimmen (Special Illustration Rare = volles "
    "Artwork ueber die ganze Karte; Illustration Rare = Artwork-Hintergrund; "
    "Ultra Rare = glaenzend/Full-Art ex; Double Rare = normales ex). Seltenheit "
    "ist wichtig fuer den Wert — lieber genau hinsehen.\n"
    "Bei Fehler: {\"error\": \"nicht erkennbar\"}"
)

# Versiegelte Produkttypen (für Scalp-Tracking relevant)
SEALED_TYPES = {"display", "etb", "bundle", "collection", "tin", "box"}

# Schlüsselwörter zur Heuristik, wenn product_type fehlt oder aus Freitext kommt
_SEALED_KEYWORDS = {
    "display": ["display", "36er", "booster box", "boosterbox"],
    "etb": ["elite trainer box", "etb", "top-trainer-box", "top trainer box"],
    "tin": ["tin", "dose", "pokébox tin"],
    "bundle": ["bundle", "booster bundle", "6er", "build & battle", "build and battle"],
    "collection": ["collection", "kollektion", "premium", "ex box", "v box",
                   "blister", "sammler"],
    "box": ["box"],
}


def is_sealed(product_type: str | None) -> bool:
    """True, wenn der Produkttyp ein versiegeltes Produkt ist."""
    return (product_type or "").strip().lower() in SEALED_TYPES


def guess_product_type(text: str | None) -> str:
    """Heuristik: leitet den Produkttyp aus einem Frei-/Namenstext ab.

    Wird genutzt, wenn Gemini kein product_type liefert oder bei den
    command-basierten Flows (/preis, /add), wo kein Bild vorliegt.
    """
    t = (text or "").lower()
    # spezifische vor generischen Treffern prüfen (z.B. "etb" vor "box")
    for ptype in ("display", "etb", "tin", "bundle", "collection", "box"):
        for kw in _SEALED_KEYWORDS[ptype]:
            if kw in t:
                return ptype
    return "single_card"

_configured = False


def _ensure_configured() -> bool:
    """Konfiguriert das Gemini-SDK einmalig. False, wenn kein Key gesetzt."""
    global _configured
    if not config.GEMINI_API_KEY:
        return False
    if not _configured:
        import google.generativeai as genai
        genai.configure(api_key=config.GEMINI_API_KEY)
        _configured = True
    return True


def _extract_json(text: str) -> dict:
    """Robustes JSON-Parsing: entfernt Markdown-Fences und Schnörkel."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # ```json ... ``` oder ``` ... ```
        cleaned = cleaned.split("```", 2)
        cleaned = cleaned[1] if len(cleaned) > 1 else text
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    cleaned = cleaned.strip()
    # ersten { bis letzten } isolieren
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1:
        cleaned = cleaned[start:end + 1]
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # einfache Reparatur: einfache Anführungszeichen -> doppelte
        try:
            return json.loads(cleaned.replace("'", '"'))
        except json.JSONDecodeError:
            log.warning("Gemini-Antwort nicht parsebar: %s", text[:300])
            return {"error": "nicht erkennbar"}


def recognize(image_path: str) -> dict:
    """Analysiert ein Karten-Foto und gibt das strukturierte Ergebnis zurück.

    Rückgabe entweder mit den Erkennungsfeldern oder {'error': ...}.
    """
    if not _ensure_configured():
        return {"error": "GEMINI_API_KEY nicht konfiguriert"}

    try:
        import google.generativeai as genai
        with open(image_path, "rb") as f:
            image_bytes = f.read()

        # MIME aus den Magic-Bytes ableiten (Telegram=JPEG, andere Quellen=PNG)
        mime = "image/png" if image_bytes[:8].startswith(b"\x89PNG") else "image/jpeg"

        model = genai.GenerativeModel(config.GEMINI_MODEL)
        response = model.generate_content([
            PROMPT,
            {"mime_type": mime, "data": image_bytes},
        ])
        result = _extract_json(response.text or "")
    except Exception as exc:
        log.exception("Gemini-Bilderkennung fehlgeschlagen")
        return {"error": f"Erkennungsfehler: {exc}"}

    if "error" in result:
        return result

    # Confidence normalisieren (0-1)
    try:
        conf = float(result.get("confidence", 0))
        result["confidence"] = max(0.0, min(1.0, conf))
    except (TypeError, ValueError):
        result["confidence"] = 0.0

    # Englischen Namen absichern (für die Preissuche auf pokemontcg.io)
    if not result.get("card_name_en"):
        result["card_name_en"] = result.get("card_name", "")

    # product_type absichern: fehlt er, per Heuristik aus Name/Set ableiten
    ptype = (result.get("product_type") or "").strip().lower()
    if ptype not in (SEALED_TYPES | {"single_card", "other"}):
        ptype = guess_product_type(
            f"{result.get('card_name', '')} {result.get('set_name', '')}"
        )
    result["product_type"] = ptype

    return result
