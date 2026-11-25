"""Entry point: PYTHONPATH=backend python -m value_screener.cli <command>"""
from __future__ import annotations
import argparse
import json
import logging
import os
from datetime import datetime, date, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from .rules import assess, technical_signal, ScreenConfig
from .store import Store
from .tushare_client import TushareClient, TushareError
from .financial_schedule import confirmed_disclosures, recent_closed_quarters
from .market_session import market_session, early_probe, minimum_quotes, validate_quotes, MARKET_CLOSE

LOGGER = logging.getLogger("value_screener")
CN_TZ = ZoneInfo("Asia/Shanghai")


def fetch_market(args) -> bool:
    """Fetch validated daily quotes only; do not calculate or publish NCAV.

    Returns False on exchange holidays or an early probe awaiting publication
    so the legacy combined CLI can skip recalculation. The scheduled workflow uses this command independently.
    """
    db = os.environ.get("DATABASE_URL", "")
    if not db:
        raise RuntimeError("DATABASE_URL is required")
    client = TushareClient.from_environment()
    now = datetime.now(CN_TZ)
    day = market_session(client, args.date, now)
    if day == now.date() and (now.hour, now.minute) < MARKET_CLOSE:
        raise ValueError("Requested same-day market data before market close")
    store = Store(db)
    run_id = uuid4()
    store.start_run(run_id, "market_refresh", day)
    stats = {"asof": day.isoformat(), "api_source": client.source,
             "github_run_id": os.environ.get("GITHUB_RUN_ID"), "local_run_id": os.environ.get("PIPELINE_RUN_ID"),
             "notes": "Market data only. NCAV publication is handled by recalculate-ncav."}
    try:
        if not client.trade_open(day.strftime("%Y%m%d")):
            store.finish_run(run_id, "skipped", stats)
            LOGGER.info("Closed trading session %s; market refresh skipped", day)
            return False
        stocks = client.stocks()
        if not stocks:
            raise RuntimeError("No stock list returned")
        stats["listed_stocks"] = len(stocks)
        quotes = client.daily(day.strftime("%Y%m%d"))
        min_rows = minimum_quotes(len({s["ts_code"] for s in stocks}), args.date, day, now)
        stats["provider_quote_rows"] = len(quotes)
        valid = validate_quotes(stocks, quotes, day)
        stats["valid_quote_rows"] = len(valid)
        stats["minimum_quote_rows"] = min_rows
        LOGGER.info("daily_basic probe: date=%s source=%s rows=%d valid=%d minimum=%d",
                    day, client.source, len(quotes), len(valid), min_rows)
        if len(valid) < min_rows:
            if early_probe(day, args.date, now):
                stats["reason"] = "awaiting_same_day_publication"
                stats["recalc_needed"] = False
                store.finish_run(run_id, "skipped", stats)
                LOGGER.warning("Market data not ready: date=%s valid=%d minimum=%d; "
                               "keeping prior snapshot; scheduled refresh remains 19:30 CST",
                               day, len(valid), min_rows)
                return False
            # Diagnose an empty expected session without substituting another day.
            if not quotes:
                stats["provider_empty"] = True
                try:
                    prior = market_session(client, None, datetime.combine(day, datetime.min.time(), CN_TZ))
                    prior_rows = len(client.daily(prior.strftime("%Y%m%d")))
                    stats["diagnostic_prior_date"] = prior.isoformat()
                    stats["diagnostic_prior_rows"] = prior_rows
                    LOGGER.warning("Read-only prior-session diagnostic: date=%s rows=%d", prior, prior_rows)
                except Exception as probe_error:
                    LOGGER.warning("Prior-session diagnostic unavailable: %s", type(probe_error).__name__)
            raise RuntimeError(
                f"Daily quotes incomplete for {day}: {len(valid)} valid versus {min_rows} minimum"
            )
        signature_before = store.market_data_signature(day)
        store.upsert_stocks(stocks)
        stats["quoted_stocks"] = store.upsert_quotes(valid, day)
        if stats["quoted_stocks"] < min_rows:
            raise RuntimeError(
                f"Valid daily quotes incomplete: {stats['quoted_stocks']} versus {min_rows} minimum"
            )
        stats["data_changed"] = signature_before != store.market_data_signature(day)
        # A previously fetched but never published session needs its first NCAV
        # calculation even if today's re-fetch is byte-for-byte identical.
        stats["recalc_needed"] = (
            stats["data_changed"] or not store.has_published_snapshot(day)
        )
        store.finish_run(run_id, "success", stats)
        LOGGER.info("Market refresh successful: %s", json.dumps(stats, ensure_ascii=False))
        return True
    except Exception as exc:
        store.finish_run(run_id, "failed", stats, str(exc))
        LOGGER.exception("Market refresh failed; no successful market run published")
        raise


