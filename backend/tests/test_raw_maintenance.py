import unittest
from datetime import date,datetime,timedelta,timezone
from value_screener.raw_maintenance import retention_cutoffs,reader_candidates,NotReady

class MaintenanceTests(unittest.TestCase):
    def test_30_calendar_days_and_120_distinct_sessions(self):
        latest=date(2026,11,24)
        sessions=[latest-timedelta(days=i) for i in range(130)]
        full,compact=retention_cutoffs(latest,sessions+sessions[:5])
        self.assertEqual(full,latest-timedelta(days=29))
        self.assertEqual(compact,latest-timedelta(days=119))
        with self.assertRaises(NotReady):
            retention_cutoffs(latest,sessions[:119])
    def test_current_previous_and_grace_are_protected(self):
        now=datetime(2026,11,24,tzinfo=timezone.utc)
        objects=[{"Key":k,"LastModified":now-timedelta(days=d)} for k,d in
                 [("current",10),("previous",8),("young",2),("old",20),("old2",30)]]
        self.assertEqual({o["Key"] for o in reader_candidates(objects,"current",now)},{"previous","old","old2"})
        objects=[{"Key":k,"LastModified":now-timedelta(days=d)} for k,d in
                 [("current",0),("previous",1),("young",2),("old",20)]]
        self.assertEqual([o["Key"] for o in reader_candidates(objects,"current",now)],["old"])
