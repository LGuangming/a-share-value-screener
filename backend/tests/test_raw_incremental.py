import unittest
from datetime import date
from value_screener.raw_incremental import day_stem, collect_rows, code_coverage

class IncrementalTests(unittest.TestCase):
    def test_daily_paths_do_not_overwrite_completed_years(self):
        self.assertEqual(day_stem("daily_prices",date(2026,11,24)),
            "a-share-backtest/01_raw_data/daily_prices/year=2026/month=11/day=20261124")
    def test_incomplete_market_overlap(self):
        baseline={str(x) for x in range(100)}
        with self.assertRaises(RuntimeError):
            code_coverage("adj_factor",set(list(baseline)[:90]),baseline,0)
        code_coverage("suspend_d",set(),baseline,0)
    def test_duplicate_and_wrong_date(self):
        row=dict(ts_code="600000.SH",trade_date="20261124",open=10,high=11,low=9,close=10,vol=20,amount=30)
        self.assertEqual(len(collect_rows("daily_prices",[row],date(2026,11,24))),1)
        with self.assertRaises(RuntimeError):
            collect_rows("daily_prices",[row,row],date(2026,11,24))
        with self.assertRaises(RuntimeError):
            collect_rows("daily_prices",[row],date(2026,11,23))
