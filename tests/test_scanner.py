"""Subito scanner (clean-room, ADR-0011): params, normalization of real anonymized pages (generic item, car, gift),
offset paging, pages()/search() and errors with a fake transport. No network."""
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from subito_scanner import (
    REGIONS,
    InvalidQueryError,
    PermanentSubitoError,
    SubitoQuery,
    SubitoScanner,
    SubitoScannerConfig,
    TransientSubitoError,
    normalize,
)
from subito_scanner.testing import FakeSession, FakeTime, response

FAST = SubitoScannerConfig(min_interval_s=0, timeout_s=5, max_retries=0, backoff_base_s=0, backoff_cap_s=0, max_retry_after_s=0)

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


SEARCH, VEHICLE, GIFT = load("search_page"), load("vehicle_page"), load("gift_page")


def adapter(outcomes, config=FAST) -> tuple[SubitoScanner, FakeSession]:
    time = FakeTime()
    session = FakeSession(time, outcomes)
    return SubitoScanner(config=config, session=session, clock=time.clock, sleep=time.sleep), session


# --- params -----------------------------------------------------------------------------------

def test_params_defaults():
    assert SubitoScanner().validate_params({"search_text": " resident evil "}) == {
        "search_text": "resident evil", "type": "s", "title_only": False, "shippable_only": False, "per_page": 20,
    }


def test_full_params():
    params = SubitoScanner().validate_params({
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
    with pytest.raises(InvalidQueryError) as exc:
        SubitoScanner().validate_params(raw)
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
    assert len(page.listings) == 5 and page.newest_first is True
    assert page.next_cursor == 20  # count_all is far larger


def test_offset_cursor_and_last_page():
    last = {**SEARCH, "count_all": 25}
    subito, session = adapter([response(200, last)])
    page = subito.fetch_page(subito.validate_params({"search_text": "x"}), 20)
    assert session.calls[0]["params"]["start"] == 20
    assert page.next_cursor is None  # 20 + 20 >= 25


def test_unexpected_schema_is_permanent_and_errors_propagate():
    subito, _ = adapter([response(200, {"error": "nope"})])
    with pytest.raises(PermanentSubitoError, match="ads"):
        subito.fetch_page(subito.validate_params({"search_text": "x"}), None)
    subito, _ = adapter([response(503)])
    with pytest.raises(TransientSubitoError):
        subito.fetch_page(subito.validate_params({"search_text": "x"}), None)


def test_malformed_ads_become_rejected():
    broken = {**SEARCH, "ads": [SEARCH["ads"][0], {"urn": "x", "subject": ""}, "nope"]}
    subito, _ = adapter([response(200, broken)])
    page = subito.fetch_page(subito.validate_params({"search_text": "x"}), None)
    assert len(page.listings) == 1 and len(page.rejected) == 2


def test_capabilities():
    assert SubitoScanner.capabilities == frozenset({"description", "images", "location", "seller", "condition", "published_at"})


# --- pages() and search() ---------------------------------------------------------------------

def test_pages_follow_the_offset_and_stop_at_max_pages():
    subito, session = adapter([response(200, SEARCH), response(200, SEARCH)])
    pages = list(subito.pages(SubitoQuery(search_text="x", per_page=5), max_pages=2))
    assert len(pages) == 2
    assert [call["params"]["start"] for call in session.calls] == [0, 5]
    assert all(call["params"]["lim"] == 5 for call in session.calls)


def test_pages_stop_at_the_last_page():
    subito, session = adapter([response(200, {**SEARCH, "count_all": 3})])
    pages = list(subito.pages(SubitoQuery(search_text="x"), max_pages=3))
    assert len(pages) == 1 and pages[0].next_cursor is None and len(session.calls) == 1


def test_pages_are_lazy():
    subito, session = adapter([response(200, SEARCH)])
    iterator = subito.pages(SubitoQuery(search_text="x"))
    assert session.calls == []
    next(iterator)
    assert len(session.calls) == 1


def test_pages_rejects_zero_max_pages():
    subito, _ = adapter([])
    with pytest.raises(ValueError):
        list(subito.pages(SubitoQuery(search_text="x"), max_pages=0))


def test_search_defaults_to_one_page_and_returns_listings():
    subito, session = adapter([response(200, SEARCH)])
    listings = subito.search(SubitoQuery(search_text="biohazard", region="4", price_to=25, shippable_only=True))
    assert len(session.calls) == 1
    assert session.calls[0]["params"] == {"q": "biohazard", "r": "4", "t": "s", "shp": "true", "pe": "25", "lim": 20, "start": 0, "sort": "datedesc"}
    assert [listing.external_id for listing in listings] == [normalize(ad).external_id for ad in SEARCH["ads"]]


def test_search_returns_each_ad_once_across_pages():
    subito, _ = adapter([response(200, SEARCH), response(200, SEARCH)])
    listings = subito.search(SubitoQuery(search_text="x", per_page=5), max_pages=2)
    assert len(listings) == len(SEARCH["ads"])


def test_search_propagates_transient_errors():
    subito, _ = adapter([response(503)])
    with pytest.raises(TransientSubitoError):
        subito.search(SubitoQuery(search_text="x"))


def test_default_config_is_the_historic_policy():
    scanner = SubitoScanner()
    assert scanner.config == SubitoScannerConfig(min_interval_s=3.0, timeout_s=30.0, max_retries=3, backoff_base_s=2.0, backoff_cap_s=60.0, max_retry_after_s=120.0)


def test_request_headers_are_unchanged():
    subito, session = adapter([response(200, SEARCH)])
    subito.search(SubitoQuery(search_text="x"))
    assert session.calls[0]["headers"] == {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
        "Accept": "application/json",
        "X-Subito-Channel": "web",
    }
    assert session.calls[0]["timeout"] == 5
