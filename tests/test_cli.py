"""Command line, with the scanner on a fake transport: JSON on stdout, logs on stderr, exit codes. No network."""
import json
from pathlib import Path

import pytest
import requests

from subito_scanner import SubitoScanner, SubitoScannerConfig
from subito_scanner.cli import main
from subito_scanner.testing import FakeSession, FakeTime, response

FAST = SubitoScannerConfig(min_interval_s=0, timeout_s=5, max_retries=1, backoff_base_s=0, backoff_cap_s=0, max_retry_after_s=0)
SEARCH = json.loads((Path(__file__).parent / "fixtures" / "search_page.json").read_text())


def factory(outcomes):
    time = FakeTime()
    session = FakeSession(time, outcomes)
    return (lambda: SubitoScanner(config=FAST, session=session, clock=time.clock, sleep=time.sleep)), session


def test_search_prints_json_on_stdout(capsys):
    make, session = factory([response(200, SEARCH)])
    code = main(["search", "biohazard", "--region", "4", "--price-to", "25", "--shippable-only", "--format", "json"], scanner_factory=make)
    out, err = capsys.readouterr()
    assert code == 0 and err == ""
    document = json.loads(out)
    assert document["query"] == {"search_text": "biohazard", "type": "s", "title_only": False, "shippable_only": True, "per_page": 20, "region": "4", "price_to": "25"}
    assert document["pages_read"] == 1 and document["next_cursor"] == 20
    assert len(document["listings"]) == 5 and document["rejected"] == []
    first = document["listings"][0]
    assert first["external_id"] == "900000001" and first["price_amount"] == "140.00" and "raw_payload" not in first
    assert session.calls[0]["params"] == {"q": "biohazard", "r": "4", "t": "s", "shp": "true", "pe": "25", "lim": 20, "start": 0, "sort": "datedesc"}


def test_raw_includes_payload_and_pages_follow_offsets(capsys):
    make, session = factory([response(200, SEARCH), response(200, SEARCH)])
    assert main(["search", "x", "--pages", "2", "--per-page", "5", "--raw"], scanner_factory=make) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["pages_read"] == 2
    assert len(document["listings"]) == 5  # the same ads on both pages are printed once
    assert document["listings"][0]["raw_payload"] == SEARCH["ads"][0]
    assert [c["params"]["start"] for c in session.calls] == [0, 5]


def test_transient_error_exits_2_with_nothing_on_stdout(capsys):
    make, _ = factory([response(503), response(503)])
    assert main(["search", "x"], scanner_factory=make) == 2
    out, err = capsys.readouterr()
    assert out == "" and "503" in err


def test_network_error_exits_2(capsys):
    make, _ = factory([requests.ConnectionError("down")] * 2)
    assert main(["search", "x"], scanner_factory=make) == 2
    assert capsys.readouterr().out == ""


def test_permanent_error_exits_1(capsys):
    make, _ = factory([response(403)])
    assert main(["search", "x"], scanner_factory=make) == 1
    out, err = capsys.readouterr()
    assert out == "" and "403" in err


def test_unexpected_schema_exits_1(capsys):
    make, _ = factory([response(200, {"error": "nope"})])
    assert main(["search", "x"], scanner_factory=make) == 1


@pytest.mark.parametrize(
    "argv",
    [["search"], ["search", "x", "--region", "21"], ["search", "x", "--price-to", "2.5"], ["search", "x", "--per-page", "0"],
     ["search", "x", "--pages", "0"], ["search", "x", "--price-from", "50", "--price-to", "5"], ["search", "x", "--format", "csv"],
     ["search", "x", "--since", "2026"], []],
)
def test_invalid_arguments_exit_1_without_requests(argv, capsys):
    make, session = factory([])
    with pytest.raises(SystemExit) as exc:
        code = main(argv, scanner_factory=make)
        raise SystemExit(code)
    assert exc.value.code == 1
    assert session.calls == [] and capsys.readouterr().out == ""


@pytest.mark.parametrize("argv", [["--help"], ["search", "--help"], ["--version"]])
def test_help_and_version_exit_0(argv, capsys):
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 0
    assert "subito-scanner" in capsys.readouterr().out
