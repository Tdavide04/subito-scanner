"""Command line: `subito-scanner search ...` prints one JSON document on stdout; logs go to stderr.

Exit codes: 0 success, 1 permanent error (invalid arguments, HTTP 4xx, unexpected response),
2 transient error (network, timeout, 5xx, 429 after the client's own retries). Nothing is stored and
nothing is sent anywhere else.
"""
import argparse
import json
import logging
import sys
from collections.abc import Callable, Sequence
from typing import Any, NoReturn

from subito_scanner import __version__
from subito_scanner.errors import InvalidQueryError, PermanentSubitoError, TransientSubitoError
from subito_scanner.query import MAX_PER_PAGE, REGIONS, SubitoQuery
from subito_scanner.scanner import SubitoScanner

EXIT_OK, EXIT_PERMANENT, EXIT_TRANSIENT = 0, 1, 2
MAX_PAGES = 10

logger = logging.getLogger("subito_scanner.cli")


class _Parser(argparse.ArgumentParser):
    """argparse exits with 2 on usage errors; here 2 means "try again later", so usage errors exit with 1."""

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(EXIT_PERMANENT, f"{self.prog}: error: {message}\n")


def _whole_euros(value: str) -> int:
    if not value.isdigit():
        raise argparse.ArgumentTypeError("whole euros, e.g. 25")
    return int(value)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="subito-scanner", description="Read-only search of Subito.it public listings.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)

    search = commands.add_parser(
        "search",
        help="search listings and print them as JSON",
        description="Search Subito.it listings, newest first, and print them as one JSON document on stdout.",
        epilog=(
            "Regions: " + ", ".join(f"{key} {name}" for key, name in REGIONS.items()) + ". "
            "Exit codes: 0 ok, 1 permanent error, 2 transient error. "
            "Requests are at least 3 seconds apart; --pages 3 takes at least 6 seconds."
        ),
    )
    search.add_argument("search_text", nargs="?", default="", help="words to look for (optional when --category is given)")
    search.add_argument("--category", help="numeric Subito category, e.g. 44 (Videogiochi)")
    search.add_argument("--region", choices=list(REGIONS), metavar="1..20", help="region number, see the list below")
    search.add_argument("--type", dest="ad_type", choices=["s", "g"], default="s", help="s: for sale (default), g: gifts")
    search.add_argument("--title-only", action="store_true", help="search the title only")
    search.add_argument("--shippable-only", action="store_true", help="only ads that can be shipped")
    search.add_argument("--price-from", type=_whole_euros, metavar="EUR", help="minimum price, whole euros")
    search.add_argument("--price-to", type=_whole_euros, metavar="EUR", help="maximum price, whole euros")
    search.add_argument("--per-page", type=int, default=20, metavar=f"1..{MAX_PER_PAGE}", help="results per page (default 20)")
    search.add_argument("--pages", type=int, default=1, metavar=f"1..{MAX_PAGES}", help="pages to read (default 1)")
    search.add_argument("--format", choices=["json"], default="json", help="output format (default json)")
    search.add_argument("--raw", action="store_true", help="include each ad's raw API payload")
    search.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default="WARNING", help="stderr log level (default WARNING)")
    return parser


def main(argv: Sequence[str] | None = None, scanner_factory: Callable[[], SubitoScanner] = SubitoScanner) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(stream=sys.stderr, level=args.log_level, format="%(levelname)s %(name)s: %(message)s", force=True)
    if not 1 <= args.pages <= MAX_PAGES:
        return _fail(EXIT_PERMANENT, f"--pages must be between 1 and {MAX_PAGES}")
    try:
        query = SubitoQuery(
            search_text=args.search_text, category=args.category, region=args.region, ad_type=args.ad_type,
            title_only=args.title_only, shippable_only=args.shippable_only, price_from=args.price_from,
            price_to=args.price_to, per_page=args.per_page,
        )
    except InvalidQueryError as exc:
        return _fail(EXIT_PERMANENT, f"invalid search: {exc}")

    scanner = scanner_factory()
    listings: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen: set[str] = set()
    pages_read, next_cursor = 0, None
    try:
        for page in scanner.pages(query, max_pages=args.pages):
            pages_read += 1
            next_cursor = page.next_cursor
            for listing in page.listings:
                if listing.external_id not in seen:
                    seen.add(listing.external_id)
                    listings.append(listing.to_dict(include_raw=args.raw))
            rejected += [{"external_id": r.external_id, "reason": r.reason} for r in page.rejected]
    except TransientSubitoError as exc:
        return _fail(EXIT_TRANSIENT, str(exc))
    except PermanentSubitoError as exc:
        return _fail(EXIT_PERMANENT, str(exc))

    document = {
        "query": query.to_params(),
        "pages_read": pages_read,
        "next_cursor": next_cursor,
        "listings": listings,
        "rejected": rejected,
    }
    json.dump(document, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return EXIT_OK


def _fail(code: int, message: str) -> int:
    logger.error(message)
    return code

