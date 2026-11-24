"""Offline calendar selection regression tests."""
import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo
from value_screener.market_session import market_session, early_probe, minimum_quotes, validate_quotes
from value_screener.tushare_client import TushareError

CN = ZoneInfo("Asia/Shanghai")

class Calendar:
    def query(self, api, params, fields):
        assert api == "trade_cal"
        return [{"cal_date": x, "is_open": 1} for x in
                ("20261120", "20261123", "20261124")
                if params["start_date"] <= x <= params["end_date"]]

class MarketSessionTests(unittest.TestCase):
    def test_delayed_monday_workflow_on_tuesday_morning(self):
        self.assertEqual(market_session(Calendar(), None, datetime(2026,11,24,6,47,tzinfo=CN)), date(2026,11,23))
    def test_after_cutoff(self):
        self.assertEqual(market_session(Calendar(), None, datetime(2026,11,24,19,30,tzinfo=CN)), date(2026,11,24))
    def test_monday_preopen(self):
        self.assertEqual(market_session(Calendar(), None, datetime(2026,11,23,6,0,tzinfo=CN)), date(2026,11,20))
    def test_explicit_date_is_never_substituted(self):
        self.assertEqual(market_session(Calendar(), "2026-11-24", datetime(2026,11,24,6,47,tzinfo=CN)), date(2026,11,24))
    def test_future_date_rejected(self):
        with self.assertRaises(ValueError):
            market_session(Calendar(), "2026-11-25", datetime(2026,11,24,6,47,tzinfo=CN))

    def test_market_close_and_early_probe(self):
        for hour, minute, expected in ((14,59,23),(15,0,24),(17,48,24),(19,29,24),(19,30,24)):
            now = datetime(2026,11,24,hour,minute,tzinfo=CN)
            day = market_session(Calendar(), None, now)
            self.assertEqual(day, date(2026,11,expected))
            self.assertEqual(early_probe(day, None, now), (15,0) <= (hour,minute) < (19,30))
        self.assertFalse(early_probe(date(2026,11,24), "2026-11-24", datetime(2026,11,24,17,48,tzinfo=CN)))

    def test_coverage_threshold(self):
        now = datetime(2026,11,24,17,48,tzinfo=CN)
        self.assertEqual(minimum_quotes(5581, None, now.date(), now), 5302)
        self.assertEqual(minimum_quotes(5581, "2026-11-23", date(2026,11,23), now), 3907)

    def test_unique_valid_quotes_before_writes(self):
        day = date(2026,11,24)
        stocks = [{"ts_code":"600000.SH"},{"ts_code":"000001.SZ"}]
        valid = {"ts_code":"600000.SH","trade_date":"20261124","close":10,"total_mv":100}
        invalid = {"ts_code":"000001.SZ","trade_date":"20261124","close":10,"total_mv":None}
        self.assertEqual(validate_quotes(stocks, [valid,invalid], day), [valid])
        with self.assertRaises(TushareError):
            validate_quotes(stocks, [valid,valid], day)
        with self.assertRaises(TushareError):
            validate_quotes(stocks, [dict(valid,trade_date="20261123")], day)
