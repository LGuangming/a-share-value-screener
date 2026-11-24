"""Offline calendar selection regression tests."""
import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo
from value_screener.market_session import market_session

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
