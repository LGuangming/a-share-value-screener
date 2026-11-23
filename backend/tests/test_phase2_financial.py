"""Guard phase-2 disclosure periods and single-stock financial collection."""
import unittest
from value_screener.phase2_disclosures import reporting_periods
from value_screener.phase2_financial import FIELDS, fetch_code


class Phase2FinancialTests(unittest.TestCase):
    def test_periods_are_quarterly(self):
        xs = reporting_periods(2020, 2021)
        self.assertEqual(len(xs), 8)
        self.assertEqual(xs[0], "20200331")
        self.assertEqual(xs[-1], "20211231")

    def test_no_fake_pit_data_from_empty_reports(self):
        class Client:
            def query(self, api_name, params, fields):
                return []
        rows, dup = fetch_code(Client(), "balancesheet", "600000.SH")
        self.assertEqual(rows, [])
        self.assertEqual(dup, 0)

    def test_preserves_distinct_versions(self):
        class Client:
            def query(self, api_name, params, fields):
                return [
                    {"ts_code": "600000.SH", "end_date": "20241231",
                     "f_ann_date": "20250401", "total_liab": 100},
                    {"ts_code": "600000.SH", "end_date": "20241231",
                     "f_ann_date": "20250501", "total_liab": 120},
                ]
        rows, dup = fetch_code(Client(), "balancesheet", "600000.SH")
        self.assertEqual(len(rows), 2)
        self.assertEqual(dup, 0)

    def test_rejects_wrong_stock(self):
        class Client:
            def query(self, api_name, params, fields):
                return [{"ts_code": "000001.SZ", "end_date": "20241231"}]
        with self.assertRaisesRegex(RuntimeError, "Unexpected code"):
            fetch_code(Client(), "cashflow", "600000.SH")

    def test_fields_contains_ncav_inputs(self):
        self.assertIn("total_cur_assets", FIELDS["balancesheet"])
        self.assertIn("total_liab", FIELDS["balancesheet"])
        self.assertIn("n_cashflow_act", FIELDS["cashflow"])


if __name__ == "__main__":
    unittest.main()
