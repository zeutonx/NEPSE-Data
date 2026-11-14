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
    # length=-1 comes back short (50 of 60) -> falls back to 50-row pages from the start
    client.json.side_effect = [page(50, 0), page(50, 0), page(10, 50)]
    rows = scrapers.company_list(client, sectors=[1])
    assert len(rows) == 60
    assert [c.kwargs["params"]["length"] for c in client.json.call_args_list] == [-1, 50, 50]
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
        "sector": "Hotel & Tourism", "action": "Merger", "action_code": "1", "swap_ratio": None, "trading": "No",
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


def test_adjust_ignores_book_close_after_last_trading_day():
    # bonus announced, book close still ahead (API, 2026-11-24): prices must stay unadjusted
    from sharesansar.adjust import adjust_history, corporate_events
    hist = [{"date": "2026-11-13", "close": 333.0}, {"date": "2026-11-05", "close": 333.7}]
    divs = [{"book_close_date": "2026-11-24", "bonus_pct": 5.0, "cash_pct": 0.26, "fiscal_year": "2082/2083"},
            {"book_close_date": "2026-11-06", "bonus_pct": 10.0, "fiscal_year": "2081/2082"}]
    adj = adjust_history(hist, corporate_events(divs, []))
    assert adj[0]["adj_close"] == 333.0 and adj[0]["adjustments"] == ""
    assert adj[1]["adj_close"] == round(333.7 / 1.1, 2) and "2082/2083" not in adj[1]["adjustments"]


def test_company_info_placeholders_become_none():
    page = """<h4>Share Registrar: <span>ADB Bond</span></h4>
    <table id="myTableCInfo"><tbody>
    <tr><td>Name</td><td>ADB Bond</td></tr><tr><td>Listed Shares</td><td> - </td></tr>
    <tr><td>Email</td><td> - </td></tr><tr><td>Website Link</td><td><a href="//"></a></td></tr>
    </tbody></table>"""
    client = mock.Mock()
    client.soup.return_value = BeautifulSoup(page, "lxml")
    info = scrapers.company_info(client, "adblb")
    assert info["listed_shares"] is None and info["email"] is None
    assert info["website_link"] is None and info["share_registrar"] is None

    client.soup.return_value = BeautifulSoup(
        '<table id="myTableCInfo"><tr><td>Website Link</td><td><a href="//http://www.nicasiabank.com/"></a></td></tr></table>',
        "lxml")
    assert scrapers.company_info(client, "nica")["website_link"] == "http://www.nicasiabank.com/"


def test_dividend_row_dates():
    row = scrapers._dividend_row({"bonus_share": "", "cash_dividend": "10.8", "total_dividend": "10.8",
                                  "announcement_date": "2026-09-07", "distribution_date": "",
                                  "bookclose_date": "2026-09-30 [Closed]", "bonus_listing_date": "-",
                                  "year": "2082/2083"})
    assert row["book_close_date"] == "2026-09-30" and row["book_closed"] is True
    assert row["distribution_date"] is None and row["bonus_listing_date"] is None
    assert row["bonus_pct"] == 0.0 and row["cash_pct"] == 10.8


def test_datatable_fetches_all_in_one_request():
    client = mock.Mock()
    client.json.return_value = {"recordsTotal": 322, "data": [{"id": i} for i in range(322)]}
    rows = list(scrapers._datatable(client, "/existing-issues", {"type": 3}, method="GET"))
    assert len(rows) == 322 and client.json.call_count == 1
    assert client.json.call_args.kwargs["params"]["length"] == -1


def test_datatable_warns_on_boundary_repeat(caplog):
    # tie-sorted results: row 49 shows up again as the first row of page 2, row 50 is lost
    first = [{"id": i, "DT_Row_Index": i + 1} for i in range(50)]
    second = [{"id": 49, "DT_Row_Index": 51}] + [{"id": i, "DT_Row_Index": i + 1} for i in range(51, 60)]
    client = mock.Mock()
    client.json.side_effect = [{"recordsTotal": 60, "data": first}, {"recordsTotal": 60, "data": second}]
    rows = list(scrapers._datatable(client, "/x", {}, method="GET", limit=60))
    assert len(rows) == 60
    assert "repeats across a page boundary" in caplog.text


