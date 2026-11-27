"""One-shot private GitHub task bridge. No remote shell, Docker or MCP endpoint.

Run as a dedicated non-root Linux user with a locally installed immutable
profile configuration. GitHub issues carry exact JSON requests; only pinned
numeric actor/repository identities are accepted. Results never contain
subprocess output, paths, environment variables, hostnames or credentials.
"""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import math
import time
import subprocess
import sys
import urllib.request
import urllib.error

TITLE="VPS CONTROL v1"
SCHEMA="ashare-vps-control-v1"
PROFILE=re.compile(r"[a-z][a-z0-9_-]{0,39}\Z")
REPO=re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
MAX_RESPONSE=2_000_000
TTL=900
VERSION=3
LANES={"research","collection"}
SPOOL=Path("/var/lib/ashare-control-spool")
READ_TASKS={"capabilities","data_inventory","backtest_status","task_status","task_logs","docker_status","deployment_status"}
REPORT_KINDS={"preflight","signals","portfolio","walk","progress","containers","deployment"}
INVENTORY_KINDS={"market","security","dividend","reference"}
MAX_REPORT_BYTES=4*1024*1024
MAX_ENTRIES=20000

def dump(doc):
    return json.dumps(doc,sort_keys=True,separators=(",",":"),allow_nan=False)

def strict_json(text):
    def pairs(items):
        d={}
        for k,v in items:
            if k in d:raise ValueError("Duplicate JSON key")
            d[k]=v
        return d
    return json.loads(text,object_pairs_hook=pairs)

def trusted_file(path, executable=False):
    """Require root-owned, non-symlink and non-group/world-writable path chain."""
    p=Path(path)
    if not p.is_absolute() or p.resolve()!=p:raise ValueError("Untrusted local path")
    for x in (p,*p.parents):
        st=x.stat()
        if st.st_uid!=0 or st.st_mode & 0o022:raise ValueError("Local path must be root-owned and immutable to service")
    if not p.is_file() or (executable and not p.stat().st_mode & 0o111):
        raise ValueError("Invalid trusted local file")
    return p

def config(path):
    doc=strict_json(trusted_file(path).read_text())
    required={"repository","repository_id","actor_ids","profiles"}
    if not required<=set(doc) or set(doc)-required-{"inventory","reports","async_profiles"}:
        raise ValueError("Invalid config fields")
    if (not REPO.fullmatch(doc["repository"]) or type(doc["repository_id"]) is not int or doc["repository_id"]<=0
        or not isinstance(doc["actor_ids"],list) or not doc["actor_ids"]
        or any(type(x) is not int or x<=0 for x in doc["actor_ids"])):
        raise ValueError("Invalid pinned identities")
    for name,p in doc["profiles"].items():
        if (not PROFILE.fullmatch(name) or set(p)!={"script","timeout_seconds"} or
            type(p["timeout_seconds"]) is not int or not 1<=p["timeout_seconds"]<=7200):
            raise ValueError("Invalid local profile")
        script=trusted_file(p["script"],True)
        if not script.is_relative_to(Path("/opt/ashare-control/profiles")):
            raise ValueError("Profile outside trusted directory")
    async_profiles=doc.get("async_profiles",{})
    if not isinstance(async_profiles,dict) or len(async_profiles)>16 or any(not PROFILE.fullmatch(k) or v not in LANES or k in doc["profiles"] for k,v in async_profiles.items()):
        raise ValueError("Invalid asynchronous profile mapping")
    for name,root in doc.get("inventory",{}).items():
        if name not in INVENTORY_KINDS:raise ValueError("Invalid inventory alias")
        local_path(root)
    for name,spec in doc.get("reports",{}).items():
        if not PROFILE.fullmatch(name) or set(spec)!={"kind","file"} or spec["kind"] not in REPORT_KINDS:
            raise ValueError("Invalid report alias")
        local_path(spec["file"])
    if len(doc.get("reports",{}))>32:raise ValueError("Too many report aliases")
    return doc

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise ValueError("GitHub redirect refused")