def recalculate_ncav(args):
    """Calculate the latest stored close using only earlier disclosed financials.

    No external API calls, no extra Tushare permissions, no look-ahead leakage.
    Replacing an existing same-trade-date snapshot is atomic.
    """
    asof = date.fromisoformat(args.date) if getattr(args, "date", None) else datetime.now(CN_TZ).date()
    db = os.environ.get("DATABASE_URL", "")
    if not db:
        raise RuntimeError("DATABASE_URL is required")
    store = Store(db)
    # Local mode validates existing tables without DDL or GRANT.
    # Preserve the legacy additive migration in cloud mode.
    store.ensure_price_history_tables()
    trigger_run_id = getattr(args, "trigger_run_id", None)
    local_trigger = getattr(args, "trigger_local_run_id", None)
    if local_trigger is not None and not store.has_successful_local_trigger(local_trigger):
        LOGGER.info("Skipping recalculate-ncav: no eligible changes in local pipeline run")
        return
    if trigger_run_id is not None and not store.has_successful_data_trigger(trigger_run_id):
        LOGGER.info("Skipping recalculate-ncav: no eligible changes for the latest quote date in upstream run %s",
                    trigger_run_id)
        return
    quote_day = store.latest_quote_date(asof)
    if quote_day is None:
        raise RuntimeError("No cached daily quotes available; fetch market data first")
    if (asof - quote_day).days > 14:
        raise RuntimeError(
            f"Latest cached quote date {quote_day} is more than 14 days old; "
            "refusing to publish a stale NCAV snapshot"
        )
    coverage = store.quote_coverage(quote_day)
    min_rows = max(1000, round(coverage["listed_stocks"] * 0.70))
    if coverage["quoted_stocks"] < min_rows:
        raise RuntimeError(
            f"Cached daily quotes incomplete: {coverage['quoted_stocks']} versus {min_rows} minimum"
        )
    run_id = uuid4()
    store.start_run(run_id, "daily_scan", quote_day)
    stats = {"asof": quote_day.isoformat(), "quote_day": quote_day.isoformat(),
             "calculated_at": asof.isoformat(),
             "api_source": "cached_market_quotes",
             "financial_source": "cached_disclosed_reports",
             "market_api_calls": 0, "financial_api_calls": 0,
             "listed_stocks": coverage["listed_stocks"],
             "quoted_stocks": coverage["quoted_stocks"],
             "notes": "Recalculated with stored PostgreSQL data; disclosures strictly earlier than quote day."}
    try:
        rows = store.screen_inputs(quote_day)
        stats["pre_screened"] = len(rows)
        stats["screen_scope"] = "all_sh_sz_mainboard"
        results = []
        for row in rows:
            stock = {"ts_code": row["ts_code"], "name": row["name"], "is_st": row["is_st"],
                     "industry": row.get("industry")}
            r = assess(stock, row["quote"], row["balance"], row["income"],
                       row["cashflow"], ScreenConfig(max_price=None, max_pb=None))
            r["report_date"] = row["report_date"]
            r["ann_date"] = row["ann_date"]
            if r["status"] == "FINANCIAL_IGNORED":
                results.append(r)
                continue
            if row["report_date"] is not None:
                if ((row.get("income_end_date") is not None and
                     row["income_end_date"] != row["report_date"]) or
                    (row.get("cashflow_end_date") is not None and
                     row["cashflow_end_date"] != row["report_date"])):
                    r["flags"].append("FINANCIAL_PERIOD_MISMATCH")
                    r["assessment"] += " 利润或现金流与资产负债表报告期间不一致，需核对口径。"
            if row["report_date"] and (quote_day - row["report_date"]).days > 280:
                r["flags"].append("STALE_FINANCIALS")
                r["is_candidate"] = False
                r["status"] = "STALE_FINANCIALS"
                r["assessment"] += " 财报报告期距今超过280天，暂不进入候选池。"
            results.append(r)
        selected = [r["ts_code"] for r in results if r["is_candidate"]]
        histories = store.price_history(selected, quote_day)
        for r in results:
            if r["is_candidate"]:
                r["signal"] = technical_signal(
                    histories.get(r["ts_code"], []), True, r["flags"]
                )
        stats["default_price_pb_pre_screened"] = sum(r["price"] is not None and r["price"]<=10 and (r["pb"] is None or r["pb"]<=1.2) for r in results)
        stats["candidate_count"] = len(selected)
        stats["financial_data_gap"] = sum(r["status"] == "DATA_GAP" for r in results)
        stats["financial_ignored"] = sum(r["status"] == "FINANCIAL_IGNORED" for r in results)
        stats["netnet_applicable_pre_screened"] = len(results)-stats["financial_ignored"]
        stats["insufficient_price_history"] = sum(
            r["signal"] == "INSUFFICIENT_HISTORY" for r in results
        )
        stats["rows_published"] = len(results)
        store.publish(quote_day, run_id, results, stats)
        LOGGER.info("NCAV recalculation published: %s",
                    json.dumps(stats, ensure_ascii=False))
    except Exception as exc:
        store.finish_run(run_id, "failed", stats, str(exc))
        LOGGER.exception("NCAV recalculation failed; previous snapshot is preserved")
        raise