def test_merger_action_matches_site_renderer():
    assert scrapers._merger_action("1") == "Merger"
    assert [scrapers._merger_action(c) for c in ("0", "", None)] == ["Acquisition"] * 3
    assert scrapers._yes_no("1") == "Yes" and [scrapers._yes_no(c) for c in ("0", "", None)] == ["No"] * 3


def test_merged_company_operation_date_from_either_detail():
    recs = [{"id": 1, "symbol": "<a>A</a>", "companyname": "<a>A Ltd</a>",
             "company_detail": {"operationdate": "2020-01-05"}, "companyDetail": {"operationdate": ""}},
            {"id": 2, "symbol": "<a>B</a>", "companyname": "<a>B Ltd</a>",
             "company_detail": {"operationdate": None}, "companyDetail": {"operationdate": ""}}]
    client = mock.Mock()
    client.json.return_value = {"recordsTotal": 2, "data": recs}
    rows = scrapers.merged_companies(client)
    assert [r["operation_date"] for r in rows] == ["2020-01-05", None]


def test_nbsp_in_names():
    assert scrapers._strip_html("<p>Wean Nepal Laghubitta &amp;&nbsp;Swarojgar Laghubitta</p>") == \
        "Wean Nepal Laghubitta & Swarojgar Laghubitta"


def test_dividend_years_ordered_by_label():
    client = mock.Mock()
    client.soup.return_value = BeautifulSoup(
        '<select name="year"><option value="">All</option><option value="24">2075/2076</option>'
        '<option value="5">2074/2075</option><option value="23">2063/2064</option>'
        '<option value="32">2082/2083</option></select>', "lxml")
    client.json.return_value = {"recordsTotal": 0, "data": []}
    scrapers.all_dividends(client, years=3)
    assert [c.kwargs["params"]["year"] for c in client.json.call_args_list] == [32, 24, 5]


def test_datasheet_build(tmp_path):
    from sharesansar import datasheet
    companies = [
        {"symbol": "NABIL", "company_name": "Nabil Bank Limited", "sector": "Commercial Bank", "listed_shares": 1},
        {"symbol": "OHL", "company_name": "Oriental Hotels", "sector": "Hotel & Tourism", "listed_shares": 2},
    ]
    hist = {16: [{"date": "2026-11-13", "open": 550.0, "high": 550.0, "low": 546.0, "close": 549.0},
                 {"date": "2026-09-29", "open": 560.0, "high": 570.0, "low": 555.0, "close": 566.0},
                 {"date": "2026-09-29", "open": 560.0, "high": 570.0, "low": 555.0, "close": 566.0}],
            17: []}
    divs = [{"symbol": "NABIL", "book_close_date": "2026-09-30", "bonus_pct": 5.0, "cash_pct": 0, "fiscal_year": "x"}]
    with mock.patch.object(scrapers, "company_list", return_value=companies), \
         mock.patch.object(scrapers, "all_dividends", return_value=divs), \
         mock.patch.object(scrapers, "right_shares", return_value=[]), \
         mock.patch.object(scrapers, "company_info",
                           side_effect=lambda c, s: {"company_id": 16 if s == "NABIL" else 17, "paid_up": 100.0}), \
         mock.patch.object(scrapers, "price_history", side_effect=lambda c, cid: list(hist[cid])):
        root = datasheet.build(mock.Mock(), tmp_path)

    nabil = datasheet._read_csv(root / "Commercial_Bank" / "NABIL.csv")
    assert [r["date"] for r in nabil] == ["2026-09-29", "2026-11-13"]  # deduped, oldest first
    assert nabil[0]["adj_close"] == "539.05" and nabil[1]["adj_close"] == "549.0"
    assert nabil[0]["sector"] == "Commercial Bank" and nabil[0]["company_name"] == "Nabil Bank Limited"
    assert (root / "Hotel_and_Tourism" / "OHL.csv").exists()
    index = datasheet._read_csv(root / "companies.csv")
    assert [(e["symbol"], e["rows"], e["file"]) for e in index] == [
        ("NABIL", "2", "Commercial_Bank/NABIL.csv"), ("OHL", "0", "Hotel_and_Tourism/OHL.csv")]
    assert len(datasheet._read_csv(root / "all_companies_prices.csv")) == 2

    # --resume keeps both files without refetching and must not blank company_id in the index
    with mock.patch.object(scrapers, "company_list", return_value=companies), \
         mock.patch.object(scrapers, "all_dividends", return_value=divs), \
         mock.patch.object(scrapers, "right_shares", return_value=[]), \
         mock.patch.object(scrapers, "company_info", side_effect=AssertionError("refetched")):
        datasheet.build(mock.Mock(), tmp_path, resume=True)
    index = datasheet._read_csv(root / "companies.csv")
    assert [(e["symbol"], e["company_id"], e["rows"], e["status"]) for e in index] == [
        ("NABIL", "16", "2", "kept"), ("OHL", "17", "0", "kept")]


