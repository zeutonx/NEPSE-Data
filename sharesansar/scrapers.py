"""Scrapers for the main ShareSansar data sets.

Endpoints (all verified against the live site, Nov 2026):

  company list   GET  /company-list?sector=<id>&start&length   (DataTables JSON, page size <= 50)
  share prices   GET  /today-share-price                       (HTML table #headFixed, latest day)
                 POST /ajaxtodayshareprice  _token, sector, date (same table, any trading date)
  company page   GET  /company/<symbol>                        (#myTableCInfo + #companyid)
  price history  POST /company-price-history  company=<id>     (DataTables JSON)
  floorsheet     POST /company-floor-sheet    company=<SYMBOL> (DataTables JSON)
  dividends      POST /company-dividend       company=<id>     (DataTables JSON)
"""
from __future__ import annotations

import html
import logging
import re
from typing import Any, Iterator

from bs4 import BeautifulSoup

from .client import SharesansarClient
from .parse import cell_text, label_value_pairs, parse_table, soup_of, to_number

log = logging.getLogger(__name__)

PAGE_SIZE = 50  # the server returns nothing for length > 50

SECTORS: dict[int, str] = {
    1: "Commercial Bank",
    2: "Corporate Debentures",
    3: "Development Bank",
    4: "Finance",
    5: "Government Bonds",
    6: "Hotel & Tourism",
    7: "Hydropower",
    20: "Investment",
    8: "Life Insurance",
    9: "Manufacturing and Processing",
    12: "Microfinance",
    13: "Mutual Fund",
    15: "Non-Life Insurance",
    16: "Others",
    17: "Preference Share",
    18: "Promoter Share",
    19: "Trading",
}

_TAG_RE = re.compile(r"<[^>]+>")
_PLACEHOLDERS = ("", "-", "--", "N/A", "n/a")


def _blank(value: Any) -> Any:
    """Map the site's empty placeholders ('', '-', ...) to None; leave everything else alone."""
    if isinstance(value, str) and value.strip() in _PLACEHOLDERS:
        return None
    return value


def _strip_html(value: Any) -> Any:
    if isinstance(value, str):
        if "<" in value:
            value = " ".join(_TAG_RE.sub(" ", value).split())
        return html.unescape(value)
    return value


def _clean_record(rec: dict[str, Any], keep_text: tuple[str, ...] = ()) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in rec.items():
        if k == "DT_Row_Index":
            continue
        v = _blank(_strip_html(v))
        if isinstance(v, str) and k not in keep_text:
            v = to_number(v)
        out[k] = v
    return out


def _datatable(client: SharesansarClient, path: str, data: dict[str, Any], method: str = "POST",
               limit: int | None = None) -> Iterator[dict[str, Any]]:
    """Walk every page of a server-side DataTables endpoint."""
    start, draw = 0, 1
    while True:
        params = {**data, "draw": draw, "start": start, "length": PAGE_SIZE, "search[value]": "",
                  "search[regex]": "false"}
        if method == "POST":
            payload = client.json(path, method="POST", data=params, headers={"X-CSRF-Token": client.csrf})
        else:
            payload = client.json(path, params=params)
        rows = payload.get("data") or []
        total = int(payload.get("recordsFiltered") or payload.get("recordsTotal") or 0)
        yield from rows
        start += len(rows)
        draw += 1
        if limit is not None and start >= limit:
            return
        if not rows or start >= total:
            if start < total:
                log.warning("%s %s: got %d of %d rows (site stopped returning data)", path, data, start, total)
            return


# --------------------------------------------------------------------------- companies
def company_list(client: SharesansarClient, sectors: list[int] | None = None) -> list[dict[str, Any]]:
    """All listed companies with listed/promoter/public shares, paid-up, market cap, LTP."""
    out = []
    for sid in sectors or list(SECTORS):
        name = SECTORS.get(sid, str(sid))
        n = 0
        for rec in _datatable(client, "/company-list", {"sector": sid}, method="GET"):
            row = _clean_record(rec, keep_text=("symbol", "companyname", "listing_date", "date"))
            row = {
                "symbol": row.get("symbol"),
                "company_name": row.get("companyname"),
                "sector": name,
                "sector_id": sid,
                "listed_shares": row.get("shares"),
                "promoter_shares": row.get("prom_share"),
                "promoter_pct": _pct(rec.get("prom_share_per")),
                "public_shares": row.get("public_share"),
                "public_pct": _pct(rec.get("public_share_per")),
                "paid_up_per_share": row.get("paidup"),
                "total_paid_up_capital": row.get("paidupcap"),
                "market_cap": row.get("marketcap"),
                "listing_date": row.get("listing_date") or None,
                "ltp": row.get("ltp"),
                "as_of": row.get("date"),
            }
            out.append(row)
            n += 1
        log.info("company-list: %-28s %d companies", name, n)
    return out


