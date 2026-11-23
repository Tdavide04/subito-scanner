"""Subito scanner, written from scratch (Personal Collection Hub ADR-0011): the upstream SubitoScanner has no licence, so no code is
taken from it. Everything here comes from observed behaviour of the public search API
(`hades.subito.it/v1/search/items`): parameter names confirmed by the `filters` echo of real responses,
fields read from real (anonymized) responses in tests/fixtures.

Read-only: one GET per page, paced by `RateLimitedHttp`. No login, no cookies kept on purpose, no CAPTCHA handling.
"""
from collections.abc import Callable, Iterator
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from subito_scanner.config import DEFAULT_CONFIG, SubitoScannerConfig
from subito_scanner.errors import PermanentSubitoError
from subito_scanner.http import RateLimitedHttp
from subito_scanner.models import SubitoListing, SubitoPage, SubitoRejected
from subito_scanner.query import SubitoQuery, validate_params

SEARCH_URL = "https://hades.subito.it/v1/search/items"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
    "Accept": "application/json",
    "X-Subito-Channel": "web",
}
# /item_condition keys: 10 Nuovo, 20 Come nuovo, 30 Ottimo, 40 Buono, 50 Danneggiato
CONDITIONS = {"10": "new", "20": "like_new", "30": "good", "40": "fair", "50": "poor"}
VEHICLES_MACROCATEGORY = "1"  # Motori: an item condition does not apply
IMAGE_RULE = "?rule=gallery-desktop-1x-auto"  # the CDN base URL alone answers 400
# what a listing from this scanner can carry besides the required fields
CAPABILITIES = frozenset({"description", "images", "location", "seller", "condition", "published_at"})


class SubitoScanner:
    """Searches Subito.it. One instance per process: its HTTP client paces every request it makes.

    `pages()` and `search()` take a `SubitoQuery`; `validate_params()` and `fetch_page()` work on plain
    params dictionaries, for applications that store queries as JSON.
    """

    marketplace = "subito"
    capabilities = CAPABILITIES

    def __init__(
        self,
        config: SubitoScannerConfig = DEFAULT_CONFIG,
        session: Any | None = None,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
        wall_clock: Callable[[], datetime] | None = None,
        jitter: Callable[[float], float] | None = None,
    ):
        options: dict[str, Any] = {
            name: value
            for name, value in (("clock", clock), ("sleep", sleep), ("wall_clock", wall_clock), ("jitter", jitter))
            if value is not None
        }
        self.config = config
        self._http = RateLimitedHttp(config, session=session, **options)

    def validate_params(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Normalised params, or InvalidQueryError listing every problem."""
        return validate_params(raw)

    def fetch_page(self, params: dict[str, Any], cursor: int | None) -> SubitoPage:
        """One page of results for already validated params; `cursor` is the offset from the previous page."""
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
            raise PermanentSubitoError("subito: response without an 'ads' list (the API may have changed or blocked the request)")
        listings, rejected = [], []
        for raw in ads:
            try:
                listings.append(normalize(raw))
            except (ValueError, TypeError, KeyError, InvalidOperation, AttributeError, IndexError) as exc:
                rejected.append(SubitoRejected(external_id=_list_id(raw) if isinstance(raw, dict) else None, reason=str(exc)))
        total = data.get("count_all")
        next_start = start + params["per_page"]
        more = isinstance(total, int) and next_start < total and len(ads) > 0
        return SubitoPage(listings=listings, rejected=rejected, next_cursor=next_start if more else None, newest_first=True)

    def pages(self, query: SubitoQuery, max_pages: int = 3) -> Iterator[SubitoPage]:
        """Pages newest first, stopping after `max_pages` or at the last page. Requests are made lazily."""
        if max_pages < 1:
            raise ValueError("max_pages must be at least 1")
        params = validate_params(query.to_params())
        cursor: int | None = None
        for _ in range(max_pages):
            page = self.fetch_page(params, cursor)
            yield page
            if page.next_cursor is None:
                return
            cursor = page.next_cursor

    def search(self, query: SubitoQuery, max_pages: int = 1) -> list[SubitoListing]:
        """Listings from up to `max_pages` pages, newest first, each ad once (ads can move between pages)."""
        seen: set[str] = set()
        listings: list[SubitoListing] = []
        for page in self.pages(query, max_pages=max_pages):
            for listing in page.listings:
                if listing.external_id not in seen:
                    seen.add(listing.external_id)
                    listings.append(listing)
        return listings


def _list_id(ad: dict[str, Any]) -> str | None:
    urn = ad.get("urn")
    if not isinstance(urn, str) or not urn:
        return None
    return urn.rsplit(":list:", 1)[-1] if ":list:" in urn else urn


def _feature(ad: dict[str, Any], uri: str) -> dict[str, Any] | None:
    for feature in ad.get("features") or []:
        if isinstance(feature, dict) and feature.get("uri") == uri and feature.get("values"):
            return feature["values"][0]  # type: ignore[no-any-return]
    return None


def _amount(value: dict[str, Any] | None) -> Decimal | None:
    if not value or value.get("key") in (None, ""):
        return None
    key = str(value["key"])
    # "2,99" uses the Italian decimal comma (thousands dots dropped); "140" or "12.5" are plain numbers
    text = key.replace(".", "").replace(",", ".") if "," in key else key
    return Decimal(text).quantize(Decimal("0.01"))


def normalize(ad: Any) -> SubitoListing:
    """One search result → SubitoListing. Gift ads are free (never a zero price); a missing price is missing."""
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
    condition: str | None
    condition_raw: str | None
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

    return SubitoListing(
        external_id=_list_id(ad),  # type: ignore[arg-type]  # None is rejected by SubitoListing
        title=ad.get("subject"),  # type: ignore[arg-type]
        url=(ad.get("urls") or {}).get("default"),  # type: ignore[arg-type]
        price_status="free" if gift else "priced" if price is not None else "missing",
        price_amount=price,
        currency="EUR" if price is not None else None,
        description=ad.get("body") or None,
        images=images,
        location=location,
        seller_external_id=str(advertiser["user_id"]) if advertiser.get("user_id") else None,
        seller_name=advertiser.get("name") or None,
        condition=condition,  # type: ignore[arg-type]
        condition_raw=condition_raw,
        published_at=published,
        shipping_status=shipping_status,  # type: ignore[arg-type]
        shipping_amount=shipping_amount,
        raw_payload=ad,
    )
