"""Search queries: the typed `SubitoQuery` and validation of raw params dictionaries."""
import re
from dataclasses import dataclass
from typing import Any

from subito_scanner.errors import InvalidQueryError

# region keys as echoed by the API for r=1..20
REGIONS = {
    "1": "Valle d'Aosta", "2": "Piemonte", "3": "Liguria", "4": "Lombardia", "5": "Trentino-Alto Adige", "6": "Veneto",
    "7": "Friuli-Venezia Giulia", "8": "Emilia-Romagna", "9": "Toscana", "10": "Umbria", "11": "Lazio", "12": "Marche",
    "13": "Abruzzo", "14": "Molise", "15": "Campania", "16": "Puglia", "17": "Basilicata", "18": "Calabria",
    "19": "Sardegna", "20": "Sicilia",
}
AD_TYPES = {"s": "In vendita", "g": "In regalo"}
WHOLE_EUROS = re.compile(r"[0-9]{1,7}")
ALLOWED_PARAMS = frozenset({"search_text", "category", "region", "type", "title_only", "shippable_only", "price_from", "price_to", "per_page"})
DEFAULT_PER_PAGE = 20
MAX_PER_PAGE = 50


def validate_params(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalised params for a search, or InvalidQueryError listing every problem.

    Prices are whole euros as text ("50"), because Subito filters by whole euros.
    """
    errors: list[tuple[tuple[str, ...], str]] = []
    errors += [((key,), "unknown parameter") for key in raw if key not in ALLOWED_PARAMS]

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
    per_page = raw.get("per_page", DEFAULT_PER_PAGE)
    if not isinstance(per_page, int) or isinstance(per_page, bool) or not 1 <= per_page <= MAX_PER_PAGE:
        errors.append((("per_page",), f"must be a whole number between 1 and {MAX_PER_PAGE}"))
    if text == "" and not category:
        errors.append((("search_text",), "enter a search text or a category"))
    if errors:
        raise InvalidQueryError(errors)

    params: dict[str, Any] = {"search_text": text, "type": ad_type, **flags, "per_page": per_page}
    if category:
        params["category"] = category
    if region:
        params["region"] = region
    params.update(prices)
    return params


@dataclass(frozen=True)
class SubitoQuery:
    """A Subito search. Validated on construction (raises InvalidQueryError).

    search_text: words to look for (at least this or `category`).
    category: numeric Subito category, e.g. "44" (Videogiochi).
    region: "1".."20", see REGIONS.
    ad_type: "s" for sale (default) or "g" for gifts.
    title_only: search the title only. shippable_only: only ads that can be shipped.
    price_from, price_to: whole euros. per_page: 1..50 (default 20).
    """

    search_text: str = ""
    category: str | None = None
    region: str | None = None
    ad_type: str = "s"
    title_only: bool = False
    shippable_only: bool = False
    price_from: int | None = None
    price_to: int | None = None
    per_page: int = DEFAULT_PER_PAGE

    def __post_init__(self) -> None:
        for name in ("price_from", "price_to"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
                raise InvalidQueryError([((name,), "must be a non-negative whole number of euros")])
        validate_params(self.to_params())

    def to_params(self) -> dict[str, Any]:
        """The params dictionary accepted by `validate_params` and `SubitoScanner.fetch_page`."""
        raw: dict[str, Any] = {
            "search_text": self.search_text, "type": self.ad_type, "title_only": self.title_only,
            "shippable_only": self.shippable_only, "per_page": self.per_page,
        }
        if self.category:
            raw["category"] = self.category
        if self.region:
            raw["region"] = self.region
        if self.price_from is not None:
            raw["price_from"] = str(self.price_from)
        if self.price_to is not None:
            raw["price_to"] = str(self.price_to)
        return raw

    @classmethod
    def from_params(cls, raw: dict[str, Any]) -> "SubitoQuery":
        """Build a query from a params dictionary (for example one stored by an application)."""
        params = validate_params(raw)
        return cls(
            search_text=params["search_text"], category=params.get("category"), region=params.get("region"),
            ad_type=params["type"], title_only=params["title_only"], shippable_only=params["shippable_only"],
            price_from=int(params["price_from"]) if "price_from" in params else None,
            price_to=int(params["price_to"]) if "price_to" in params else None,
            per_page=params["per_page"],
        )
