"""Subito Scanner: read-only search of Subito.it public listings, as a library and a command line tool."""

__version__ = "0.2.0"

from subito_scanner.config import DEFAULT_CONFIG, ScannerConfig, SubitoScannerConfig  # noqa: E402
from subito_scanner.errors import InvalidQueryError, PermanentSubitoError, SubitoError, TransientSubitoError  # noqa: E402
from subito_scanner.models import SubitoListing, SubitoPage, SubitoRejected  # noqa: E402
from subito_scanner.query import AD_TYPES, REGIONS, SubitoQuery, validate_params  # noqa: E402
from subito_scanner.scanner import CONDITIONS, SEARCH_URL, SubitoScanner, normalize  # noqa: E402

__all__ = [
    "AD_TYPES", "CONDITIONS", "DEFAULT_CONFIG", "REGIONS", "SEARCH_URL",
    "InvalidQueryError", "PermanentSubitoError", "ScannerConfig", "SubitoError", "SubitoListing", "SubitoPage",
    "SubitoQuery", "SubitoRejected", "SubitoScanner", "SubitoScannerConfig", "TransientSubitoError",
    "normalize", "validate_params",
]
