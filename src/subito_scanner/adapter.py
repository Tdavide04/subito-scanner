"""Subito adapter, written from scratch (Personal Collection Hub ADR-0011): the upstream SubitoScanner has no licence, so no code is
taken from it. Everything here comes from observed behaviour of the public search API
(`hades.subito.it/v1/search/items`): parameter names confirmed by the `filters` echo of real responses,
fields read from real (anonymized) responses in tests/fixtures.
"""
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from pch_marketplace_kit.domain import ListingCandidate
from pch_marketplace_kit.adapter import InvalidParams, Page, PermanentMarketplaceError, Rejected
from pch_marketplace_kit.http import HttpPolicy, MarketplaceHttp

SEARCH_URL = "https://hades.subito.it/v1/search/items"
DEFAULT_POLICY = HttpPolicy(min_interval_s=3.0, timeout_s=30.0, max_retries=3, backoff_base_s=2.0, backoff_cap_s=60.0, max_retry_after_s=120.0)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
    "Accept": "application/json",
    "X-Subito-Channel": "web",
}
# region keys as echoed by the API for r=1..20
REGIONS = {
    "1": "Valle d'Aosta", "2": "Piemonte", "3": "Liguria", "4": "Lombardia", "5": "Trentino-Alto Adige", "6": "Veneto",
    "7": "Friuli-Venezia Giulia", "8": "Emilia-Romagna", "9": "Toscana", "10": "Umbria", "11": "Lazio", "12": "Marche",
    "13": "Abruzzo", "14": "Molise", "15": "Campania", "16": "Puglia", "17": "Basilicata", "18": "Calabria",
    "19": "Sardegna", "20": "Sicilia",
}
AD_TYPES = {"s": "In vendita", "g": "In regalo"}
# /item_condition keys: 10 Nuovo, 20 Come nuovo, 30 Ottimo, 40 Buono, 50 Danneggiato
CONDITIONS = {"10": "new", "20": "like_new", "30": "good", "40": "fair", "50": "poor"}
VEHICLES_MACROCATEGORY = "1"  # Motori: an item condition does not apply (ADR-0005)
IMAGE_RULE = "?rule=gallery-desktop-1x-auto"  # the CDN base URL alone answers 400
WHOLE_EUROS = re.compile(r"[0-9]{1,7}")


class SubitoAdapter:
    marketplace = "subito"
    capabilities = frozenset({"description", "images", "location", "seller", "condition", "published_at"})

    def __init__(self, policy: HttpPolicy = DEFAULT_POLICY, session: Any | None = None, **http_options: Any):
        self._http = MarketplaceHttp("subito", policy, session=session, **http_options)

    def validate_params(self, raw: dict[str, Any]) -> dict[str, Any]:
        errors: list[tuple[tuple[str, ...], str]] = []
        allowed = {"search_text", "category", "region", "type", "title_only", "shippable_only", "price_from", "price_to", "per_page"}
        errors += [((key,), "unknown parameter") for key in raw if key not in allowed]

        text = raw.get("search_text", "")
        text = text.strip() if isinstance(text, str) else None
        if text is None:
            errors.append((("search_text",), "must be text"))
        category = str(raw.get("category", "")).strip()
        if category and not category.isdigit():
            errors.append((("category",), "must be a numeric Subito category"))
        region = str(raw.get("region", "")).strip()
        if region and region not in REGIONS:
            errors.append((("region",), "must be one of the 20 Italian regions"))
        ad_type = raw.get("type", "s")
        if ad_type not in AD_TYPES:
            errors.append((("type",), "must be 's' (in vendita) or 'g' (in regalo)"))
        flags = {}
        for key in ("title_only", "shippable_only"):
            value = raw.get(key, False)
            if not isinstance(value, bool):
                errors.append(((key,), "must be true or false"))
            flags[key] = value is True
        prices = {}
        for key in ("price_from", "price_to"):
            value = raw.get(key)
            if value in (None, ""):
                continue
            if not isinstance(value, str) or not WHOLE_EUROS.fullmatch(value):
                errors.append(((key,), 'whole euros as text, e.g. "50"'))
            else:
                prices[key] = value
        if "price_from" in prices and "price_to" in prices and int(prices["price_from"]) > int(prices["price_to"]):
            errors.append((("price_to",), "must not be lower than price_from"))
        per_page = raw.get("per_page", 20)
        if not isinstance(per_page, int) or isinstance(per_page, bool) or not 1 <= per_page <= 50:
            errors.append((("per_page",), "must be a whole number between 1 and 50"))
        if text == "" and not category:
            errors.append((("search_text",), "enter a search text or a category"))
        if errors:
            raise InvalidParams(errors)

        params: dict[str, Any] = {"search_text": text, "type": ad_type, **flags, "per_page": per_page}
        if category:
            params["category"] = category
        if region:
            params["region"] = region
        params.update(prices)
        return params

    def fetch_page(self, params: dict[str, Any], cursor: Any | None) -> Page:
        start = cursor or 0
        query: dict[str, Any] = {}
        for name, key in (("search_text", "q"), ("category", "c"), ("region", "r"), ("type", "t")):
            if params.get(name):
                query[key] = params[name]
        if params.get("title_only"):
            query["qso"] = "true"
        if params.get("shippable_only"):
            query["shp"] = "true"
        for name, key in (("price_from", "ps"), ("price_to", "pe")):
            if params.get(name):
                query[key] = params[name]
        query.update({"lim": params["per_page"], "start": start, "sort": "datedesc"})

        data = self._http.get_json(SEARCH_URL, params=query, headers=HEADERS)
        ads = data.get("ads") if isinstance(data, dict) else None
        if not isinstance(ads, list):
            raise PermanentMarketplaceError("subito: response without an 'ads' list (the API may have changed or blocked the request)")
        candidates, rejected = [], []
        for raw in ads:
            try:
                candidates.append(normalize(raw))
            except (ValueError, TypeError, KeyError, InvalidOperation, AttributeError, IndexError) as exc:
                rejected.append(Rejected(external_id=_list_id(raw) if isinstance(raw, dict) else None, reason=str(exc)))
        total = data.get("count_all")
        next_start = start + params["per_page"]
        more = isinstance(total, int) and next_start < total and len(ads) > 0
        return Page(candidates=candidates, rejected=rejected, next_cursor=next_start if more else None, newest_first=True)


