"""Market-wide series: NEPSE index and sub-indices, gold/silver and foreign exchange.

Sources
  indices      sharesansar.com  GET  /index-history-data?index_id=<id>&from&to  (DataTables JSON, since 1997)
  gold/silver  sharesansar.com  POST /home-goldsilver   (today's FENEGOSIDA rate per tola; no history on the site,
                                                         so each run appends today's rate to a growing CSV)
  forex        nrb.org.np       GET  /api/forex/v1/rates?from&to&per_page&page  (Nepal Rastra Bank official
                                                         rates, daily since 2000)

  <out>/market/
      indices/NEPSE_Index.csv, Banking_SubIndex.csv, ...   one file per index, oldest day first
      indices_all.csv                                      every index in one long file
      indices_close_wide.csv                               date x index closing values
      gold_silver.csv                                      date, item, price_per_tola, change
      forex.csv                                            date, currency, name, unit, buy, sell (+ per 1 unit)
      forex_wide.csv                                       date x currency, NRB buy rate per 1 unit
"""
from __future__ import annotations

import csv
import datetime as dt
import logging
import re
from pathlib import Path
from typing import Any

from .client import SharesansarClient
from .parse import cell_text, soup_of, to_number

log = logging.getLogger(__name__)

NRB_URL = "https://www.nrb.org.np"

INDICES: dict[int, str] = {
    12: "NEPSE Index",
    16: "Sensitive Index",
    4: "Float Index",
    15: "Sensitive Float Index",
    1: "Banking SubIndex",
    2: "Development Bank Index",
    3: "Finance Index",
    5: "Hotels And Tourism",
    6: "HydroPower Index",
    7: "Insurance",
    18: "Investment",
    8: "Life Insurance",
    9: "Manufacturing And Processing",
    10: "Microfinance Index",
    11: "Mutual Fund",
    13: "Non Life Insurance",
    14: "Others Index",
    17: "Trading Index",
}


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", name.replace("&", "and")).strip("_")


