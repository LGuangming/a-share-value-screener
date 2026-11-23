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

LOGGER = logging.getLogger("value_screener")
CN_TZ = ZoneInfo("Asia/Shanghai")


def run_scan(args):
    day = date.fromisoformat(args.date) if args.date else datetime.now(CN_TZ).date()
    db = os.environ.get("DATABASE_URL", "")
    if not db:
        raise RuntimeError("DATABASE_URL is required; see README.md")
    client = TushareClient.from_environment()
    store = Store(db)
    run_id = uuid4()
    store.start_run(run_id, "daily_scan", day)
    stats = {"asof": day.isoformat(), "api_source": client.source,
             "financial_source": "cached_disclosed_reports",
             "financial_api_calls": 0,
             "notes": "Daily scan never fetches financial reports; missing or stale data stays flagged."}
    try:
        if not client.trade_open(day.strftime("%Y%m%d")):
            LOGGER.info("Closed session %s; skipping", day)
            store.finish_run(run_id, "skipped", stats)
            return
        stocks = client.stocks()
        stats["listed_stocks"] = store.upsert_stocks(stocks)
        quotes = client.daily(day.strftime("%Y%m%d"))
        # Avoid publishing false 'zero candidate' results when provider only returns partial quotes.
        min_rows = max(1000, round(stats["listed_stocks"] * 0.70))
        if len(quotes) < min_rows:
            raise RuntimeError(f"Daily quotes incomplete: {len(quotes)} versus {min_rows} minimum")
        stats["quoted_stocks"] = store.upsert_quotes(quotes, day)
        # Financials are updated by the isolated refresh-financials command.
        # Keep the daily close and cached-as-of screening independent of API 429s.
        rows = store.screen_inputs(day)
        stats["pre_screened"] = len(rows)
        results = []
        for row in rows:
            stock = {"ts_code": row["ts_code"], "name": row["name"], "is_st": row["is_st"]}
            r = assess(stock,row["quote"],row["balance"],row["income"],row["cashflow"],ScreenConfig())
            r["report_date"] = row["report_date"]
            r["ann_date"] = row["ann_date"]
            if row["report_date"] is not None:
                if (row.get("income_end_date") is not None and row["income_end_date"] != row["report_date"]) or (row.get("cashflow_end_date") is not None and row["cashflow_end_date"] != row["report_date"]):
                    r["flags"].append("FINANCIAL_PERIOD_MISMATCH")
                    r["assessment"] += " 利润或现金流与资产负债表报告期间不一致，需核对口径。"
            # No outdated balance can be used indefinitely without a stale-data flag.
            if row["report_date"] and (day - row["report_date"]).days > 280:
                r["flags"].append("STALE_FINANCIALS")
                r["is_candidate"] = False
                r["status"] = "STALE_FINANCIALS"
                r["assessment"] += " 财报报告期距今超过280天，暂不进入候选池。"
            results.append(r)
        selected = [r["ts_code"] for r in results if r["is_candidate"]]
        histories = store.price_history(selected,day)
        for r in results:
            if r["is_candidate"]:
                r["signal"] = technical_signal(histories.get(r["ts_code"], []),True,r["flags"])
        stats["candidate_count"] = len(selected)
        stats["financial_data_gap"] = sum(r["status"] == "DATA_GAP" for r in results)
        stats["rows_published"] = len(results)
        store.publish(day,run_id,results,stats)
        # Successful status and rows are committed atomically in Store.publish.
        LOGGER.info("Scan successful: %s",json.dumps(stats,ensure_ascii=False))
    except Exception as exc:
        store.finish_run(run_id,"failed",stats,str(exc))
        LOGGER.exception("Scan failed; no success marker published")
        raise


