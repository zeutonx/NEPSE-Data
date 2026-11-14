"""Price datasheet: full daily price history for every listed company, one CSV per company,
organised into one folder per sector, plus a combined CSV and an index.

  <out>/datasheet/
      companies.csv                      index: symbol, name, sector, shares, rows, first/last date, file
      all_companies_prices.csv           every company's rows in one file
      Commercial_Bank/NABIL.csv
      Hydropower/UPPER.csv
      ...

Each company CSV holds, oldest day first:
  date, symbol, company_name, sector, open, high, low, close, pct_change, volume, turnover,
  adj_open, adj_high, adj_low, adj_close, adjustments
Adjusted prices use bonus and right share events (see adjust.py); pass adjusted=False to skip.
"""
from __future__ import annotations

import csv
import logging
import re
from pathlib import Path
from typing import Any

from . import adjust, scrapers
from .client import SharesansarClient

log = logging.getLogger(__name__)

PRICE_COLUMNS = ["date", "symbol", "company_name", "sector", "open", "high", "low", "close", "pct_change",
                 "volume", "turnover"]
ADJ_COLUMNS = ["adj_open", "adj_high", "adj_low", "adj_close", "adjustments"]
INDEX_COLUMNS = ["symbol", "company_name", "sector", "company_id", "listed_shares", "paid_up_per_share",
                 "market_cap", "listing_date", "ltp", "rows", "first_date", "last_date", "file", "status"]


def folder_name(sector: str) -> str:
    """'Hotel & Tourism' -> 'Hotel_and_Tourism' (safe on Windows, macOS and Linux)."""
    s = sector.replace("&", "and")
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_") or "Unknown"


def file_name(symbol: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", symbol) + ".csv"


def dedupe_and_sort(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop repeated trading dates (the site lists a few twice) and order oldest first."""
    seen: set[str] = set()
    rows = []
    for r in history:
        d = r.get("date")
        if not d or d in seen:
            continue
        seen.add(d)
        rows.append(r)
    rows.sort(key=lambda r: r["date"])
    return rows


def _write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".csv.tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)  # never leave a half-written CSV behind if the run is interrupted


def _read_csv(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def build(client: SharesansarClient, out: Path, sectors: list[int] | None = None,
          symbols: list[str] | None = None, adjusted: bool = True, resume: bool = False,
          max_companies: int | None = None) -> Path:
    root = out / "datasheet"
    columns = PRICE_COLUMNS + (ADJ_COLUMNS if adjusted else [])

    companies = scrapers.company_list(client, sectors)
    if symbols:
        wanted = {s.upper() for s in symbols}
        companies = [c for c in companies if c["symbol"] in wanted]
    companies.sort(key=lambda c: (c["sector"], c["symbol"] or ""))
    if max_companies:
        companies = companies[:max_companies]
    log.info("datasheet: %d companies", len(companies))

    div_by_sym: dict[str, list[dict[str, Any]]] = {}
    right_by_sym: dict[str, list[dict[str, Any]]] = {}
    if adjusted:
        for d in scrapers.all_dividends(client):
            div_by_sym.setdefault(d["symbol"], []).append(d)
        for r in scrapers.right_shares(client):
            right_by_sym.setdefault(r["symbol"], []).append(r)
        log.info("datasheet: corporate actions for %d (dividends) / %d (rights) symbols",
                 len(div_by_sym), len(right_by_sym))

    # company_id only comes from each company page, which a resumed run skips for kept files,
    # so carry it over from the previous index instead of writing it out blank
    prev_ids: dict[str, str] = {}
    if resume and (root / "companies.csv").exists():
        prev_ids = {e["symbol"]: e["company_id"] for e in _read_csv(root / "companies.csv") if e.get("company_id")}

    index = []
    for i, c in enumerate(companies, 1):
        sym, sector = c["symbol"], c["sector"]
        path = root / folder_name(sector) / file_name(sym)
        entry = {k: c.get(k) for k in INDEX_COLUMNS if k in c}
        entry["file"] = str(path.relative_to(root)).replace("\\", "/")

        if resume and path.exists():
            rows = _read_csv(path)
            entry.update(status="kept", rows=len(rows), company_id=prev_ids.get(sym),
                         first_date=rows[0]["date"] if rows else None, last_date=rows[-1]["date"] if rows else None)
            index.append(entry)
            continue

        try:
            info = scrapers.company_info(client, sym)
            cid = info.get("company_id")
            if not cid:
                raise RuntimeError("no company id on company page")
            history = dedupe_and_sort(scrapers.price_history(client, cid))
        except Exception as exc:  # keep going; report at the end
            log.error("[%d/%d] %s: %s", i, len(companies), sym, exc)
            entry.update(status=f"error: {exc}", rows=0)
            index.append(entry)
            continue

        if adjusted:
            face = info.get("paid_up") if isinstance(info.get("paid_up"), (int, float)) else 100.0
            events = adjust.corporate_events(div_by_sym.get(sym, []), right_by_sym.get(sym, []), face_value=face)
            history = adjust.adjust_history(history, events)
        for r in history:
            r.update(symbol=sym, company_name=c["company_name"], sector=sector)

        _write_csv(path, history, columns)
        entry.update(company_id=cid, status="ok", rows=len(history),
                     first_date=history[0]["date"] if history else None,
                     last_date=history[-1]["date"] if history else None)
        index.append(entry)
        log.info("[%d/%d] %-10s %-28s %5d days", i, len(companies), sym, sector, len(history))

    _write_csv(root / "companies.csv", index, INDEX_COLUMNS)

    # combined file, streamed from the per-company CSVs so memory stays flat
    combined = root / "all_companies_prices.csv"
    tmp = combined.with_suffix(".csv.tmp")
    total = 0
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for e in index:
            p = root / e["file"]
            if p.exists():
                rows = _read_csv(p)
                w.writerows(rows)
                total += len(rows)
    tmp.replace(combined)

    failed = [e["symbol"] for e in index if str(e.get("status", "")).startswith("error")]
    log.info("datasheet: %d companies, %d price rows -> %s", len(index), total, root)
    if failed:
        log.warning("datasheet: %d failed (re-run with --resume to retry only these): %s",
                    len(failed), ", ".join(failed))
    return root
