# NEPSE-Data

Tools for extracting Nepal Stock Exchange (NEPSE) data for analysis: company
fundamentals, daily prices, price history and floorsheets, starting with a
scraper for [ShareSansar](https://www.sharesansar.com/).

## ShareSansar scraper

```bash
pip install -r requirements.txt

python -m sharesansar companies                 # every listed company, all 17 sectors
python -m sharesansar prices                    # latest trading day, every traded scrip
python -m sharesansar prices --date 2026-11-04  # a past trading day
python -m sharesansar company NABIL NICA --history --floorsheet --dividends
python -m sharesansar all                       # companies + prices + every company profile
python -m sharesansar all --history 250         # ...plus last 250 days of OHLC per company
python -m sharesansar corporate-actions         # mergers, merged companies, right issues, dividends
python -m sharesansar company NICA --adjusted --history 500   # bonus/right-adjusted prices
python -m sharesansar datasheet                 # price datasheet: one CSV per company, by sector
```

### Market data: indices, gold/silver, forex

```bash
python -m sharesansar market                    # all three
python -m sharesansar market --no-forex         # skip one
```

```
data/market/
  indices/NEPSE_Index.csv, Sensitive_Index.csv, Banking_SubIndex.csv, ...   (18 indices, since 1997)
  indices_all.csv            every index, long format: date, index, open, high, low, close, change, pct_change, turnover
  indices_close_wide.csv     date x index closing values
  gold_silver.csv            date, item (Fine Gold / Tejabi Gold / Silver), price_per_tola, change
  forex.csv                  NRB rates: date, currency, name, unit, buy, sell, buy_per_unit, sell_per_unit
  forex_wide.csv             date x currency, buy rate per 1 unit
```

- Indices come from ShareSansar's index history (one request per index).
- Forex comes from the Nepal Rastra Bank's official rates API (daily since 2000). Later
  runs continue from the last date in `forex.csv`.
- ShareSansar only publishes today's gold/silver rate (FENEGOSIDA), not a history, so each
  run appends today's rate to `gold_silver.csv`. Run `market` once a day to build it up.

### Daily update on GitHub

`.github/workflows/daily-update.yml` runs every day at 18:00 Nepal time (12:15 UTC), and can
also be started by hand from the Actions tab ("Run workflow"). It runs the tests, then
`market` and `prices`, and commits `data/market/` and `data/prices/` back to the repository.
The price datasheet is not part of it: a full run takes ~45 minutes and the combined CSV is
larger than GitHub's 100 MB file limit.

### Price datasheet

`datasheet` downloads the full daily price history of every listed company and writes:

```
data/datasheet/
  companies.csv               index: symbol, name, sector, shares, market cap, rows, first/last date, file
  all_companies_prices.csv    every company combined
  Commercial_Bank/NABIL.csv
  Hydropower/UPPER.csv
  ...
```

Each company file is oldest day first: `date, symbol, company_name, sector, open, high, low,
close, pct_change, volume, turnover, adj_open, adj_high, adj_low, adj_close, adjustments`.
Duplicate dates from the site are dropped. It takes about 2 requests per company
(~45 minutes for all ~650 at 2 s); `--resume` keeps files already written, so an
interrupted run can pick up where it stopped. `--sector`, `--symbols`, `--no-adjusted` narrow it.

Files are written to `data/` (change with `--out`) as both CSV and JSON
(`--format csv|json|both`).

| Command | Output | Fields |
|---|---|---|
| `companies` | `data/companies.{csv,json}` | symbol, company_name, sector, listed/promoter/public shares and %, paid-up, total paid-up capital, market cap, listing date, LTP, as_of |
| `prices` | `data/prices/prices_<date>.*` | date, symbol, company_name, confidence, open, high, low, close, ltp, vwap, volume, prev_close, turnover, transactions, change, range, pct_change, range_pct, vwap_pct, avg_120d, avg_180d, week52_high, week52_low |
| `company` / `all` | `data/company_info.*` | company_id, name, sector, listed shares, paid-up, total paid-up value, operation date, phone, email, address, website, share registrar, latest quote (price, change, open/high/low/volume), 52-week high/low, 120/180-day averages |
| `--history [N]` | `data/history/<SYMBOL>.*` | date, open, high, low, close, pct_change, volume, turnover (newest first) |
| `--floorsheet` | `data/floorsheet/<SYMBOL>_<date>.*` | contract_no, buyer_broker, seller_broker, quantity, rate, amount |
| `--dividends` | `data/dividends/<SYMBOL>.*` | fiscal_year, bonus_pct, cash_pct, total_pct, announcement / book-close / distribution / bonus-listing dates |
| `--rights` | `data/right_shares/<SYMBOL>.*` | ratio, right_per_share, total_units, issue_price, book-close / opening / closing / listing dates, issue manager |
| `--adjusted` | `data/adjusted/<SYMBOL>.*` | price history plus adj_open/high/low/close and the list of adjustments applied |
| `corporate-actions` | `data/corporate_actions/mergers_acquisitions.*` | completed and in-process mergers/acquisitions: merging companies, new symbol, sector, swap ratio, trading, MoU / final approval / joint transaction dates |
| | `data/corporate_actions/merged_companies.*` | symbols delisted after merging |
| | `data/corporate_actions/right_shares.*` | every right issue on the market |
| | `data/corporate_actions/dividends.*` | every company's bonus and cash dividend by fiscal year |

`all` also writes the `corporate_actions` files (dividends for the latest 3 fiscal years
unless `--dividend-years` says otherwise).

### Adjusted prices

`--adjusted` rewrites older prices so they are comparable across book-close dates,
using the same formulas as ShareSansar's adjustment calculators:

- bonus b%: `P / (1 + b/100)`
- right r:1 at issue price S: `(P + r*S) / (1 + r)`
- cash c% (only with `--adjust-cash`): `P - c/100 * face value`

Events are applied oldest first to every price before their book-close date.

### Being polite

- `robots.txt` is checked before every request (it currently allows everything).
- One request every 2 seconds by default (`--delay`), with retry and backoff on errors.
- List endpoints are fetched whole in one request (`length=-1`). Walking them 50 rows at a
  time can silently repeat one row and drop another at a page boundary, because the site
  sorts on non-unique columns. 50-row paging is only used as a fallback or for `--history N`.
  A full `all` run is about one request per company (~10–15 minutes for ~330 companies at 2 s).

### Known quirks in the site's own data

- NABIL price history lists 2012-10-01 twice; de-duplicate on date before computing returns.
- A few dividend rows have bonus + cash ≠ total, and some symbol/year pairs appear twice.
- Many debentures, bonds and mutual funds have no promoter/public split or listing date.

### Using it from Python

```python
from sharesansar import SharesansarClient, scrapers

c = SharesansarClient()
as_of, prices = scrapers.share_prices(c)
info = scrapers.company_info(c, "NABIL")
hist = scrapers.price_history(c, info["company_id"], limit=250)
```

### Tests

```bash
pip install pytest && python -m pytest tests
```

Tests run offline against HTML captured from the live site.
