"""Select only preinstalled locally approved immutable research runtimes.

Not a downloader, package installer, privileged updater or production deployer.
Local root owns the registry; the non-root worker can select one of two slots.
"""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import re
import stat
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent))
import bridge

REGISTRY=Path("/etc/ashare-control/runtime-registry.json")
BASE=Path("/var/lib/ashare-research-work")
SLOTS=("baseline","candidate")

def trusted_directory(value):
    p=Path(value)
    if not p.is_absolute() or p.resolve()!=p or not p.is_dir():
        raise ValueError("Invalid immutable runtime directory")
    for x in (p,*p.parents):
        s=x.stat()
        if s.st_uid!=0 or s.st_mode&0o022:raise ValueError("Untrusted runtime directory")
    return p

def tree_hash(path):
    root=trusted_directory(path);h=hashlib.sha256();total=0;count=0
    for parent,dirs,files in os.walk(root,followlinks=False):
        dirs.sort();files.sort()
        for n in dirs:
            p=Path(parent)/n;trusted_directory(p)
        for n in files:
            p=bridge.trusted_file(Path(parent)/n)
            st=p.stat();count+=1;total+=st.st_size
            if count>10000 or total>128*1024*1024:raise ValueError("Backend digest size cap")
            h.update(str(p.relative_to(root)).encode()+b"\0")
            h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()

def load(path=REGISTRY):
    d=bridge.bounded_json(bridge.trusted_file(path))
    if set(d)!={"baseline","candidate"}:raise ValueError("Exactly two approved slots required")
    for slot,v in d.items():
        if set(v)!={"revision","python","backend","python_sha256","backend_sha256"}:
            raise ValueError("Invalid slot fields")
        if not re.fullmatch(r"[0-9a-f]{40}",v["revision"]) or any(not re.fullmatch(r"[0-9a-f]{64}",v[k]) for k in ("python_sha256","backend_sha256")):
            raise ValueError("Invalid release digests")
    if d["baseline"]["revision"]==d["candidate"]["revision"]:raise ValueError("Slots must have distinct revisions")
    return d

def verify(d,slot):
    if slot not in SLOTS:raise ValueError("Invalid runtime slot")
    v=d[slot];p=bridge.trusted_file(v["python"],True)
    if p.stat().st_size>128*1024*1024:raise ValueError("Python executable size cap")
    if hashlib.sha256(p.read_bytes()).hexdigest()!=v["python_sha256"] or tree_hash(v["backend"])!=v["backend_sha256"]:
        raise ValueError("Runtime source/executable digest changed")
    return v

def selected(d,base=BASE):
    p=base/"runtime-selection.json"
    if not p.exists():return "baseline"
    x=bridge.bounded_json(p)
    if set(x)!={"slot","revision"} or x["slot"] not in SLOTS or x["revision"]!=d[x["slot"]]["revision"]:
        raise ValueError("Selection no longer matches local registry")
    return x["slot"]

def switch(slot,d,base=BASE):
    v=verify(d,slot)
    bridge.save(base/"runtime-selection.json",{"slot":slot,"revision":v["revision"]})
    bridge.save(base/"runtime-status.json",{"status":"LOCAL_RESEARCH_RUNTIME_V1","slot":slot,"revision":v["revision"]})

def resolve(doc):
    # Opt-in from root-owned research/maintenance configuration, never from GitHub.
    if doc.pop("use_approved_runtime",False):
        d=load();v=verify(d,selected(d))
        doc.update(python=v["python"],backend=v["backend"])
    return doc

if __name__=="__main__":
    if len(sys.argv)==3 and sys.argv[1]=="digest":
        # Administrator preparation: compute exact digest of trusted backend tree.
        print(tree_hash(sys.argv[2]))
    elif len(sys.argv)==2 and sys.argv[1] in SLOTS and os.geteuid()!=0:
        switch(sys.argv[1],load())
    else:raise SystemExit("Use digest PATH or a fixed approved slot as non-root")
