from datetime import date
import unittest
from value_screener.backtest_financial_evidence import evidence,select_candidate


class FinancialEvidenceTests(unittest.TestCase):
    def row(self, period="20240930", actual="20241020", ann="20241021", assets=100):
        return {"ts_code":"600001.SH","end_date":period,"f_ann_date":actual,
                "ann_date":ann,"report_type":"1","total_cur_assets":assets,"total_liab":50}

    def test_strict_date_and_latest_period(self):
        old=self.row("20240630","20240820","20240821")
        newer=self.row()
        self.assertEqual(select_candidate([old,newer],"balancesheet",date(2024,10,21))[0],old)
        self.assertEqual(select_candidate([old,newer],"balancesheet",date(2024,10,22))[0],newer)

    def test_conflict_and_no_fallback(self):
        newer=self.row()
        old=self.row("20240630","20240820","20240821")
        self.assertEqual(select_candidate([old,newer,self.row(assets=110)],"balancesheet",date(2024,10,22))[1],"conflicting_latest_version")
        self.assertEqual(select_candidate([old,self.row(assets=None)],"balancesheet",date(2024,10,22))[1],"missing_core")
        self.assertEqual(select_candidate([old,self.row(actual=None)],"balancesheet",date(2024,10,22))[1],"unresolved_disclosure_version")

    def test_indicator_and_date_validation(self):
        self.assertEqual(evidence(self.row(ann="20240901"),"balancesheet")[2],"invalid_disclosure")
        self.assertEqual(select_candidate([self.row()],"fina_indicator",date(2024,10,22))[1],"indicator_lineage_unverified")
