"""Subito adapter (clean-room, ADR-0011): params, normalization of real anonymized pages (generic item, car, gift),
offset paging and errors with a fake transport. No network."""
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from pch_marketplace_kit.adapter import InvalidParams, PermanentMarketplaceError, TransientMarketplaceError
from subito_scanner import REGIONS, SubitoAdapter, normalize
from pch_marketplace_kit.testing import FakeSession, FakeTime, response
from pch_marketplace_kit.http import HttpPolicy

FAST = HttpPolicy(min_interval_s=0, timeout_s=5, max_retries=0, backoff_base_s=0, backoff_cap_s=0, max_retry_after_s=0)

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


SEARCH, VEHICLE, GIFT = load("search_page"), load("vehicle_page"), load("gift_page")


def adapter(outcomes) -> tuple[SubitoAdapter, FakeSession]:
    time = FakeTime()
    session = FakeSession(time, outcomes)
    return SubitoAdapter(policy=FAST, session=session, clock=time.clock, sleep=time.sleep), session


# --- params -----------------------------------------------------------------------------------

def test_params_defaults():
    assert SubitoAdapter().validate_params({"search_text": " resident evil "}) == {
        "search_text": "resident evil", "type": "s", "title_only": False, "shippable_only": False, "per_page": 20,
    }


def test_full_params():
    params = SubitoAdapter().validate_params({
        "search_text": "resident evil", "category": "44", "region": "6", "type": "s", "title_only": True,
        "shippable_only": True, "price_from": "5", "price_to": "50", "per_page": 30,
    })
    assert params["region"] == "6" and params["price_from"] == "5" and params["title_only"] is True


@pytest.mark.parametrize(
    ("raw", "loc"),
    [
        ({}, ("search_text",)),
        ({"search_text": "x", "region": "21"}, ("region",)),
        ({"search_text": "x", "category": "abc"}, ("category",)),
        ({"search_text": "x", "type": "u"}, ("type",)),
        ({"search_text": "x", "title_only": "yes"}, ("title_only",)),
        ({"search_text": "x", "price_from": "5.50"}, ("price_from",)),  # Subito filters by whole euros
        ({"search_text": "x", "price_from": "50", "price_to": "5"}, ("price_to",)),
        ({"search_text": "x", "per_page": 0}, ("per_page",)),
        ({"search_text": "x", "since": "2026-01-01"}, ("since",)),
    ],
)
def test_invalid_params(raw, loc):
    with pytest.raises(InvalidParams) as exc:
        SubitoAdapter().validate_params(raw)
    assert loc in [error_loc for error_loc, _ in exc.value.errors]


def test_regions_table_is_complete():
    assert len(REGIONS) == 20 and REGIONS["6"] == "Veneto" and REGIONS["20"] == "Sicilia"


# --- normalization ----------------------------------------------------------------------------

def test_generic_item_normalises_with_shipping_condition_location_and_date():
    candidate = normalize(SEARCH["ads"][0])
    assert candidate.external_id == "900000001"
    assert candidate.title == "PlayStation 4"
    assert candidate.url == "https://www.subito.it/videogiochi/annuncio-di-esempio-900000001.htm"
    assert (candidate.price_status, candidate.price_amount, candidate.currency) == ("priced", Decimal("140.00"), "EUR")
    assert (candidate.shipping_status, candidate.shipping_amount, candidate.fees_amount) == ("known", Decimal("2.99"), None)
    assert (candidate.condition, candidate.condition_raw) == ("like_new", "Come nuovo - perfetto o ricondizionato")
    assert candidate.location == {"region": "Veneto", "province": "Vicenza", "city": "Schio"}
    assert candidate.published_at == datetime(2026, 11, 21, 13, 43, 55, 133000, tzinfo=UTC)
    assert candidate.images == ["https://images.example.test/subito/1?rule=gallery-desktop-1x-auto"]
    assert (candidate.seller_external_id, candidate.seller_name) == ("501", None)
    assert candidate.description == "Descrizione di esempio dell'annuncio 1."
    assert candidate.raw_payload == SEARCH["ads"][0]


