"""Fixed, budgeted Tushare capture runner for the separate collection account."""
from __future__ import annotations
import argparse
from datetime import date,datetime,timezone,timedelta
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from research_runner import trusted,local_input,atomic
import bridge
from worker import store

BASE=Path("/var/lib/ashare-collection-work")
CONFIG=Path("/etc/ashare-control/collection.json")

def load(path=CONFIG):
    d=json.loads(trusted(path).read_text())
    if set(d)!={"python","backend","start","end","calendar","reference_budget","dividend_budget","daily_budget"}:
        raise ValueError("Invalid collection configuration")
    trusted(d["python"]);trusted(d["backend"],True)
    start,end=map(date.fromisoformat,(d["start"],d["end"]))
    if not date(2020,1,1)<=start<=end<=date.today():raise ValueError("Invalid collection dates")
    if d["calendar"] is not None:
        calendar=local_input(d["calendar"])
        if calendar.is_relative_to(BASE) or json.loads(calendar.read_text()).get("status")!="OPERATOR_REVIEWED_CALENDAR_NOT_CERTIFIED":
            raise ValueError("Independent reviewed calendar required")
    for k,cap in (("reference_budget",30),("dividend_budget",50),("daily_budget",100)):
        if type(d[k]) is not int or not 1<=d[k]<=cap:raise ValueError("Invalid local request budget")
    return d

def reserve(base,budget,limit,now):
    # Conservative reservation before any request, no refund after interruption.
    # China calendar day; this covers only the dedicated local collection worker,
    # not other jobs sharing the provider account.
    day=now.astimezone(timezone(timedelta(hours=8))).date().isoformat()
    p=base/"budget.json"
    previous=json.loads(p.read_text()) if p.exists() else {"day":day,"reserved":0}
    if previous["day"]>day:raise ValueError("Clock moved backward")
    used=previous["reserved"] if previous["day"]==day else 0
    if type(used) is not int or used<0 or used+budget>limit:
        raise ValueError("Local daily request budget exhausted")
    atomic(p,{"day":day,"reserved":used+budget})

def command(stage,d,base):
    if stage not in ("reference","dividend"):raise ValueError("Unknown capture stage")
    module="bt05_reference_capture" if stage=="reference" else "bt05_dividend_capture"
    args=[d["python"],"-P","-m","value_screener."+module,"capture",
          "--root",str(base/stage),"--start",d["start"],"--end",d["end"],
          "--budget",str(d[stage+"_budget"]),"--confirm","CAPTURE-"+stage.upper()]
    if stage=="dividend":
        if not d["calendar"]:raise ValueError("Reviewed calendar required for dividend capture")
        args+=["--calendar",d["calendar"]]
    return args

def reference_capture(d,token):
    # Import only the locally pinned, root-owned backend; never a remote path.
    sys.path.insert(0,d["backend"])
    from value_screener import bt05_reference_capture as ref
    from value_screener.tushare_client import TushareClient
    start,end=map(date.fromisoformat,(d["start"],d["end"]))
    root=BASE/"reference"
    expected=[(kind,key,lo,hi) for lo,hi in ref.slices(start,end)
              for kind,key in [("calendar","SSE"),("calendar","SZSE")]+[("index",c) for c in ref.INDEXES]]
    def verified():
        return sum(ref.read_slice(root,*item) is not None for item in expected)
    def publish(state,n,requests=0):
        store(bridge.SPOOL/"collection"/"results"/"bt05-reference.json",
              {"status":"BT05_REFERENCE_CAPTURE_V1","state":state,"expected_slices":len(expected),
               "verified_slices":n,"new_requests":requests,"formal_backtest_ready":False})
    n=verified()
    publish("running",n)
    # First real invocation uses exactly one request. A later explicit task
    # can collect the remainder under the fixed local daily budget.
    budget=min(1 if n==0 else d["reference_budget"],len(expected)-n)
    try:
        used=0
        if budget:
            reserve(BASE,budget,d["daily_budget"],datetime.now(timezone.utc))
            result=ref.capture(root,start,end,ref.INDEXES,TushareClient(token,api_source="official"),budget)
            used=result["new_requests"]
        n=verified()
        if n==len(expected):
            candidate=BASE/"reference-candidate.json"
            doc=ref.build(root,start,end,ref.INDEXES)
            if candidate.exists():
                if json.loads(candidate.read_text())!=doc:raise ValueError("Existing candidate differs")
            else:ref.write_new(candidate,doc)
            publish("candidate_ready",n,used)
        else:publish("partial",n,used)
        return 0
    except Exception:
        # Never expose provider response, credentials or raw exception.
        try:publish("failed",verified())
        except Exception:publish("failed",n)
        return 2

def main():
    p=argparse.ArgumentParser();p.add_argument("stage",choices=("reference","dividend"))
    a=p.parse_args()
    if os.geteuid()==0:p.error("Refuse root")
    d=load();token=os.environ.get("TUSHARE_TOKEN")
    if not token or os.environ.get("TUSHARE_API_SOURCE")!="official":p.error("Official collection credential required")
    out=BASE/a.stage
    if BASE.resolve()!=BASE or out.resolve()!=out:p.error("Symlink collection root")
    out.mkdir(mode=0o700,exist_ok=True)
    with (BASE/"collection.lock").open("a") as f:
        fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if a.stage=="reference":raise SystemExit(reference_capture(d,token))
        args=command(a.stage,d,BASE)
        reserve(BASE,d[a.stage+"_budget"],d["daily_budget"],datetime.now(timezone.utc))
        # Deliberately suppress raw provider errors/output to avoid token leakage.
        # Actual captured receipts remain local and are audited separately.
        env={"PATH":"/usr/bin:/bin","LANG":"C.UTF-8","PYTHONPATH":d["backend"],
             "PYTHONNOUSERSITE":"1","PYTHONDONTWRITEBYTECODE":"1","TUSHARE_TOKEN":token,"TUSHARE_API_SOURCE":"official"}
        code=subprocess.call(args,cwd="/",env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        raise SystemExit(code)
if __name__=="__main__":main()