class GitHub:
    def __init__(self,token,repo):
        if not token or not REPO.fullmatch(repo):raise ValueError("Invalid credentials/repo")
        self.token=token;self.repo=repo
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    def call(self,path,method="GET",body=None):
        url="https://api.github.com/repos/"+self.repo+path
        req=urllib.request.Request(url,method=method,
            data=dump(body).encode() if body is not None else None,
            headers={"Authorization":"Bearer "+self.token,"Accept":"application/vnd.github+json",
                     "X-GitHub-Api-Version":"2022-11-28","User-Agent":"ashare-vps-control-v1",
                     "Content-Type":"application/json"})
        with self.opener.open(req,timeout=20) as r:
            data=r.read(MAX_RESPONSE+1)
            if len(data)>MAX_RESPONSE:raise ValueError("GitHub response too large")
            return strict_json(data)
    def verify_repo(self,expected):
        r=self.call("")
        if r.get("private") is not True or r.get("visibility")!="private" or r.get("id")!=expected:
            raise ValueError("Dedicated private repository identity required")
        if r.get("archived") or not r.get("has_issues"):raise ValueError("Repository unavailable")
    def issues(self):
        return self.call("/issues?state=open&sort=created&direction=desc&per_page=100")
    def report(self,n,doc):
        return self.call(f"/issues/{n}/comments","POST",{"body":"VPS RESULT v1\n\n"+dump(doc)})
    def close(self,n):
        self.call(f"/issues/{n}","PATCH",{"state":"closed"})

def parse_request(issue,actors,now):
    if (issue.get("title")!=TITLE or issue.get("state")!="open" or "pull_request" in issue
        or issue.get("user",{}).get("id") not in actors or issue.get("user",{}).get("type")!="User"):
        return None
    n=issue.get("number");iid=issue.get("id")
    if type(n) is not int or n<=0 or type(iid) is not int or iid<=0:return None
    try:
        created=datetime.fromisoformat(issue["created_at"].replace("Z","+00:00"))
        age=(now-created).total_seconds()
        if not 0<=age<=TTL:return None
        body=issue["body"]
        if not isinstance(body,str) or len(body)>1000:return None
        doc=strict_json(body)
        if not isinstance(doc,dict) or doc.get("schema")!=SCHEMA:return None
        if doc.get("task")=="status" and set(doc)=={"schema","task"}:pass
        elif doc.get("task") in ("capabilities","data_inventory","task_status") and set(doc)=={"schema","task"}:pass
        elif doc.get("task") in ("backtest_status","task_logs","docker_status","deployment_status") and set(doc)=={"schema","task","report"}:
            if not isinstance(doc["report"],str) or not PROFILE.fullmatch(doc["report"]):return None
        elif doc.get("task")=="run_profile" and set(doc)=={"schema","task","profile"}:
            if not isinstance(doc["profile"],str) or not PROFILE.fullmatch(doc["profile"]):return None
        else:return None
        return n,iid,doc,hashlib.sha256(dump(doc).encode()).hexdigest()
    except (ValueError,TypeError,KeyError,AttributeError):
        return None

def status():
    info={}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key,_,value=line.partition(":")
        if key in ("MemTotal","MemAvailable"):info[key]=int(value.split()[0])
    disk=shutil.disk_usage("/opt/ashare") if Path("/opt/ashare").is_dir() else shutil.disk_usage("/")
    return {"cpu_count":os.cpu_count(),"load_average_1m":round(os.getloadavg()[0],2),
            "memory_total_mib":info["MemTotal"]//1024,"memory_available_mib":info["MemAvailable"]//1024,
            "disk_total_gib":round(disk.total/2**30,2),"disk_free_gib":round(disk.free/2**30,2)}

def local_path(value):
    p=Path(value)
    if not p.is_absolute() or ".." in p.parts:
        raise ValueError("Invalid configured absolute path")
    # Paths are chosen only by local admin, not a remote request.
    return p

def open_local(path,directory=False):
    """Open every path component without following symlinks; no FIFO/device reads."""
    p=local_path(path)
    fd=os.open("/",os.O_RDONLY|os.O_DIRECTORY)
    try:
        for i,name in enumerate(p.parts[1:]):
            last=i==len(p.parts)-2
            flags=os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK
            if not last or directory:flags|=os.O_DIRECTORY
            child=os.open(name,flags,dir_fd=fd)
            os.close(fd);fd=child
        st=os.fstat(fd)
        if directory and not stat.S_ISDIR(st.st_mode):raise ValueError("Not directory")
        if not directory and not stat.S_ISREG(st.st_mode):raise ValueError("Not regular file")
        return fd
    except BaseException:
        os.close(fd);raise

