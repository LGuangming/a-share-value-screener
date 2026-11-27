"""Local administrator-only Docker state snapshot producer.

No Docker socket in bridge, no container inspect or environment output.
Run manually from a trusted root shell, or in a separately reviewed local job.
Only aliases from a fixed list and state enums are emitted.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

ALIASES={"web","worker","postgres","redis","backtest"}
STATES={"running","exited","created","restarting","paused","dead"}

def snapshot(mapping,rows,now):
    if not mapping or not set(mapping)<=ALIASES or len(set(mapping.values()))!=len(mapping) or any(
        not isinstance(v,str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}",v) for v in mapping.values()):
        raise ValueError("Invalid fixed container names")
    found={}
    for line in rows.splitlines():
        parts=line.split("\t")
        if len(parts)!=2:raise ValueError("Unexpected Docker output")
        name,state=parts
        if name in mapping.values():
            if name in found:raise ValueError("Duplicate container")
            found[name]=state if state in STATES else "unknown"
    return {"status":"LOCAL_DOCKER_SNAPSHOT_V1","captured_unix":int(now),
            "containers":{k:found.get(v,"not_found") for k,v in mapping.items()}}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--mapping",type=Path,required=True)
    a=p.parse_args()
    if os.geteuid()!=0:p.error("Local admin only; do not grant bridge Docker access")
    out=Path("/var/lib/ashare-control-snapshots/docker.json")
    # Do not follow writable directory/symlinks when root publishes.
    for parent in (out.parent,*out.parent.parents):
        st=parent.stat()
        if parent.resolve()!=parent or st.st_uid!=0 or st.st_mode&0o022:p.error("Snapshot directory must be trusted root-owned")
    mapping=json.loads(a.mapping.read_text())
    rows=subprocess.check_output(["/usr/bin/docker","--host","unix:///var/run/docker.sock",
        "ps","-a","--format","{{.Names}}\t{{.State}}"],text=True,timeout=20,
        env={"PATH":"/usr/bin:/bin","HOME":"/root"})
    doc=snapshot(mapping,rows,time.time())
    with tempfile.NamedTemporaryFile(mode="w",dir=out.parent,delete=False) as f:
        name=f.name;json.dump(doc,f);f.flush();os.fchmod(f.fileno(),0o644);os.fsync(f.fileno())
    os.replace(name,out)
if __name__=="__main__":main()