def _pct(value: Any) -> float | None:
    if not value:
        return None
    m = re.search(r"-?[\d.]+", str(value))
    return float(m.group()) if m else None


# --------------------------------------------------------------------------- prices
def _price_table(html_or_soup: str | BeautifulSoup) -> tuple[str | None, list[dict[str, Any]]]:
    soup = soup_of(html_or_soup) if isinstance(html_or_soup, str) else html_or_soup
    table = soup.find("table", id="headFixed")
    if table is None:
        raise RuntimeError("price table #headFixed not found; page layout may have changed")
    as_of = None
    m = re.search(r"As of\s*:\s*(\d{4}-\d{2}-\d{2})", soup.get_text(" "))
    if m:
        as_of = m.group(1)
    rows = []
    for r in parse_table(table):
        if r.get("symbol") is None or len(r) < 5:  # "No Record Found" row
            continue
        r.pop("s_no", None)
        r["symbol"] = str(r["symbol"])
        rows.append({"date": as_of, **r})
    # company names live in the link title attribute
    names = {a.get_text(strip=True): a.get("title") for a in table.select("tbody td a[title]")}
    out = []
    for r in rows:
        clean = {"date": r.pop("date"), "symbol": r.pop("symbol")}
        clean["company_name"] = names.get(clean["symbol"])
        clean.update({_PRICE_RENAME.get(k, k): v for k, v in r.items()})
        out.append(clean)
    return as_of, out


_PRICE_RENAME = {
    "conf": "confidence", "vol": "volume", "trans": "transactions",
    "diff": "change", "diff_pct": "pct_change",
    "days_120": "avg_120d", "days_180": "avg_180d",
    "weeks_high_52": "week52_high", "weeks_low_52": "week52_low",
}


def share_prices(client: SharesansarClient, date: str | None = None, sector: str = "all_sec"
                 ) -> tuple[str | None, list[dict[str, Any]]]:
    """Day's OHLC, LTP, VWAP, volume, turnover, transactions, % change, 52w hi/lo for every traded scrip.

    date: 'YYYY-MM-DD' for a past trading day, None for the latest.
    Returns (as_of_date, rows). rows is empty on a non-trading day.
    """
    if date is None and sector == "all_sec":
        return _price_table(client.soup("/today-share-price"))
    payload = {"_token": client.csrf, "sector": sector, "date": date or ""}
    resp = client.request("POST", "/ajaxtodayshareprice", ajax=True, data=payload)
    return _price_table(resp.text)


# --------------------------------------------------------------------------- company page
def company_info(client: SharesansarClient, symbol: str) -> dict[str, Any]:
    """Profile from /company/<symbol>: sector, listed shares, paid-up, contacts, registrar, 52w range."""
    soup = client.soup(f"/company/{symbol.lower()}")
    info: dict[str, Any] = {"symbol": symbol.upper()}
    cid = soup.find(id="companyid")
    info["company_id"] = int(cid.get_text(strip=True)) if cid and cid.get_text(strip=True).isdigit() else None

    table = soup.find("table", id="myTableCInfo")
    if table is not None:
        pairs = label_value_pairs(table)
        link = table.find("a", href=True)
        if link:
            href = re.sub(r"^(https?:)?//(?=https?://)", "", link["href"].strip())  # '//http://x' -> 'http://x'
            href = ("https:" + href) if href.startswith("//") else href
            # an empty link renders as href="//" -> "https://"
            pairs["website_link"] = href if re.sub(r"^https?://", "", href).strip("/") else ""
        for k, v in pairs.items():
            v = _clean_unit(v) if k in ("listed_shares", "paid_up", "total_paid_up_value") else v
            info[k] = _blank(v)

    for h in soup.find_all(["h4", "h3"]):
        t = cell_text(h)
        if t.startswith("Share Registrar:"):
            reg = _blank(t.split(":", 1)[1].strip())
            # with no registrar on file the site repeats the company name here
            info["share_registrar"] = None if reg == info.get("name") else reg

    text = soup.get_text(" ", strip=True)
    m = re.search(r"52 Weeks? High\s*-\s*Low\s*:?\s*([\d,.]+)\s*-\s*([\d,.]+)", text)
    if m:
        info["week52_high"], info["week52_low"] = to_number(m.group(1)), to_number(m.group(2))
    m = re.search(r"As on\s*:\s*(\d{4}-\d{2}-\d{2})\s+([\d,.]+)\s+(-?[\d,.]+)\s+(-?[\d,.]+)\s*%", text)
    if m:
        info["quote_date"] = m.group(1)
        info["last_price"], info["change"], info["pct_change"] = (to_number(g) for g in m.groups()[1:])
    for label in ("Open", "High", "Low", "Volume"):
        m = re.search(rf"\b{label}\s*:\s*([\d,.]+)", text)
        if m:
            info[label.lower()] = to_number(m.group(1))
    for days in (120, 180):
        m = re.search(rf"{days} Days? Average\s*:?\s*([\d,.]+)", text)
        if m:
            info[f"avg_{days}_days"] = to_number(m.group(1))
    return info


