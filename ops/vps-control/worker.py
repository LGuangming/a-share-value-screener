"""Independent non-root local job worker. No GitHub credentials or API calls."""
from __future__ import annotations
import argparse
import fcntl
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

# Python -I excludes script directory; installation directory is root-owned.
sys.path.insert(0,str(Path(__file__).resolve().parent))
import bridge

def config(path,lane):
    d=bridge.strict_json(bridge.trusted_file(path).read_text())
    if set(d)!={"repository_id","profiles"} or type(d["repository_id"]) is not int or d["repository_id"]<=0:
        raise ValueError("Invalid local worker config")
    if not isinstance(d["profiles"],dict) or len(d["profiles"])>16:raise ValueError("Invalid profiles")
    for k,v in d["profiles"].items():
        if not bridge.PROFILE.fullmatch(k) or set(v)!={"script","timeout_seconds"} or type(v["timeout_seconds"]) is not int or not 1<=v["timeout_seconds"]<=7200:
            raise ValueError("Invalid local worker profile")
        allowed=Path("/opt/ashare-control/profiles")
        script=bridge.trusted_file(v["script"],True)
        if not script.is_relative_to(allowed):raise ValueError("Untrusted worker profile")
        if lane=="collection" and (k not in ("collect-reference","collect-dividend") or script!=allowed/k):
            raise ValueError("Collection worker accepts only fixed collection wrappers")
    return d

def read_request(path,lane,repository_id):
    d=bridge.bounded_json(path)
    fields={"schema","repository_id","issue_id","profile","request_sha256","lane"}
    if (set(d)!=fields or d["schema"]!="ashare-local-job-v1" or d["repository_id"]!=repository_id
        or d["lane"]!=lane or type(d["issue_id"]) is not int or d["issue_id"]<=0
        or path.name!=f"issue-{d['issue_id']}.json" or not isinstance(d["profile"],str)
        or not bridge.PROFILE.fullmatch(d["profile"]) or not isinstance(d["request_sha256"],str)
        or not re.fullmatch(r"[0-9a-f]{64}",d["request_sha256"])):
        raise ValueError("Invalid job request")
    return d

def store(path,doc):
    tmp=path.with_suffix(".tmp")
    if tmp.exists():tmp.unlink()
    with tmp.open("x") as f:
        f.write(bridge.dump(doc));f.flush();os.fchmod(f.fileno(),0o640);os.fsync(f.fileno())
    os.replace(tmp,path)
    fd=os.open(path.parent,os.O_DIRECTORY|os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)

def execute(profile,spec,lane,credential):
    if spec is None:return {"status":"rejected"}
    script=bridge.trusted_file(spec["script"],True)
    env={"PATH":"/usr/bin:/bin","LANG":"C.UTF-8"}
    if lane=="research":
        env["ASHARE_RESEARCH_WORK"]="/var/lib/ashare-research-work"
    else:
        if credential is None:raise ValueError("Missing worker credential")
        token=credential.read_text().strip()
        if not token:raise ValueError("Empty collection credential")
        env["TUSHARE_TOKEN"]=token
        env["TUSHARE_API_SOURCE"]="official"
    started=time.monotonic()
    with subprocess.Popen([str(script)],cwd="/",env=env,stdin=subprocess.DEVNULL,
                          stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True) as proc:
        try:
            code=proc.wait(timeout=spec["timeout_seconds"])
            result={"status":"completed" if code==0 else "failed","exit_code":code}
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid,signal.SIGKILL);proc.wait()
            result={"status":"timeout"}
    result["elapsed_seconds"]=round(time.monotonic()-started,3)
    return result

def run(lane,cfg,credential=None,executor=execute):
    inbox=bridge.SPOOL/lane/"requests";out=bridge.SPOOL/lane/"results"
    with (out/"worker.lock").open("a") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        # Files are never deleted automatically. Retain one receipt per request.
        for path in sorted(inbox.glob("issue-*.json")):
            req=read_request(path,lane,cfg["repository_id"])
            dest=out/path.name
            identity={**req,"schema":"ashare-local-result-v1"}
            if dest.exists():
                old=bridge.bounded_json(dest)
                if any(old.get(k)!=v for k,v in identity.items()):raise ValueError("Worker receipt identity changed")
                if old["status"]=="running":
                    store(dest,{**identity,"status":"interrupted_unknown_do_not_retry"})
                    return
                if old["status"] not in ("completed","failed","timeout","rejected","interrupted_unknown_do_not_retry"):
                    raise ValueError("Unknown worker receipt status")
                continue
            store(dest,{**identity,"status":"running"})
            try:result=executor(req["profile"],cfg["profiles"].get(req["profile"]),lane,credential)
            except Exception:result={"status":"failed"}
            store(dest,{**identity,**result})
            return

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--lane",required=True,choices=sorted(bridge.LANES))
    p.add_argument("--config",required=True,type=Path)
    p.add_argument("--credential",type=Path)
    a=p.parse_args()
    if os.geteuid()==0:p.error("Refuse root worker")
    if a.lane=="research" and a.credential:p.error("Research worker must not receive collection credential")
    try:run(a.lane,config(a.config,a.lane),a.credential)
    except Exception:
        print("VPS_WORKER_FAILED: check local configuration and queue receipts",file=sys.stderr)
        raise SystemExit(1)
if __name__=="__main__":main()