def bounded_json(path):
    fd=open_local(path)
    with os.fdopen(fd,"rb") as f:
        if os.fstat(f.fileno()).st_size>MAX_REPORT_BYTES:raise ValueError("Report too large")
        data=f.read(MAX_REPORT_BYTES+1)
    if len(data)>MAX_REPORT_BYTES:raise ValueError("Report too large")
    doc=strict_json(data)
    if not isinstance(doc,dict):raise ValueError("Report must be object")
    return doc

def entries(path):
    fd=open_local(path,True)
    try:
        with os.scandir(fd) as scan:
            for i,e in enumerate(scan):
                if i>=MAX_ENTRIES:raise ValueError("Inventory directory cap")
                yield e.name,e.is_dir(follow_symlinks=False),e.is_file(follow_symlinks=False)
    finally:os.close(fd)

def inventory(kind,root):
    root=local_path(root);months=set();days=set();years=set();seen=0
    if kind=="reference":
        count=0
        for name,is_dir,is_file in entries(root):
            if is_file and re.fullmatch(r"(calendar-(SSE|SZSE)|index-000(300|905|852)\.SH)-\d{4}-\d{2}-\d{2}-\d{4}-\d{2}-\d{2}\.manifest\.json",name):
                count+=1
        return {"receipt_files":count,"verification":"presence_only"}
    for y,is_dir,_ in entries(root):
        if not is_dir or not re.fullmatch(r"year=20\d{2}",y):continue
        for m,is_dir,_ in entries(root/y):
            if not is_dir or not re.fullmatch(r"month=(0[1-9]|1[0-2])",m):continue
            for n,is_dir,is_file in entries(root/y/m):
                seen+=1
                if seen>MAX_ENTRIES:raise ValueError("Inventory entry cap")
                if is_file and n=="manifest.json":
                    months.add(y[-4:]+"-"+m[-2:]);years.add(y[-4:])
                elif is_dir and re.fullmatch(r"day=20\d{6}",n) and n[4:10]==y[-4:]+m[-2:]:
                    try:
                        d=datetime.strptime(n[4:],"%Y%m%d").date()
                    except ValueError:continue
                    if any(name=="manifest.json" and file for name,_,file in entries(root/y/m/n)):
                        days.add(d.isoformat());years.add(y[-4:])
    return {"monthly_receipts":len(months),"daily_receipts":len(days),
            "years":sorted(years),"first_month":min(months) if months else None,
            "last_month":max(months) if months else None,
            "first_day":min(days) if days else None,"last_day":max(days) if days else None,
            "verification":"presence_only"}

def number(v):
    return v if type(v) in (int,float) and math.isfinite(v) and abs(v)<=1e16 else None

def numeric(doc,fields):
    return {k:number(doc[k]) for k in fields if k in doc and number(doc[k]) is not None}

