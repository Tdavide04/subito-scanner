"""Public types: SubitoListing validation and JSON form, SubitoQuery, config and errors."""
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from subito_scanner import (
    InvalidQueryError,
    PermanentSubitoError,
    ScannerConfig,
    SubitoError,
    SubitoListing,
    SubitoPage,
    SubitoQuery,
    SubitoScannerConfig,
    TransientSubitoError,
)

BASE = {"external_id": "1", "title": "Resident Evil", "url": "https://www.subito.it/a-1.htm", "price_status": "missing", "raw_payload": {}}


def listing(**changes):
    return SubitoListing(**{**BASE, **changes})


def test_minimal_listing():
    item = listing()
    assert item.shipping_status == "unknown" and item.images == [] and item.fees_amount is None


@pytest.mark.parametrize(
    "changes",
    [
        {"external_id": ""},
        {"title": "  "},
        {"url": "javascript:alert(1)"},
        {"images": ["data:image/png;base64,xx"]},
        {"price_status": "priced"},  # no amount
        {"price_status": "free", "price_amount": Decimal("1.00"), "currency": "EUR"},
        {"price_status": "priced", "price_amount": Decimal("1.001"), "currency": "EUR"},
        {"price_status": "priced", "price_amount": Decimal("-1"), "currency": "EUR"},
        {"price_status": "priced", "price_amount": Decimal("1")},  # no currency
        {"shipping_status": "known"},
        {"shipping_status": "unknown", "shipping_amount": Decimal("2.99")},
        {"condition": "mint"},
        {"published_at": datetime(2026, 11, 21, 12, 0)},  # naive
        {"price_status": "cheap"},
    ],
)
def test_invalid_listings_are_refused(changes):
    with pytest.raises(ValueError):
        listing(**changes)


def test_listing_is_frozen():
    with pytest.raises(FrozenInstanceError):
        listing().title = "x"  # type: ignore[misc]


def test_to_dict_uses_strings_for_money_and_iso_dates():
    item = listing(
        price_status="priced", price_amount=Decimal("140.00"), currency="EUR", shipping_status="known",
        shipping_amount=Decimal("2.99"), published_at=datetime(2026, 11, 21, 13, 43, tzinfo=UTC), raw_payload={"urn": "x"},
    )
    data = item.to_dict()
    assert data["price_amount"] == "140.00" and data["shipping_amount"] == "2.99" and data["fees_amount"] is None
    assert data["published_at"] == "2026-11-21T13:43:00+00:00"
    assert "raw_payload" not in data
    assert item.to_dict(include_raw=True)["raw_payload"] == {"urn": "x"}


def test_page_defaults():
    page = SubitoPage(listings=[])
    assert page.rejected == [] and page.next_cursor is None and page.newest_first is True


def test_query_to_params_and_back():
    query = SubitoQuery(search_text="biohazard", region="4", price_to=25, shippable_only=True)
    params = query.to_params()
    assert params == {"search_text": "biohazard", "type": "s", "title_only": False, "shippable_only": True, "per_page": 20, "region": "4", "price_to": "25"}
    assert SubitoQuery.from_params(params) == query


def test_query_from_stored_params_normalises_text():
    assert SubitoQuery.from_params({"search_text": " resident evil ", "price_from": "5"}) == SubitoQuery(search_text="resident evil", price_from=5)


@pytest.mark.parametrize(
    "kwargs",
    [{}, {"search_text": "x", "region": "21"}, {"search_text": "x", "price_from": -1}, {"search_text": "x", "price_from": 50, "price_to": 5},
     {"search_text": "x", "per_page": 51}, {"search_text": "x", "ad_type": "u"}, {"category": "abc"}],
)
def test_invalid_queries_are_refused(kwargs):
    with pytest.raises(InvalidQueryError) as exc:
        SubitoQuery(**kwargs)
    assert exc.value.errors


def test_category_alone_is_a_valid_query():
    assert SubitoQuery(category="44").to_params()["category"] == "44"


def test_config_defaults_and_alias():
    assert ScannerConfig is SubitoScannerConfig
    assert SubitoScannerConfig().min_interval_s == 3.0


@pytest.mark.parametrize("changes", [{"min_interval_s": -1}, {"timeout_s": 0}, {"max_retries": -1}])
def test_invalid_config_is_refused(changes):
    with pytest.raises(ValueError):
        SubitoScannerConfig(**changes)


def test_error_hierarchy():
    assert issubclass(InvalidQueryError, SubitoError) and issubclass(InvalidQueryError, ValueError)
    assert issubclass(TransientSubitoError, SubitoError) and issubclass(PermanentSubitoError, SubitoError)
    assert PermanentSubitoError("x", status=404).status == 404
    assert str(InvalidQueryError([(("region",), "bad")])) == "region: bad"
