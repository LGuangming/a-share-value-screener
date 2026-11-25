"""Single-host local worker entry point. No cron or production actions installed."""
from __future__ import annotations
import argparse
import os
import subprocess
import sys
from uuid import uuid4
from .archive_storage import archive_driver,local_config
from .local_archive import archive_lock

def run_module(module,args,env):
    subprocess.run([sys.executable,"-m","value_screener."+module,*args],env=env,check=True)

def run(task,budget=40,day=None,apply=False,year=2025):
    if archive_driver()!="local":
        raise RuntimeError("Local pipeline requires ARCHIVE_STORAGE_DRIVER=local")
    root,_=local_config()
    if not os.getenv("DATABASE_URL") and task!="backtest":
        raise RuntimeError("DATABASE_URL is required")
    if not 0<=budget<=50:
        raise RuntimeError("Financial budget must be between 0 and 50")
    if apply and (task!="maintenance" or os.getenv("ARCHIVE_LOCAL_ALLOW_MAINTENANCE_APPLY")!="true"):
        raise RuntimeError("Explicit local maintenance opt-in required")
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
            raise RuntimeError("Unknown local task")
        print("LOCAL_PIPELINE_DONE task="+task+" run_id="+run_id,flush=True)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("task",choices=("market","financial","maintenance","backtest"))
    parser.add_argument("--budget",type=int,default=40)
    parser.add_argument("--date")
    parser.add_argument("--apply",action="store_true")
    parser.add_argument("--year",type=int,default=2025)
    args=parser.parse_args()
    run(args.task,args.budget,args.date,args.apply,args.year)

if __name__=="__main__":
    main()
