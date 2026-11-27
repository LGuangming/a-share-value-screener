"""Fixed local offline BT05 profile runner.

Install root-owned entrypoints and config under /opt/ashare-control/profiles
and /etc/ashare-control. The remote task never provides dates, paths, CLI
arguments, Python imports or credentials. No network-dependent tasks here.
"""
from __future__ import annotations
import argparse
import hashlib
from datetime import date
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parent))
import runtime_slots

BASE=Path(os.environ.get("ASHARE_RESEARCH_WORK","/var/lib/ashare-control-work"))
if BASE not in (Path("/var/lib/ashare-control-work"),Path("/var/lib/ashare-research-work")):
    raise ValueError("Invalid locally selected work directory")
CONFIG=Path("/etc/ashare-control/research.json")
STAGES=("preflight","signals","portfolio","walk")

def trusted(path,directory=False):
    p=Path(path)
    if not p.is_absolute() or p.resolve()!=p:raise ValueError("Untrusted runtime path")
    for x in (p,*p.parents):
        st=x.stat()
        if st.st_uid!=0 or st.st_mode&0o022:raise ValueError("Runtime must be root-owned and immutable")
    if directory and not p.is_dir() or not directory and not p.is_file():raise ValueError("Invalid runtime file")
    return p

def local_input(value):
    p=Path(value)
    if not p.is_absolute() or p.resolve()!=p or not p.exists():
        raise ValueError("Configured input missing or symlink")
    if p.is_relative_to(BASE):raise ValueError("Input overlaps writable work directory")
    return p

def load(path=CONFIG):
    doc=json.loads(trusted(path).read_text())
    fields={"python","backend","market","security","dividend","calendar","benchmark","actions","fees",
            "warmup","start","end","run_name","shards","execution_config","walk_plan"}
    if not fields<=set(doc) or set(doc)-fields-{"use_approved_runtime"}:raise ValueError("Invalid research config keys")
    if "use_approved_runtime" in doc and type(doc["use_approved_runtime"]) is not bool:raise ValueError("Invalid runtime opt-in")
    doc=runtime_slots.resolve(doc)
    py=trusted(doc["python"]);backend=trusted(doc["backend"],True)
    if not os.access(py,os.X_OK):raise ValueError("Python not executable")
    # The complete checkout/runtime and all imported code must be locally reviewed.
    if not (backend/"value_screener"/"bt05_research.py").is_file():raise ValueError("Accepted BT05 backend required")
    for k in ("market","security","dividend","calendar","benchmark","actions","fees"):
        local_input(doc[k])
    for k in ("execution_config","walk_plan"):
        if doc[k] is not None:local_input(doc[k])
    a,b,c=[date.fromisoformat(doc[k]) for k in ("warmup","start","end")]
    if not a<b<c or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,39}",doc["run_name"]):
        raise ValueError("Invalid reviewed window/run identity")
    if type(doc["shards"]) is not int or not 1<=doc["shards"]<=16:raise ValueError("Invalid shard count")
    return doc

def command(stage,doc,work):
    if stage not in STAGES:raise ValueError("Invalid stage")
    w=Path(work)
    modules={"preflight":"bt05_preflight","signals":"bt04_research","portfolio":"bt05_research","walk":"bt05_walk_forward"}
    args=[doc["python"],"-P","-m","value_screener."+modules[stage],
          "--market",doc["market"],"--security",doc["security"],"--scratch",str(w/"scratch"),
          "--calendar",doc["calendar"]]
    if stage=="preflight":
        args+=["--dividend",doc["dividend"],"--benchmark",doc["benchmark"],"--actions",doc["actions"],
               "--warmup",doc["warmup"],"--start",doc["start"],"--end",doc["end"],"--output",str(w/"preflight.json")]
    elif stage=="signals":
        args+=["--warmup",doc["warmup"],"--start",doc["start"],"--end",doc["end"],
               "--shards",str(doc["shards"]),"--provisional","--output",str(w/"signals")]
    elif stage=="portfolio":
        args+=["--signals",str(w/"signals"),"--output",str(w/"portfolio"),"--start",doc["start"],"--end",doc["end"],
               "--actions",doc["actions"],"--fees",doc["fees"]]
        if doc["execution_config"]:args+=["--config",doc["execution_config"]]
    else:
        if not doc["walk_plan"]:raise ValueError("Walk plan not locally configured")
        args+=["--output",str(w/"walk"),"--plan",doc["walk_plan"],"--benchmark",doc["benchmark"],
               "--actions",doc["actions"],"--fees",doc["fees"],"--shards",str(doc["shards"])]
    return args

