"""Full-day floorsheet: every trade of every traded scrip, one file per trading day.

ShareSansar only serves the floorsheet of the latest trading day, and only per scrip (the
market-wide /floorsheet listing comes back empty without a company filter). So a day is
collected by fetching each scrip that traded that day (from the day's price table), which is
about one request per scrip (~300 on a normal day, ~10 minutes at 2 s per request).

  data/floorsheet/daily/<YYYY>/floorsheet_<date>.csv.gz   every trade that day
  data/floorsheet/summary.csv                             one row per day and scrip

Trade files columns:
  date, symbol, contract_no, trade_seq, buyer_broker, seller_broker, quantity, rate, amount

The site gives no trade times. Within one scrip, contract numbers rise through the day, so
`trade_seq` (1, 2, 3, ... per scrip, ordered by contract_no) is the order the trades happened.

summary.csv compares each scrip's trade count with the `transactions` column of the price
table, so an incomplete scrip shows up as complete = False.
"""
from __future__ import annotations

import csv
import gzip
import io
import logging
from pathlib import Path
from typing import Any

from . import scrapers
from .client import SharesansarClient

log = logging.getLogger(__name__)

TRADE_COLUMNS = ["date", "symbol", "contract_no", "trade_seq", "buyer_broker", "seller_broker",
                 "quantity", "rate", "amount"]
SUMMARY_COLUMNS = ["date", "symbol", "trades", "price_table_trades", "complete", "quantity", "amount",
                   "vwap", "first_rate", "last_rate", "high", "low", "buyer_brokers", "seller_brokers"]


def day_path(out: Path, date: str) -> Path:
    return out / "floorsheet" / "daily" / date[:4] / f"floorsheet_{date}.csv.gz"


def _contract_key(row: dict[str, Any]) -> tuple[int, str]:
    c = str(row.get("contract_no") or "")
    return (int(c), c) if c.isdigit() else (0, c)


def order_trades(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort by symbol then contract number, drop repeated contracts, number trades per scrip."""
    seen: set[tuple[str, str]] = set()
    ordered: list[dict[str, Any]] = []
    for r in sorted(rows, key=lambda r: (r["symbol"], _contract_key(r))):
        key = (r["symbol"], str(r["contract_no"]))
        if key in seen:
            continue
        seen.add(key)
        ordered.append(r)
    seq: dict[str, int] = {}
    for r in ordered:
        seq[r["symbol"]] = seq.get(r["symbol"], 0) + 1
        r["trade_seq"] = seq[r["symbol"]]
    return ordered


def summarize(trades: list[dict[str, Any]], prices: list[dict[str, Any]], date: str) -> list[dict[str, Any]]:
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for t in trades:
        by_symbol.setdefault(t["symbol"], []).append(t)
    expected = {p["symbol"]: p.get("transactions") for p in prices if p.get("symbol")}
    rows = []
    for sym in sorted(set(by_symbol) | set(expected)):
        ts = by_symbol.get(sym, [])  # already in trade order
        qty = sum(t["quantity"] or 0 for t in ts)
        amt = sum(t["amount"] or 0 for t in ts)
        rates = [t["rate"] for t in ts if t["rate"] is not None]
        exp = expected.get(sym)
        rows.append({
            "date": date, "symbol": sym, "trades": len(ts), "price_table_trades": exp,
            "complete": (len(ts) == exp) if isinstance(exp, (int, float)) else None,
            "quantity": qty, "amount": round(amt, 2), "vwap": round(amt / qty, 4) if qty else None,
            "first_rate": rates[0] if rates else None, "last_rate": rates[-1] if rates else None,
            "high": max(rates) if rates else None, "low": min(rates) if rates else None,
            "buyer_brokers": len({t["buyer_broker"] for t in ts}),
            "seller_brokers": len({t["seller_broker"] for t in ts}),
        })
    return rows


def write_day(path: Path, trades: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    # mtime=0 keeps the gzip bytes identical for identical data, so re-runs make no git diff
    with open(tmp, "wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as gz, \
            io.TextIOWrapper(gz, encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=TRADE_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(trades)
    tmp.replace(path)


def read_day(path: Path) -> list[dict[str, str]]:
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def update_summary(out: Path, rows: list[dict[str, Any]]) -> None:
    path = out / "floorsheet" / "summary.csv"
    old: list[dict[str, Any]] = []
    if path.exists():
        with open(path, newline="", encoding="utf-8") as f:
            old = list(csv.DictReader(f))
    days = {r["date"] for r in rows}
    merged = [r for r in old if r["date"] not in days] + rows
    merged.sort(key=lambda r: (r["date"], r["symbol"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".csv.tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=SUMMARY_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(merged)
    tmp.replace(path)


def collect_day(client: SharesansarClient, out: Path, force: bool = False,
                symbols: list[str] | None = None) -> int:
    """Fetch the latest trading day's full floorsheet. Returns a process exit code."""
    as_of, prices = scrapers.share_prices(client)
    if not as_of or not prices:
        log.warning("no price table for the latest day; nothing to collect")
        return 0
    path = day_path(out, as_of)
    if symbols:  # a sample run must never stand in for the full day
        path = path.with_name(f"floorsheet_{as_of}_sample.csv.gz")
    elif path.exists() and not force:
        log.info("floorsheet for %s already saved (%s); use --force to fetch again", as_of, path)
        return 0
    traded = [p for p in prices if p.get("symbol") and (p.get("transactions") or p.get("volume"))]
    if symbols:
        wanted = {s.upper() for s in symbols}
        traded = [p for p in traded if p["symbol"] in wanted]
    log.info("collecting floorsheet for %s: %d traded scrips", as_of, len(traded))

    trades: list[dict[str, Any]] = []
    failed: list[str] = []
    stale: list[str] = []
    for i, p in enumerate(traded, 1):
        sym = p["symbol"]
        try:
            rows = scrapers.floorsheet(client, sym)
        except Exception as exc:  # one scrip failing should not lose the rest of the day
            log.error("[%d/%d] %s: %s", i, len(traded), sym, exc)
            failed.append(sym)
            continue
        same_day = [r for r in rows if r["date"] == as_of]
        if rows and not same_day:
            stale.append(sym)
        trades.extend(same_day)
        log.debug("[%d/%d] %s: %d trades", i, len(traded), sym, len(same_day))
        if i % 50 == 0:
            log.info("[%d/%d] %d trades so far", i, len(traded), len(trades))

    if not trades:
        log.error("no trades collected for %s (failed: %d, other date: %d)", as_of, len(failed), len(stale))
        return 1
    trades = order_trades(trades)
    summary = summarize(trades, traded, as_of)
    write_day(path, trades)
    if not symbols:
        update_summary(out, summary)
    incomplete = [s["symbol"] for s in summary if s["complete"] is False]
    log.info("wrote %d trades for %d scrips -> %s", len(trades), len({t['symbol'] for t in trades}), path)
    if failed:
        log.warning("failed scrips: %s", ", ".join(failed))
    if stale:
        log.warning("floorsheet was for another date: %s", ", ".join(stale))
    if incomplete:
        log.warning("%d scrips have a different trade count than the price table: %s",
                    len(incomplete), ", ".join(incomplete[:30]))
    return 0