def run_financial_refresh(args):
    """Daily 40-company limit: finish coverage backlog before disclosure updates.

    On non-trading days reuse the last saved quote universe. The independent
    financial job never modifies the published daily scan snapshot.
    """
    day = date.fromisoformat(args.asof) if args.asof else datetime.now(CN_TZ).date()
    db = os.environ.get("DATABASE_URL", "")
    if not db:
        raise RuntimeError("DATABASE_URL is required")
    client = TushareClient.from_environment()
    store = Store(db)
    run_id = uuid4()
    store.start_run(run_id, "financial_refresh", day)
    stats = {"asof": day.isoformat(), "budget": args.budget,
             "api_source": client.source, "mode": None, "attempted": 0,
             "succeeded": 0, "failed": 0, "financial_api_calls": 0,
             "disclosure_api_calls": 0,
             "notes": "Fill incomplete statements first, then poll actual disclosures. No daily snapshot modified."}
    try:
        if args.budget == 0:
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
            store.finish_run(run_id, "skipped", stats)
            LOGGER.info("Financial refresh skipped: %s", json.dumps(stats, ensure_ascii=False))
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
        store.finish_run(run_id, "success", stats)
        LOGGER.info("Financial refresh finished: %s", json.dumps(stats, ensure_ascii=False))
    except Exception as exc:
        store.finish_run(run_id, "failed", stats, str(exc))
        LOGGER.exception("Financial refresh failed; stored statements and daily scan remain intact")
        raise


def bootstrap_prices(args):
    db = os.environ.get("DATABASE_URL", "")
    if not db:
        raise RuntimeError("DATABASE_URL is required")
    client, store = TushareClient.from_environment(), Store(db)
    end = date.fromisoformat(args.end_date) if args.end_date else datetime.now(CN_TZ).date()
    start = end - timedelta(days=args.days)
    stocks = client.stocks()
    store.upsert_stocks(stocks)
    sessions = client.query("trade_cal", {"exchange":"SSE", "start_date":start.strftime("%Y%m%d"),
                                         "end_date":end.strftime("%Y%m%d")},"cal_date,is_open")
    trading_days = sorted([x["cal_date"] for x in sessions if str(x.get("is_open")) == "1"])
    if not trading_days:
        raise RuntimeError("no sessions returned for bootstrap")
    for code in trading_days:
        day = Store._parse_date(code)
        quotes = client.daily(code)
        if len(quotes) < 1000:
            raise RuntimeError(f"Price history incomplete for {code}: {len(quotes)} rows")
        count = store.upsert_quotes(quotes, day)
        LOGGER.info("Price history %s: %s rows",code,count)


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
    day = date.fromisoformat(args.date) if args.date else datetime.now(CN_TZ).date()
    client = TushareClient.from_environment()
    open_session = client.trade_open(day.strftime("%Y%m%d"))
    stocks = client.stocks()
    if not stocks:
        raise TushareError("stock_basic returned no listed securities")
    checks = ["trade_cal", "stock_basic"]
    quotes_count = None
    if open_session:
        quotes = client.daily(day.strftime("%Y%m%d"))
        quotes_count = len(quotes)
        if quotes_count < 1000:
            raise TushareError(f"daily_basic incomplete: {quotes_count} quotes; expected at least 1000")
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
    check.add_argument("--date", help="Asia/Shanghai YYYY-MM-DD; default today")
    scan = subs.add_parser("scan",help="Scan a mainland-China trading day")
    scan.add_argument("--date",help="Asia/Shanghai YYYY-MM-DD; default today")
    finance = subs.add_parser("refresh-financials", help="Independently refresh selected financial reports")
    finance.add_argument("--asof", help="Reference date in Asia/Shanghai (YYYY-MM-DD); default today")
    finance.add_argument("--budget", type=int, default=40,
                         help="Companies to refresh (3 financial calls each); default 5")
    vip = subs.add_parser("bootstrap-financials",help="Bulk quarter reports (requires Tushare VIP access)")
    vip.add_argument("--period",required=True,help="Quarter-end YYYYMMDD, e.g. 20260930")
    vip.add_argument("--min-coverage",type=float,default=0.80,
                     help="Warn if returned company coverage below threshold (default 80%%)")
    bootstrap = subs.add_parser("bootstrap-prices",help="Backfill price history for signals")
    bootstrap.add_argument("--days",type=int,default=110,help="Calendar days to backfill")
    bootstrap.add_argument("--end-date",help="YYYY-MM-DD; default today")
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
    elif args.command == "scan":
        run_scan(args)
    elif args.command == "refresh-financials":
        if not 0 <= args.budget <= 50:
            parser.error("--budget must be between 0 and 50")
        run_financial_refresh(args)
    elif args.command == "bootstrap-financials":
        if not 0 < args.min_coverage <= 1:
            parser.error("--min-coverage must be in (0,1]")
        bootstrap_financials(args)
    else:
        if args.days < 1:
            parser.error("--days must be positive")
        bootstrap_prices(args)


if __name__ == "__main__":
    main()