def test_index_history_sorted_and_deduped():
    from sharesansar import markets
    recs = [{"index_id": 12, "open": "2649.60", "high": "2656.23", "low": "2639.67", "current": "2648.89",
             "per_change": "-0.23", "change_": "-6.32", "turnover": "3054274725.05", "published_date": "2026-11-13"},
            {"index_id": 12, "open": "173.26", "high": "173.26", "low": "173.26", "current": "173.26",
             "per_change": "0.00", "change_": "0.00", "turnover": "0.00", "published_date": "1997-07-20"},
            {"index_id": 12, "open": "173.26", "high": "173.26", "low": "173.26", "current": "173.26",
             "per_change": "0.00", "change_": "0.00", "turnover": "0.00", "published_date": "1997-07-20"}]
    client = mock.Mock()
    client.json.return_value = {"recordsTotal": 3, "data": recs}
    rows = markets.index_history(client, 12)
    assert [r["date"] for r in rows] == ["1997-07-20", "2026-11-13"]
    assert rows[1]["index"] == "NEPSE Index" and rows[1]["close"] == 2648.89 and rows[1]["pct_change"] == -0.23


def test_gold_silver_today():
    from sharesansar import markets
    html = ('<p>As of <span class="text-org">2026-11-13</span> </p><table><thead><tr><th>Name</th><th>Price(Rs)</th>'
            '<th>Change(Rs)</th></tr></thead><tbody><tr><td>Fine Gold</td><td> 271,700 </td><td> -12600 </td></tr>'
            '<tr><td>Silver</td><td> 3,700 </td><td> -155 </td></tr></tbody></table>')
    client = mock.Mock()
    client.csrf = "tok"
    client.request.return_value = mock.Mock(text=html)
    rows = markets.gold_silver_today(client)
    assert rows == [{"date": "2026-11-13", "item": "Fine Gold", "price_per_tola": 271700, "change": -12600},
                    {"date": "2026-11-13", "item": "Silver", "price_per_tola": 3700, "change": -155}]


def test_nrb_forex_per_unit():
    from sharesansar import markets
    payload = {"data": {"payload": [{"date": "2026-11-01", "rates": [
        {"currency": {"iso3": "INR", "name": "Indian Rupee", "unit": 100}, "buy": "160.00", "sell": "160.15"},
        {"currency": {"iso3": "USD", "name": "U.S. Dollar", "unit": 1}, "buy": "151.63", "sell": "152.23"}]}]},
        "pagination": {"page": 1, "pages": 1}}
    client = mock.Mock()
    client.request.return_value = mock.Mock(json=lambda: payload)
    rows = markets.nrb_forex(client, "2026-11-01", "2026-11-01")
    assert rows[0]["currency"] == "INR" and rows[0]["buy_per_unit"] == 1.6
    assert rows[1]["sell_per_unit"] == 152.23


