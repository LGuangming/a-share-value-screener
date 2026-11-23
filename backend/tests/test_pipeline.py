"""Contract tests: finance throttling cannot stop daily market publication."""
import os
import sys
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock, patch

# Production's PostgreSQL driver is intentionally not installed in the
# pure-unit-tests CI job. Stub only the DB module at import time; each test
# replaces the Store factory with an isolated mock.
from unittest.mock import Mock as _Mock
with patch.dict("sys.modules", {"value_screener.store": _Mock(Store=_Mock())}):
    from value_screener.cli import run_scan, run_financial_refresh, TushareError


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
        metrics = store.publish.call_args.args[3]
        self.assertEqual(metrics["financial_api_calls"], 0)
        self.assertEqual(metrics["financial_source"], "cached_disclosed_reports")
        store.finish_run.assert_not_called()

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
