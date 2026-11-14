"""Build a Kaggle-ready copy of the NEPSE dataset.

Reads data/ (as produced by `python -m sharesansar ...`) and writes kaggle_dataset/:
flat CSVs, README.md and dataset-metadata.json for the Kaggle CLI.

Works on either copy of the data:
- a local run with the full datasheet (data/datasheet/all_companies_prices.csv), or
- the GitHub copy, which keeps one file per company in data/prices/by_company/.
Corporate-action files are included when data/corporate_actions/ exists
(`python -m sharesansar corporate-actions` creates it).

    python kaggle/build.py --user zeuton
    kaggle datasets version -p kaggle_dataset -m "Update to <date>"

Nothing in data/ is changed. .github/workflows/kaggle-publish.yml runs this every two weeks.
"""
import argparse
import json
import shutil
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "kaggle_dataset"

SLUG = "nepse-stock-market-data"
TITLE = "NEPSE Nepal Stock Market Data 1995-2026"  # Kaggle: 6-50 chars
SUBTITLE = "Daily prices of 655 NEPSE stocks, indices, NRB forex and corporate actions"  # 20-80 chars
LICENSE = "CC-BY-NC-SA-4.0"

# ---------------------------------------------------------------- column docs
COLS = {
    "nepse_prices.csv": [
        ("date", "string", "Trading day, YYYY-MM-DD (Gregorian)"),
        ("symbol", "string", "NEPSE ticker symbol"),
        ("company_name", "string", "Company name"),
        ("sector", "string", "NEPSE sector"),
        ("open", "number", "Opening price, NPR (as traded, not adjusted)"),
        ("high", "number", "Day high, NPR"),
        ("low", "number", "Day low, NPR"),
        ("close", "number", "Closing price (LTP), NPR"),
        ("pct_change", "number", "Percent change vs previous close, as reported by the source"),
        ("volume", "number", "Shares traded"),
        ("turnover", "number", "Traded amount, NPR (0 on many early days where the source has none)"),
        ("adj_open", "number", "Open adjusted for later bonus and right issues"),
        ("adj_high", "number", "High adjusted for later bonus and right issues"),
        ("adj_low", "number", "Low adjusted for later bonus and right issues"),
        ("adj_close", "number", "Close adjusted for later bonus and right issues (use this for returns)"),
        ("adjustments", "string", "Corporate actions after this date that the adj_* prices account for; empty when none"),
    ],
    "companies.csv": [
        ("symbol", "string", "NEPSE ticker symbol"),
        ("company_name", "string", "Company name"),
        ("sector", "string", "NEPSE sector"),
        ("company_id", "integer", "ShareSansar company id"),
        ("listed_shares", "number", "Listed shares"),
        ("promoter_shares", "number", "Promoter shares"),
        ("promoter_pct", "number", "Promoter holding, %"),
        ("public_shares", "number", "Public shares"),
        ("public_pct", "number", "Public holding, %"),
        ("paid_up_per_share", "number", "Paid-up value per share, NPR"),
        ("total_paid_up_capital", "number", "Total paid-up capital, NPR"),
        ("market_cap", "number", "Market capitalisation, NPR, on as_of"),
        ("listing_date", "string", "Listing date, YYYY-MM-DD, where known"),
        ("ltp", "number", "Last traded price, NPR, on as_of"),
        ("as_of", "string", "Date of the snapshot"),
        ("price_rows", "integer", "Number of rows for this symbol in nepse_prices.csv"),
        ("first_date", "string", "First date in nepse_prices.csv"),
        ("last_date", "string", "Last date in nepse_prices.csv"),
    ],
    "indices.csv": [
        ("date", "string", "Trading day, YYYY-MM-DD"),
        ("index", "string", "Index name (NEPSE Index, Sensitive Index, Banking SubIndex, ...)"),
        ("index_id", "integer", "Source id of the index"),
        ("open", "number", "Open"),
        ("high", "number", "High"),
        ("low", "number", "Low"),
        ("close", "number", "Close"),
        ("change", "number", "Point change"),
        ("pct_change", "number", "Percent change"),
        ("turnover", "number", "Turnover, NPR"),
    ],
    "indices_close_wide.csv": [("date", "string", "Trading day; one further column per index holding its close")],
    "forex.csv": [
        ("date", "string", "Date, YYYY-MM-DD"),
        ("currency", "string", "ISO currency code"),
        ("name", "string", "Currency name"),
        ("unit", "integer", "Units the NRB quotes the rate for (1, 10 or 100)"),
        ("buy", "number", "NRB buying rate, NPR per `unit`; empty when not quoted"),
        ("sell", "number", "NRB selling rate, NPR per `unit`; empty when not quoted"),
        ("buy_per_unit", "number", "Buying rate, NPR per 1 unit of currency"),
        ("sell_per_unit", "number", "Selling rate, NPR per 1 unit of currency"),
    ],
    "forex_wide.csv": [("date", "string", "Date; one further column per currency holding the NRB buying rate in NPR per 1 unit")],
    "dividends.csv": [
        ("symbol", "string", "NEPSE ticker symbol"),
        ("company_name", "string", "Company name"),
        ("fiscal_year", "string", "Nepali fiscal year (Bikram Sambat), e.g. 2081/2082"),
        ("bonus_pct", "number", "Bonus (stock) dividend, % of paid-up"),
        ("cash_pct", "number", "Cash dividend, % of paid-up"),
        ("total_pct", "number", "Total dividend, %"),
        ("announcement_date", "string", "Announcement date"),
        ("book_close_date", "string", "Book close date"),
        ("book_closed", "boolean", "Whether the book close has happened"),
        ("distribution_date", "string", "Distribution date"),
        ("bonus_listing_date", "string", "Bonus share listing date"),
    ],
    "right_shares.csv": [
        ("company_id", "integer", "Source company id"),
        ("symbol", "string", "NEPSE ticker symbol"),
        ("company_name", "string", "Company name"),
        ("ratio", "string", "Right ratio, e.g. 1:0.50"),
        ("right_per_share", "number", "Right shares per existing share"),
        ("total_units", "number", "Total right units issued"),
        ("issue_price", "number", "Issue price, NPR"),
        ("book_close_date", "string", "Book close date"),
        ("opening_date", "string", "Subscription opening date"),
        ("closing_date", "string", "Subscription closing date"),
        ("listing_date", "string", "Listing date"),
        ("issue_manager", "string", "Issue manager"),
        ("share_type", "string", "Share type"),
        ("status", "integer", "Source status code"),
        ("announcement_link", "string", "Link to the announcement"),
    ],
    "mergers_acquisitions.csv": [
        ("status", "string", "Completed or In process"),
        ("merging_companies", "string", "Companies taking part"),
        ("new_symbol", "string", "Symbol after the merger"),
        ("new_company_name", "string", "Name after the merger"),
        ("new_company_id", "integer", "Source company id after the merger"),
        ("sector", "string", "Sector"),
        ("action", "string", "Merger or Acquisition"),
        ("action_code", "integer", "Source action code"),
        ("swap_ratio", "string", "Share swap ratio"),
        ("trading", "string", "Whether the new entity is trading"),
        ("mou_date", "string", "MoU date"),
        ("final_approval_date", "string", "Final approval date"),
        ("joint_transaction_date", "string", "Joint transaction date"),
    ],
    "merged_companies.csv": [
        ("company_id", "integer", "Source company id"),
        ("symbol", "string", "Former symbol"),
        ("company_name", "string", "Former company name"),
        ("operation_date", "string", "Date the company stopped operating separately, where known"),
    ],
}