def run_scan(args):
    """Legacy combined CLI (manual only). Workflows use independent commands."""
    if fetch_market(args):
        recalculate_ncav(args)


def run_financial_refresh(args):
    """Daily 40-company limit: finish coverage backlog before disclosure updates.

    On non-trading days reuse the last saved quote universe. The independent
    financial job never modifies the published daily scan snapshot.
    """
    day = date.fromisoformat(args.asof) if args.asof else datetime.now(CN_TZ).date()
    db = os.environ.get("DATABASE_URL", "")
    if not db:
        raise RuntimeError("DATABASE_URL is required")
    archive_first = getattr(args, "archive_first", False)
    archive_only = archive_first and args.budget == 0
    client = None if archive_only else TushareClient.from_environment()
    store = Store(db)
    run_id = uuid4()
    store.start_run(run_id, "financial_refresh", day)
    stats = {"asof": day.isoformat(), "budget": args.budget,
             "api_source": client.source if client else "archive_only", "github_run_id": os.environ.get("GITHUB_RUN_ID"), "local_run_id": os.environ.get("PIPELINE_RUN_ID"),
             "recalc_needed": False, "mode": None, "attempted": 0,
             "succeeded": 0, "failed": 0, "financial_api_calls": 0,
             "disclosure_api_calls": 0,
             "notes": "Fill incomplete statements first, then poll actual disclosures. No daily snapshot modified."}
    try:
        if args.budget == 0 and not archive_first:
            store.finish_run(run_id, "skipped", stats)
            LOGGER.info("Financial refresh skipped: budget=0")
            return
        quote_day = store.latest_quote_date(day)
        if quote_day is None:
            raise RuntimeError("No cached daily quotes found; run the daily scan first")
        if (day - quote_day).days > 14:
            raise RuntimeError(
                f"Latest cached quote date {quote_day} is more than 14 days old; "
                "refusing to refresh against an obsolete stock universe"
            )
        stats["quote_day"] = quote_day.isoformat()
        # Compare only financials usable with the latest completed market
        # session; later disclosures must not rewrite that historical date.
        signature_before = store.financial_inputs_signature(quote_day)
        if archive_first:
            if day!=datetime.now(CN_TZ).date():
                raise RuntimeError("Archive-first refresh requires current live date")
            from .financial_archive_import import import_archive
            before=store.financial_backlog(quote_day,day,0)
            stats["backlog_before_archive"]=before["remaining"]
            stats.update(import_archive(store,quote_day,day))
            stats["backlog_after_archive"]=store.financial_backlog(quote_day,day,0)["remaining"]
            LOGGER.info("Financial archive reuse: %s",json.dumps(stats,ensure_ascii=False))
            if archive_only:
                stats["mode"]="archive_only"
                stats["recalc_needed"]=signature_before!=store.financial_inputs_signature(quote_day) or getattr(args,"force_recalc",False)
                store.finish_run(run_id,"success" if stats["recalc_needed"] else "skipped",stats)
                LOGGER.info("Financial refresh finished: %s",json.dumps(stats,ensure_ascii=False))
                return
        backlog = store.financial_backlog(quote_day, day, args.budget)
        stats["backlog_remaining_before"] = backlog["remaining"]
        if backlog["remaining"] > 0:
            stats["mode"] = "backlog"
            queue = [{"ts_code": code, "period": None, "actual_date": None}
                     for code in backlog["queue"]]
        else:
            stats["mode"] = "disclosure_incremental"
            events = []
            for period in recent_closed_quarters(day):
                rows = client.disclosure_rows(period.strftime("%Y%m%d"))
                stats["disclosure_api_calls"] += 1
                if len(rows) >= 6000:
                    raise RuntimeError(
                        f"disclosure_date for {period} hit the 6000-row maximum; "
                        "cannot verify complete new-disclosure coverage"
                    )
                events.extend(confirmed_disclosures(rows, period, day))
            stats["confirmed_disclosures"] = len(events)
            queue = store.financial_disclosure_queue(quote_day, day, events, args.budget)
        stats["queued"] = len(queue)
        if not queue:
            if archive_first:
                stats["recalc_needed"]=signature_before!=store.financial_inputs_signature(quote_day)
            store.finish_run(run_id, "success" if stats["recalc_needed"] else "skipped", stats)
            LOGGER.info("Financial refresh finished without provider queue: %s", json.dumps(stats, ensure_ascii=False))
            return

        for event in queue:
            code = event["ts_code"]
            period, actual_date = event["period"], event["actual_date"]
            kinds = store.financial_missing_kinds(code, day, period, actual_date)
            if not kinds:
                LOGGER.info("No report kinds due for %s; skipping", code)
                continue
            stats["attempted"] += 1
            begin = (day - timedelta(days=540)).strftime("%Y%m%d")
            end_date = day.strftime("%Y%m%d")
            counts = {}
            try:
                for kind in kinds:
                    stats["financial_api_calls"] += 1
                    reports = client.report_rows(kind, code, begin, end_date)
                    counts[kind] = store.ingest_reports(code, kind, reports)
                if all(counts.values()):
                    store.mark_financial_fetch(code, True)
                    stats["succeeded"] += 1
                else:
                    missing = [kind for kind, count in counts.items() if not count]
                    store.mark_financial_fetch(
                        code, False, "missing eligible report: " + ",".join(missing)
                    )
                    stats["failed"] += 1
                    LOGGER.warning("Financial reports incomplete for %s: %s",
                                   code, ",".join(missing))
            except TushareError as exc:
                store.mark_financial_fetch(code, False, str(exc)[:400])
                stats["failed"] += 1
                LOGGER.warning("Financial refresh failed for %s: %s", code, exc)
                error_lower = str(exc).lower()
                if ("rate-limited" in error_lower or "provider server error" in error_lower or
                        "permission" in error_lower or "quota" in error_lower or
                        "权限" in str(exc) or "积分" in str(exc) or "40203" in str(exc)):
                    raise RuntimeError(
                        "Financial provider throttled or lacks permissions. "
                        "Stopping independent refresh; published scan remains intact."
                    ) from exc
            if stats["failed"] >= 3:
                raise RuntimeError(
                    "Three incomplete financial-company refreshes; stopping this batch. "
                    "Previously saved statements remain intact."
                )
        stats["recalc_needed"] = (
            signature_before != store.financial_inputs_signature(quote_day)
        )
        if archive_first:
            stats["backlog_after_refresh"]=store.financial_backlog(quote_day,day,0)["remaining"]
        store.finish_run(run_id, "success", stats)
        LOGGER.info("Financial refresh finished: %s", json.dumps(stats, ensure_ascii=False))
    except Exception as exc:
        store.finish_run(run_id, "failed", stats, str(exc))
        LOGGER.exception("Financial refresh failed; stored statements and daily scan remain intact")
        raise


