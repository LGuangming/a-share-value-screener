"""Explicit offline task router for standalone research worker."""
from __future__ import annotations
import argparse
from datetime import date
import fcntl
from pathlib import Path
import sys
import time
from .backtest_bulk import progress

from . import backtest_dataset, backtest_financial_daily, backtest_financial_evidence, backtest_security_evidence, backtest_quality


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest="task",required=True)
    market=sub.add_parser("market")
    market.add_argument("--start-year",type=int,default=2020)
    market.add_argument("--end-year",type=int,default=2025)
    market.add_argument("--month",type=int,choices=range(1,13))
    update=sub.add_parser("update")
    selection=update.add_mutually_exclusive_group(required=True)
    selection.add_argument("--day",type=date.fromisoformat)
    selection.add_argument("--year",type=int)
    update.add_argument("--month",type=int,choices=range(1,13))
    fin=sub.add_parser("financial")
    fin.add_argument("--year",type=int,required=True,choices=range(2020,2026))
    fin.add_argument("--month",type=int,choices=range(1,13))
    audit=sub.add_parser("financial-audit")
    audit.add_argument("--report",required=True)
    status=sub.add_parser("status")
    status.add_argument("--year",type=int,required=True)
    status.add_argument("--report",required=True)
    sec=sub.add_parser("security")
    sec.add_argument("--year",type=int,required=True)
    sec.add_argument("--month",type=int,required=True,choices=range(1,13))
    sec.add_argument("--capture-day",type=date.fromisoformat)
    a=p.parse_args()
    if a.task=="market":
        fn=backtest_dataset.main
        argv=["--archive","/data/archive","--output","/market","--scratch","/scratch",
              "--start-year",str(a.start_year),"--end-year",str(a.end_year)]
    elif a.task=="update":
        fn=backtest_dataset.main
        if a.day and a.month is not None:
            p.error("--month requires --year")
        year=a.day.year if a.day else a.year
        argv=["--archive","/data/archive","--output","/market","--scratch","/scratch",
              "--incremental","--start-year",str(year),"--end-year",str(year)]
        if a.day:
            argv+=["--day",a.day.isoformat()]
    elif a.task=="financial":
        fn=backtest_financial_daily.main
        argv=["--archive","/data/archive","--market","/market","--output","/financial","--scratch","/scratch","--year",str(a.year)]
    elif a.task=="financial-audit":
        if Path(a.report).name!=a.report or not a.report.endswith(".json") or a.report.startswith("."):
            p.error("Use a plain JSON filename")
        fn=backtest_financial_evidence.main
        argv=["--archive","/data/archive","--output","/manifests/"+a.report]
    elif a.task=="status":
        if Path(a.report).name!=a.report or not a.report.endswith(".json") or a.report.startswith("."):
            p.error("Use a plain JSON filename")
        fn=backtest_quality.main
        argv=["--archive","/data/archive","--market","/market","--financial","/financial",
              "--security","/security","--scratch","/scratch","--output","/manifests/"+a.report,
              "--year",str(a.year)]
    else:
        fn=backtest_security_evidence.main
        argv=["build","--archive","/data/archive","--market","/market","--evidence","/evidence",
              "--output","/security","--scratch","/scratch","--year",str(a.year)]
        if a.capture_day:
            argv+=["--capture-day",a.capture_day.isoformat()]
    if getattr(a,"month",None):
        argv+=["--month",str(a.month)]
    # Serialize all offline research tasks across invocations on the same VPS.
    with open("/scratch/.backtest-task.lock","a") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        sys.argv=[sys.argv[0],*argv]
        started=time.monotonic()
        progress("BT03_TASK_START",started,task=a.task)
        try:
            fn()
        except BaseException:
            progress("BT03_TASK_ERROR",started,task=a.task)
            raise
        progress("BT03_TASK_DONE",started,task=a.task)


if __name__=="__main__":
    main()
