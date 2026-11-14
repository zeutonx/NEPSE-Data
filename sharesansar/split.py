"""Break the combined data sets into separate files.

  data/market/forex/<CUR>.csv                   one file per currency (date, buy, sell, per-unit rates)
  data/market/gold_silver/<Item>.csv            Fine_Gold.csv, Tejabi_Gold.csv, Silver.csv
  data/prices/daily/<date>/<Sector>.csv         that day's prices, one file per sector
  data/prices/by_company/<Sector>/<SYMBOL>.csv  one file per company; every run appends that day's row
  data/datasheet/by_sector/<Sector>.csv         the local price datasheet, split by sector
  data/datasheet/by_year/<YYYY>.csv             ... and by calendar year

Index files are already one per index (data/market/indices/).
"""
from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Any

from .datasheet import file_name, folder_name

log = logging.getLogger(__name__)


def _read(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _write(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".csv.tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)


def _group(rows: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        groups.setdefault(str(r.get(key) or "Unknown"), []).append(r)
    return groups


def split_market(out: Path) -> None:
    market = out / "market"
    forex = _read(market / "forex.csv")
    if forex:
        cols = ["date", "currency", "name", "unit", "buy", "sell", "buy_per_unit", "sell_per_unit"]
        groups = _group(forex, "currency")
        for cur, rows in groups.items():
            _write(market / "forex" / f"{folder_name(cur)}.csv", sorted(rows, key=lambda r: r["date"]), cols)
        log.info("split forex into %d currency files", len(groups))
    gold = _read(market / "gold_silver.csv")
    if gold:
        groups = _group(gold, "item")
        for item, rows in groups.items():
            _write(market / "gold_silver" / f"{folder_name(item)}.csv", sorted(rows, key=lambda r: r["date"]),
                   ["date", "item", "price_per_tola", "change"])
        log.info("split gold/silver into %d files", len(groups))


def split_prices(rows: list[dict[str, Any]], sectors: dict[str, str], out: Path, as_of: str) -> None:
    """rows: one day's share prices; sectors: symbol -> sector name."""
    if not rows:
        return
    cols = list(rows[0].keys())
    if "sector" not in cols:
        cols.insert(cols.index("company_name") + 1 if "company_name" in cols else 2, "sector")
    for r in rows:
        r["sector"] = sectors.get(r["symbol"], "Unknown")

    by_sector = _group(rows, "sector")
    for sector, srows in by_sector.items():
        _write(out / "prices" / "daily" / as_of / f"{folder_name(sector)}.csv",
               sorted(srows, key=lambda r: r["symbol"]), cols)

    for r in rows:
        path = out / "prices" / "by_company" / folder_name(r["sector"]) / file_name(r["symbol"])
        existing = [e for e in _read(path) if e.get("date") != r["date"]]
        _write(path, sorted(existing + [r], key=lambda e: e["date"]), cols)
    log.info("split %d prices for %s into %d sector files and per-company files", len(rows), as_of, len(by_sector))


def split_datasheet(out: Path) -> None:
    """Break data/datasheet/all_companies_prices.csv into smaller combined files:

      data/datasheet/by_sector/<Sector>.csv   every company in that sector
      data/datasheet/by_year/<YYYY>.csv       every company, one calendar year

    Streams the combined file so memory stays flat; each output is far below GitHub's 100 MB limit.
    """
    root = out / "datasheet"
    src = root / "all_companies_prices.csv"
    if not src.exists():
        raise FileNotFoundError(f"{src} not found; run `python -m sharesansar datasheet` first")
    writers: dict[Path, tuple[Any, Any]] = {}
    counts: dict[Path, int] = {}

    def writer(path: Path, cols: list[str]):
        if path not in writers:
            path.parent.mkdir(parents=True, exist_ok=True)
            f = open(path.with_suffix(".csv.tmp"), "w", newline="", encoding="utf-8")
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            writers[path] = (f, w)
            counts[path] = 0
        counts[path] += 1
        return writers[path][1]

    total = 0
    with open(src, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        cols = reader.fieldnames or []
        for r in reader:
            total += 1
            writer(root / "by_sector" / f"{folder_name(r.get('sector') or 'Unknown')}.csv", cols).writerow(r)
            year = (r.get("date") or "unknown")[:4]
            writer(root / "by_year" / f"{year}.csv", cols).writerow(r)
    for path, (fh, _) in writers.items():
        fh.close()
        path.with_suffix(".csv.tmp").replace(path)
    sectors = sum(1 for p in counts if p.parent.name == "by_sector")
    years = sum(1 for p in counts if p.parent.name == "by_year")
    log.info("split %d rows into %d sector files and %d year files", total, sectors, years)