def bootstrap_prices(args):
    """Backfill recent unadjusted close prices in a compact resume-safe table.

    Historical closes are sufficient for the existing 20/50-day technical
    observation signals; avoid copying millions of full quote JSON records.
    Previously completed market sessions are skipped on retries.
    """
    today = datetime.now(CN_TZ).date()
    requested_end = date.fromisoformat(args.end_date) if args.end_date else today
    if requested_end > today:
        raise ValueError("--end-date cannot be in the future")
    db = os.environ.get("DATABASE_URL", "")
    if not db:
        raise RuntimeError("DATABASE_URL is required")
    store = Store(db)
    # If today's session has not yet been saved, do not fetch unfinished
    # same-day quotes. Historical lookback ends at the latest cached session.
    end = store.latest_quote_date(requested_end)
    if end is None:
        raise RuntimeError("No cached trading day found; run Market Data Refresh first")
    start = end - timedelta(days=args.days)
    client = TushareClient.from_environment()
    stocks = client.stocks()
    listed_codes = {str(row["ts_code"]) for row in stocks if row.get("ts_code")}
    if not listed_codes:
        raise RuntimeError("No currently listed stocks returned")
    store.ensure_price_history_tables()
    store.upsert_stocks(stocks)
    min_rows = max(1000, round(len(listed_codes) * 0.70))
    sessions = client.query(
        "trade_cal",
        {"exchange": "SSE", "start_date": start.strftime("%Y%m%d"),
         "end_date": end.strftime("%Y%m%d")},
        "cal_date,is_open",
    )
    trading_days = sorted({
        row["cal_date"] for row in sessions
        if str(row.get("is_open")) == "1" and row.get("cal_date")
    })
    if len(trading_days) < args.min_sessions:
        raise RuntimeError(
            f"Only {len(trading_days)} trading sessions in the history window; "
            f"at least {args.min_sessions} required. Increase --days."
        )
    completed = store.completed_history_days(start, end, min_rows)
    pending = [
        code for code in trading_days
        if Store._parse_date(code) not in completed
    ]
    LOGGER.info(
        "Price-history backfill: range=%s..%s, sessions=%d, cached=%d, "
        "missing=%d, minimum_daily_quotes=%d",
        start, end, len(trading_days), len(trading_days) - len(pending),
        len(pending), min_rows,
    )
    written = 0
    for index, code in enumerate(pending, 1):
        day = Store._parse_date(code)
        quote_rows = client.daily(code)
        if len(quote_rows) < min_rows:
            raise RuntimeError(
                f"Incomplete historical prices for {code}: {len(quote_rows)} rows "
                f"(need {min_rows}). Previously completed days remain saved."
            )
        saved = store.save_compact_history(
            day, quote_rows, listed_codes, min_rows
        )
        written += saved
        LOGGER.info(
            "Historical closes %d/%d: %s, saved %d listed-company prices",
            index, len(pending), code, saved,
        )
    LOGGER.info(
        "Price-history backfill completed: %s",
        json.dumps({
            "from": start.isoformat(), "to": end.isoformat(),
            "trading_sessions": len(trading_days),
            "already_cached_sessions": len(trading_days) - len(pending),
            "downloaded_sessions": len(pending),
            "api_daily_basic_calls": len(pending),
            "rows_saved": written,
            "minimum_sessions_for_signals": args.min_sessions,
        }, ensure_ascii=False),
    )



