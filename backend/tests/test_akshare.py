import unittest
from datetime import date
from unittest.mock import Mock

from value_screener.akshare_client import (
    normalize_spot, normalize_report, AKShareClient, AKShareError
)
from value_screener.akshare_test import code_list
from value_screener.rules import assess


class AKShareNormalizationTests(unittest.TestCase):
    def test_market_value_yuan_to_tushare_10000_yuan(self):
        quotes = normalize_spot([
            {"代码":"000910","名称":"测试甲","最新价":4.40,"总市值":2_409_000_000,
             "市净率":0.45,"换手率":2.1},
            {"代码":"600051","名称":"测试乙","最新价":5.01,"总市值":1_558_000_000},
            {"代码":"123456","名称":"invalid","最新价":1.0,"总市值":50}
        ])
        self.assertAlmostEqual(quotes["000910.SZ"]["total_mv"],240900)
        self.assertEqual(quotes["600051.SH"]["market_cap_yuan"], 1_558_000_000)
        self.assertEqual(quotes["600051.SH"]["pb"], None)
        assessment = assess({"ts_code":"000910.SZ","name":"测试甲"}, quotes["000910.SZ"],
                            {"total_cur_assets": 6e9,"total_liab":2e9,"total_assets":8e9})
        self.assertAlmostEqual(assessment["market_cap"],2_409_000_000)
        self.assertAlmostEqual(assessment["ncav"],4_000_000_000)

    def test_financial_notice_date_point_in_time(self):
        rows = [
            {"REPORT_DATE":"2026-09-30","NOTICE_DATE":"2026-10-28","TOTAL_CURRENT_ASSETS":9_979_000_000,
             "TOTAL_LIABILITIES":5_020_000_000,"TOTAL_ASSETS":14_000_000_000,
             "REPORT_TYPE_NAME":"合并期末"},
            {"REPORT_DATE":"2026-09-30","NOTICE_DATE":"2026-11-23","TOTAL_CURRENT_ASSETS":1,
             "TOTAL_LIABILITIES":1,"REPORT_TYPE_NAME":"合并期末"},
        ]
        got, period, announced, warnings=normalize_report(rows,"balancesheet",date(2026,11,23))
        self.assertEqual(period,date(2026,9,30))
        self.assertEqual(announced,date(2026,10,28))
        self.assertEqual(got["total_cur_assets"],9_979_000_000)
        self.assertNotIn("CONSOLIDATION_NOT_CONFIRMED", warnings)

    def test_never_guesses_missing_disclosure_date(self):
        row={"REPORT_DATE":"2026-09-30","TOTAL_CURRENT_ASSETS":100,"TOTAL_LIABILITIES":50}
        val, day, notice, warnings=normalize_report([row],"balancesheet",date(2026,11,23))
        self.assertIsNone(val)
        self.assertIn("NO_VERIFIABLE_DISCLOSURE",warnings)

    def test_parent_statement_rejected(self):
        row={"REPORT_DATE":"2026-09-30","NOTICE_DATE":"2026-10-28",
             "REPORT_TYPE_NAME":"母公司","TOTAL_CURRENT_ASSETS":100,"TOTAL_LIABILITIES":50}
        val,*_=normalize_report([row],"balancesheet",date(2026,11,23))
        self.assertIsNone(val)

    def test_accounting_unknown_is_not_zero(self):
        row={"REPORT_DATE":"2026-09-30","NOTICE_DATE":"2026-10-28",
             "TOTAL_CURRENT_ASSETS":4e9,"TOTAL_LIABILITIES":1e9}
        val,*_=normalize_report([row],"balancesheet",date(2026,11,23))
        self.assertIsNone(val["inventories"])
        self.assertIsNone(val["minority_int"])

    def test_stock_code_validation(self):
        self.assertEqual(code_list("000910,600051,000910"), ["000910.SZ","600051.SH"])
        with self.assertRaises(ValueError):
            code_list("000910; DROP TABLE stocks")
        with self.assertRaises(ValueError):
            code_list(",".join(["000910"]*10 + [f"60000{x}" for x in range(8)]))

    def test_mock_provider_still_uses_dataframe_contract(self):
        fake=Mock()
        fake.stock_zh_a_spot_em.return_value=Mock()
        fake.stock_zh_a_spot_em.return_value.to_dict.return_value=[
            {"代码":f"600{x:03d}","名称":f"Sample{x}","最新价":5,
             "总市值":1_000_000_000} for x in range(1001)
        ]
        client=AKShareClient(provider=fake)
        result=client.spot()
        self.assertGreaterEqual(len(result),1000)

    def test_bounded_individual_stock_quotes_no_market_wide_call(self):
        fake = Mock()
        fake.stock_individual_info_em.return_value.to_dict.return_value = [
            {"item":"股票代码","value":"000910"},
            {"item":"股票简称","value":"测试样本"},
            {"item":"最新","value":4.4},
            {"item":"总市值","value":2_409_000_000},
        ]
        result = AKShareClient(provider=fake).spot(["000910.SZ"])
        self.assertEqual(set(result),{"000910.SZ"})
        self.assertEqual(result["000910.SZ"]["pb"],None)
        self.assertAlmostEqual(result["000910.SZ"]["total_mv"],240900)
        fake.stock_zh_a_spot_em.assert_not_called()

    def test_invalid_single_quote_never_substitutes_estimated_market_cap(self):
        fake = Mock()
        fake.stock_individual_info_em.return_value.to_dict.return_value = [
            {"item":"股票代码","value":"000910"},
            {"item":"最新","value":4.4}
        ]
        with self.assertRaises(AKShareError):
            AKShareClient(provider=fake).spot(["000910.SZ"])

    def test_partial_market_data_rejected(self):
        fake=Mock()
        fake.stock_zh_a_spot_em.return_value.to_dict.return_value=[
            {"代码":"000910","名称":"仅一只","最新价":4.4,"总市值":2e9}
        ]
        with self.assertRaises(AKShareError):
            AKShareClient(provider=fake).spot()


if __name__ == "__main__":
    unittest.main()
