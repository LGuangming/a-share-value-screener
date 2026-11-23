"""Real-price ENGINEERING smoke check, NOT a strategy-performance publication.

Unadjusted 2025 quotes omit historic ST, adjustment factors, limit prices,
corporate actions and point-in-time financial reports. We only verify that
the event-driven engine can consume a genuine Parquet dataset consistently.
"""
from __future__ import annotations
import argparse
from datetime import date
from pathlib import Path
import json

from .backtest_engine import Bar, BacktestConfig, run_backtest, generate_ma_trend_signals

ENGINEERING_CODES = ("000001.SZ","600000.SH","600036.SH","601318.SH")


def smoke(root: Path) -> dict:
    import duckdb
    manifest = json.loads((root/"manifest.json").read_text(encoding="utf-8"))
    if not manifest.get("partition_files") or manifest["sessions"]<150:
        raise RuntimeError("Historical Parquet manifest is incomplete")
    all_files = [str(root / part["path"]) for part in manifest["partition_files"]]
    db = duckdb.connect()
    rows = db.execute("""
        SELECT ts_code,trade_date,open,high,low,close,vol
        FROM read_parquet(?)
        WHERE ts_code IN ('000001.SZ','600000.SH','600036.SH','601318.SH')
        ORDER BY trade_date,ts_code
    """, [all_files]).fetchall()
    bars=[]
    for code,day,op,hi,lo,cl,vol in rows:
        bars.append(Bar(code,day,op,hi,lo,cl,vol))
    if len(bars)<250:
        raise RuntimeError("Not enough real observations for engineering smoke test")
    signals=generate_ma_trend_signals(bars)
    report={"source_year":manifest["year"],"source_rows":manifest["rows"],
            "sample_quotes":len(bars),
            "signals":sum(len(x) for x in signals.values()),
            "result_type":"ENGINEERING_ONLY_NOT_STRATEGY_PERFORMANCE","tests":[]}
    for hold in (5,10,20):
        result=run_backtest(bars,signals,BacktestConfig(
            holding_sessions=hold,strict_tradeability=False))
        buys=[f for f in result["fills"] if f["side"]=="BUY"]
        if any(f["day"] <= f["signal_day"] for f in buys):
            raise RuntimeError("Look-ahead entry discovered in backtest smoke test")
        report["tests"].append({"holding_sessions":hold,
                                "entry_fills":len(buys),
                                "closed_round_trips":len(result["trades"]),
                                "sessions":len(result["equity"]),
                                "validation":"PASSED_ENGINEERING_CONSTRAINTS"})
    print("BACKTEST_ENGINEERING_SMOKE "+json.dumps(report,ensure_ascii=False),flush=True)
    return report


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--root",default="artifacts/history-2025")
    args=parser.parse_args()
    smoke(Path(args.root))