def _write(path: Path, rows: list[dict[str, Any]], columns: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if columns is None:
        columns = []
        for r in rows:
            columns.extend(k for k in r if k not in columns)
    tmp = path.with_suffix(".csv.tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)


def _read(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# --------------------------------------------------------------------------- indices
def index_history(client: SharesansarClient, index_id: int, start: str = "1990-01-01",
                  end: str | None = None) -> list[dict[str, Any]]:
    """Daily open/high/low/close, change, % change and turnover of one index, oldest day first."""
    from .scrapers import _datatable  # shared DataTables walker (length=-1, page fallback)

    end = end or dt.date.today().isoformat()
    seen, rows = set(), []
    for rec in _datatable(client, "/index-history-data", {"index_id": index_id, "from": start, "to": end},
                          method="GET"):
        date = rec.get("published_date")
        if not date or date in seen:
            continue
        seen.add(date)
        rows.append({
            "date": date,
            "index": INDICES.get(index_id, str(index_id)),
            "index_id": index_id,
            "open": to_number(str(rec.get("open") or "")),
            "high": to_number(str(rec.get("high") or "")),
            "low": to_number(str(rec.get("low") or "")),
            "close": to_number(str(rec.get("current") or "")),
            "change": to_number(str(rec.get("change_") or "")),
            "pct_change": to_number(str(rec.get("per_change") or "")),
            "turnover": to_number(str(rec.get("turnover") or "")),
        })
    rows.sort(key=lambda r: r["date"])
    return rows


def all_indices(client: SharesansarClient, out: Path, ids: list[int] | None = None) -> int:
    root = out / "market"
    combined: list[dict[str, Any]] = []
    wide: dict[str, dict[str, Any]] = {}
    for iid in ids or list(INDICES):
        rows = index_history(client, iid)
        name = INDICES.get(iid, str(iid))
        _write(root / "indices" / f"{_safe(name)}.csv", rows)
        combined.extend(rows)
        for r in rows:
            wide.setdefault(r["date"], {"date": r["date"]})[name] = r["close"]
        log.info("index %-28s %5d days (%s .. %s)", name, len(rows),
                 rows[0]["date"] if rows else "-", rows[-1]["date"] if rows else "-")
    _write(root / "indices_all.csv", combined)
    names = [INDICES.get(i, str(i)) for i in (ids or list(INDICES))]
    _write(root / "indices_close_wide.csv", [wide[d] for d in sorted(wide)], ["date"] + names)
    return len(combined)


# --------------------------------------------------------------------------- gold / silver
def gold_silver_today(client: SharesansarClient) -> list[dict[str, Any]]:
    """Today's Fine Gold, Tejabi Gold and Silver price per tola (FENEGOSIDA rate via ShareSansar)."""
    resp = client.request("POST", "/home-goldsilver", ajax=True, data={"_token": client.csrf},
                          headers={"X-CSRF-Token": client.csrf})
    soup = soup_of(resp.text)
    m = re.search(r"As of\s*(\d{4}-\d{2}-\d{2})", soup.get_text(" "))
    as_of = m.group(1) if m else dt.date.today().isoformat()
    rows = []
    for tr in soup.select("tbody tr"):
        tds = [cell_text(td) for td in tr.find_all("td")]
        if len(tds) >= 2:
            rows.append({"date": as_of, "item": tds[0], "price_per_tola": to_number(tds[1]),
                         "change": to_number(tds[2]) if len(tds) > 2 else None})
    if not rows:
        raise RuntimeError("gold/silver table not found; page layout may have changed")
    return rows


def update_gold_silver(client: SharesansarClient, out: Path) -> list[dict[str, Any]]:
    """Append today's rates to market/gold_silver.csv (one row per date and item)."""
    path = out / "market" / "gold_silver.csv"
    existing = _read(path)
    today = gold_silver_today(client)
    keys = {(r["date"], r["item"]) for r in today}
    rows = [r for r in existing if (r["date"], r["item"]) not in keys] + today
    rows.sort(key=lambda r: (r["date"], r["item"]))
    _write(path, rows, ["date", "item", "price_per_tola", "change"])
    log.info("gold/silver %s: %s", today[0]["date"],
             ", ".join(f"{r['item']} {r['price_per_tola']}" for r in today))
    return rows


# --------------------------------------------------------------------------- forex (NRB)
def nrb_forex(client: SharesansarClient, start: str, end: str) -> list[dict[str, Any]]:
    """Nepal Rastra Bank buy/sell rates for every currency, every day in [start, end]."""
    rows = []
    s = dt.date.fromisoformat(start)
    e = dt.date.fromisoformat(end)
    while s <= e:
        chunk_end = min(dt.date(s.year, 12, 31), e)  # one calendar year per query, then pages of 100 days
        page = 1
        while True:
            j = client.request("GET", "/api/forex/v1/rates", params={
                "from": s.isoformat(), "to": chunk_end.isoformat(), "per_page": 100, "page": page}).json()
            data = j.get("data") or {}
            for day in data.get("payload") or []:
                for r in day.get("rates") or []:
                    cur = r.get("currency") or {}
                    unit = cur.get("unit") or 1
                    buy, sell = to_number(str(r.get("buy") or "")), to_number(str(r.get("sell") or ""))
                    rows.append({
                        "date": day.get("date"), "currency": cur.get("iso3"), "name": cur.get("name"),
                        "unit": unit, "buy": buy, "sell": sell,
                        "buy_per_unit": round(buy / unit, 6) if isinstance(buy, (int, float)) else None,
                        "sell_per_unit": round(sell / unit, 6) if isinstance(sell, (int, float)) else None,
                    })
            pag = j.get("pagination") or data.get("pagination") or {}
            pages = int(pag.get("pages") or 1)
            if page >= pages:
                break
            page += 1
        log.info("forex %s .. %s: %d rows so far", s, chunk_end, len(rows))
        s = chunk_end + dt.timedelta(days=1)
    return rows


FOREX_COLUMNS = ["date", "currency", "name", "unit", "buy", "sell", "buy_per_unit", "sell_per_unit"]


def update_forex(out: Path, start: str = "2000-01-01", delay: float = 2.0) -> int:
    """Fetch NRB rates, continuing from the last date already in market/forex.csv."""
    path = out / "market" / "forex.csv"
    existing = _read(path)
    if existing:
        last = max(r["date"] for r in existing)
        start = (dt.date.fromisoformat(last) + dt.timedelta(days=1)).isoformat()
    end = dt.date.today().isoformat()
    new = []
    if start <= end:
        nrb = SharesansarClient(delay=delay, base_url=NRB_URL)
        new = nrb_forex(nrb, start, end)
    seen = {(r["date"], r["currency"]) for r in new}
    rows = [r for r in existing if (r["date"], r["currency"]) not in seen] + new
    rows.sort(key=lambda r: (r["date"], r["currency"] or ""))
    _write(path, rows, FOREX_COLUMNS)

    wide: dict[str, dict[str, Any]] = {}
    currencies: list[str] = []
    for r in rows:
        if r["currency"] and r["currency"] not in currencies:
            currencies.append(r["currency"])
        wide.setdefault(r["date"], {"date": r["date"]})[r["currency"]] = r["buy_per_unit"]
    _write(out / "market" / "forex_wide.csv", [wide[d] for d in sorted(wide)], ["date"] + currencies)
    log.info("forex: %d new rows, %d total, %d days", len(new), len(rows), len(wide))
    return len(rows)