def test_every_real_item_normalises():
    for ad in SEARCH["ads"] + VEHICLE["ads"] + GIFT["ads"]:
        normalize(ad)


def test_car_has_no_applicable_condition_and_no_shipping():
    candidate = normalize(VEHICLE["ads"][0])
    assert candidate.condition == "not_applicable"
    assert candidate.shipping_status == "pickup_only"
    assert candidate.price_status == "priced" and candidate.price_amount > 1000


def test_gift_ads_are_free_not_zero_priced():
    for ad in GIFT["ads"]:  # one has no price, one says "0 €"
        candidate = normalize(ad)
        assert (candidate.price_status, candidate.price_amount) == ("free", None)


@pytest.mark.parametrize(
    ("features", "expected"),
    [
        ([], ("unknown", None)),
        ([{"uri": "/item_shipping_allowed", "values": [{"key": "1", "value": "Sì"}]}], ("unknown", None)),  # shippable, cost not given
        ([{"uri": "/item_shipping_allowed", "values": [{"key": "0", "value": "No"}]}], ("pickup_only", None)),
        ([{"uri": "/item_shipping_allowed", "values": [{"key": "1"}]}, {"uri": "/item_shipping_cost_tuttosubito", "values": [{"key": "0,99"}]}], ("known", Decimal("0.99"))),
    ],
)
def test_shipping_states(features, expected):
    ad = {**SEARCH["ads"][0], "features": [f for f in SEARCH["ads"][0]["features"] if f["uri"] == "/price"] + features}
    candidate = normalize(ad)
    assert (candidate.shipping_status, candidate.shipping_amount) == expected


def test_item_without_price_is_missing():
    ad = {**SEARCH["ads"][0], "features": [f for f in SEARCH["ads"][0]["features"] if f["uri"] != "/price"]}
    assert normalize(ad).price_status == "missing"


# --- acquisition with a fake transport --------------------------------------------------------

def test_first_page_sends_hades_params_and_pages_by_offset():
    subito, session = adapter([response(200, SEARCH)])
    params = subito.validate_params({"search_text": "resident evil", "category": "44", "region": "6", "title_only": True, "shippable_only": True, "price_to": "50"})

    page = subito.fetch_page(params, None)

    (call,) = session.calls
    assert call["url"] == "https://hades.subito.it/v1/search/items"
    assert call["params"] == {"q": "resident evil", "c": "44", "r": "6", "t": "s", "qso": "true", "shp": "true", "pe": "50", "lim": 20, "start": 0, "sort": "datedesc"}
    assert call["headers"]["X-Subito-Channel"] == "web"
    assert len(page.candidates) == 5 and page.newest_first is True
    assert page.next_cursor == 20  # count_all is far larger


def test_offset_cursor_and_last_page():
    last = {**SEARCH, "count_all": 25}
    subito, session = adapter([response(200, last)])
    page = subito.fetch_page(subito.validate_params({"search_text": "x"}), 20)
    assert session.calls[0]["params"]["start"] == 20
    assert page.next_cursor is None  # 20 + 20 >= 25


def test_unexpected_schema_is_permanent_and_errors_propagate():
    subito, _ = adapter([response(200, {"error": "nope"})])
    with pytest.raises(PermanentMarketplaceError, match="ads"):
        subito.fetch_page(subito.validate_params({"search_text": "x"}), None)
    subito, _ = adapter([response(503)])
    with pytest.raises(TransientMarketplaceError):
        subito.fetch_page(subito.validate_params({"search_text": "x"}), None)


def test_malformed_ads_become_rejected():
    broken = {**SEARCH, "ads": [SEARCH["ads"][0], {"urn": "x", "subject": ""}, "nope"]}
    subito, _ = adapter([response(200, broken)])
    page = subito.fetch_page(subito.validate_params({"search_text": "x"}), None)
    assert len(page.candidates) == 1 and len(page.rejected) == 2


def test_capabilities():
    assert SubitoAdapter.capabilities == frozenset({"description", "images", "location", "seller", "condition", "published_at"})
