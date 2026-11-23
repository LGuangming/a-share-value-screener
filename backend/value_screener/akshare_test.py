"""Bounded AKShare experiment; isolated job_type and strategy.

Usage:
  PYTHONPATH=backend python -m value_screener.akshare_test --codes 000910,600051,002462
  PYTHONPATH=backend python -m value_screener.akshare_test --publish --codes 000910,600051,002462

Without --publish: no database credentials or writes; emits JSON report.
With --publish: writes only strategy=akshare_test_v1 and job_type=akshare_test.
"""
from __future__ import annotations

import argparse
from datetime import datetime, date, time
import json
import logging
import os
from pathlib import Path
import re
from uuid import uuid4
from zoneinfo import ZoneInfo

from .akshare_client import AKShareClient, AKShareError
from .rules import assess, ScreenConfig

LOGGER = logging.getLogger("akshare_test")
CN_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_CODES = "000910,600051,002462"
TEST_STRATEGY = "akshare_test_v1"


def code_list(raw: str) -> list[str]:
    result: list[str] = []
    for part in raw.split(","):
        s = part.strip().upper()
        if not s:
            continue
        if re.fullmatch(r"\d{6}", s):
            s = s + (".SH" if s.startswith(("600","601","603","605","688","689")) else ".SZ")
        if not re.fullmatch(r"\d{6}\.(SH|SZ)", s):
            raise ValueError("Only Shanghai/Shenzhen six-digit A-share codes are supported: " + s)
        if s not in result:
            result.append(s)
    if not 1 <= len(result) <= 8:
        raise ValueError("AKShare experiment requires 1 to 8 explicit tickers; never bulk scan by default")
    return result


def scan_codes(client: AKShareClient, codes: list[str], asof: date) -> dict:
    spot = client.spot()
    results: list[dict] = []
    failures: list[dict] = []
    financial_ok = 0
    for code in codes:
        quote = spot.get(code)
        if not quote:
            failures.append({"ts_code": code, "error": "Quote missing in AKShare live snapshot"})
            continue
        statements: dict = {}
        periods: dict = {}
        flags: list[str] = []
        for kind in ("balancesheet", "income", "cashflow"):
            try:
                data, report_date, disclosed_at, warnings = client.report(code, kind, asof)
                statements[kind] = data
                periods[kind] = (report_date, disclosed_at)
                flags.extend(warnings)
            except AKShareError as exc:
                LOGGER.warning("%s %s failed: %s", code, kind, exc)
                failures.append({"ts_code": code, "statement": kind, "error": str(exc)[:160]})
                statements[kind] = None
                periods[kind] = (None, None)
                flags.append("AKSHARE_FINANCIAL_SOURCE_UNAVAILABLE")
        if statements["balancesheet"] is not None:
            financial_ok += 1
        stock = {"ts_code": code, "name": quote["name"], "is_st": "ST" in quote["name"].upper()}
        record = assess(stock, quote, statements["balancesheet"], statements["income"],
                        statements["cashflow"], ScreenConfig())
        balance_day, notice_day = periods["balancesheet"]
        record["report_date"] = balance_day
        record["ann_date"] = notice_day
        record["flags"] += sorted(set(flags))
        record["flags"].append("AKSHARE_EXPERIMENTAL")
        record["flags"].append("LIVE_QUOTE_DATE_UNVERIFIED")
        income_day = periods["income"][0]
        cash_day = periods["cashflow"][0]
        if balance_day and ((income_day and income_day != balance_day) or
                            (cash_day and cash_day != balance_day)):
            record["flags"].append("FINANCIAL_PERIOD_MISMATCH")
        # Consolidation must be reconciled to official announcement first.
        if "CONSOLIDATION_NOT_CONFIRMED" in record["flags"]:
            record["flags"].append("REQUIRES_OFFICIAL_REPORT_CHECK")
        # No historical prices are obtained by the live-spot endpoint.
        record["signal"] = "NOT_EVALUATED"
        record["assessment"] += " AKShare实验数据，报价交易日及合并口径须与上市公司公告核对；未生成买卖信号。"
        results.append(record)

    return {
        "source": "AKShare / Eastmoney experimental",
        "asof": asof.isoformat(),
        "quote_timestamp_certified": False,
        "spot_universe": len(spot),
        "sample_codes": codes,
        "sample_available": len(results),
        "balancesheets_usable": financial_ok,
        "strict_book_candidates_unverified": sum(bool(r["is_candidate"]) for r in results),
        "failures": failures,
        "results": results,
        "disclaimer": "Test-only, not a full market screen, verified closing quote, historical backtest or trading recommendation.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="AKShare bounded test alongside official Tushare pipeline")
    parser.add_argument("--codes", default=DEFAULT_CODES, help="Comma-separated 1-8 A-share codes")
    parser.add_argument("--output", default="akshare-test-results.json")
    parser.add_argument("--publish", action="store_true", help="Write isolated AKShare test records into Neon")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    now = datetime.now(CN_TZ)
    codes = code_list(args.codes)
    if args.publish and (now.weekday() > 4 or now.time() < time(15, 45)):
        raise RuntimeError("Publishing requires a mainland weekday after 15:45 Beijing time; AKShare live spot lacks a verified session date")

    report = scan_codes(AKShareClient(), codes, now.date())
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    LOGGER.info("AKShare test: %s quote rows, %s/%s samples with usable balance sheets, %s warnings",
                report["spot_universe"], report["balancesheets_usable"], len(codes), len(report["failures"]))
    if not args.publish:
        return

    if report["balancesheets_usable"] == 0:
        raise RuntimeError("No dated and usable asset/liability report; refusing to publish misleading AKShare snapshot")
    db = os.getenv("DATABASE_URL")
    if not db:
        raise RuntimeError("DATABASE_URL write credential required only for --publish")
    from .store import Store  # optional database dependency for publish mode only
    store = Store(db)
    run_id = uuid4()
    store.start_run(run_id, "akshare_test", now.date())
    metrics = {k: report[k] for k in
               ("source", "asof", "sample_codes", "spot_universe", "sample_available",
                "balancesheets_usable", "strict_book_candidates_unverified")}
    metrics["financial_data_gap"] = len(report["results"]) - report["balancesheets_usable"]
    metrics["warning_count"] = len(report["failures"])
    try:
        stocks = [{"ts_code": r["ts_code"], "name": r["name"], "industry": None, "market": "主板"}
                  for r in report["results"]]
        store.upsert_stocks(stocks)
        stored = store.publish(now.date(), run_id, report["results"], metrics,
                               strategy=TEST_STRATEGY)
        LOGGER.info("Published %d AKShare rows under %s. Formal Tushare snapshots untouched.",
                    stored, TEST_STRATEGY)
    except Exception as exc:
        store.finish_run(run_id, "failed", metrics, str(exc))
        raise


if __name__ == "__main__":
    main()
