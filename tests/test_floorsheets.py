"""Offline tests for the full-day floorsheet collector."""
from unittest import mock

from sharesansar import floorsheets


def _t(sym, contract, qty, rate, buyer="1", seller="2", date="2026-11-24"):
    return {"date": date, "symbol": sym, "contract_no": str(contract), "buyer_broker": buyer,
            "seller_broker": seller, "quantity": qty, "rate": rate, "amount": qty * rate}


def test_order_trades_sorts_dedupes_and_numbers():
    rows = [_t("NABIL", 2026112401000003, 10, 551.0), _t("ADBL", 2026112402000002, 5, 330.0),
            _t("NABIL", 2026112401000001, 30, 549.9), _t("NABIL", 2026112401000003, 10, 551.0)]
    out = floorsheets.order_trades(rows)
    assert [(r["symbol"], r["contract_no"][-1], r["trade_seq"]) for r in out] == [
        ("ADBL", "2", 1), ("NABIL", "1", 1), ("NABIL", "3", 2)]


def test_summarize_flags_incomplete():
    trades = floorsheets.order_trades([_t("NABIL", 1, 30, 550.0, buyer="65"), _t("NABIL", 2, 10, 560.0)])
    prices = [{"symbol": "NABIL", "transactions": 2}, {"symbol": "ADBL", "transactions": 4}]
    s = {r["symbol"]: r for r in floorsheets.summarize(trades, prices, "2026-11-24")}
    n = s["NABIL"]
    assert (n["trades"], n["complete"], n["quantity"], n["amount"]) == (2, True, 40, 22100.0)
    assert (n["first_rate"], n["last_rate"], n["high"], n["low"]) == (550.0, 560.0, 560.0, 550.0)
    assert n["vwap"] == 552.5 and n["buyer_brokers"] == 2
    assert s["ADBL"]["trades"] == 0 and s["ADBL"]["complete"] is False


def test_write_day_roundtrip_is_deterministic(tmp_path):
    trades = floorsheets.order_trades([_t("NABIL", 1, 30, 550.0)])
    p = floorsheets.day_path(tmp_path, "2026-11-24")
    floorsheets.write_day(p, trades)
    first = p.read_bytes()
    floorsheets.write_day(p, trades)
    assert p.read_bytes() == first  # no spurious git diffs on re-runs
    rows = floorsheets.read_day(p)
    assert p.parent.name == "2026" and rows[0]["symbol"] == "NABIL" and rows[0]["trade_seq"] == "1"


def test_collect_day(tmp_path):
    prices = [{"symbol": "NABIL", "transactions": 2, "volume": 40.0},
              {"symbol": "ADBL", "transactions": 1, "volume": 5.0}]
    sheets = {"NABIL": [_t("NABIL", 2, 10, 560.0), _t("NABIL", 1, 30, 550.0)],
              "ADBL": [_t("ADBL", 7, 5, 330.0, date="2026-11-23")]}  # stale day: dropped
    with mock.patch.object(floorsheets.scrapers, "share_prices", return_value=("2026-11-24", prices)), \
            mock.patch.object(floorsheets.scrapers, "floorsheet", side_effect=lambda c, s: sheets[s]) as fs:
        assert floorsheets.collect_day(None, tmp_path) == 0
        rows = floorsheets.read_day(floorsheets.day_path(tmp_path, "2026-11-24"))
        assert [(r["symbol"], r["trade_seq"]) for r in rows] == [("NABIL", "1"), ("NABIL", "2")]
        summary = (tmp_path / "floorsheet" / "summary.csv").read_text().splitlines()
        assert len(summary) == 3 and "ADBL,0,1,False" in summary[1]
        calls = fs.call_count
        assert floorsheets.collect_day(None, tmp_path) == 0  # already saved: no new requests
        assert fs.call_count == calls


def test_symbols_sample_does_not_replace_day(tmp_path):
    prices = [{"symbol": "NABIL", "transactions": 1, "volume": 30.0}]
    with mock.patch.object(floorsheets.scrapers, "share_prices", return_value=("2026-11-24", prices)), \
            mock.patch.object(floorsheets.scrapers, "floorsheet", return_value=[_t("NABIL", 1, 30, 550.0)]):
        assert floorsheets.collect_day(None, tmp_path, symbols=["nabil"]) == 0
    day = floorsheets.day_path(tmp_path, "2026-11-24")
    assert not day.exists() and day.with_name("floorsheet_2026-11-24_sample.csv.gz").exists()
    assert not (tmp_path / "floorsheet" / "summary.csv").exists()
