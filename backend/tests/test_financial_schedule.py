import unittest
from datetime import date
from value_screener.financial_schedule import recent_closed_quarters, confirmed_disclosures


class DisclosureScheduleTests(unittest.TestCase):
    def test_quarters_in_november(self):
        self.assertEqual(recent_closed_quarters(date(2026, 11, 29)),
                         [date(2026, 9, 30), date(2026, 6, 30)])

    def test_quarters_cross_new_year(self):
        self.assertEqual(recent_closed_quarters(date(2027, 1, 2)),
                         [date(2026, 12, 31), date(2026, 9, 30)])

    def test_planned_or_future_date_is_not_release(self):
        rows = [
            {"ts_code": "000001.SZ", "end_date": "20260930", "pre_date": "20261028"},
            {"ts_code": "600001.SH", "end_date": "20260930", "actual_date": "20261201"},
            {"ts_code": "000002.SZ", "end_date": "20260630", "actual_date": "20260730"},
        ]
        self.assertEqual(confirmed_disclosures(rows, date(2026, 9, 30), date(2026, 11, 29)), [])

    def test_actual_date_and_duplicate_deduped(self):
        rows = [
            {"ts_code": "000001.SZ", "end_date": "20260930", "actual_date": "20261020"},
            {"ts_code": "000001.SZ", "end_date": "20260930", "actual_date": "20261028"},
            {"ts_code": "000002.SZ", "end_date": "20260930", "actual_date": "INVALID"},
        ]
        self.assertEqual(confirmed_disclosures(rows, date(2026, 9, 30), date(2026, 11, 29)),
                         [{"ts_code": "000001.SZ", "period": date(2026, 9, 30),
                           "actual_date": date(2026, 10, 28)}])


if __name__ == "__main__":
    unittest.main()
