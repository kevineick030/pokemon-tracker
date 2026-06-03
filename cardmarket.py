"""Cardmarket API v2.0 Client mit OAuth 1.0a (HMAC-SHA1) Authentifizierung.

Cardmarket nutzt eine "Dedicated App"-Autorisierung: App-Token/Secret +
Access-Token/Secret werden im Backend erzeugt, es gibt keinen
Callback-/Request-Token-Flow.

Wichtige Eigenheit von Cardmarket-OAuth:
  - Die "realm" im Authorization-Header ist die vollständige Request-URL
    (inkl. Query). Sie fließt NICHT in den Signature Base String ein.
  - Der Signature Base String verwendet die URL OHNE Query, plus alle
    oauth_*-Parameter UND alle Query-Parameter, alphabetisch sortiert.

Doku: https://api.cardmarket.com/ws/documentation/API_2.0:Auth_OAuth_Header
"""
import time
import uuid
import hmac
import hashlib
import base64
import logging
import urllib.parse
from statistics import median

import requests

import config

log = logging.getLogger(__name__)


class CardmarketError(Exception):
    """Fehler bei Cardmarket-API-Aufrufen."""


class CardmarketClient:
    def __init__(self):
        self.app_token = config.MKM_APP_TOKEN
        self.app_secret = config.MKM_APP_SECRET
        self.access_token = config.MKM_ACCESS_TOKEN
        self.access_token_secret = config.MKM_ACCESS_TOKEN_SECRET
        self.base_url = config.MKM_BASE_URL
        self.session = requests.Session()

    # ---------------------------------------------------------------- OAuth
    @staticmethod
    def _quote(value: str) -> str:
        """RFC 3986 prozentkodieren (oauth-konform)."""
        return urllib.parse.quote(str(value), safe="~")

    def _build_auth_header(self, method: str, url: str,
                           query_params: dict | None = None) -> str:
        """Erzeugt den OAuth-1.0a-Authorization-Header für Cardmarket.

        `url` ist die vollständige Request-URL ohne Query-String.
        `query_params` sind die Query-Parameter (werden mitsigniert).
        """
        query_params = query_params or {}

        oauth_params = {
            "oauth_consumer_key": self.app_token,
            "oauth_token": self.access_token,
            "oauth_nonce": uuid.uuid4().hex,
            "oauth_timestamp": str(int(time.time())),
            "oauth_signature_method": "HMAC-SHA1",
            "oauth_version": "1.0",
        }

        # Signature Base String: alle oauth_* + Query-Parameter, sortiert
        all_params = {**oauth_params, **{k: str(v) for k, v in query_params.items()}}
        encoded = sorted(
            (self._quote(k), self._quote(v)) for k, v in all_params.items()
        )
        param_string = "&".join(f"{k}={v}" for k, v in encoded)

        base_string = "&".join([
            method.upper(),
            self._quote(url),
            self._quote(param_string),
        ])

        signing_key = f"{self._quote(self.app_secret)}&{self._quote(self.access_token_secret)}"
        digest = hmac.new(
            signing_key.encode("utf-8"),
            base_string.encode("utf-8"),
            hashlib.sha1,
        ).digest()
        signature = base64.b64encode(digest).decode("utf-8")

        # realm = vollständige Request-URL (ohne Query), NICHT in Signatur
        header_params = {
            "realm": url,
            **oauth_params,
            "oauth_signature": signature,
        }
        header = "OAuth " + ", ".join(
            f'{k}="{self._quote(v)}"' for k, v in header_params.items()
        )
        return header

    # ---------------------------------------------------------------- HTTP
    def _request(self, method: str, path: str,
                 query_params: dict | None = None) -> dict:
        """Führt einen authentifizierten Request aus und gibt JSON zurück."""
        url = f"{self.base_url}/{path.lstrip('/')}"
        query_params = query_params or {}
        auth_header = self._build_auth_header(method, url, query_params)

        try:
            resp = self.session.request(
                method,
                url,
                params=query_params,
                headers={"Authorization": auth_header},
                timeout=30,
            )
        except requests.RequestException as exc:
            raise CardmarketError(f"Netzwerkfehler: {exc}") from exc

        if resp.status_code == 204:
            return {}
        if resp.status_code == 401:
            raise CardmarketError(
                "401 Unauthorized — OAuth-Signatur/Tokens prüfen."
            )
        if resp.status_code == 429:
            raise CardmarketError("429 — Cardmarket Rate-Limit erreicht.")
        if not resp.ok:
            raise CardmarketError(
                f"HTTP {resp.status_code} bei {path}: {resp.text[:200]}"
            )
        try:
            return resp.json()
        except ValueError:
            return {}

    # ---------------------------------------------------------------- API
    def ping(self) -> bool:
        """Prüft Verbindung + Auth über den /account-Endpunkt."""
        try:
            data = self._request("GET", "account")
            return "account" in data
        except CardmarketError as exc:
            log.error("Cardmarket-Ping fehlgeschlagen: %s", exc)
            return False

    def find_products(self, name: str, game_id: int = 6,
                      exact: bool = False) -> list[dict]:
        """Sucht Produkte nach Namen. game_id 6 = Pokémon.

        Gibt eine Liste von Produkt-Dicts zurück (idProduct, enName, ...).
        """
        params = {
            "search": name,
            "idGame": game_id,
            "exact": "true" if exact else "false",
        }
        data = self._request("GET", "products/find", params)
        products = data.get("product", [])
        if isinstance(products, dict):  # API liefert Einzeltreffer als dict
            products = [products]
        return products

    def get_product(self, product_id: int) -> dict:
        """Produktdetails inkl. priceGuide."""
        data = self._request("GET", f"products/{product_id}")
        return data.get("product", {})

    def get_wantslist(self, wantslist_id: str) -> list[dict]:
        """Karten einer Cardmarket-Wunschliste.

        Endpoint: GET /wantslist/{id}. Gibt eine flache Liste der Items mit
        idProduct + bestem verfügbaren Namen zurück.
        """
        data = self._request("GET", f"wantslist/{wantslist_id}")
        wl = data.get("wantslist", data)
        # Cardmarket verschachtelt die Items teils unterschiedlich
        items = wl.get("item") if isinstance(wl, dict) else None
        if items is None and isinstance(wl, dict):
            items = wl.get("want")
        if isinstance(items, dict):
            items = [items]
        items = items or []

        result = []
        for it in items:
            product = it.get("product", {}) or {}
            name = (
                product.get("enName")
                or product.get("locName")
                or product.get("name")
                or it.get("metaproduct", {}).get("enName")
            )
            product_id = it.get("idProduct") or product.get("idProduct")
            if name:
                result.append({"name": name, "product_id": product_id})
        return result

    def get_articles(self, product_id: int, **filters) -> list[dict]:
        """Angebote (Articles) zu einem Produkt.

        Unterstützte Filter (Cardmarket): minCondition, isFoil, isSigned,
        isAltered, minUserScore, start, maxResults, idLanguage.
        """
        params = {
            "start": 0,
            "maxResults": 100,
            **filters,
        }
        data = self._request("GET", f"articles/{product_id}", params)
        articles = data.get("article", [])
        if isinstance(articles, dict):
            articles = [articles]
        return articles


