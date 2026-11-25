#!/usr/bin/python3 -I
"""Fixed read-only SSH command subset. Integrate only after local administrator approval."""
import json
import os
from pathlib import Path
import stat
import sys
import time

ROOT=Path("/opt/ashare/migration")
SCHEMAS={
    "data-inventory":({"database_bytes","archive_bytes","archive_objects","table_count"},86400),
    "migration-status":({"running","locked","oom_events","oom_kills","container_restarts"},900),
    "migration-verify":({"passed","checks","failed_checks"},900),
}
BOOLEANS={"running","locked","passed"}
LIMIT=16384

def trusted_dir(path):
    for p in (path,*path.parents):
        s=p.lstat()
        if not stat.S_ISDIR(s.st_mode) or s.st_uid!=0 or s.st_mode & 0o022:
            raise ValueError("untrusted directory")

def validate(command,doc,now=None):
    fields,ttl=SCHEMAS[command]
    if not isinstance(doc,dict) or set(doc)!=fields|{"captured_at_epoch"}:
        raise ValueError("unexpected summary fields")
    stamp=doc["captured_at_epoch"]
    now=time.time() if now is None else now
    if type(stamp) is not int or not 0<=now-stamp<=ttl:
        raise ValueError("stale or future evidence")
    for k in fields:
        if k in BOOLEANS:
            if type(doc[k]) is not bool: raise ValueError("expected boolean")
        elif type(doc[k]) is not int or not 0<=doc[k]<=2**63-1:
            raise ValueError("expected bounded integer")
    if command=="migration-verify" and (doc["failed_checks"]>doc["checks"] or doc["passed"]!=(doc["checks"]>0 and doc["failed_checks"]==0)):
        raise ValueError("inconsistent verification")
    return doc

def read_summary(root,command):
    folder=root/"ops-public"
    trusted_dir(folder)
    fd=os.open(folder/(command+".json"),os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,"rb") as f:
        s=os.fstat(f.fileno())
        if not stat.S_ISREG(s.st_mode) or s.st_uid!=0 or s.st_mode & 0o022 or s.st_size>LIMIT:
            raise ValueError("untrusted summary")
        raw=f.read(LIMIT+1)
        if len(raw)>LIMIT: raise ValueError("summary too large")
    return validate(command,json.loads(raw))

def capacity(root):
    trusted_dir(root)
    fs=os.statvfs(root)
    values={}
    for line in Path("/proc/meminfo").read_text().splitlines():
        k,_,v=line.partition(":")
        if k in ("MemAvailable","MemTotal","SwapFree","SwapTotal"):
            values[k]=int(v.split()[0])*1024
    free=fs.f_bavail*fs.f_frsize
    return {"disk_free_bytes":free,"disk_free_inodes":fs.f_favail,"memory_available_bytes":values["MemAvailable"],"memory_total_bytes":values["MemTotal"],"swap_used_bytes":values["SwapTotal"]-values["SwapFree"],"baseline_start_ok":free>=30*1024**3 and values["MemAvailable"]>=1536*1024**2}

def dispatch(command,root=ROOT):
    if command=="data-capacity":
        return capacity(root)
    if command in SCHEMAS:
        return read_summary(root,command)
    raise PermissionError("Operation not permitted")

def main():
    if len(sys.argv)!=1:
        print("ERROR: Operation not permitted")
        return 64
    command=os.environ.get("SSH_ORIGINAL_COMMAND","")
    if command not in (*SCHEMAS,"data-capacity"):
        print("ERROR: Operation not permitted")
        return 64
    try:
        print(json.dumps(dispatch(command),sort_keys=True,separators=(",",":")))
        return 0
    except Exception:
        print("ERROR: Migration evidence unavailable or invalid")
        return 1

if __name__=="__main__":
    raise SystemExit(main())