def _clean_unit(v: str) -> Any:
    return to_number(re.sub(r"(Rs\.?|units)", "", v, flags=re.I).strip())


def price_history(client: SharesansarClient, company_id: int, limit: int | None = None) -> list[dict[str, Any]]:
    """Daily OHLC / % change / volume / turnover, newest first. limit=None fetches everything."""
    rows = []
    for rec in _datatable(client, "/company-price-history", {"company": company_id}, limit=limit):
        r = _clean_record(rec, keep_text=("published_date",))
        rows.append({
            "date": r.get("published_date"),
            "open": r.get("open"), "high": r.get("high"), "low": r.get("low"), "close": r.get("close"),
            "pct_change": r.get("per_change"),
            "volume": r.get("traded_quantity"), "turnover": r.get("traded_amount"),
        })
    return rows[:limit] if limit else rows


def floorsheet(client: SharesansarClient, symbol: str, limit: int | None = None) -> list[dict[str, Any]]:
    """Latest day's floorsheet (contract, buyer/seller broker, qty, rate, amount) for one scrip."""
    rows = []
    for rec in _datatable(client, "/company-floor-sheet",
                          {"company": symbol.upper(), "buyer": "", "seller": ""}, limit=limit):
        r = _clean_record(rec, keep_text=("date_", "contract_no", "buyer", "seller"))
        rows.append({
            "date": r.get("date_"), "symbol": symbol.upper(), "contract_no": str(r.get("contract_no")),
            "buyer_broker": r.get("buyer"), "seller_broker": r.get("seller"),
            "quantity": r.get("quantity"), "rate": r.get("rate"), "amount": r.get("amount"),
        })
    return rows[:limit] if limit else rows


def dividends(client: SharesansarClient, company_id: int, symbol: str | None = None) -> list[dict[str, Any]]:
    """Dividend history for one company: bonus %, cash %, total %, announcement and book-close dates."""
    rows = []
    for rec in _datatable(client, "/company-dividend", {"company": company_id}):
        row = _dividend_row(rec)
        row["symbol"] = symbol
        row.pop("company_name")
        rows.append(row)
    return rows


# --------------------------------------------------------------------------- corporate actions
#   right shares   POST /company-rightshare  company=<id>          (per company)
#                  GET  /existing-issues?type=3                    (all right issues, market wide)
#   dividends      GET  /proposed-dividend?type=YEARWISE&year=<id>&sector=0   (per fiscal year)
#                  GET  /proposed-dividend?type=LATEST&duration=1_YEAR
#   mergers        GET  /merger-acquisition?type=1 (completed) / type=0 (in process)
#   merged cos.    GET  /merged-companies                           (delisted after merger)

MERGER_ACTION = {"1": "Merger", "2": "Acquisition"}
YES_NO = {"0": "No", "1": "Yes"}


def _link_text(html: Any) -> Any:
    return _strip_html(html) if html else html


def _right_row(rec: dict[str, Any]) -> dict[str, Any]:
    comp = rec.get("company") or {}
    ratio = rec.get("ratio_value") or ""
    return {
        "company_id": rec.get("companyid"),
        "symbol": _link_text(comp.get("symbol")),
        "company_name": _link_text(comp.get("companyname")),
        "ratio": ratio,
        "right_per_share": right_ratio(ratio),
        "total_units": to_number(rec.get("total_units") or ""),
        "issue_price": to_number(rec.get("issue_price") or ""),
        "book_close_date": rec.get("final_date") or None,
        "opening_date": rec.get("opening_date") or None,
        "closing_date": rec.get("closing_date") or None,
        "listing_date": rec.get("listing_date") or None,
        "issue_manager": rec.get("issue_manager"),
        "share_type": rec.get("displayable_share_type"),
        "status": rec.get("status"),
        "announcement_link": rec.get("announcement_link"),
    }


