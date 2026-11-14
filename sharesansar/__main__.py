"""Command line entry point.

Examples
--------
  python -m sharesansar prices                      # latest trading day, all scrips
  python -m sharesansar prices --date 2026-11-04
  python -m sharesansar companies                   # every listed company (all sectors)
  python -m sharesansar company NABIL --history --floorsheet --dividends
  python -m sharesansar all                         # companies + prices + every company profile
  python -m sharesansar all --history 250           # ...plus last 250 days of OHLC per scrip
  python -m sharesansar corporate-actions           # mergers, merged cos., right issues, dividends
  python -m sharesansar company NICA --adjusted     # bonus/right-adjusted price history
  python -m sharesansar datasheet                   # full price history, one CSV per company, by sector
  python -m sharesansar market                      # NEPSE index + sub-indices, gold/silver, NRB forex

Output goes to ./data/ as CSV and JSON (change with --out).
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import adjust, datasheet, markets, scrapers
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
    s1.add_argument("--rights", action="store_true", help="also fetch right share history")
    s1.add_argument("--adjusted", action="store_true",
                    help="price history adjusted for bonus and right shares (implies --history, "
                         "--dividends, --rights; combine with --history N to limit days)")
    s1.add_argument("--adjust-cash", action="store_true", help="with --adjusted, also adjust for cash dividends")

    sx = sub.add_parser("corporate-actions",
                        help="mergers & acquisitions, merged companies, all right issues, dividends")
    sx.add_argument("--dividend-years", type=int, default=None, metavar="N",
                    help="only the latest N fiscal years of dividends (default: all)")

    sd = sub.add_parser("datasheet", help="full price history for every company: one CSV per company in "
                                          "sector folders, plus a combined CSV and an index")
    sd.add_argument("--sector", type=int, action="append", help="sector id (repeatable); default all")
    sd.add_argument("--symbols", nargs="+", help="only these symbols")
    sd.add_argument("--no-adjusted", action="store_true", help="skip bonus/right-adjusted columns")
    sd.add_argument("--resume", action="store_true", help="keep company CSVs that already exist")
    sd.add_argument("--max-companies", type=int, help="limit number of companies (for testing)")

    sm = sub.add_parser("market", help="NEPSE index and sub-indices history, gold/silver, NRB forex rates")
    sm.add_argument("--no-indices", action="store_true")
    sm.add_argument("--no-gold", action="store_true")
    sm.add_argument("--no-forex", action="store_true")
    sm.add_argument("--forex-from", default="2000-01-01",
                    help="first date for NRB forex on the first run (later runs continue from the last date)")

    sa = sub.add_parser("all", help="companies + latest prices + every company profile")
    sa.add_argument("--history", type=int, default=None, metavar="DAYS",
                    help="also fetch the last DAYS of price history for each company (0 = all)")
    sa.add_argument("--dividends", action="store_true", help="per-company dividend history")
    sa.add_argument("--rights", action="store_true", help="per-company right share history")
    sa.add_argument("--adjusted", action="store_true", help="bonus/right-adjusted history per company")
    sa.add_argument("--dividend-years", type=int, default=3, metavar="N",
                    help="fiscal years of market-wide dividends to fetch (default 3, 0 = all)")
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
                   history=a.history, floorsheet=a.floorsheet, divs=a.dividends, rights=a.rights,
                   adjusted=a.adjusted, adjust_cash=a.adjust_cash)

    elif a.cmd == "datasheet":
        datasheet.build(client, out, sectors=a.sector, symbols=a.symbols, adjusted=not a.no_adjusted,
                        resume=a.resume, max_companies=a.max_companies)

    elif a.cmd == "market":
        if not a.no_indices:
            markets.all_indices(client, out)
        if not a.no_gold:
            markets.update_gold_silver(client, out)
        if not a.no_forex:
            markets.update_forex(out, start=a.forex_from, delay=a.delay)

    elif a.cmd == "corporate-actions":
        _corporate_actions(client, out, a.format, a.dividend_years)

    elif a.cmd == "all":
        companies = scrapers.company_list(client)
        write(companies, out / "companies", a.format)
        as_of, prices = scrapers.share_prices(client)
        write(prices, out / "prices" / f"prices_{as_of}", a.format)
        symbols = sorted({c["symbol"] for c in companies if c["symbol"]})
        if a.max_companies:
            symbols = symbols[: a.max_companies]
        _corporate_actions(client, out, a.format, a.dividend_years or None)
        _companies(client, symbols, out, a.format, history=a.history, floorsheet=False, divs=a.dividends,
                   rights=a.rights, adjusted=a.adjusted, adjust_cash=False)
    return 0


def _corporate_actions(client, out, fmt, dividend_years) -> None:
    ca = out / "corporate_actions"
    write(scrapers.mergers(client), ca / "mergers_acquisitions", fmt)
    write(scrapers.merged_companies(client), ca / "merged_companies", fmt)
    write(scrapers.right_shares(client), ca / "right_shares", fmt)
    write(scrapers.all_dividends(client, years=dividend_years), ca / "dividends", fmt)


def _companies(client, symbols, out, fmt, history, floorsheet, divs, rights=False, adjusted=False,
               adjust_cash=False) -> None:
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
        if not cid:
            log.warning("%s: no company id on page, skipping history/dividends/rights", sym)
        hist = div_rows = right_rows = None
        if (history is not None or adjusted) and cid:
            hist = scrapers.price_history(client, cid, limit=history or None)
            write(hist, out / "history" / sym, fmt, quiet=True)
        if (divs or adjusted) and cid:
            div_rows = scrapers.dividends(client, cid, sym)
            write(div_rows, out / "dividends" / sym, fmt, quiet=True)
        if (rights or adjusted) and cid:
            right_rows = scrapers.right_shares(client, cid)
            for r in right_rows:
                r["symbol"] = sym
            write(right_rows, out / "right_shares" / sym, fmt, quiet=True)
        if adjusted and hist is not None:
            face = info.get("paid_up") if isinstance(info.get("paid_up"), (int, float)) else 100.0
            events = adjust.corporate_events(div_rows or [], right_rows or [], include_cash=adjust_cash,
                                             face_value=face)
            adj = adjust.adjust_history(hist, events)
            write(adj, out / "adjusted" / sym, fmt, quiet=True)
        if floorsheet:
            rows = scrapers.floorsheet(client, sym)
            date = rows[0]["date"] if rows else "latest"
            write(rows, out / "floorsheet" / f"{sym}_{date}", fmt, quiet=True)
    write(profiles, out / "company_info", fmt)
    if failed:
        log.warning("failed symbols: %s", ", ".join(failed))


if __name__ == "__main__":
    sys.exit(main())
