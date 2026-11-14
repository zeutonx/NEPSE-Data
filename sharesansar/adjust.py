"""Adjust historical prices for bonus shares, right shares and (optionally) cash dividends.

The price before a book-close date includes the entitlement; after it the stock trades "ex".
To compare prices across that date, older prices are adjusted the same way the
ShareSansar bonus/right adjustment calculators do:

  bonus b %             P' = P / (1 + b/100)
  right r : 1 at S      P' = (P + r*S) / (1 + r)
  cash c % (optional)   P' = P - c/100 * face_value

Each price is run through every event whose book-close date is after the trading day,
oldest event first, so later events compound on earlier ones.
"""
from __future__ import annotations

from typing import Any, Callable

PRICE_FIELDS = ("open", "high", "low", "close")


def corporate_events(dividends: list[dict[str, Any]], rights: list[dict[str, Any]],
                     include_cash: bool = False, face_value: float = 100.0) -> list[dict[str, Any]]:
    """Normalise dividend and right-share rows into dated price-adjustment events."""
    events: list[dict[str, Any]] = []
    for d in dividends:
        date = d.get("book_close_date")
        if not date:
            continue
        bonus = d.get("bonus_pct") or 0.0
        cash = d.get("cash_pct") or 0.0
        if bonus:
            events.append({"date": date, "type": "bonus", "value": bonus,
                           "fn": _bonus(bonus), "note": f"{bonus}% bonus ({d.get('fiscal_year')})"})
        if include_cash and cash:
            events.append({"date": date, "type": "cash", "value": cash,
                           "fn": _cash(cash, face_value), "note": f"{cash}% cash ({d.get('fiscal_year')})"})
    for r in rights:
        date, ratio, price = r.get("book_close_date"), r.get("right_per_share"), r.get("issue_price")
        if not date or not ratio or price is None:
            continue
        events.append({"date": date, "type": "right", "value": ratio,
                       "fn": _right(ratio, price), "note": f"right {r.get('ratio')} @ {price}"})
    events.sort(key=lambda e: e["date"])
    return events


def adjust_history(history: list[dict[str, Any]], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return history rows with adj_open/adj_high/adj_low/adj_close and the events applied.

    Events whose book-close date is after the latest trading day in `history` are announced but
    not yet effective (the stock has not gone ex), so they are left out; otherwise every price,
    including today's, would be discounted for an entitlement it still carries.
    """
    dates = [r["date"] for r in history if r.get("date")]
    if dates:
        last = max(dates)
        events = [e for e in events if e["date"] <= last]
    out = []
    for row in history:
        date = row.get("date")
        applicable = [e for e in events if date and e["date"] > date]
        new = dict(row)
        for f in PRICE_FIELDS:
            p = row.get(f)
            if isinstance(p, (int, float)):
                for e in applicable:
                    p = e["fn"](p)
                new[f"adj_{f}"] = round(p, 2)
            else:
                new[f"adj_{f}"] = None
        new["adjustments"] = "; ".join(e["note"] for e in applicable)
        out.append(new)
    return out


def _bonus(pct: float) -> Callable[[float], float]:
    return lambda p: p / (1 + pct / 100)


def _right(ratio: float, issue_price: float) -> Callable[[float], float]:
    return lambda p: (p + ratio * issue_price) / (1 + ratio)


def _cash(pct: float, face_value: float) -> Callable[[float], float]:
    return lambda p: p - pct / 100 * face_value