def right_ratio(ratio: str) -> float | None:
    """'100:15' -> 0.15 and '1:0.50' -> 0.5 (new shares per existing share)."""
    m = re.match(r"\s*([\d.]+)\s*:\s*([\d.]+)", ratio or "")
    if not m or float(m.group(1)) == 0:
        return None
    return round(float(m.group(2)) / float(m.group(1)), 6)


def right_shares(client: SharesansarClient, company_id: int | None = None) -> list[dict[str, Any]]:
    """Right share issues for one company, or every right issue on the market when company_id is None."""
    if company_id is None:
        recs = _datatable(client, "/existing-issues", {"type": 3}, method="GET")
    else:
        recs = _datatable(client, "/company-rightshare", {"company": company_id})
    rows = []
    for rec in recs:
        row = _right_row(rec)
        if company_id is not None:
            row["company_id"] = company_id
        rows.append(row)
    return rows


def _dividend_row(rec: dict[str, Any]) -> dict[str, Any]:
    bc = rec.get("bookclose_date") or ""
    return {
        "symbol": _link_text(rec.get("symbol")),
        "company_name": _link_text(rec.get("companyname")),
        "fiscal_year": rec.get("year"),
        "bonus_pct": to_number(rec.get("bonus_share") or "") or 0.0,
        "cash_pct": to_number(rec.get("cash_dividend") or "") or 0.0,
        "total_pct": to_number(rec.get("total_dividend") or "") or 0.0,
        "announcement_date": _blank(rec.get("announcement_date")),
        "book_close_date": bc.split(" ")[0] or None,
        "book_closed": "[Closed]" in bc,
        "distribution_date": _blank(rec.get("distribution_date")),
        "bonus_listing_date": _blank(rec.get("bonus_listing_date")),
    }


def dividend_years(client: SharesansarClient) -> dict[int, str]:
    """Fiscal-year ids used by /proposed-dividend, e.g. {32: '2082/2083', ...}."""
    soup = client.soup("/proposed-dividend")
    sel = soup.find("select", attrs={"name": "year"})
    if sel is None:
        raise RuntimeError("fiscal year selector not found on /proposed-dividend")
    return {int(o["value"]): o.get_text(strip=True) for o in sel.find_all("option") if o.get("value", "").isdigit()}


def all_dividends(client: SharesansarClient, years: int | None = None) -> list[dict[str, Any]]:
    """Every company's bonus/cash dividend, fiscal year by fiscal year (newest first).

    years: only the latest N fiscal years (None = all the site has).
    """
    year_ids = sorted(dividend_years(client), reverse=True)
    if years:
        year_ids = year_ids[:years]
    rows = []
    for yid in year_ids:
        n = 0
        for rec in _datatable(client, "/proposed-dividend", {"type": "YEARWISE", "year": yid, "sector": 0},
                              method="GET"):
            rows.append(_dividend_row(rec))
            n += 1
        log.info("dividends: year id %d -> %d rows", yid, n)
    return rows


def mergers(client: SharesansarClient) -> list[dict[str, Any]]:
    """Mergers & acquisitions, both completed and in process."""
    rows = []
    for type_id, status in ((1, "Completed"), (0, "In process")):
        for rec in _datatable(client, "/merger-acquisition", {"type": type_id}, method="GET"):
            comp = rec.get("company") or {}
            sector = rec.get("sector") or {}
            rows.append({
                "status": status,
                "merging_companies": _link_text(rec.get("companies")),
                "new_symbol": comp.get("symbol"),
                "new_company_name": comp.get("companyname"),
                "new_company_id": rec.get("companyid"),
                "sector": _link_text(sector.get("sectorname")),
                "action": MERGER_ACTION.get(str(rec.get("action")), rec.get("action")),
                "swap_ratio": rec.get("swap_ratio"),
                "trading": YES_NO.get(str(rec.get("tradingid")), rec.get("tradingid")),
                "mou_date": rec.get("mou_date"),
                "final_approval_date": rec.get("final_date"),
                "joint_transaction_date": rec.get("transaction_date"),
            })
    return rows


def merged_companies(client: SharesansarClient) -> list[dict[str, Any]]:
    """Companies that no longer trade because they merged into another one."""
    rows = []
    for rec in _datatable(client, "/merged-companies", {}, method="GET"):
        detail = rec.get("companyDetail") or rec.get("company_detail") or {}
        rows.append({
            "company_id": rec.get("id"),
            "symbol": _link_text(rec.get("symbol")),
            "company_name": _link_text(rec.get("companyname")),
            "operation_date": detail.get("operationdate") or None,
        })
    return rows