def atomic(path,doc):
    p=path.with_suffix(".tmp")
    with p.open("w") as f:json.dump(doc,f,sort_keys=True);f.flush();os.fsync(f.fileno())
    os.replace(p,path)
    fd=os.open(path.parent,os.O_DIRECTORY|os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)

def run(stage,doc):
    work=BASE/doc["run_name"]
    if work.resolve()!=work:raise ValueError("Invalid work path")
    work.mkdir(mode=0o700,exist_ok=True)
    identity={"config":doc,"evidence_sha256":{k:hashlib.sha256(Path(doc[k]).read_bytes()).hexdigest() for k in ("calendar","benchmark","actions","fees","execution_config","walk_plan") if doc[k] is not None}}
    marker=work/"local-run-contract.json"
    if marker.exists():
        if json.loads(marker.read_text())!=identity:raise ValueError("Run inputs changed; choose new run_name")
    else:atomic(marker,identity)
    for k in ("scratch","signals","portfolio","walk"):
        p=work/k
        if p.resolve()!=p:raise ValueError("Symlink work path")
        p.mkdir(mode=0o700,exist_ok=True)
    if stage=="preflight" and (work/"preflight.json").exists():
        # Do not overwrite receipts or silently reuse stale preflight; choose a new run name.
        raise ValueError("Preflight output exists; operator must choose new run_name")
    if stage in ("signals","portfolio","walk"):
        p=work/"preflight.json"
        if not p.is_file() or json.loads(p.read_text()).get("research_preflight_pass") is not True:
            raise ValueError("Passing short-window preflight required")
    if stage=="portfolio" and not (work/"signals"/"summary.json").is_file():
        raise ValueError("Completed signal run required")
    if stage=="walk" and not (work/"portfolio"/"summary.json").is_file():
        raise ValueError("Completed short portfolio acceptance required")
    # Fixed root-reviewed Python environment; no GitHub or Tushare credentials.
    env={"PATH":"/usr/bin:/bin","LANG":"C.UTF-8","PYTHONPATH":doc["backend"],
         "TMPDIR":str(work/"scratch"),"PYTHONNOUSERSITE":"1","PYTHONDONTWRITEBYTECODE":"1"}
    progress=work/"progress.json"
    stage_label={"signals":"signals","portfolio":"portfolio","walk":"walk","preflight":"preflight"}[stage]
    atomic(progress,{"status":"running","stage":stage_label})
    started=time.monotonic()
    # Store local logs only. Never pass raw logs to GitHub. Cap by discarding output
    # beyond 1 MiB while continuing to drain to avoid pipe deadlocks.
    log=work/(stage+"-latest.log")
    with log.open("wb") as f, subprocess.Popen(command(stage,doc,work),cwd="/",env=env,
            stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT) as proc:
        written=0
        while True:
            chunk=proc.stdout.read(65536)
            if not chunk:break
            if written<1048576:
                data=chunk[:1048576-written];f.write(data);written+=len(data)
        code=proc.wait()
    atomic(progress,{"status":"completed" if code==0 else "blocked" if stage=="preflight" and code==2 else "failed",
          "stage":stage_label,"exit_code":code,"elapsed_seconds":round(time.monotonic()-started,3)})
    return code

def main():
    p=argparse.ArgumentParser()
    p.add_argument("stage",choices=STAGES)
    a=p.parse_args()
    if os.geteuid()==0:p.error("Refuse root")
    raise SystemExit(run(a.stage,load()))
if __name__=="__main__":main()
