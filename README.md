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
| `--dividends` | `data/dividends/<SYMBOL>.*` | bonus_share, cash_dividend, total_dividend, announcement/bookclose dates, fiscal year |

### Being polite

- `robots.txt` is checked before every request (it currently allows everything).
- One request every 2 seconds by default (`--delay`), with retry and backoff on errors.
- The site's tables page at 50 rows, so a full `all` run is roughly 20 requests for
  the company list plus one per company (~10–15 minutes for ~330 companies at 2 s).
  Price history adds one request per 50 days per company.

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