def clean_report(kind,doc):
    # Strict field allowlist: never copy messages, paths, source metadata,
    # environment variables, securities, trades, logs or arbitrary strings.
    out={"kind":kind,"formal_backtest_ready":False}
    if kind=="preflight":
        if doc.get("status")!="BT05_PREFLIGHT_RESEARCH_ONLY":raise ValueError("Unexpected preflight status")
        out["research_preflight_pass"]=doc.get("research_preflight_pass") is True
        names=("calendar","market_security","dividend_capture","actions","benchmark")
        allowed=("sessions","execution_sessions","partitions","mainboard_rows","provisional_evidence_rows",
                 "missing_factors","verified_sessions","raw_rows","empty_responses","supported","blocked")
        checks=doc.get("checks",{})
        out["checks"]={k:{"ok":checks[k].get("ok") is True,**numeric(checks[k],allowed)}
                       for k in names if isinstance(checks.get(k),dict)}
        out["blockers"]=[k for k in names if k in doc.get("blockers",[])]
        out["resources"]=numeric(doc.get("resource_observation",{}),("elapsed_seconds","process_peak_rss_kib","scratch_free_bytes"))
    elif kind=="signals":
        if doc.get("status")!="BT04_RESEARCH_ONLY_NOT_CERTIFIED":raise ValueError("Unexpected signal status")
        out.update(numeric(doc,("rows","shards_completed")))
        out["signal_counts"]=numeric(doc.get("signal_counts",{}),("ma_trend_research_v0","oversold_reversion_v0"))
    elif kind=="portfolio":
        if doc.get("status")!="BT05_ENGINEERING_ONLY_NOT_CERTIFIED":raise ValueError("Unexpected portfolio status")
        out.update(numeric(doc,("sessions","rows_seen_including_warmup")))
        out["results"]={}
        fields=("total_return","annualized_return","max_drawdown","closed_trades","win_rate","profit_factor",
                "sharpe_zero_rf","completed_sessions","fees_paid","traded_notional","open_positions","stale_mark_count",
                "blocked_buys","blocked_sell_attempts","dividend_receivable","corporate_actions")
        for name in ("ma_trend_research_v0","oversold_reversion_v0"):
            x=doc.get("results",{}).get(name)
            if isinstance(x,dict):out["results"][name]=numeric(x.get("metrics",{}),fields)
    elif kind=="walk":
        if doc.get("status")!="BT05_WALK_FORWARD_ENGINEERING_ONLY":raise ValueError("Unexpected walk status")
        out["aggregate"]={k:numeric(v,("total_folds","selected_folds","unselected_folds","positive_excess_folds","mean_fold_excess_return"))
            for k,v in doc.get("aggregate",{}).items() if k in ("ma_trend_research_v0","oversold_reversion_v0") and isinstance(v,dict)}
    elif kind=="progress":
        if doc.get("status") not in ("running","completed","failed","blocked","timeout"):raise ValueError("Unknown progress")
        out["state"]=doc["status"]
        if doc.get("stage") in ("reference","dividend","preflight","signals","portfolio","walk"):out["stage"]=doc["stage"]
        out.update(numeric(doc,("completed","total","elapsed_seconds","exit_code","peak_rss_kib")))
    elif kind=="containers":
        if doc.get("status")!="LOCAL_DOCKER_SNAPSHOT_V1":raise ValueError("Unexpected docker snapshot")
        out["containers"]={}
        for k,v in doc.get("containers",{}).items():
            if k in ("web","worker","postgres","redis","backtest") and v in ("running","exited","created","restarting","paused","dead","not_found","unknown"):
                out["containers"][k]=v
        out.update(numeric(doc,("captured_unix",)))
    elif kind=="deployment":
        if doc.get("status")!="LOCAL_DEPLOYMENT_SNAPSHOT_V1":raise ValueError("Unexpected deployment snapshot")
        if doc.get("state") not in ("unknown","healthy","unhealthy","pending"):raise ValueError("Invalid deployment state")
        out["state"]=doc["state"];out.update(numeric(doc,("captured_unix",)))
        rev=doc.get("revision")
        if isinstance(rev,str) and re.fullmatch(r"[0-9a-f]{40}",rev):out["revision"]=rev
    else:raise ValueError("Unknown report kind")
    return out

