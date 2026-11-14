"""Offline tests against HTML/JSON captured from sharesansar.com (Nov 2026)."""
from pathlib import Path
from unittest import mock

from bs4 import BeautifulSoup

from sharesansar import scrapers
from sharesansar.parse import snake, to_number

FIX = Path(__file__).parent / "fixtures"


def test_to_number():
    assert to_number("1,905.00") == 1905.0
    assert to_number("44") == 44
    assert to_number("-0.55") == -0.55
    assert to_number("-") is None
    assert to_number("2026-11-13") == "2026-11-13"


def test_snake():
    assert snake("Prev. Close") == "prev_close"
    assert snake("Diff %") == "diff_pct"
    assert snake("52 Weeks High") == "weeks_high_52"


def test_price_table():
    as_of, rows = scrapers._price_table((FIX / "prices.html").read_text())
    assert as_of == "2026-11-13"
    assert len(rows) == 2
    r = rows[0]
    assert r["symbol"] == "ACLBSL"
    assert r["company_name"].startswith("Aarambha Chautari")
    assert (r["open"], r["high"], r["low"], r["close"]) == (817.0, 817.0, 795.0, 813.0)
    assert r["volume"] == 1905.0 and r["transactions"] == 44
    assert r["pct_change"] == -0.55 and r["week52_high"] == 1095.0


def test_no_record_day():
    html = '<h5>As of : 2026-11-11</h5><table id="headFixed"><thead><tr><th>S.No</th><th>Symbol</th></tr></thead>' \
           '<tbody><tr><td colspan="24">No Record Found.</td></tr></tbody></table>'
    assert scrapers._price_table(html)[1] == []


def test_company_info():
    client = mock.Mock()
    client.soup.return_value = BeautifulSoup((FIX / "company_nabil.html").read_text(), "lxml")
    info = scrapers.company_info(client, "nabil")
    assert info["company_id"] == 16
    assert info["sector"] == "Commercial Bank"
    assert info["listed_shares"] == 284098483.0
    assert info["website_link"] == "https://www.nabilbank.com"
    assert info["share_registrar"] == "Nabil Investment Banking Limited"
    assert (info["week52_high"], info["week52_low"]) == (581.0, 485.0)
    assert info["last_price"] == 549.0


def test_company_list_paginates():
    page = lambda n, start: {
        "recordsTotal": 60, "recordsFiltered": 60,
        "data": [{"symbol": f"<a href='x'>S{start + i}</a>", "companyname": "<a>Co</a>", "shares": "1,000.00",
                  "prom_share": "510.00", "prom_share_per": " (51.00%)", "public_share": "490.00",
                  "public_share_per": " (49.00%)", "paidup": "100.00", "paidupcap": "100,000.00",
                  "marketcap": "250,000.00", "listing_date": "2018-08-22", "ltp": "250.00",
                  "date": "2026-11-13", "DT_Row_Index": start + i + 1} for i in range(n)],
    }
    client = mock.Mock()
    client.json.side_effect = [page(50, 0), page(10, 50)]
    rows = scrapers.company_list(client, sectors=[1])
    assert len(rows) == 60
    assert rows[0]["symbol"] == "S0" and rows[0]["sector"] == "Commercial Bank"
    assert rows[0]["promoter_pct"] == 51.0 and rows[0]["market_cap"] == 250000.0


def test_right_ratio():
    assert scrapers.right_ratio("100:15") == 0.15
    assert scrapers.right_ratio("1:0.50") == 0.5
    assert scrapers.right_ratio("") is None


def test_merger_rows():
    rec = {"companyid": 1690, "companies": "<p>Orchid Holdings Limited &amp; Mountain Glory Limited</p>",
           "action": "1", "swap_ratio": None, "tradingid": "0", "final_date": None,
           "transaction_date": "2026-05-01", "mou_date": None,
           "company": {"id": 1690, "symbol": "OGL", "companyname": "Orchid Glory Limited"},
           "sector": {"id": 6, "sectorname": "Hotel &amp; Tourism"}}
    client = mock.Mock()
    client.json.side_effect = [{"recordsTotal": 1, "data": [rec]}, {"recordsTotal": 0, "data": []}]
    rows = scrapers.mergers(client)
    assert rows == [{
        "status": "Completed", "merging_companies": "Orchid Holdings Limited & Mountain Glory Limited",
        "new_symbol": "OGL", "new_company_name": "Orchid Glory Limited", "new_company_id": 1690,
        "sector": "Hotel & Tourism", "action": "Merger", "swap_ratio": None, "trading": "No",
        "mou_date": None, "final_approval_date": None, "joint_transaction_date": "2026-05-01"}]


def test_adjust_bonus_and_right():
    from sharesansar.adjust import adjust_history, corporate_events
    hist = [{"date": "2026-11-01", "close": 500.0}, {"date": "2026-06-01", "close": 550.0},
            {"date": "2026-01-01", "close": 600.0}]
    divs = [{"book_close_date": "2026-09-30", "bonus_pct": 10.0, "cash_pct": 5.0, "fiscal_year": "82/83"}]
    rights = [{"book_close_date": "2026-03-01", "right_per_share": 0.5, "issue_price": 100.0, "ratio": "1:0.5"}]
    adj = adjust_history(hist, corporate_events(divs, rights))
    assert adj[0]["adj_close"] == 500.0                     # after every event
    assert adj[1]["adj_close"] == round(550 / 1.1, 2)        # before bonus only
    assert adj[2]["adj_close"] == round(((600 + 50) / 1.5) / 1.1, 2)  # right first, then bonus
    cash = adjust_history(hist, corporate_events(divs, [], include_cash=True))
    assert cash[1]["adj_close"] == round(550 / 1.1 - 5, 2)
