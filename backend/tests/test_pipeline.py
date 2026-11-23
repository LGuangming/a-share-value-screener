"""Contract tests: finance throttling cannot stop daily market publication."""
import os
import sys
import unittest
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

# Production's PostgreSQL driver is intentionally not installed in the
# pure-unit-tests CI job. Stub only the DB module at import time; each test
# replaces the Store factory with an isolated mock.
from unittest.mock import Mock as _Mock
with patch.dict("sys.modules", {"value_screener.store": _Mock(Store=_Mock())}):
    from value_screener.cli import run_scan, fetch_market, recalculate_ncav, run_financial_refresh, bootstrap_prices, TushareError


class PipelineIsolationTests(unittest.TestCase):
    def prepare(self):
        client = Mock()
        client.source = "relay"
        client.trade_open.return_value = True
        client.stocks.return_value = [{"ts_code": "000910.SZ", "name": "Sample"}]
        client.daily.return_value = [{"ts_code": "000910.SZ"}] * 1000
        store = Mock()
        store.upsert_stocks.return_value = 1000
        store.upsert_quotes.return_value = 1000
        store.quote_coverage.return_value = {"listed_stocks": 1000, "quoted_stocks": 1000}
        store.has_successful_data_trigger.return_value = True
        store.has_published_snapshot.return_value = False
        store.market_data_signature.side_effect = ["market-before", "market-after"]
        store.financial_inputs_signature.side_effect = ["finance-before", "finance-after"]
        store.screen_inputs.return_value = []
        store.latest_quote_date.return_value = date(2026, 11, 20)
        store.financial_backlog.return_value = {"remaining": 1, "queue": ["000910.SZ"]}
        store.financial_missing_kinds.return_value = ["balancesheet", "income", "cashflow"]
        store.financial_disclosure_queue.return_value = []
        client.disclosure_rows.return_value = []
        store.ingest_reports.return_value = 1
        return client, store

    def test_daily_scan_never_requests_financial_api(self):
        client, store = self.prepare()
        with (patch.dict(os.environ, {"DATABASE_URL": "test-only-url"}),
              patch.object(run_scan.__globals__["TushareClient"], "from_environment", return_value=client),
              patch.dict(run_scan.__globals__, {"Store": lambda _url: store})):
            run_scan(SimpleNamespace(date="2026-11-20"))
        client.trade_open.assert_called_once_with("20261120")
        client.stocks.assert_called_once()
        client.daily.assert_called_once_with("20261120")
        client.report_rows.assert_not_called()
        store.financial_backlog.assert_not_called()
        store.ingest_reports.assert_not_called()
        store.publish.assert_called_once()
        store.finish_run.assert_called_once()
        self.assertEqual(store.finish_run.call_args.args[1], "success")
        metrics = store.publish.call_args.args[3]
        self.assertEqual(metrics["financial_api_calls"], 0)
        self.assertEqual(metrics["financial_source"], "cached_disclosed_reports")
        self.assertEqual(metrics["market_api_calls"], 0)

    def test_fetch_market_is_only_a_data_ingestion_step(self):
        client, store = self.prepare()
        with (patch.dict(os.environ, {"DATABASE_URL": "test-only-url"}),
              patch.object(run_scan.__globals__["TushareClient"], "from_environment", return_value=client),
              patch.dict(run_scan.__globals__, {"Store": lambda _url: store})):
            self.assertTrue(fetch_market(SimpleNamespace(date="2026-11-20")))
        store.upsert_stocks.assert_called_once()
        store.upsert_quotes.assert_called_once()
        store.publish.assert_not_called()
        store.screen_inputs.assert_not_called()
        store.finish_run.assert_called_once()
        self.assertEqual(store.finish_run.call_args.args[1], "success")
        self.assertTrue(store.finish_run.call_args.args[2]["data_changed"])
        self.assertTrue(store.finish_run.call_args.args[2]["recalc_needed"])

    def test_identical_market_rerun_does_not_request_recalculation(self):
        client, store = self.prepare()
        store.market_data_signature.side_effect = ["same", "same"]
        store.has_published_snapshot.return_value = True
        with (patch.dict(os.environ, {"DATABASE_URL": "test-only-url"}),
              patch.object(run_scan.__globals__["TushareClient"], "from_environment", return_value=client),
              patch.dict(run_scan.__globals__, {"Store": lambda _url: store})):
            fetch_market(SimpleNamespace(date="2026-11-20"))
        stats = store.finish_run.call_args.args[2]
        self.assertFalse(stats["data_changed"])
        self.assertFalse(stats["recalc_needed"])
        store.publish.assert_not_called()

    def test_identical_market_rerun_without_snapshot_publishes_first_time(self):
        client, store = self.prepare()
        store.market_data_signature.side_effect = ["same", "same"]
        store.has_published_snapshot.return_value = False
        with (patch.dict(os.environ, {"DATABASE_URL": "test-only-url"}),
              patch.object(run_scan.__globals__["TushareClient"], "from_environment", return_value=client),
              patch.dict(run_scan.__globals__, {"Store": lambda _url: store})):
            fetch_market(SimpleNamespace(date="2026-11-20"))
        stats = store.finish_run.call_args.args[2]
        self.assertFalse(stats["data_changed"])
        self.assertTrue(stats["recalc_needed"])

    def test_recalculate_uses_no_tushare_requests(self):
        client, store = self.prepare()
        with (patch.dict(os.environ, {"DATABASE_URL": "test-only-url"}),
              patch.object(run_scan.__globals__["TushareClient"], "from_environment",
                           side_effect=AssertionError("No Tushare client allowed")),
              patch.dict(run_scan.__globals__, {"Store": lambda _url: store})):
            recalculate_ncav(SimpleNamespace(date="2026-11-23", trigger_run_id=None))
        store.quote_coverage.assert_called_once_with(date(2026, 11, 20))
        store.publish.assert_called_once()
        self.assertEqual(store.publish.call_args.args[3]["market_api_calls"], 0)
        self.assertEqual(store.publish.call_args.args[3]["financial_api_calls"], 0)

    def test_readonly_probe_does_not_trigger_recalculation(self):
        client, store = self.prepare()
        store.has_successful_data_trigger.return_value = False
        with (patch.dict(os.environ, {"DATABASE_URL": "test-only-url"}),
              patch.dict(run_scan.__globals__, {"Store": lambda _url: store})):
            recalculate_ncav(SimpleNamespace(date="2026-11-23", trigger_run_id=12345))
        store.has_successful_data_trigger.assert_called_once_with(12345)
        store.latest_quote_date.assert_not_called()
        store.start_run.assert_not_called()
        store.publish.assert_not_called()

    def test_successful_workflow_run_triggers_recalculation(self):
        client, store = self.prepare()
        with (patch.dict(os.environ, {"DATABASE_URL": "test-only-url"}),
              patch.dict(run_scan.__globals__, {"Store": lambda _url: store})):
            recalculate_ncav(SimpleNamespace(date="2026-11-23", trigger_run_id=12345))
        store.has_successful_data_trigger.assert_called_once_with(12345)
        store.publish.assert_called_once()

    def test_market_holiday_does_not_publish(self):
        client, store = self.prepare()
        client.trade_open.return_value = False
        with (patch.dict(os.environ, {"DATABASE_URL": "test-only-url"}),
              patch.object(run_scan.__globals__["TushareClient"], "from_environment", return_value=client),
              patch.dict(run_scan.__globals__, {"Store": lambda _url: store})):
            self.assertFalse(fetch_market(SimpleNamespace(date="2026-11-21")))
        client.stocks.assert_not_called()
        store.upsert_quotes.assert_not_called()
        store.publish.assert_not_called()
        self.assertEqual(store.finish_run.call_args.args[1], "skipped")

    def test_incomplete_cached_quotes_block_recalculation(self):
        client, store = self.prepare()
        store.quote_coverage.return_value = {"listed_stocks": 5500, "quoted_stocks": 500}
        with (patch.dict(os.environ, {"DATABASE_URL": "test-only-url"}),
              patch.dict(run_scan.__globals__, {"Store": lambda _url: store})):
            with self.assertRaisesRegex(RuntimeError, "Cached daily quotes incomplete"):
                recalculate_ncav(SimpleNamespace(date="2026-11-23", trigger_run_id=None))
        store.publish.assert_not_called()
        store.start_run.assert_not_called()

    def test_financial_refresh_is_independent_from_publication(self):
        client, store = self.prepare()
        with (patch.dict(os.environ, {"DATABASE_URL": "test-only-url"}),
              patch.object(run_scan.__globals__["TushareClient"], "from_environment", return_value=client),
              patch.dict(run_scan.__globals__, {"Store": lambda _url: store})):
            run_financial_refresh(SimpleNamespace(asof="2026-11-23", budget=1))
        self.assertEqual(client.report_rows.call_count, 3)
        client.disclosure_rows.assert_not_called()
        self.assertEqual([call.args[0] for call in client.report_rows.call_args_list],
                         ["balancesheet", "income", "cashflow"])
        store.mark_financial_fetch.assert_called_once_with("000910.SZ", True)
        self.assertEqual(store.finish_run.call_args.args[1], "success")
        self.assertTrue(store.finish_run.call_args.args[2]["recalc_needed"])
        store.financial_inputs_signature.assert_has_calls([
            unittest.mock.call(date(2026, 11, 20)),
            unittest.mock.call(date(2026, 11, 20)),
        ])
        store.publish.assert_not_called()
        client.daily.assert_not_called()
        client.stocks.assert_not_called()

    def test_provider_429_fails_only_financial_refresh(self):
        client, store = self.prepare()
        client.report_rows.side_effect = TushareError("balancesheet: rate-limited (429)")
        with (patch.dict(os.environ, {"DATABASE_URL": "test-only-url"}),
              patch.object(run_scan.__globals__["TushareClient"], "from_environment", return_value=client),
              patch.dict(run_scan.__globals__, {"Store": lambda _url: store})):
            with self.assertRaisesRegex(RuntimeError, "Stopping independent refresh"):
                run_financial_refresh(SimpleNamespace(asof="2026-11-23", budget=1))
        self.assertEqual(store.finish_run.call_args.args[1], "failed")
        self.assertEqual(store.finish_run.call_args.args[2]["failed"], 1)
        store.publish.assert_not_called()
        client.daily.assert_not_called()

    def test_no_financial_queue_skips_without_fetch(self):
        client, store = self.prepare()
        store.financial_backlog.return_value = {"remaining": 0, "queue": []}
        with (patch.dict(os.environ, {"DATABASE_URL": "test-only-url"}),
              patch.object(run_scan.__globals__["TushareClient"], "from_environment", return_value=client),
              patch.dict(run_scan.__globals__, {"Store": lambda _url: store})):
            run_financial_refresh(SimpleNamespace(asof="2026-11-23", budget=5))
        client.report_rows.assert_not_called()
        self.assertEqual(client.disclosure_rows.call_count, 2)
        self.assertEqual(store.finish_run.call_args.args[1], "skipped")
        store.publish.assert_not_called()

    def test_incremental_fetches_only_newly_disclosed_missing_kind(self):
        client, store = self.prepare()
        store.financial_backlog.return_value = {"remaining": 0, "queue": []}
        store.financial_disclosure_queue.return_value = [
            {"ts_code": "000910.SZ", "period": date(2026, 9, 30),
             "actual_date": date(2026, 10, 28)}
        ]
        store.financial_missing_kinds.return_value = ["cashflow"]
        client.disclosure_rows.side_effect = [
            [{"ts_code": "000910.SZ", "end_date": "20260930",
              "actual_date": "20261028"}],
            [],
        ]
        with (patch.dict(os.environ, {"DATABASE_URL": "test-only-url"}),
              patch.object(run_scan.__globals__["TushareClient"], "from_environment", return_value=client),
              patch.dict(run_scan.__globals__, {"Store": lambda _url: store})):
            run_financial_refresh(SimpleNamespace(asof="2026-11-23", budget=40))
        client.report_rows.assert_called_once()
        self.assertEqual(client.report_rows.call_args.args[0], "cashflow")
        self.assertEqual(store.finish_run.call_args.args[2]["mode"], "disclosure_incremental")
        self.assertEqual(store.finish_run.call_args.args[2]["financial_api_calls"], 1)
        self.assertTrue(store.finish_run.call_args.args[2]["recalc_needed"])
        store.publish.assert_not_called()

    def test_weekend_uses_last_trading_day_quotes(self):
        client, store = self.prepare()
        store.latest_quote_date.return_value = date(2026, 11, 27)
        with (patch.dict(os.environ, {"DATABASE_URL": "test-only-url"}),
              patch.object(run_scan.__globals__["TushareClient"], "from_environment", return_value=client),
              patch.dict(run_scan.__globals__, {"Store": lambda _url: store})):
            run_financial_refresh(SimpleNamespace(asof="2026-11-29", budget=40))
        store.financial_backlog.assert_called_once_with(date(2026, 11, 27), date(2026, 11, 29), 40)
        client.trade_open.assert_not_called()
        client.daily.assert_not_called()
        client.stocks.assert_not_called()

    def test_weekend_new_disclosure_never_rewrites_friday_snapshot(self):
        client, store = self.prepare()
        store.latest_quote_date.return_value = date(2026, 11, 27)
        store.financial_inputs_signature.side_effect = ["friday-before", "friday-before"]
        with (patch.dict(os.environ, {"DATABASE_URL": "test-only-url"}),
              patch.object(run_scan.__globals__["TushareClient"], "from_environment", return_value=client),
              patch.dict(run_scan.__globals__, {"Store": lambda _url: store})):
            run_financial_refresh(SimpleNamespace(asof="2026-11-29", budget=40))
        stats = store.finish_run.call_args.args[2]
        self.assertEqual(stats["succeeded"], 1)
        self.assertFalse(stats["recalc_needed"])
        store.publish.assert_not_called()

    def test_repeated_or_unchanged_report_download_does_not_recalculate(self):
        client, store = self.prepare()
        store.financial_inputs_signature.side_effect = ["same", "same"]
        with (patch.dict(os.environ, {"DATABASE_URL": "test-only-url"}),
              patch.object(run_scan.__globals__["TushareClient"], "from_environment", return_value=client),
              patch.dict(run_scan.__globals__, {"Store": lambda _url: store})):
            run_financial_refresh(SimpleNamespace(asof="2026-11-23", budget=40))
        stats = store.finish_run.call_args.args[2]
        self.assertEqual(stats["succeeded"], 1)
        self.assertFalse(stats["recalc_needed"])

    def test_incremental_provider_error_cannot_publish_scan(self):
        client, store = self.prepare()
        store.financial_backlog.return_value = {"remaining": 0, "queue": []}
        client.disclosure_rows.side_effect = TushareError("disclosure_date: provider error 40203")
        with (patch.dict(os.environ, {"DATABASE_URL": "test-only-url"}),
              patch.object(run_scan.__globals__["TushareClient"], "from_environment", return_value=client),
              patch.dict(run_scan.__globals__, {"Store": lambda _url: store})):
            with self.assertRaises(TushareError):
                run_financial_refresh(SimpleNamespace(asof="2026-11-29", budget=40))
        self.assertEqual(store.finish_run.call_args.args[1], "failed")
        store.publish.assert_not_called()

    def test_empty_financial_budget_skips_without_cached_quotes(self):
        client, store = self.prepare()
        with (patch.dict(os.environ, {"DATABASE_URL": "test-only-url"}),
              patch.object(run_scan.__globals__["TushareClient"], "from_environment", return_value=client),
              patch.dict(run_scan.__globals__, {"Store": lambda _url: store})):
            run_financial_refresh(SimpleNamespace(asof="2026-11-23", budget=0))
        client.report_rows.assert_not_called()
        store.latest_quote_date.assert_not_called()
        self.assertEqual(store.finish_run.call_args.args[1], "skipped")

    def test_price_history_backfill_skips_cached_sessions_and_uses_compact_table(self):
        client, store = self.prepare()
        store.latest_quote_date.return_value = date(2026, 11, 20)
        client.stocks.return_value = [
            {"ts_code": f"{i:06d}.SZ", "name": f"Stock {i}"}
            for i in range(1000)
        ]
        first = date(2026, 9, 1)
        dates = [first + timedelta(days=i) for i in range(51)]
        days = [d.strftime("%Y%m%d") for d in dates]
        client.query.return_value = [{"cal_date": d, "is_open": 1} for d in days]
        store.completed_history_days.return_value = set(dates[:50])
        client.daily.return_value = [
            {"ts_code": f"{i:06d}.SZ", "close": 1.2}
            for i in range(1000)
        ]
        store.save_compact_history.return_value = 1000
        factory = Mock(return_value=store)
        factory._parse_date.side_effect = lambda x: date.fromisoformat(
            f"{x[:4]}-{x[4:6]}-{x[6:]}"
        )
        with (patch.dict(os.environ, {"DATABASE_URL": "test-only-url"}),
              patch.object(run_scan.__globals__["TushareClient"], "from_environment",
                           return_value=client),
              patch.dict(run_scan.__globals__, {"Store": factory})):
            bootstrap_prices(SimpleNamespace(days=120, min_sessions=50,
                                             end_date="2026-11-20"))
        client.daily.assert_called_once_with(days[-1])
        store.save_compact_history.assert_called_once()
        self.assertEqual(store.save_compact_history.call_args.args[0], dates[-1])
        store.upsert_quotes.assert_not_called()
        store.ensure_price_history_tables.assert_called_once()
        store.completed_history_days.assert_called_once()

    def test_price_history_backfill_all_cached_makes_no_daily_requests(self):
        client, store = self.prepare()
        d = date(2026, 9, 1)
        days = [(d + timedelta(days=i)).strftime("%Y%m%d") for i in range(50)]
        client.query.return_value = [{"cal_date": v, "is_open": 1} for v in days]
        store.completed_history_days.return_value = {
            d + timedelta(days=i) for i in range(50)
        }
        factory = Mock(return_value=store)
        factory._parse_date.side_effect = lambda x: date.fromisoformat(
            f"{x[:4]}-{x[4:6]}-{x[6:]}"
        )
        with (patch.dict(os.environ, {"DATABASE_URL": "test-only-url"}),
              patch.object(run_scan.__globals__["TushareClient"], "from_environment",
                           return_value=client),
              patch.dict(run_scan.__globals__, {"Store": factory})):
            bootstrap_prices(SimpleNamespace(days=120, min_sessions=50,
                                             end_date="2026-11-20"))
        client.daily.assert_not_called()
        store.save_compact_history.assert_not_called()

    def test_price_history_backfill_rejects_too_short_calendar_window(self):
        client, store = self.prepare()
        client.query.return_value = [
            {"cal_date": "20261120", "is_open": 1}
        ]
        factory = Mock(return_value=store)
        factory._parse_date.side_effect = lambda x: date.fromisoformat(
            f"{x[:4]}-{x[4:6]}-{x[6:]}"
        )
        with (patch.dict(os.environ, {"DATABASE_URL": "test-only-url"}),
              patch.object(run_scan.__globals__["TushareClient"], "from_environment",
                           return_value=client),
              patch.dict(run_scan.__globals__, {"Store": factory})):
            with self.assertRaisesRegex(RuntimeError, "at least 50 required"):
                bootstrap_prices(SimpleNamespace(days=120, min_sessions=50,
                                                 end_date="2026-11-20"))
        client.daily.assert_not_called()
        store.save_compact_history.assert_not_called()

    def test_stale_market_quotes_block_financial_refresh(self):
        client, store = self.prepare()
        store.latest_quote_date.return_value = date(2026, 9, 30)
        with (patch.dict(os.environ, {"DATABASE_URL": "test-only-url"}),
              patch.object(run_scan.__globals__["TushareClient"], "from_environment", return_value=client),
              patch.dict(run_scan.__globals__, {"Store": lambda _url: store})):
            with self.assertRaisesRegex(RuntimeError, "more than 14 days old"):
                run_financial_refresh(SimpleNamespace(asof="2026-11-23", budget=5))
        client.report_rows.assert_not_called()
        store.publish.assert_not_called()
        self.assertEqual(store.finish_run.call_args.args[1], "failed")


if __name__ == "__main__":
    unittest.main()