def bootstrap_financials(args):
    """Provider-authorized VIP quarter snapshot. Does not publish a daily screen."""
    db = os.environ.get("DATABASE_URL", "")
    if not db:
        raise RuntimeError("DATABASE_URL is required")
    if len(args.period) != 8 or not args.period.isdigit() or args.period[4:] not in ("0331","0630","0930","1231"):
        raise ValueError("--period must be a quarter-end YYYYMMDD")
    client, store = TushareClient.from_environment(), Store(db)
    listed = client.stocks()
    store.upsert_stocks(listed)
    codes = {row["ts_code"] for row in listed if row.get("ts_code")}
    report_sets = []
    for kind in ("balancesheet","income","cashflow"):
        items = client.bulk_report_rows(kind,args.period)
        count, updated = store.ingest_bulk_reports(kind,items,codes)
        ratio = len(updated)/max(1,len(codes))
        LOGGER.info("VIP %s period=%s: %d rows, %d companies, %.1f%% coverage",
                    kind,args.period,count,len(updated),100*ratio)
        if ratio < args.min_coverage:
            LOGGER.warning("VIP %s coverage below requested %.1f%%. Provider may paginate, "
                           "truncate, or restrict entitlement; not treating as complete.",
                           kind,100*args.min_coverage)
        report_sets.append(updated)
    complete = set.intersection(*report_sets)
    store.mark_bulk_financial_success(complete)
    LOGGER.info("VIP bootstrap: all three reports available for %d/%d currently listed companies; "
                "run the daily scan to publish a snapshot",len(complete),len(codes))


