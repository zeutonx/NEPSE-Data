"""Write lists of dicts to CSV and/or JSON."""
from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def write(rows: list[dict[str, Any]], stem: Path, fmt: str = "both", quiet: bool = False) -> None:
    stem.parent.mkdir(parents=True, exist_ok=True)
    if fmt in ("csv", "both"):
        fields: list[str] = []
        for r in rows:
            fields.extend(k for k in r if k not in fields)
        with open(stem.with_suffix(".csv"), "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
    if fmt in ("json", "both"):
        with open(stem.with_suffix(".json"), "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)
    (log.debug if quiet else log.info)("wrote %d rows -> %s.{%s}", len(rows), stem, fmt)
