#!/usr/bin/python3
"""Local root-only monitoring, summary publication and non-destructive stop."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from ops_dispatch import ROOT, SCHEMAS, LIMIT, validate, trusted_dir, capacity

from image_lock import IMAGE_REF, validate_container_image

NAME="ashare-migration-postgres"
PROJECT="ashare-migration"
VOLUME="ashare-migration-pgdata"
NETWORK="ashare-migration-private"
DOCKER=["/usr/bin/docker","--host","unix:///var/run/docker.sock"]
ENV={"PATH":"/usr/bin:/bin","LANG":"C","HOME":"/root"}

def docker(*args):
    return subprocess.check_output([*DOCKER,*args],env=ENV,stderr=subprocess.DEVNULL,timeout=60)

def identity(info,root):
    labels=info["Config"]["Labels"]
    if info["Name"]!="/"+NAME or labels.get("com.docker.compose.project")!=PROJECT or labels.get("com.docker.compose.service")!="postgres" or labels.get("io.ashare.scope")!="migration-only":
        raise ValueError("unexpected container identity")
    if info["Config"]["Image"]!=IMAGE_REF:
        raise ValueError("unexpected image")
    mounts={m["Destination"]:m for m in info["Mounts"]}
    if set(mounts)!={"/var/lib/postgresql","/run/migration-secrets","/docker-entrypoint-initdb.d"}:
        raise ValueError("unexpected mounts")
    m=mounts["/var/lib/postgresql"]
    if m["Type"]!="volume" or m["Name"]!=VOLUME or not m["RW"]:
        raise ValueError("unexpected data volume")
    for dst,src in (("/run/migration-secrets","config/private/db-secrets"),("/docker-entrypoint-initdb.d","bin/db-init")):
        m=mounts[dst]
        if m["Type"]!="bind" or Path(m["Source"])!=root/src or m["RW"]:
            raise ValueError("unexpected bind")
    hc=info["HostConfig"]
    if hc["Memory"]!=800*1024**2 or hc["MemorySwap"]!=800*1024**2 or hc["NanoCpus"]!=1500000000 or hc["Privileged"] or hc["PortBindings"]!={"5432/tcp":[{"HostIp":"127.0.0.1","HostPort":"55432"}]}:
        raise ValueError("unexpected resource or port configuration")
    if set(info["NetworkSettings"]["Networks"])!={NETWORK}:
        raise ValueError("unexpected network")
    cid=info["Id"]
    if len(cid)!=64 or any(c not in "0123456789abcdef" for c in cid):
        raise ValueError("invalid container id")
    return cid

def inspect_container(root):
    trusted_dir(root)
    info=json.loads(docker("inspect","--type","container",NAME))[0]
    identity(info,root)
    local_image=json.loads(docker("image","inspect",IMAGE_REF))[0]
    validate_container_image(info,local_image)
    vol=json.loads(docker("volume","inspect",VOLUME))[0]
    net=json.loads(docker("network","inspect",NETWORK))[0]
    if (vol.get("Labels") or {}).get("io.ashare.scope")!="migration-only" or (vol.get("Labels") or {}).get("com.docker.compose.project")!=PROJECT or net["Internal"] or net["Driver"]!="bridge" or (net.get("Labels") or {}).get("com.docker.compose.project")!=PROJECT:
        raise ValueError("unexpected volume/network ownership")
    options=net.get("Options") or {}
    if options.get("com.docker.network.bridge.host_binding_ipv4")!="127.0.0.1" or options.get("com.docker.network.bridge.enable_icc")!="false" or not set(net.get("Containers") or {})<={info["Id"]}:
        raise ValueError("unexpected network access")
    return info

def monitor(root):
    info=inspect_container(root)
    state=info["State"]
    result={"running":state["Running"],"container_restarts":info["RestartCount"],"container_oom_killed":state["OOMKilled"],**capacity(root)}
    if state["Running"]:
        pid=state["Pid"]
        if type(pid) is not int or pid<=0: raise ValueError("invalid pid")
        rel=next(x.split(":",2)[2] for x in Path(f"/proc/{pid}/cgroup").read_text().splitlines() if x.startswith("0::"))
        cg=Path("/sys/fs/cgroup")/rel.lstrip("/")
        if not cg.resolve().is_relative_to(Path("/sys/fs/cgroup")): raise ValueError("invalid cgroup")
        ev=dict(line.split() for line in (cg/"memory.events").read_text().splitlines())
        result.update(memory_peak_bytes=int((cg/"memory.peak").read_text()),memory_current_bytes=int((cg/"memory.current").read_text()),oom_events=int(ev["oom"]),oom_kills=int(ev["oom_kill"]),oom_group_kills=int(ev["oom_group_kill"]),memory_max_events=int(ev["max"]))
        result["safe_to_continue"]=(not state["OOMKilled"] and info["RestartCount"]==0 and result["oom_events"]==result["oom_kills"]==result["oom_group_kills"]==0 and result["memory_available_bytes"]>=512*1024**2 and result["disk_free_bytes"]>=10*1024**3)
        # Ensure the inspected container did not restart during sampling.
        after=inspect_container(root)
        if not after["State"]["Running"] or after["Id"]!=info["Id"] or after["State"]["Pid"]!=pid or after["RestartCount"]!=info["RestartCount"]:
            raise ValueError("container changed during monitoring")
    else:
        result["safe_to_continue"]=False
    return result

def stop(root):
    info=inspect_container(root)
    cid=identity(info,root)
    if info["State"]["Running"]:
        docker("stop","--time","30",cid)
    after=inspect_container(root)
    if after["Id"]!=cid or after["State"]["Running"]:
        raise ValueError("stop not confirmed")
    # No rm, down, volume removal, production restart or deletion.
    print("PASS: isolated migration container stopped; volume retained")

def publish(root,command,raw):
    folder=root/"ops-public"
    trusted_dir(folder)
    if len(raw)>LIMIT: raise ValueError("summary too large")
    doc=validate(command,json.loads(raw))
    fd,tmp=tempfile.mkstemp(dir=folder,prefix=".summary-")
    try:
        with os.fdopen(fd,"w") as f:
            os.fchmod(f.fileno(),0o644)
            json.dump(doc,f,sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp,folder/(command+".json"))
    finally:
        Path(tmp).unlink(missing_ok=True)

def main():
    p=argparse.ArgumentParser()
    p.add_argument("action",choices=["monitor","stop","publish-summary"])
    p.add_argument("--approved-isolated-stop",action="store_true")
    p.add_argument("--summary",choices=tuple(SCHEMAS))
    a=p.parse_args()
    if os.geteuid()!=0: raise PermissionError("local administrator required")
    if a.action=="monitor":
        result=monitor(ROOT)
        print(json.dumps(result,sort_keys=True))
        if not result["safe_to_continue"]: raise SystemExit(1)
    elif a.action=="stop":
        if not a.approved_isolated_stop: raise PermissionError("stop approval required")
        stop(ROOT)
    else:
        if not a.summary: raise ValueError("summary required")
        publish(ROOT,a.summary,sys.stdin.buffer.read(LIMIT+1))
        print("PASS: aggregate summary published locally")

if __name__=="__main__":
    try: main()
    except Exception as e:
        print("FAILED:",type(e).__name__,file=sys.stderr)
        raise SystemExit(1) from None