def check_api(args):
    """Read-only connectivity probe, with no Neon database writes."""
    client = TushareClient.from_environment()
    now = datetime.now(CN_TZ)
    day = market_session(client, args.date, now)
    if day == now.date() and (now.hour, now.minute) < MARKET_CLOSE:
        raise ValueError("Requested same-day market data before market close")
    open_session = client.trade_open(day.strftime("%Y%m%d"))
    stocks = client.stocks()
    if not stocks:
        raise TushareError("stock_basic returned no listed securities")
    checks = ["trade_cal", "stock_basic"]
    quotes_count = None
    if open_session:
        quotes = client.daily(day.strftime("%Y%m%d"))
        quotes_count = len(quotes)
        valid_count = len(validate_quotes(stocks, quotes, day))
        min_rows = minimum_quotes(len({s["ts_code"] for s in stocks}), args.date, day, now)
        if valid_count < min_rows:
            if early_probe(day, args.date, now):
                LOGGER.warning("API probe: awaiting same-day publication date=%s valid=%d minimum=%d; no database writes",
                               day, valid_count, min_rows)
                return
            raise TushareError(f"daily_basic incomplete: {valid_count} valid quotes; expected at least {min_rows}")
        checks.append("daily_basic")
    LOGGER.info("API connectivity OK: %s", json.dumps({
        "api_source": client.source,
        "session_date": day.isoformat(),
        "session_open": open_session,
        "listed_count": len(stocks),
        "quote_rows": quotes_count,
        "checks": checks,
        "database_writes": False
    }, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="A股Net-Net筛选与每日数据更新")
    subs = parser.add_subparsers(dest="command",required=True)
    subs.add_parser("init-db", help="Create the database tables")
    check = subs.add_parser("check-api", help="Test the selected Tushare API without database writes")
    check.add_argument("--date", help="Asia/Shanghai YYYY-MM-DD; default latest eligible session")
    market = subs.add_parser("fetch-market", help="Update stock universe and quotes only")
    market.add_argument("--date", help="Asia/Shanghai YYYY-MM-DD; default latest eligible session")
    recalc = subs.add_parser("recalculate-ncav", help="Publish NCAV using cached market and financial data")
    recalc.add_argument("--date", help="Optional latest date bound (default today)")
    triggers = recalc.add_mutually_exclusive_group()
    triggers.add_argument("--trigger-local-run-id", type=lambda v: str(__import__("uuid").UUID(v)), help="Only recalculate after a successful changed local pipeline run")
    triggers.add_argument("--trigger-run-id", type=int,
                        help="Only recalculate if triggering Actions run successfully saved market/finance data")
    scan = subs.add_parser("scan", help="Legacy combined market fetch and NCAV calculation")
    scan.add_argument("--date", help="Asia/Shanghai YYYY-MM-DD; default latest eligible session")
    finance = subs.add_parser("refresh-financials", help="Independently refresh selected financial reports")
    finance.add_argument("--asof", help="Reference date in Asia/Shanghai (YYYY-MM-DD); default today")
    finance.add_argument("--budget", type=int, default=40,
                         help="Maximum companies to refresh (missing report kinds only); default 40")
    finance.add_argument("--force-recalc",action="store_true",help="Force publication after archive-only scope migration")
    finance.add_argument("--archive-first",action="store_true",help="Reuse verified archive before provider fallback; budget 0 = archive only")
    vip = subs.add_parser("bootstrap-financials",help="Bulk quarter reports (requires Tushare VIP access)")
    vip.add_argument("--period",required=True,help="Quarter-end YYYYMMDD, e.g. 20260930")
    vip.add_argument("--min-coverage",type=float,default=0.80,
                     help="Warn if returned company coverage below threshold (default 80%%)")
    bootstrap = subs.add_parser("bootstrap-prices", help="Resume-safe compact history for 20/50-day signals")
    bootstrap.add_argument("--days", type=int, default=120,
                           help="Historical calendar-day window (default 120)")
    bootstrap.add_argument("--min-sessions", type=int, default=50,
                           help="Require at least this many open sessions (default 50)")
    bootstrap.add_argument("--end-date", help="YYYY-MM-DD; latest cached session when omitted")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
    if args.command == "init-db":
        if not os.environ.get("DATABASE_URL"):
            raise RuntimeError("DATABASE_URL is required")
        schema = Path(__file__).resolve().parents[2] / "database" / "schema.sql"
        Store(os.environ["DATABASE_URL"]).initialize(schema.read_text(encoding="utf-8"))
        LOGGER.info("Schema initialized")
    elif args.command == "check-api":
        check_api(args)
    elif args.command == "fetch-market":
        fetch_market(args)
    elif args.command == "recalculate-ncav":
        recalculate_ncav(args)
    elif args.command == "scan":
        run_scan(args)
    elif args.command == "refresh-financials":
        if not 0 <= args.budget <= 50:
            parser.error("--budget must be between 0 and 50")
        if args.force_recalc and (not args.archive_first or args.budget != 0):
            parser.error("--force-recalc requires archive-first with budget 0")
        run_financial_refresh(args)
    elif args.command == "bootstrap-financials":
        if not 0 < args.min_coverage <= 1:
            parser.error("--min-coverage must be in (0,1]")
        bootstrap_financials(args)
    else:
        if not 70 <= args.days <= 365:
            parser.error("--days must be between 70 and 365")
        if not 50 <= args.min_sessions <= 100:
            parser.error("--min-sessions must be between 50 and 100")
        bootstrap_prices(args)


if __name__ == "__main__":
    main()
