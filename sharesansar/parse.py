"""HTML/text helpers: table parsing, header normalisation and number cleaning."""
from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup, Tag

_NUM_RE = re.compile(r"^\(?-?[\d,]*\.?\d+\)?%?$")


def snake(text: str) -> str:
    """'Prev. Close' -> 'prev_close', 'Diff %' -> 'diff_pct', '52 Weeks High' -> 'weeks_52_high'."""
    t = text.strip().lower().replace("%", " pct ").replace("&", " and ")
    t = re.sub(r"[^a-z0-9]+", "_", t).strip("_")
    if t and t[0].isdigit():  # python-friendly identifiers
        head, _, rest = t.partition("_")
        t = f"{rest}_{head}" if rest else f"n{head}"
    return t


def to_number(value: str) -> Any:
    """'1,905.00' -> 1905.0, '(12.5)' -> -12.5, '-' / '' -> None, other text unchanged."""
    v = value.strip().replace("\xa0", " ")
    if v in ("", "-", "--", "N/A", "n/a"):
        return None
    if _NUM_RE.match(v.replace(" ", "")):
        neg = v.startswith("(") and v.endswith(")")
        num = float(v.strip("()%").replace(",", ""))
        num = -num if neg else num
        return int(num) if num.is_integer() and "." not in v else num
    return v


def cell_text(td: Tag) -> str:
    return " ".join(td.get_text(" ", strip=True).split())


def parse_table(table: Tag, numeric: bool = True) -> list[dict[str, Any]]:
    """Turn a <table> with a header row into a list of dicts keyed by snake_case headers."""
    head = table.find("thead")
    header_row = head.find_all("tr")[-1] if head else table.find("tr")
    headers = [snake(cell_text(th)) or f"col{i}" for i, th in enumerate(header_row.find_all(["th", "td"]))]
    # de-duplicate header names
    seen: dict[str, int] = {}
    for i, h in enumerate(headers):
        if h in seen:
            seen[h] += 1
            headers[i] = f"{h}_{seen[h]}"
        else:
            seen[h] = 0

    body = table.find("tbody") or table
    rows = []
    for tr in body.find_all("tr"):
        if tr is header_row:
            continue
        cells = tr.find_all("td")
        if not cells:
            continue
        values = [cell_text(td) for td in cells]
        row = {h: (to_number(v) if numeric else v) for h, v in zip(headers, values)}
        rows.append(row)
    return rows


def label_value_pairs(root: Tag) -> dict[str, str]:
    """Collect 'Label : Value' style pairs from 2-cell table rows inside root."""
    out: dict[str, str] = {}
    for tr in root.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if len(cells) == 2:
            key = snake(cell_text(cells[0]).rstrip(":"))
            if key and key not in out:
                out[key] = cell_text(cells[1])
    return out


def soup_of(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")