def diagnostics(doc,cfg,state):
    task=doc["task"]
    if task=="capabilities":
        return {"status":"completed","bridge_version":VERSION,"inventory":sorted(cfg.get("inventory",{})),
                "reports":{k:v["kind"] for k,v in cfg.get("reports",{}).items()},
                "profiles":sorted(cfg["profiles"]),"async_profiles":cfg.get("async_profiles",{}),"async_execution":bool(cfg.get("async_profiles")),"raw_logs":False,"remote_shell":False}
    if task=="data_inventory":
        result={}
        for kind,root in sorted(cfg.get("inventory",{}).items()):
            try:result[kind]={"state":"readable",**inventory(kind,root)}
            except FileNotFoundError:result[kind]={"state":"missing"}
            except PermissionError:result[kind]={"state":"permission_denied"}
            except (OSError,ValueError):result[kind]={"state":"invalid_or_capped"}
        return {"status":"completed","inventory":result,"verification":"presence_only_not_data_audit"}
    if task=="task_status":
        result=[]
        for p in sorted(state.glob("issue-*.json"),key=lambda p:p.stat().st_mtime,reverse=True)[:20]:
            item=bounded_json(p)
            if item.get("repository_id")!=cfg["repository_id"]:continue
            previous=item.get("result") or {}
            value=previous.get("status")
            if item.get("delivery")=="running":continue
            if item.get("delivery")=="queued":
                value=queue_status(item)
            if value not in ("completed","failed","timeout","rejected","unavailable","interrupted_unknown_do_not_retry","queued","running"):value="unknown"
            result.append({"issue_number":item["issue_number"],"status":value,
                           **numeric(previous,("exit_code","elapsed_seconds"))})
        return {"status":"completed","recent_tasks":result,"running_jobs_observable_while_busy":bool(cfg.get("async_profiles"))}
    spec=cfg.get("reports",{}).get(doc["report"])
    allowed={"backtest_status":{"preflight","signals","portfolio","walk"},"task_logs":{"progress"},
             "docker_status":{"containers"},"deployment_status":{"deployment"}}[task]
    if not spec or spec["kind"] not in allowed:return {"status":"rejected","reason":"report_not_locally_enabled"}
    try:
        snapshot=clean_report(spec["kind"],bounded_json(spec["file"]))
        return {"status":"completed","report":doc["report"],"snapshot":snapshot}
    except FileNotFoundError:return {"status":"unavailable","reason":"report_not_created"}
    except PermissionError:return {"status":"unavailable","reason":"report_not_readable"}
    except (ValueError,TypeError,KeyError,AttributeError,OSError):return {"status":"unavailable","reason":"invalid_or_oversized_report"}

def execute(doc,profiles):
    if doc["task"]=="status":return {"status":"completed","metrics":status()}
    p=profiles.get(doc["profile"])
    if p is None:return {"status":"rejected","reason":"profile_not_locally_enabled"}
    script=trusted_file(p["script"],True)
    # No user-supplied arguments, shell, inherited token, environment, or stdin.
    # Local profile can maintain its own private log. Never post its output.
    started=time.monotonic()
    with subprocess.Popen([str(script)],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,cwd="/",env={"PATH":"/usr/bin:/bin","LANG":"C.UTF-8"},
        start_new_session=True) as proc:
        try:
            code=proc.wait(timeout=p["timeout_seconds"])
            return {"status":"completed" if code==0 else "failed","exit_code":code,"profile":doc["profile"],"elapsed_seconds":round(time.monotonic()-started,3)}
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid,signal.SIGKILL);proc.wait()
            return {"status":"timeout","profile":doc["profile"],"elapsed_seconds":round(time.monotonic()-started,3)}

def save(path,doc):
    temp=path.with_suffix(".tmp")
    # State directory is exclusively owned by service. Never trust remote paths.
    if temp.exists() or temp.is_symlink():temp.unlink()
    with temp.open("x",encoding="utf-8") as f:
        f.write(dump(doc));f.flush();os.fsync(f.fileno())
    os.replace(temp,path)
    fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)

def spool_path(lane,kind,iid):
    if lane not in LANES or kind not in ("requests","results") or type(iid) is not int or iid<=0:
        raise ValueError("Invalid spool identity")
    return SPOOL/lane/kind/f"issue-{iid}.json"

def publish_queue(old):
    path=spool_path(old["lane"],"requests",old["issue_id"])
    doc={"schema":"ashare-local-job-v1","repository_id":old["repository_id"],"issue_id":old["issue_id"],
         "profile":old["profile"],"request_sha256":old["request_sha256"],"lane":old["lane"]}
    if path.exists():
        if bounded_json(path)!=doc:raise ValueError("Queue identity changed")
        return
    # Setgid inbox gives worker read-only group access. Publish last; never
    # overwrite an existing request or recycle an issue identity.
    tmp=path.with_suffix(".tmp")
    if tmp.exists():tmp.unlink()
    with tmp.open("x") as f:
        f.write(dump(doc));f.flush();os.fchmod(f.fileno(),0o640);os.fsync(f.fileno())
    os.link(tmp,path);tmp.unlink()
    fd=os.open(path.parent,os.O_DIRECTORY|os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)

