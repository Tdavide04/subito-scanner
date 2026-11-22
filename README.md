# subito-scanner

Subito.it adapter for [Personal Collection Hub](https://github.com/Tdavide04/personal-collection-hub), built on the [pch-marketplace-kit](https://github.com/Tdavide04/pch-marketplace-kit) contract.

It reads the public search API (`hades.subito.it/v1/search/items`) read-only, through the kit's rate-limited HTTP client, and turns each ad into a `ListingCandidate`: price as `Decimal` with its price status, TuttoSubito shipping cost when known, item condition on the canonical scale, structured location, ISO date and CDN images. It keeps no state and stores nothing.

The code is written from scratch: the original SubitoScanner has no licence, so nothing is copied from it (PCH ADR-0011). Parameter names and fields come from real, anonymised responses (`tests/fixtures/`).

## Use

The hub discovers it through the `pch.marketplaces` entry point (`subito`). On its own:

```python
from subito_scanner import SubitoAdapter

adapter = SubitoAdapter()
params = adapter.validate_params({"search_text": "resident evil", "region": "4", "shippable_only": True})
page = adapter.fetch_page(params, None)  # next page: adapter.fetch_page(params, page.next_cursor)
```

Query params: `search_text`, `category`, `region` (1–20), `type` (`s` for sale, `g` gift), `title_only`, `shippable_only`, `price_from`, `price_to` (whole euros), `per_page`.

## Development

```sh
uv sync
uv run pytest
```

Licensed under MIT.
