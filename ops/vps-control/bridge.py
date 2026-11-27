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
    if set(doc)!={"repository","repository_id","actor_ids","profiles"}:
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

def execute(doc,profiles):
    if doc["task"]=="status":return {"status":"completed","metrics":status()}
    p=profiles.get(doc["profile"])
    if p is None:return {"status":"rejected","reason":"profile_not_locally_enabled"}
    script=trusted_file(p["script"],True)
    # No user-supplied arguments, shell, inherited token, environment, or stdin.
    # Local profile can maintain its own private log. Never post its output.
    with subprocess.Popen([str(script)],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,cwd="/",env={"PATH":"/usr/bin:/bin","LANG":"C.UTF-8"},
        start_new_session=True) as proc:
        try:
            code=proc.wait(timeout=p["timeout_seconds"])
            return {"status":"completed" if code==0 else "failed","exit_code":code}
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid,signal.SIGKILL);proc.wait()
            return {"status":"timeout"}

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

def run(cfg,gh,state,now,executor=execute):
    gh.verify_repo(cfg["repository_id"])
    lock=state/"poll.lock"
    with lock.open("a") as handle:
        fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        # Recover report delivery separately from execution. A crash after
        # starting a task is uncertain, never automatically rerun the task.
        for p in sorted(state.glob("issue-*.json")):
            old=strict_json(p.read_text())
            if old["repository_id"]!=cfg["repository_id"]:raise ValueError("State repository mismatch")
            if old["delivery"]=="closed":continue
            if old["delivery"]=="running":
                old["result"]={"status":"interrupted_unknown_do_not_retry"};old["delivery"]="pending";save(p,old)
            deliver(gh,p,old)
            return
        for issue in gh.issues():
            req=parse_request(issue,set(cfg["actor_ids"]),now)
            if req is None:continue
            n,iid,doc,h=req;p=state/f"issue-{iid}.json"
            if p.exists():continue
            # Fetch current issue and require exact request match before execution.
            latest=gh.call(f"/issues/{n}")
            if parse_request(latest,set(cfg["actor_ids"]),now)!=req:continue
            old={"repository_id":cfg["repository_id"],"issue_number":n,"issue_id":iid,
                 "request_sha256":h,"delivery":"running","result":None}
            save(p,old)
            try:result=executor(doc,cfg["profiles"])
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
