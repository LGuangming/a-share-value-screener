import unittest
from datetime import date
from value_screener.financial_archive_import import select_latest

class ArchiveSelectionTests(unittest.TestCase):
    def row(self,**kwargs):
        return dict(ts_code="600000.SH",report_type="1",end_date="20260930",ann_date="20261027",f_ann_date="20261028",n_income=100,**{}) | kwargs

    def test_actual_date_and_current_period(self):
        day=date(2026,11,24)
        result,_,status=select_latest([self.row()],"income",day,None)
        self.assertEqual(status,"selected")
        self.assertEqual(result[:2],(date(2026,9,30),date(2026,10,28)))
        self.assertIsNone(select_latest([self.row()],"income",date(2026,10,28))[0])
        self.assertIsNone(select_latest([self.row(f_ann_date=None)],"income",day)[0])
        self.assertIsNone(select_latest([self.row(ann_date="20261125")],"income",day)[0])

    def test_never_replace_same_period(self):
        self.assertIsNone(select_latest([self.row()],"income",date(2026,11,24),date(2026,9,30))[0])

    def test_conflict_and_missing_metric_not_zero(self):
        day=date(2026,11,24)
        self.assertEqual(select_latest([self.row(),self.row(n_income=200)],"income",day)[2],"conflicting_latest_version")
        self.assertEqual(select_latest([self.row(n_income=None)],"income",day)[2],"missing_core_metric")
        self.assertEqual(select_latest([self.row(n_income=0)],"income",day)[2],"selected")

    def test_ignore_parent_company_and_future_disclosure(self):
        rows=[self.row(report_type="6"),self.row(f_ann_date="20261201")]
        self.assertIsNone(select_latest(rows,"income",date(2026,11,24))[0])
