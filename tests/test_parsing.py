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
