"""Subito.it adapter for Personal Collection Hub: the public search API, read-only."""
from collections.abc import Mapping

from subito_scanner.adapter import AD_TYPES, CONDITIONS, DEFAULT_POLICY, REGIONS, SEARCH_URL, SubitoAdapter, normalize

__version__ = "0.1.0"

__all__ = ["AD_TYPES", "CONDITIONS", "DEFAULT_POLICY", "REGIONS", "SEARCH_URL", "SubitoAdapter", "create_adapter", "normalize"]


def create_adapter(settings: Mapping[str, str]) -> SubitoAdapter:
    """Entry point factory (`pch.marketplaces`): Subito needs no settings."""
    return SubitoAdapter()