# ---------------------------------------------------------------------------
# Hilfsfunktionen für die Angebotsauswertung (DE-Filter, Reputation, Median)
# ---------------------------------------------------------------------------

# Cardmarket-Sprach-IDs
LANGUAGE_IDS = {1: "EN", 2: "FR", 3: "DE", 4: "ES", 5: "IT", 7: "JP"}

# Mapping von Verkäufer-Reputation (idReputation) auf grobe %-Werte.
# Cardmarket liefert eine 1-5-Skala; wir bilden konservativ ab.
REPUTATION_PCT = {0: 0.0, 1: 50.0, 2: 90.0, 3: 98.0, 4: 99.0, 5: 100.0}


def parse_article(article: dict) -> dict:
    """Normalisiert ein Cardmarket-Article-Dict in ein flaches Format."""
    seller = article.get("seller", {}) or {}
    addr = seller.get("address", {}) or {}
    lang = article.get("language", {}) or {}

    reputation_raw = seller.get("reputation")
    # Manche Antworten liefern bereits %-Werte, andere die 0-5-Skala.
    if isinstance(reputation_raw, (int, float)) and reputation_raw <= 5:
        reputation_pct = REPUTATION_PCT.get(int(reputation_raw), 0.0)
    elif reputation_raw is not None:
        reputation_pct = float(reputation_raw)
    else:
        reputation_pct = 0.0

    return {
        "article_id": article.get("idArticle"),
        "price": float(article.get("price", 0.0)),
        "condition": article.get("condition", "?"),
        "language": LANGUAGE_IDS.get(lang.get("idLanguage"), "?"),
        "seller_name": seller.get("username", "?"),
        "seller_country": addr.get("country", "?"),
        "seller_reputation": reputation_pct,
        "is_foil": bool(article.get("isFoil")),
        "comments": article.get("comments", ""),
    }


def filter_de_offers(articles: list[dict],
                     min_reputation: float = config.MIN_SELLER_REPUTATION
                     ) -> list[dict]:
    """Nur DE-Verkäufer mit Reputation >= min_reputation."""
    parsed = [parse_article(a) for a in articles]
    return [
        a for a in parsed
        if a["seller_country"] == config.SELLER_COUNTRY
        and a["seller_reputation"] >= min_reputation
        and a["price"] > 0
    ]


def market_median(offers: list[dict],
                  sample: int = config.MARKET_PRICE_SAMPLE_SIZE) -> float | None:
    """Median der günstigsten `sample` DE-Angebote."""
    if not offers:
        return None
    prices = sorted(o["price"] for o in offers)[:sample]
    return round(median(prices), 2)
