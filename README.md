# subito-scanner

Read-only search of [Subito.it](https://www.subito.it) public listings, as a Python library and a command line tool.

It reads the public search API (`hades.subito.it/v1/search/items`), one paced request per page, and turns each ad into a typed `SubitoListing`: price as `Decimal` with a price status, TuttoSubito shipping cost when known, item condition on a common scale, structured location, ISO date and image URLs. It keeps no state, stores nothing and sends nothing anywhere else.

The code is written from scratch: the original SubitoScanner project has no licence, so nothing is copied from it. Parameter names and fields come from real, anonymised responses (`tests/fixtures/`).

## Install

Not on PyPI. Install from git, pinned to a commit or tag:

```sh
uv tool install "git+https://github.com/Tdavide04/subito-scanner@<commit>"   # the command line tool
uv add "subito-scanner @ git+https://github.com/Tdavide04/subito-scanner@<commit>"  # as a library
```

Python 3.13 or later. The only dependency is `requests`.

## Command line

```sh
subito-scanner search "biohazard" --region 4 --price-to 25 --shippable-only --format json
```

| Option | Meaning |
| --- | --- |
| `search_text` | words to look for (optional when `--category` is given) |
| `--category N` | numeric Subito category, e.g. `44` (Videogiochi) |
| `--region 1..20` | region; `subito-scanner search --help` lists them (4 = Lombardia) |
| `--type s\|g` | `s` for sale (default), `g` gifts |
| `--title-only` | search the title only |
| `--shippable-only` | only ads that can be shipped |
| `--price-from EUR`, `--price-to EUR` | price range in whole euros |
| `--per-page 1..50` | results per page (default 20) |
| `--pages 1..10` | pages to read (default 1) |
| `--format json` | output format (JSON is the only one) |
| `--raw` | include each ad's raw API payload |
| `--log-level` | stderr log level (default `WARNING`) |

The result is one JSON document on stdout:

```json
{
  "query": {"search_text": "biohazard", "type": "s", "title_only": false, "shippable_only": true, "per_page": 20, "region": "4", "price_to": "25"},
  "pages_read": 1,
  "next_cursor": 20,
  "listings": [{"external_id": "…", "title": "…", "url": "https://www.subito.it/…", "price_status": "priced", "price_amount": "20.00", "currency": "EUR", "shipping_status": "known", "shipping_amount": "2.99", "…": "…"}],
  "rejected": []
}
```

Logs and error messages go to stderr. Exit codes:

| Code | Meaning |
| --- | --- |
| 0 | success |
| 1 | permanent error: invalid arguments, HTTP 4xx, unexpected response; retrying will not help |
| 2 | transient error: network, timeout, HTTP 5xx or 429 after the built-in retries; try again later |

## Python

```python
from subito_scanner import SubitoQuery, SubitoScanner

scanner = SubitoScanner()
query = SubitoQuery(search_text="biohazard", region="4", price_to=25, shippable_only=True)

for listing in scanner.search(query, max_pages=1):
    print(listing.title, listing.price_amount, listing.url)

for page in scanner.pages(query, max_pages=3):  # lazy, newest first, stops at the last page
    print(len(page.listings), page.next_cursor)
```

Applications that store queries as JSON can use plain dictionaries: `scanner.validate_params(raw)` returns normalised params (or raises `InvalidQueryError`), `scanner.fetch_page(params, cursor)` returns one `SubitoPage`, and `SubitoQuery.from_params(raw)` / `query.to_params()` convert between the two.

Query fields: `search_text`, `category`, `region` (`"1"`–`"20"`, see `REGIONS`), `ad_type` (`"s"`/`"g"`), `title_only`, `shippable_only`, `price_from`, `price_to` (whole euros), `per_page` (1–50). A search text or a category is required.

## Configuration

`SubitoScannerConfig` (also importable as `ScannerConfig`) holds the HTTP policy. The defaults are the policy the scanner has always used:

| Field | Default | Meaning |
| --- | --- | --- |
| `min_interval_s` | 3.0 | minimum gap between two requests |
| `timeout_s` | 30.0 | timeout of each request |
| `max_retries` | 3 | retries after the first attempt (network errors, 5xx, 429) |
| `backoff_base_s`, `backoff_cap_s` | 2.0, 60.0 | exponential backoff, capped, with jitter |
| `max_retry_after_s` | 120.0 | a longer `Retry-After` fails at once as transient |

```python
from subito_scanner import ScannerConfig, SubitoScanner

scanner = SubitoScanner(ScannerConfig(min_interval_s=5.0))
```

The command line always uses the defaults. There are no environment variables and no configuration files.

## Models

- `SubitoListing`: frozen. Required `external_id`, `title`, `url` (http/https only), `price_status` (`priced`, `free`, `on_request`, `missing`), `raw_payload`. Optional `price_amount` + `currency`, `description`, `images`, `location` (`region`, `province`, `city`), `seller_external_id`, `seller_name`, `condition` (`new`, `like_new`, `good`, `fair`, `poor`, `not_applicable`) with the original label in `condition_raw`, `published_at` (timezone-aware), `shipping_status` (`known`, `unknown`, `pickup_only`) + `shipping_amount`, `fees_amount` (always `None`: search results carry no buyer fee). Gift ads are `free`, never a zero price; vehicles have `not_applicable` condition. `to_dict()` gives the JSON form, amounts as strings.
- `SubitoPage`: `listings`, `rejected`, `next_cursor` (offset of the next page, `None` on the last), `newest_first`.
- `SubitoRejected`: an ad without the minimum data for a listing (`external_id` when known, `reason`). It is reported, not raised.

## Errors

All derive from `SubitoError`.

- `InvalidQueryError` (also a `ValueError`): invalid query, raised before any request; `.errors` lists `(location, message)` pairs.
- `TransientSubitoError`: network error, timeout, 5xx, or 429 after the built-in retries, or a `Retry-After` above `max_retry_after_s`.
- `PermanentSubitoError`: 4xx, non-JSON body, or a response without an `ads` list (the API may have changed or blocked the request). `.status` is the HTTP status when there is one.

## Limits

- It depends on an undocumented public API that can change or stop answering at any time; a change shows up as `PermanentSubitoError`.
- Results are what the search returns: up to 50 per page, sorted newest first. Only the filters listed above are supported.
- Prices are filtered in whole euros, as Subito does.
- No detail pages, no seller pages, no messages, no login.

## Rate limiting

One `SubitoScanner` instance paces its own requests: at least `min_interval_s` (3 s) between two requests, retries included, with exponential backoff and jitter on network errors and 5xx, and `Retry-After` honoured on 429. Use one instance per process; several instances do not coordinate. `--pages 3` takes at least 6 seconds.

## Responsible use

This tool reads public search results, slowly, without logging in. Respect Subito's terms of use and keep the request rate low: do not lower `min_interval_s`, do not run many instances in parallel, and do not use it to collect personal data about sellers. It does not get past CAPTCHAs or bot checks and is not meant to; if Subito blocks requests, stop.

## Development

```sh
uv sync
uv run ruff check .
uv run mypy
uv run pytest
uv build
```

Tests use real, anonymised responses and a fake transport and clock (`subito_scanner.testing`); they never touch the network.

## Licence

MIT, see [LICENSE](LICENSE). The HTTP client and the test helpers are adapted from [pch-marketplace-kit](https://github.com/Tdavide04/pch-marketplace-kit) (MIT, Copyright (c) 2026 Tdavide04).
