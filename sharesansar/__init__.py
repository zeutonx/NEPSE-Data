"""ShareSansar (sharesansar.com) scraper for NEPSE company and market data."""
from .client import SharesansarClient
from . import scrapers

__all__ = ["SharesansarClient", "scrapers"]
