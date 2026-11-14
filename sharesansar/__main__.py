"""Command line entry point.

Examples
--------
  python -m sharesansar prices                      # latest trading day, all scrips
  python -m sharesansar prices --date 2026-11-04
  python -m sharesansar companies                   # every listed company (all sectors)
  python -m sharesansar company NABIL --history --floorsheet --dividends
  python -m sharesansar all                         # companies + prices + every company profile
  python -m sharesansar all --history 250           # ...plus last 250 days of OHLC per scrip

Output goes to ./data/ as CSV and JSON (change with --out).
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import scrapers
from .client import SharesansarClient
from .output import write

log = logging.getLogger("sharesansar")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="sharesansar", description="Scrape NEPSE data from sharesansar.com")
    p.add_argument("--out", default="data", help="output directory (default: data)")
    p.add_argument("--delay", type=float, default=2.0, help="seconds between requests (default: 2)")
    p.add_argument("--format", choices=["csv", "json", "both"], default="both")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("prices", help="daily share prices for every traded scrip")
    sp.add_argument("--date", help="YYYY-MM-DD (default: latest trading day)")

    sc = sub.add_parser("companies", help="list of all listed companies")
    sc.add_argument("--sector", type=int, action="append", help="sector id (repeatable); default all")

    s1 = sub.add_parser("company", help="profile (and optionally history) for given symbols")
    s1.add_argument("symbols", nargs="+")
    s1.add_argument("--history", nargs="?", type=int, const=0, metavar="DAYS",
                    help="also fetch daily price history (optionally only the last DAYS rows)")
    s1.add_argument("--floorsheet", action="store_true", help="also fetch latest floorsheet")
    s1.add_argument("--dividends", action="store_true", help="also fetch dividend history")

    sa = sub.add_parser("all", help="companies + latest prices + every company profile")
    sa.add_argument("--history", type=int, default=None, metavar="DAYS",
                    help="also fetch the last DAYS of price history for each company (0 = all)")
    sa.add_argument("--dividends", action="store_true")
    sa.add_argument("--max-companies", type=int, help="limit number of profiles (for testing)")

    a = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    out = Path(a.out)
    client = SharesansarClient(delay=a.delay)

    if a.cmd == "prices":
        as_of, rows = scrapers.share_prices(client, date=a.date)
        if not rows:
            log.warning("no trading data for %s", a.date or "latest day")
            return 1
        write(rows, out / "prices" / f"prices_{as_of}", a.format)

    elif a.cmd == "companies":
        rows = scrapers.company_list(client, a.sector)
        write(rows, out / "companies", a.format)

    elif a.cmd == "company":
        _companies(client, [s.upper() for s in a.symbols], out, a.format,
                   history=a.history, floorsheet=a.floorsheet, divs=a.dividends)

    elif a.cmd == "all":
        companies = scrapers.company_list(client)
        write(companies, out / "companies", a.format)
        as_of, prices = scrapers.share_prices(client)
        write(prices, out / "prices" / f"prices_{as_of}", a.format)
        symbols = sorted({c["symbol"] for c in companies if c["symbol"]})
        if a.max_companies:
            symbols = symbols[: a.max_companies]
        _companies(client, symbols, out, a.format, history=a.history, floorsheet=False, divs=a.dividends)
    return 0


def _companies(client, symbols, out, fmt, history, floorsheet, divs) -> None:
    profiles, failed = [], []
    for i, sym in enumerate(symbols, 1):
        try:
            info = scrapers.company_info(client, sym)
        except Exception as exc:  # keep going; one bad page should not stop a 300-company run
            log.error("[%d/%d] %s: %s", i, len(symbols), sym, exc)
            failed.append(sym)
            continue
        profiles.append(info)
        log.info("[%d/%d] %s %s", i, len(symbols), sym, info.get("name", ""))
        cid = info.get("company_id")
        if history is not None and cid:
            rows = scrapers.price_history(client, cid, limit=history or None)
            write(rows, out / "history" / sym, fmt, quiet=True)
        if divs and cid:
            write(scrapers.dividends(client, cid), out / "dividends" / sym, fmt, quiet=True)
        if floorsheet:
            rows = scrapers.floorsheet(client, sym)
            date = rows[0]["date"] if rows else "latest"
            write(rows, out / "floorsheet" / f"{sym}_{date}", fmt, quiet=True)
    write(profiles, out / "company_info", fmt)
    if failed:
        log.warning("failed symbols: %s", ", ".join(failed))


if __name__ == "__main__":
    sys.exit(main())