def _nrb_day(date, usd="150.00"):
    return {"date": date, "rates": [{"currency": {"iso3": "USD", "name": "U.S. Dollar", "unit": 1},
                                     "buy": usd, "sell": usd}]}


def test_nrb_forex_overlapping_pages_and_windows():
    from sharesansar import markets
    pages = {1: [_nrb_day("2020-02-23"), _nrb_day("2020-01-01")],
             2: [_nrb_day("2020-02-23"), _nrb_day("2020-01-02")]}  # NRB repeats a day across pages

    def fake(method, path, params):
        body = {"data": {"payload": pages[params["page"]] if params["from"] == "2020-01-01" else []},
                "pagination": {"page": params["page"], "pages": 2 if params["from"] == "2020-01-01" else 1}}
        return mock.Mock(json=lambda: body)

    client = mock.Mock()
    client.request.side_effect = fake
    rows = markets.nrb_forex(client, "2020-01-01", "2020-06-30")
    assert sorted(r["date"] for r in rows) == ["2020-01-01", "2020-01-02", "2020-02-23"]
    windows = [(c.kwargs["params"]["from"], c.kwargs["params"]["to"]) for c in client.request.call_args_list
               if c.kwargs["params"]["page"] == 1]
    assert windows == [("2020-01-01", "2020-03-30"), ("2020-03-31", "2020-06-28"), ("2020-06-29", "2020-06-30")]


def test_update_forex_resumes_and_clears_duplicates(tmp_path, monkeypatch):
    from sharesansar import markets
    old = {"date": "2026-11-12", "currency": "USD", "name": "U.S. Dollar", "unit": "1", "buy": "150", "sell": "151",
           "buy_per_unit": "150", "sell_per_unit": "151"}
    markets._write(tmp_path / "market" / "forex.csv", [old, old], markets.FOREX_COLUMNS)
    calls = []

    def fake_fetch(client, start, end, prev=None):
        assert prev == {"USD": 150.0}
        calls.append(start)
        return [{**old, "date": "2026-11-13"}]

    monkeypatch.setattr(markets, "SharesansarClient", lambda **kw: None)
    monkeypatch.setattr(markets, "nrb_forex", fake_fetch)
    assert markets.update_forex(tmp_path) == 2
    assert calls == ["2026-11-13"]
    rows = markets._read(tmp_path / "market" / "forex.csv")
    assert [r["date"] for r in rows] == ["2026-11-12", "2026-11-13"]


def test_pick_forex_prefers_filled_and_closest_rate():
    from sharesansar import markets

    def row(date, buy):
        return {"date": date, "currency": "USD", "name": "U.S. Dollar", "unit": 1, "buy": buy, "sell": buy,
                "buy_per_unit": buy, "sell_per_unit": buy}

    cands = {("2020-02-27", "USD"): [row("2020-02-27", 114.36)],
             ("2020-02-28", "USD"): [row("2020-02-28", 119.44), row("2020-02-28", 114.19)],  # stray set first
             ("2026-08-06", "USD"): [row("2026-08-06", None), row("2026-08-06", 151.9)]}     # empty entry first
    rows = markets._pick_forex(cands)
    assert [(r["date"], r["buy"]) for r in rows] == [
        ("2020-02-27", 114.36), ("2020-02-28", 114.19), ("2026-08-06", 151.9)]
    rows = markets._pick_forex({("2020-02-28", "USD"): [row("2020-02-28", 119.44), row("2020-02-28", 114.19)]},
                               prev={"USD": 114.36})
    assert rows[0]["buy"] == 114.19
