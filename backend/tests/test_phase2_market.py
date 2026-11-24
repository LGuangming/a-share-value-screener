"""Offline regression tests for phase-two research snapshots."""
import unittest
from value_screener.phase2_market import (
    DATASETS, validate_row, prefix, verify_month, verify_year,
)


class Phase2MarketTests(unittest.TestCase):
    def test_market_dataset_contract(self):
        self.assertEqual(set(DATASETS),
                         {"adj_factor", "stk_limit", "daily_basic", "suspend_d"})
        self.assertTrue(prefix("adj_factor", 2025).endswith("/year=2025"))

    def test_factor_valid_and_invalid(self):
        x = {"ts_code": "600000.SH", "trade_date": "20250813", "adj_factor": 3.0}
        self.assertEqual(validate_row("adj_factor", x, "20250813"),
                         ("600000.SH", "20250813", 3.0))
        x["adj_factor"] = -1
        with self.assertRaisesRegex(RuntimeError, "Invalid numeric"):
            validate_row("adj_factor", x, "20250813")

    def test_limits_crossed_and_duplicates_are_not_silently_fixed(self):
        x = {"ts_code": "000001.SZ", "trade_date": "20250813",
             "up_limit": 9.0, "down_limit": 10.0}
        with self.assertRaisesRegex(RuntimeError, "Crossed"):
            validate_row("stk_limit", x, "20250813")

    def test_unavailable_limit_is_not_converted_into_a_fake_price(self):
        row = {"ts_code": "000001.SZ", "trade_date": "20250102",
               "up_limit": 12.0, "down_limit": None}
        self.assertEqual(validate_row("stk_limit", row, "20250102")[-1], None)
        row["down_limit"] = 0
        self.assertEqual(validate_row("stk_limit", row, "20250102")[-1], 0)
        row["down_limit"] = -1
        with self.assertRaisesRegex(RuntimeError, "Invalid price limit"):
            validate_row("stk_limit", row, "20250102")

    def test_sparse_missing_valuation_is_preserved_not_zero(self):
        row = {"ts_code": "000001.SZ", "trade_date": "20200918",
               "total_mv": None, "total_share": 100.0}
        self.assertIsNone(validate_row("daily_basic", row, "20200918")[2])
        row["total_mv"] = ""
        self.assertIsNone(validate_row("daily_basic", row, "20200918")[2])
        for bad in (0, -1, float("nan"), "invalid"):
            row["total_mv"] = bad
            with self.assertRaises(RuntimeError):
                validate_row("daily_basic", row, "20200918")

    def test_suspension_types_and_nullable_timing(self):
        x = {"ts_code": "600000.SH", "trade_date": "20250813",
             "suspend_type": "S", "suspend_timing": None}
        self.assertEqual(validate_row("suspend_d", x, "20250813")[-1], None)
        x["suspend_type"] = "UNKNOWN"
        with self.assertRaisesRegex(RuntimeError, "Invalid suspension"):
            validate_row("suspend_d", x, "20250813")

    def test_no_year_manifest_does_not_claim_complete(self):
        class MissingS3:
            def get_object(self, Bucket, Key):
                raise KeyError(Key)
        self.assertIsNone(verify_year(MissingS3(), "private", "adj_factor", 2025))
        self.assertIsNone(verify_month(MissingS3(), "private",
                                       "adj_factor", 2025, "01"))


if __name__ == "__main__":
    unittest.main()
