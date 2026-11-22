"""The adapter honours the pch-marketplace-kit contract and is discoverable through its entry point."""
import json
from importlib.metadata import entry_points
from pathlib import Path

from pch_marketplace_kit import ENTRY_POINT_GROUP, discover_adapters
from pch_marketplace_kit.http import HttpPolicy
from pch_marketplace_kit.testing import FakeSession, FakeTime, assert_adapter_shape, assert_valid_page, response
from subito_scanner import SubitoAdapter, create_adapter

FIXTURES = Path(__file__).parent / "fixtures"
FAST = HttpPolicy(min_interval_s=0, timeout_s=5, max_retries=0, backoff_base_s=0, backoff_cap_s=0, max_retry_after_s=0)


def test_shape():
    assert_adapter_shape(create_adapter({}))


def test_entry_point_builds_the_adapter():
    (entry,) = [e for e in entry_points(group=ENTRY_POINT_GROUP) if e.name == "subito"]
    assert isinstance(discover_adapters({}, [entry])["subito"], SubitoAdapter)


def test_every_fixture_page_is_valid():
    for path in sorted(FIXTURES.glob("*.json")):
        time = FakeTime()
        adapter = SubitoAdapter(policy=FAST, session=FakeSession(time, [response(200, json.loads(path.read_text()))]), clock=time.clock, sleep=time.sleep)
        page = adapter.fetch_page(adapter.validate_params({"search_text": "x"}), None)
        assert page.candidates, path.name
        assert_valid_page(page, adapter.capabilities)