def queue_result(old):
    path=spool_path(old["lane"],"results",old["issue_id"])
    try:doc=bounded_json(path)
    except FileNotFoundError:return None
    for k in ("repository_id","issue_id","profile","request_sha256","lane"):
        if doc.get(k)!=old[k]:raise ValueError("Worker result identity mismatch")
    if doc.get("schema")!="ashare-local-result-v1":raise ValueError("Worker result schema mismatch")
    state=doc.get("status")
    if state not in ("running","completed","failed","timeout","rejected","interrupted_unknown_do_not_retry"):
        raise ValueError("Invalid worker status")
    # Never transfer arbitrary worker output, errors, metadata, paths or secrets.
    result={"status":state,"profile":old["profile"],**numeric(doc,("exit_code","elapsed_seconds"))}
    if state=="running":return result
    return result

def queue_status(old):
    try:
        result=queue_result(old)
        return result["status"] if result else "queued"
    except (OSError,ValueError,TypeError,KeyError):return "unknown"

def run(cfg,gh,state,now,executor=execute):
    gh.verify_repo(cfg["repository_id"])
    lock=state/"poll.lock"
    with lock.open("a") as handle:
        fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        active=False
        # Recover report delivery separately from execution. A crash after
        # starting a task is uncertain, never automatically rerun the task.
        for p in sorted(state.glob("issue-*.json")):
            old=strict_json(p.read_text())
            if old["repository_id"]!=cfg["repository_id"]:raise ValueError("State repository mismatch")
            if old["delivery"]=="closed":continue
            if old["delivery"]=="queued":
                publish_queue(old)
                result=queue_result(old)
                if result is None or result["status"]=="running":
                    active=True;continue
                old["result"]=result;old["delivery"]="pending";save(p,old)
            if old["delivery"]=="running":
                old["result"]={"status":"interrupted_unknown_do_not_retry"};old["delivery"]="pending";save(p,old)
            deliver(gh,p,old)
            return
        for issue in gh.issues():
            req=parse_request(issue,set(cfg["actor_ids"]),now)
            if req is None:continue
            n,iid,doc,h=req;p=state/f"issue-{iid}.json"
            if p.exists():continue
            if active and doc["task"]=="run_profile":continue
            # Fetch current issue and require exact request match before execution.
            latest=gh.call(f"/issues/{n}")
            if parse_request(latest,set(cfg["actor_ids"]),now)!=req:continue
            old={"repository_id":cfg["repository_id"],"issue_number":n,"issue_id":iid,
                 "request_sha256":h,"delivery":"running","result":None}
            if doc["task"]=="run_profile" and doc["profile"] in cfg.get("async_profiles",{}):
                old.update(delivery="queued",profile=doc["profile"],lane=cfg["async_profiles"][doc["profile"]])
                save(p,old)
                publish_queue(old)
                return
            save(p,old)
            try:
                result=diagnostics(doc,cfg,state) if doc["task"] in READ_TASKS else executor(doc,cfg["profiles"])
            except Exception:result={"status":"failed","reason":"local_execution_error"}
            old["result"]=result;old["delivery"]="pending";save(p,old)
            deliver(gh,p,old)
            return

def deliver(gh,path,old):
    if old["delivery"]=="pending":
        gh.verify_repo(old["repository_id"])
        result={"schema":SCHEMA,"issue_id":old["issue_id"],"request_sha256":old["request_sha256"],**old["result"]}
        gh.report(old["issue_number"],result)
        old["delivery"]="reported";save(path,old)
    if old["delivery"]=="reported":
        gh.verify_repo(old["repository_id"])
        gh.close(old["issue_number"]);old["delivery"]="closed";save(path,old)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config",type=Path,required=True);p.add_argument("--state",type=Path,required=True)
    p.add_argument("--credential",type=Path,required=True)
    a=p.parse_args()
    if os.geteuid()==0:p.error("Refuse root execution")
    cfg=config(a.config)
    state=a.state.resolve(strict=True)
    st=state.stat()
    if not state.is_dir() or st.st_uid!=os.geteuid() or st.st_mode&0o077:p.error("Private state directory required")
    token=a.credential.read_text().strip()
    try:run(cfg,GitHub(token,cfg["repository"]),state,datetime.now(timezone.utc))
    except Exception:
        # Do not leak authorization headers or arbitrary exception details.
        print("VPS_CONTROL_FAILED: check local configuration, connectivity and state",file=sys.stderr)
        raise SystemExit(1)
if __name__=="__main__":main()
