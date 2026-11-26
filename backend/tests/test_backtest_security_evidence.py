from datetime import date
import unittest
from value_screener.backtest_security_evidence import listing_evidence,name_evidence_asof


class SecurityEvidenceTests(unittest.TestCase):
    def test_listing_dates(self):
        row={"list_date":"20200102","delist_date":"20260105","list_status":"D","name":"future name"}
        self.assertEqual(listing_evidence(row,date(2020,1,1)),"before_snapshot_listing_date")
        self.assertEqual(listing_evidence(row,date(2024,1,1)),"within_snapshot_listing_dates_not_certified")
        self.assertEqual(listing_evidence(row,date(2026,1,5)),"at_or_after_snapshot_delisting_date")
        self.assertEqual(listing_evidence(None,date(2026,1,5)),"snapshot_missing")

    def test_st_and_announcement(self):
        day=date(2026,1,5)
        row={"start_date":"20260101","end_date":None,"ann_date":"20260101","name":"*ST示例"}
        self.assertEqual(name_evidence_asof([row],day,day),("STAR_ST","namechange_candidate_not_certified"))
        self.assertEqual(name_evidence_asof([row],day,date(2026,1,4))[0],None)
        self.assertEqual(name_evidence_asof([{**row,"ann_date":"20260105"}],day,day)[0],None)
        self.assertEqual(name_evidence_asof([row,{**row,"name":"正常"}],day,day)[1],"name_interval_conflict")
        self.assertEqual(name_evidence_asof([],day,day)[0],None)
