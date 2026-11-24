import unittest
from value_screener.rules import assess, technical_signal, ScreenConfig


class TestAccountingRules(unittest.TestCase):
    def setUp(self):
        self.stock = {"ts_code": "000910.SZ", "name": "样本公司", "is_st": False}
        self.quote = {"close": 4.0, "total_mv": 200000, "pb": 0.4}  # 20亿元
        self.balance = {"total_cur_assets": 4e9, "total_liab": 1e9, "total_assets": 5e9,
                        "accounts_receiv": 8e8, "inventories": 5e8, "prepayment": 1e8,
                        "minority_int": 1e8}

    def test_full_precompute_ignores_price_pb_limits_only(self):
        quote={**self.quote,"close":25,"pb":2.5}
        self.assertEqual(assess(self.stock,quote,self.balance)["status"],"PRICE_EXCLUDED")
        cfg=ScreenConfig(max_price=None,max_pb=None)
        self.assertTrue(assess(self.stock,quote,self.balance,cfg=cfg)["is_candidate"])
        self.assertEqual(assess({**self.stock,"industry":"银行"},quote,self.balance,cfg=cfg)["status"],"FINANCIAL_IGNORED")
        self.assertEqual(assess({**self.stock,"is_st":True},quote,self.balance,cfg=cfg)["status"],"RISK_EXCLUDED")

    def test_ncav_units_and_stress(self):
        got = assess(self.stock, self.quote, self.balance, cfg=ScreenConfig())
        self.assertEqual(got["ncav"], 3e9)
        self.assertAlmostEqual(got["coverage"], 1.5)
        # 3bn - 100m minority - 160m receiv - 150m inv - 50m prepay
        self.assertAlmostEqual(got["stress_ncav"], 2.54e9)
        self.assertAlmostEqual(got["stress_coverage"], 1.27)
        self.assertTrue(got["is_candidate"])

    def test_unknown_not_assumed_zero(self):
        self.balance["minority_int"] = None
        got = assess(self.stock, self.quote, self.balance)
        self.assertIsNone(got["stress_ncav"])
        self.assertIn("STRESS_INPUT_INCOMPLETE", got["flags"])

    def test_st_risk_excluded(self):
        self.stock["name"] = "*ST某企业"
        got = assess(self.stock, self.quote, self.balance)
        self.assertEqual(got["status"], "RISK_EXCLUDED")
        self.assertFalse(got["is_candidate"])

    def test_financial_industries_ignored_not_data_gap(self):
        for industry in ("银行","证券","保险","多元金融"," 银行 "):
            for balance in (None, self.balance):
                got=assess({**self.stock,"industry":industry},self.quote,balance)
                self.assertEqual(got["status"],"FINANCIAL_IGNORED")
                self.assertFalse(got["is_candidate"])
                self.assertIsNone(got["ncav"])
                self.assertEqual(got["signal"],"NOT_ELIGIBLE")
        self.assertEqual(assess({**self.stock,"industry":"机械"},self.quote,None)["status"],"DATA_GAP")
        self.assertEqual(assess({**self.stock,"industry":None},self.quote,None)["status"],"DATA_GAP")

    def test_missing_balance_is_gap_not_failure(self):
        got = assess(self.stock, self.quote, None)
        self.assertEqual(got["status"], "DATA_GAP")
        self.assertFalse(got["is_candidate"])

    def test_coverage_requires_all_liabilities(self):
        got = assess(self.stock, self.quote, {**self.balance, "total_liab": 2e9})
        self.assertEqual(got["ncav"], 2e9)
        self.assertEqual(got["status"], "NEAR_NET_NET")

    def test_invalid_market_cap(self):
        got = assess(self.stock, {**self.quote, "total_mv": 0}, self.balance)
        self.assertEqual(got["status"], "DATA_GAP")

    def test_technical_requires_history(self):
        self.assertEqual(technical_signal([4.0] * 49, True, []), "INSUFFICIENT_HISTORY")
        self.assertEqual(technical_signal([4.0] * 50, False, []), "NOT_ELIGIBLE")

    def test_stress_gates_technical_buy_watch(self):
        balance = {**self.balance, "inventories": 2.4e9}
        got = assess(self.stock, self.quote, balance)
        self.assertIn("STRESS_COVERAGE_BELOW_100", got["flags"])
        self.assertEqual(technical_signal([4.0] * 50 + [5.0], True, got["flags"]), "FUNDAMENTAL_REVIEW")

    def test_technical_risk_gate(self):
        prices = [4.0] * 50 + [5.0]
        self.assertEqual(technical_signal(prices, True, ["NEGATIVE_OPERATING_CASHFLOW"]), "FUNDAMENTAL_REVIEW")


if __name__ == "__main__":
    unittest.main()
