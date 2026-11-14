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
```

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
