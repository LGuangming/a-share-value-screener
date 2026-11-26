"""Local maintenance routing, safety and existing task regressions."""
from datetime import date
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from value_screener import local_pipeline as p

class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env=patch.dict(os.environ,{"ARCHIVE_STORAGE_DRIVER":"local",
            "ARCHIVE_LOCAL_ROOT":self.tmp.name,"DATABASE_URL":"fixture-only",
            "TUSHARE_TOKEN":"fixture-only","TUSHARE_API_SOURCE":"official",
            "GITHUB_RUN_ID":"old","GITHUB_RUN_ATTEMPT":"1"},clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.launch=patch.object(p,"run_module")
        self.mock=self.launch.start()
        self.addCleanup(self.launch.stop)

    def test_history_prices_requires_confirmation_and_bounds(self):
        for kwargs in ({},{"confirm":p.CONFIRM,"days":366},
                       {"confirm":p.CONFIRM,"min_sessions":120},
                       {"confirm":p.CONFIRM,"end_date":"2099-01-01"}):
            with self.assertRaises(ValueError):
                p.run("history-prices",**kwargs)
        self.mock.assert_not_called()

    def test_history_prices_then_recalculate(self):
        p.run("history-prices",days=200,end_date="2025-01-02",confirm=p.CONFIRM)
        calls=self.mock.call_args_list
        self.assertEqual(calls[0].args[:2],("cli",["bootstrap-prices","--days","200",
            "--min-sessions","50","--end-date","2025-01-02"]))
        self.assertEqual(calls[1].args[:2],("cli",["recalculate-ncav"]))
        self.assertNotIn("GITHUB_RUN_ID",calls[0].args[2])

    def test_failed_backfill_does_not_publish(self):
        self.mock.side_effect=RuntimeError("fixture failure")
        with self.assertRaises(RuntimeError):
            p.run("history-prices",confirm=p.CONFIRM)
        self.assertEqual(self.mock.call_count,1)

    def test_archive_verify_has_no_provider_or_database_credentials(self):
        p.run("history-archive",start_year=2025,end_year=2023)
        self.assertEqual([c.args[1] for c in self.mock.call_args_list],
            [["verify","--year",str(y)] for y in (2025,2024,2023)])
        for c in self.mock.call_args_list:
            self.assertNotIn("DATABASE_URL",c.args[2])
            self.assertNotIn("TUSHARE_TOKEN",c.args[2])

    def test_archive_collection_confirmation_and_scratch(self):
        with self.assertRaises(ValueError):
            p.run("history-archive",archive_mode="archive",start_year=2025)
        def check(module,args,env,*,cwd):
            self.assertTrue(Path(cwd).is_dir())
            self.assertEqual(module,"history_batch_archive")
            self.assertEqual(args,["--start-year","2025","--end-year","2025"])
            self.assertNotIn("DATABASE_URL",env)
            self.assertIn("TUSHARE_TOKEN",env)
        self.mock.side_effect=check
        p.run("history-archive",archive_mode="archive",start_year=2025,confirm=p.CONFIRM)
        self.assertFalse(Path(self.mock.call_args.kwargs["cwd"]).exists())

    def test_st_merge_offline_and_provider_confirmation(self):
        for source in ("merged","audit","baostock","namechange"):
            with self.assertRaises(ValueError):
                p.run("historical-st",source=source)
        p.run("historical-st",source="merged",start_year=2025,end_year=2020,confirm=p.CONFIRM)
        call=self.mock.call_args
        self.assertEqual(call.args[:2],("phase2_historical_st",
            ["--source","merged","--start-year","2025","--end-year","2020"]))
        self.assertNotIn("TUSHARE_TOKEN",call.args[2])
        self.assertNotIn("DATABASE_URL",call.args[2])

    def test_diagnosis_no_provider(self):
        p.run("financial-diagnosis")
        self.assertEqual(self.mock.call_args.args[:2],("financial_gap_diagnosis",[]))
        self.assertNotIn("TUSHARE_TOKEN",self.mock.call_args.args[2])

    def test_maintenance_lock_prevents_overlap(self):
        from value_screener.local_archive import archive_lock
        with archive_lock(Path(self.tmp.name),"pipeline"):
            with self.assertRaisesRegex(RuntimeError,"Another local archive task"):
                p.run("financial-diagnosis")
        self.mock.assert_not_called()

    def test_invalid_range_and_nonlocal_fail_before_execution(self):
        for first,last in ((2020,2025),(2026,2025),(2025,2019)):
            with self.assertRaises(ValueError):
                p.maintenance_plan("history-archive",start_year=first,end_year=last,
                    today=date(2026,11,26))
        with patch.dict(os.environ,{"ARCHIVE_STORAGE_DRIVER":"neon"}):
            with self.assertRaisesRegex(RuntimeError,"requires ARCHIVE_STORAGE_DRIVER"):
                p.run("financial-diagnosis")
        self.mock.assert_not_called()

    def test_existing_market_flow_unchanged(self):
        p.run("market",day="2025-01-02")
        calls=self.mock.call_args_list
        self.assertEqual(calls[0].args[:2],("cli",["fetch-market","--date","2025-01-02"]))
        run_id=calls[0].args[2]["PIPELINE_RUN_ID"]
        self.assertEqual(calls[1].args[:2],("raw_incremental",["--trigger-local-run-id",run_id]))
        self.assertEqual(calls[2].args[:2],("cli",["recalculate-ncav","--trigger-local-run-id",run_id]))

    def test_maintenance_apply_remains_disabled(self):
        with self.assertRaisesRegex(RuntimeError,"opt-in required"):
            p.run("maintenance",apply=True)
        self.mock.assert_not_called()

    def test_relay_rejected_for_provider_tasks(self):
        with patch.dict(os.environ,{"TUSHARE_API_SOURCE":"relay"}):
            with self.assertRaisesRegex(RuntimeError,"official"):
                p.run("history-prices",confirm=p.CONFIRM)
            p.run("history-archive",start_year=2025)
        self.assertEqual(self.mock.call_count,1)

if __name__=="__main__":
    unittest.main()
