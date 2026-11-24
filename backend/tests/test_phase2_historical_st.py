"""Offline tests for historical ST reconciliation and classification."""
import unittest
from value_screener.phase2_historical_st import classify_st, name_status, intervals, name_evidence, resolve

class HistoricalSTTests(unittest.TestCase):
    def test_classification(self):
        for name in ("＊ＳＴ示例", "S*ST示例", "*ST示例"):
            self.assertEqual(name_status(name), 1)
            self.assertEqual(classify_st(1, "both_agree", name), "STAR_ST")
        for name in ("ST示例", "SST示例"):
            self.assertEqual(classify_st(1, "namechange_only", name), "ST")
        self.assertEqual(classify_st(1, "baostock_only", None), "ST_UNCLASSIFIED")
        self.assertEqual(classify_st(0, "baostock_only", None), "NON_ST_UNCLASSIFIED")
        self.assertEqual(classify_st(0, "both_agree", "示例退"), "DELISTING")
        self.assertEqual(classify_st(0, "both_agree", "退市示例"), "DELISTING")
        self.assertEqual(classify_st(0, "both_agree", "示例股份"), "NORMAL")
        self.assertEqual(classify_st(None, "unknown", None), "UNKNOWN")
        self.assertEqual(classify_st(None, "conflict", "*ST示例"), "CONFLICT")

    def test_effective_dates_and_conflict(self):
        periods = intervals([dict(name="*ST示例", start_date="20191231", end_date=None),
                             dict(name="示例", start_date="20200501", end_date=None)])
        self.assertIsNone(name_evidence(periods, "2019-12-30")[0])
        self.assertEqual(name_evidence(periods, "2020-04-30")[0], 1)
        self.assertEqual(name_evidence(periods, "2020-05-01")[0], 0)
        self.assertEqual(resolve(1, 0), (None, "conflict"))
        self.assertEqual(resolve(None, 1), (1, "namechange_only"))
        self.assertEqual(resolve(None, None), (None, "unknown"))
        ambiguous = intervals([dict(name="ST示例", start_date="20200101", end_date="20200601"),
                               dict(name="*ST示例", start_date="20200501", end_date=None)])
        state, name, _, conflict = name_evidence(ambiguous, "2020-05-15")
        self.assertFalse(conflict)
        self.assertEqual(classify_st(state, "both_agree", name), "ST_UNCLASSIFIED")