FILE_DESC = {
    "nepse_prices.csv": "Daily OHLC, volume and turnover for every company, raw and adjusted for bonus/right issues. Long format, one row per symbol per trading day.",
    "companies.csv": "One row per company: sector, share structure, market cap and the date range covered in nepse_prices.csv.",
    "indices.csv": "Daily OHLC for the NEPSE Index (since 1997) and 17 sub-indices, long format.",
    "indices_close_wide.csv": "Index closing values, one column per index.",
    "forex.csv": "Nepal Rastra Bank official exchange rates for 22 currencies, daily since 2000, long format.",
    "forex_wide.csv": "NRB buying rate per 1 unit, one column per currency.",
    "dividends.csv": "Cash and bonus dividends by company and fiscal year.",
    "right_shares.csv": "Right share issues.",
    "mergers_acquisitions.csv": "Mergers and acquisitions, completed and in process.",
    "merged_companies.csv": "Companies that no longer trade separately because of a merger.",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", default="zeuton", help="Kaggle username (owner of the dataset)")
    args = ap.parse_args()

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()

    # prices: full local datasheet if present, else the per-company files kept on GitHub
    combined = DATA / "datasheet" / "all_companies_prices.csv"
    if combined.exists():
        p = pd.read_csv(combined)
    else:
        files = sorted((DATA / "prices" / "by_company").glob("*/*.csv"))
        if not files:
            raise SystemExit("no price files: need data/datasheet/all_companies_prices.csv or data/prices/by_company/")
        p = pd.concat([pd.read_csv(fn) for fn in files], ignore_index=True)
    p = p.drop_duplicates(["date", "symbol"], keep="last").sort_values(["date", "symbol"])
    p.to_csv(OUT / "nepse_prices.csv", index=False)

    # companies: fundamentals + coverage
    src = DATA / "companies.csv"
    if not src.exists():
        src = DATA / "prices" / "by_company" / "companies.csv"
    c = pd.read_csv(src).drop(columns=["sector_id", "rows", "first_date", "last_date", "file", "status"],
                              errors="ignore")
    cov = p.groupby("symbol").date.agg(price_rows="count", first_date="min", last_date="max").reset_index()
    c = c.merge(cov, on="symbol", how="left").sort_values(["sector", "symbol"])
    c["price_rows"] = c["price_rows"].astype("Int64")
    c.to_csv(OUT / "companies.csv", index=False)

    # indices
    shutil.copy(DATA / "market" / "indices_all.csv", OUT / "indices.csv")
    shutil.copy(DATA / "market" / "indices_close_wide.csv", OUT / "indices_close_wide.csv")

    # forex: the NRB API gives 0 for currencies it did not quote that day -> blank
    f = pd.read_csv(DATA / "market" / "forex.csv")
    for col in ["buy", "sell", "buy_per_unit", "sell_per_unit"]:
        f.loc[f[col] == 0, col] = pd.NA
    f.to_csv(OUT / "forex.csv", index=False)
    fw = pd.read_csv(DATA / "market" / "forex_wide.csv")
    fw = fw.set_index("date").mask(lambda d: d == 0).reset_index()
    fw.to_csv(OUT / "forex_wide.csv", index=False)

    # corporate actions
    for name in ["dividends", "right_shares", "mergers_acquisitions", "merged_companies"]:
        if (DATA / "corporate_actions" / f"{name}.csv").exists():
            shutil.copy(DATA / "corporate_actions" / f"{name}.csv", OUT / f"{name}.csv")
        else:
            print(f"skipping {name}.csv (no data/corporate_actions/{name}.csv)")

    # document only the files written, and only the columns each one actually has
    for fn in list(COLS):
        if not (OUT / fn).exists():
            del COLS[fn]
            continue
        cols = list(pd.read_csv(OUT / fn, nrows=0).columns)
        known = {n: (n, t, d) for n, t, d in COLS[fn]}
        if fn == "indices_close_wide.csv":
            COLS[fn] = [COLS[fn][0]] + [(n, "number", f"{n} close") for n in cols[1:]]
        elif fn == "forex_wide.csv":
            COLS[fn] = [COLS[fn][0]] + [(n, "number", f"NRB buying rate, NPR per 1 {n}") for n in cols[1:]]
        else:
            COLS[fn] = [known.get(n, (n, "string", n.replace("_", " "))) for n in cols]

    # stats for the description
    i = pd.read_csv(OUT / "indices.csv")
    stats = dict(
        n_companies=p.symbol.nunique(), n_rows=len(p), p_min=p.date.min(), p_max=p.date.max(),
        n_sectors=p.sector.nunique(), i_min=i.date.min(), fx_min=f.date.min(), fx_max=f.date.max(),
        n_ccy=f.currency.nunique(), n_div=len(pd.read_csv(OUT / "dividends.csv")) if (OUT / "dividends.csv").exists() else 0,
    )
    readme = build_readme(stats)
    (OUT / "README.md").write_text(readme, encoding="utf-8")

    meta = {
        "title": TITLE,
        "subtitle": SUBTITLE,
        "id": f"{args.user}/{SLUG}",
        "licenses": [{"name": LICENSE}],
        "isPrivate": False,
        "keywords": ["finance", "investing", "time series analysis", "asia", "economics"],
        "description": readme,
        "resources": [
            {
                "path": fn,
                "description": FILE_DESC[fn],
                "schema": {"fields": [{"name": n, "type": t, "description": d} for n, t, d in COLS[fn]]},
            }
            for fn in COLS
        ],
    }
    (OUT / "dataset-metadata.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    for fn in sorted(OUT.iterdir()):
        print(f"{fn.stat().st_size / 1e6:9.1f} MB  {fn.name}")


def build_readme(s):
    def table(fn):
        rows = "\n".join(f"| `{n}` | {d} |" for n, _, d in COLS[fn])
        return f"| Column | Description |\n|---|---|\n{rows}"

    extra = (f", and corporate actions ({s['n_div']:,} dividend records, right issues, mergers)"
             if s["n_div"] else "")

    return f"""# NEPSE: Nepal Stock Exchange market data

Daily trading history for **{s['n_companies']} companies** listed on the Nepal Stock Exchange (NEPSE),
{s['n_rows']:,} price rows from {s['p_min']} to {s['p_max']}, across {s['n_sectors']} sectors.
Alongside the prices: the NEPSE Index since {s['i_min']} and 17 sub-indices, official Nepal Rastra
Bank exchange rates for {s['n_ccy']} currencies since {s['fx_min']}{extra}.

Prices come both **as traded** and **adjusted** for bonus shares and right issues, so long-run
returns can be computed without the jumps that NEPSE's frequent bonus issues cause.

## Files

| File | Contents |
|---|---|
""" + "\n".join(f"| `{fn}` | {FILE_DESC[fn]} |" for fn in COLS) + f"""

## Columns

""" + "\n\n".join(f"### {fn}\n{table(fn)}" for fn in COLS) + f"""

## How the adjusted prices work

For each company, every bonus issue and right issue after a given day is applied backwards:
a bonus of b% divides earlier prices by (1 + b/100), and a right issue of r new shares per share
at issue price P replaces an earlier price X with (X + r·P) / (1 + r). The `adjustments` column
lists the events applied to that row. On the most recent days `adj_*` equals the raw price.
Cash dividends are not adjusted for.

## Notes and caveats

- Dates are Gregorian (AD). `fiscal_year` in dividends is the Nepali fiscal year in Bikram Sambat.
- All money values are Nepalese Rupees (NPR).
- The data is reproduced as published by the sources. A few hundred of the 530k price rows have
  a close outside the day's high/low range, or a high below the low, mostly in the early years.
- `turnover` is 0 on about 3,800 early rows where the source has no figure.
- Forex rates the NRB did not quote on a day are left empty.
- Delisted and merged companies that no longer appear on the source are not in the price file;
  `merged_companies.csv` lists them.
- Insurance sector indices were split in 2018 (Life / Non Life); the old `Insurance` index stops then.

## Updates

A new version is published every two weeks by a GitHub Actions workflow in the source repository,
which rebuilds these files from the daily-updated data there.

## Sources and credit

- Prices, indices, company data, dividends, right issues and mergers:
  [ShareSansar](https://www.sharesansar.com/)
- Exchange rates: [Nepal Rastra Bank](https://www.nrb.org.np/forex/) official forex API

This dataset is an independent compilation for research and education. It is not affiliated
with or endorsed by NEPSE, ShareSansar or Nepal Rastra Bank. Check figures against the official
sources before making investment decisions.

The scraper and daily-update code: [github.com/zeutonx/NEPSE-Data](https://github.com/zeutonx/NEPSE-Data).

## License

{LICENSE.replace('-', ' ', 2)}: free to share and adapt for non-commercial use with credit to this
dataset and the sources above.
"""


if __name__ == "__main__":
    main()
