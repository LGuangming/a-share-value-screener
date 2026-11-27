"""Offline, fixed-plan BT03 market/security maintenance. No remote date/path/CLI."""
from __future__ import annotations
from datetime import date
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parent))
from research_runner import trusted,atomic
import bridge

BASE=Path("/var/lib/ashare-research-work")
CONFIG=Path("/etc/ashare-control/maintenance.json")
MAX_BATCHES=84

def validate(d):
    if set(d)!={"python","backend","archive","evidence","dataset","batches"}:
        raise ValueError("Invalid maintenance configuration fields")
    if not isinstance(d["dataset"],str) or not re.fullmatch(r"bt03-[a-z0-9][a-z0-9_-]{0,30}",d["dataset"]):
        raise ValueError("Invalid local dataset identity")
    batches=d["batches"]
    if not isinstance(batches,list) or not 1<=len(batches)<=MAX_BATCHES:
        raise ValueError("Invalid batch count")
    seen=set()
    for b in batches:
        if not isinstance(b,dict) or b.get("stage") not in ("market","security","incremental"):
            raise ValueError("Invalid stage")
        if b["stage"]=="incremental":
            if set(b)!={"stage","day"}:raise ValueError("Invalid incremental batch")
            day=date.fromisoformat(b["day"])
            if not 2026<=day.year<=date.today().year or day>date.today():raise ValueError("Invalid incremental date")
        else:
            if set(b)!={"stage","year","month"} or type(b["year"]) is not int or not 2020<=b["year"]<=2025 or type(b["month"]) is not int or not 1<=b["month"]<=12:
                raise ValueError("Invalid historical batch")
            if b["stage"]=="security" and bridge.dump({"stage":"market","year":b["year"],"month":b["month"]}) not in seen:
                raise ValueError("A matching market batch must precede the security batch")
        h=bridge.dump(b)
        if h in seen:raise ValueError("Duplicate batch")
        seen.add(h)
    return d

def load(path=CONFIG):
    d=validate(bridge.bounded_json(trusted(path)))
    trusted(d["python"]);trusted(d["backend"],True)
    if not os.access(d["python"],os.X_OK):raise ValueError("Runtime not executable")
    for k in ("archive","evidence"):
        p=Path(d[k])
        if not p.is_absolute() or p.resolve()!=p or not p.is_dir() or p==Path("/") or BASE.is_relative_to(p) or p.is_relative_to(BASE):
            raise ValueError("Inputs must be disjoint, existing read-only directories")
    a,e=Path(d["archive"]),Path(d["evidence"])
    if a.is_relative_to(e) or e.is_relative_to(a):raise ValueError("Overlapping evidence and archive")
    return d

def command(d,b,work):
    w=Path(work)
    p=[d["python"],"-P","-m"]
    if b["stage"]=="security":
        return p+["value_screener.backtest_security_evidence","build",
            "--archive",d["archive"],"--market",str(w/"market"),"--evidence",d["evidence"],
            "--output",str(w/"security"),"--scratch",str(w/"scratch"),
            "--year",str(b["year"]),"--month",str(b["month"])]
    p+=["value_screener.backtest_dataset","--archive",d["archive"],"--output",str(w/"market"),
        "--scratch",str(w/"scratch")]
    if b["stage"]=="incremental":
        return p+["--incremental","--start-year",b["day"][:4],"--end-year",b["day"][:4],"--day",b["day"]]
    return p+["--start-year",str(b["year"]),"--end-year",str(b["year"]),"--month",str(b["month"])]

def run(d,base=BASE,invoke=subprocess.call):
    work=Path(base)/d["dataset"]
    if work.resolve()!=work:raise ValueError("Symlink output")
    work.mkdir(mode=0o700,exist_ok=True)
    for name in ("market","security","scratch"):
        p=work/name
        if p.resolve()!=p:raise ValueError("Symlink output")
        p.mkdir(mode=0o700,exist_ok=True)
    with (work/"maintenance.lock").open("a") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        identity=hashlib.sha256(bridge.dump(d).encode()).hexdigest()
        state=work/"maintenance-state.json";report=work/"maintenance.json"
        doc=bridge.bounded_json(state) if state.exists() else {"plan_sha256":identity,"completed":0}
        if doc["plan_sha256"]!=identity:raise ValueError("Plan changed; new dataset identity required")
        n=doc["completed"];total=len(d["batches"])
        if type(n) is not int or not 0<=n<=total:raise ValueError("Invalid local checkpoint")
        base_report={"status":"BT03_LOCAL_MAINTENANCE_V1","completed_batches":n,"total_batches":total}
        if n==total:
            atomic(report,{**base_report,"state":"no_pending_batch"});return 0
        b=d["batches"][n]
        meta={**base_report,"stage":b["stage"],"year":b.get("year",int(b.get("day","2020")[:4])),
              "month":b.get("month",int(b.get("day","2020-01")[5:7]))}
        atomic(report,{**meta,"state":"running"})
        started=time.monotonic()
        env={"PATH":"/usr/bin:/bin","LANG":"C.UTF-8","PYTHONPATH":d["backend"],"PYTHONNOUSERSITE":"1",
             "PYTHONDONTWRITEBYTECODE":"1","TMPDIR":str(work/"scratch")}
        code=invoke(command(d,b,work),cwd="/",env=env,stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        # Underlying BT03 refuses differing outputs and revalidates receipts on
        # retries. A failed/interrupted batch is not advanced automatically.
        if code==0:
            doc["completed"]=n+1;atomic(state,doc)
        atomic(report,{**meta,"completed_batches":doc["completed"],"state":"completed" if code==0 else "failed",
                       "exit_code":code,"elapsed_seconds":round(time.monotonic()-started,3)})
        return code

if __name__=="__main__":
    if os.geteuid()==0:raise SystemExit("Refuse root execution")
    raise SystemExit(run(load()))
