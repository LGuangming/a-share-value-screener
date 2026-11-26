"""Single-host local worker entry point. No cron or production actions installed."""
from __future__ import annotations
import argparse
from datetime import date, datetime
import os
import subprocess
import sys
import tempfile
from uuid import uuid4
from zoneinfo import ZoneInfo
from .archive_storage import archive_driver,local_config
from .local_archive import archive_lock

TASKS=("market","financial","maintenance","backtest","history-prices",
       "history-archive","historical-st","financial-diagnosis")
CONFIRM="RUN-LOCAL-MAINTENANCE"

def run_module(module,args,env,*,cwd=None):
    subprocess.run([sys.executable,"-m","value_screener."+module,*args],
                   env=env,check=True,cwd=cwd)

def maintenance_plan(task, *, days=200, min_sessions=50, end_date=None,
                     start_year=None, end_year=None, source="merged",
                     archive_mode="verify", confirm=None, today=None):
    """Validate parameters before locks, credentials or network access."""
    today=today or datetime.now(ZoneInfo("Asia/Shanghai")).date()
    if task not in TASKS:
        raise ValueError("Unknown local task")
    if task=="history-prices":
        if not 70<=days<=365 or not 50<=min_sessions<=100:
            raise ValueError("History days must be 70..365; minimum sessions 50..100")
        if end_date and date.fromisoformat(end_date)>today:
            raise ValueError("History end date cannot be in the future")
        if confirm!=CONFIRM:
            raise ValueError("History backfill requires --confirm "+CONFIRM)
        args=["bootstrap-prices","--days",str(days),"--min-sessions",str(min_sessions)]
        if end_date:
            args+=["--end-date",end_date]
        return [("cli",args),("cli",["recalculate-ncav"])]
    if task in ("history-archive","historical-st"):
        first=start_year if start_year is not None else today.year-1
        last=end_year if end_year is not None else (2020 if task=="historical-st" else first)
        if not 2020<=last<=first<today.year:
            raise ValueError("Use a descending completed year range from 2020")
        year_args=["--start-year",str(first),"--end-year",str(last)]
        if task=="history-archive":
            if archive_mode=="verify":
                return [("neon_archive",["verify","--year",str(y)])
                        for y in range(first,last-1,-1)]
            if archive_mode!="archive":
                raise ValueError("Invalid archive mode")
            if confirm!=CONFIRM:
                raise ValueError("Historical archive collection requires --confirm "+CONFIRM)
            return [("history_batch_archive",year_args)]
        if source not in ("merged","baostock","namechange","audit"):
            raise ValueError("Invalid historical ST source")
        # Even offline merge/audit may publish manifests. Never schedule them implicitly.
        if confirm!=CONFIRM:
            raise ValueError("Historical ST maintenance requires --confirm "+CONFIRM)
        return [("phase2_historical_st",["--source",source,*year_args])]
    if task=="financial-diagnosis":
        return [("financial_gap_diagnosis",[])]
    return []

def run(task,budget=40,day=None,apply=False,year=2025,*,days=200,min_sessions=50,
        end_date=None,start_year=None,end_year=None,source="merged",archive_mode="verify",
        confirm=None):
    plan=maintenance_plan(task,days=days,min_sessions=min_sessions,end_date=end_date,
                          start_year=start_year,end_year=end_year,source=source,
                          archive_mode=archive_mode,confirm=confirm)
    if archive_driver()!="local":
        raise RuntimeError("Local pipeline requires ARCHIVE_STORAGE_DRIVER=local")
    root,_=local_config()
    if not os.getenv("DATABASE_URL") and task not in ("backtest","history-archive","historical-st"):
        raise RuntimeError("DATABASE_URL is required")
    if not 0<=budget<=50:
        raise RuntimeError("Financial budget must be between 0 and 50")
    if apply and (task!="maintenance" or os.getenv("ARCHIVE_LOCAL_ALLOW_MAINTENANCE_APPLY")!="true"):
        raise RuntimeError("Explicit local maintenance opt-in required")
    if (task=="history-prices" or (task=="history-archive" and archive_mode=="archive") or
        (task=="historical-st" and source=="namechange")) and os.getenv("TUSHARE_API_SOURCE","official").strip().lower()!="official":
        raise RuntimeError("Historical maintenance requires official Tushare API")
    with archive_lock(root,"pipeline"):
        env=dict(os.environ)
        env.pop("GITHUB_RUN_ID",None)
        env.pop("GITHUB_RUN_ATTEMPT",None)
        run_id=str(uuid4());env["PIPELINE_RUN_ID"]=run_id
        print("LOCAL_PIPELINE_START task="+task+" run_id="+run_id,flush=True)
        if task=="market":
            run_module("cli",["fetch-market"]+(["--date",day] if day else []),env)
            run_module("raw_incremental",["--trigger-local-run-id",run_id],env)
            run_module("cli",["recalculate-ncav","--trigger-local-run-id",run_id],env)
        elif task=="financial":
            run_module("cli",["refresh-financials","--archive-first","--budget",str(budget)],env)
            run_module("cli",["recalculate-ncav","--trigger-local-run-id",run_id],env)
        elif task=="maintenance":
            run_module("raw_maintenance",["--apply"] if apply else [],env)
        elif task=="backtest":
            run_module("backtest_smoke",["--archive-year",str(year)],env)
        else:
            # Offline tasks must not inherit provider credentials; archive-only
            # tasks do not need database credentials either.
            if task=="financial-diagnosis" or (task=="history-archive" and archive_mode=="verify") or (
                task=="historical-st" and source in ("merged","audit","baostock")
            ):
                env={k:v for k,v in env.items() if not k.startswith("TUSHARE_")}
            if task in ("history-archive","historical-st"):
                env.pop("DATABASE_URL",None)
                env.pop("DATABASE_READ_URL",None)
            if task=="history-archive" and archive_mode=="archive":
                # Existing batch exporter uses relative artifacts/ paths. Worker
                # /app is not writable; isolate each run in mounted TMPDIR.
                if env.get("PYTHONPATH"):
                    env["PYTHONPATH"]=os.pathsep.join(os.path.abspath(p) for p in env["PYTHONPATH"].split(os.pathsep))
                with tempfile.TemporaryDirectory(prefix="ashare-history-") as tmp:
                    for module,args in plan:
                        run_module(module,args,env,cwd=tmp)
            else:
                for module,args in plan:
                    run_module(module,args,env)
        print("LOCAL_PIPELINE_DONE task="+task+" run_id="+run_id,flush=True)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("task",choices=TASKS)
    parser.add_argument("--budget",type=int,default=40)
    parser.add_argument("--date")
    parser.add_argument("--apply",action="store_true")
    parser.add_argument("--year",type=int,default=2025)
    parser.add_argument("--days",type=int,default=200)
    parser.add_argument("--min-sessions",type=int,default=50)
    parser.add_argument("--end-date")
    parser.add_argument("--start-year",type=int)
    parser.add_argument("--end-year",type=int)
    parser.add_argument("--source",choices=("merged","baostock","namechange","audit"),default="merged")
    parser.add_argument("--archive-mode",choices=("verify","archive"),default="verify")
    parser.add_argument("--confirm")
    args=parser.parse_args()
    run(args.task,args.budget,args.date,args.apply,args.year,days=args.days,
        min_sessions=args.min_sessions,end_date=args.end_date,start_year=args.start_year,
        end_year=args.end_year,source=args.source,archive_mode=args.archive_mode,confirm=args.confirm)

if __name__=="__main__":
    main()