def _list_id(ad: dict[str, Any]) -> str | None:
    urn = ad.get("urn")
    if not isinstance(urn, str) or not urn:
        return None
    return urn.rsplit(":list:", 1)[-1] if ":list:" in urn else urn


def _feature(ad: dict[str, Any], uri: str) -> dict[str, Any] | None:
    for feature in ad.get("features") or []:
        if isinstance(feature, dict) and feature.get("uri") == uri and feature.get("values"):
            return feature["values"][0]
    return None


def _amount(value: dict[str, Any] | None) -> Decimal | None:
    if not value or value.get("key") in (None, ""):
        return None
    key = str(value["key"])
    # "2,99" uses the Italian decimal comma (thousands dots dropped); "140" or "12.5" are plain numbers
    text = key.replace(".", "").replace(",", ".") if "," in key else key
    return Decimal(text).quantize(Decimal("0.01"))


def normalize(ad: Any) -> ListingCandidate:
    """One search result → Listing Candidate. Gift ads are free (never a zero price); a missing price is missing."""
    if not isinstance(ad, dict):
        raise ValueError("ad is not a JSON object")
    gift = (ad.get("type") or {}).get("key") == "g"
    price = None if gift else _amount(_feature(ad, "/price"))

    shippable = _feature(ad, "/item_shipping_allowed") or _feature(ad, "/item_shippable")
    cost = _amount(_feature(ad, "/item_shipping_cost_tuttosubito"))
    if shippable is not None and str(shippable.get("key")) == "0":
        shipping_status, shipping_amount = "pickup_only", None
    elif cost is not None:
        shipping_status, shipping_amount = "known", cost
    elif shippable is None and (ad.get("category") or {}).get("macrocategory_id") == VEHICLES_MACROCATEGORY:
        shipping_status, shipping_amount = "pickup_only", None
    else:
        shipping_status, shipping_amount = "unknown", None

    category = ad.get("category") or {}
    condition_value = _feature(ad, "/item_condition")
    if category.get("macrocategory_id") == VEHICLES_MACROCATEGORY:
        condition, condition_raw = "not_applicable", None
    else:
        condition = CONDITIONS.get(str(condition_value.get("key"))) if condition_value else None
        condition_raw = condition_value.get("value") if condition_value else None

    geo = ad.get("geo") or {}
    location = {
        level: geo[source]["value"]
        for level, source in (("region", "region"), ("province", "city"), ("city", "town"))
        if isinstance(geo.get(source), dict) and geo[source].get("value")
    } or None
    dates = ad.get("dates") or {}
    published = datetime.fromisoformat(dates["display_iso8601"]) if dates.get("display_iso8601") else None
    advertiser = ad.get("advertiser") or {}
    images = [f"{img['cdn_base_url']}{IMAGE_RULE}" for img in ad.get("images") or [] if isinstance(img, dict) and img.get("cdn_base_url")]

    return ListingCandidate(
        external_id=_list_id(ad),
        title=ad.get("subject"),
        url=(ad.get("urls") or {}).get("default"),
        price_status="free" if gift else "priced" if price is not None else "missing",
        price_amount=price,
        currency="EUR" if price is not None else None,
        description=ad.get("body") or None,
        images=images,
        location=location,
        seller_external_id=str(advertiser["user_id"]) if advertiser.get("user_id") else None,
        seller_name=advertiser.get("name") or None,
        condition=condition,
        condition_raw=condition_raw,
        published_at=published,
        shipping_status=shipping_status,
        shipping_amount=shipping_amount,
        raw_payload=ad,
    )
