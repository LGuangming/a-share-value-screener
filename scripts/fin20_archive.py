"""FIN20: immutable, lossless local financial Parquet snapshot.

No network, database extensions, archive metadata writes, or source deletion.
Build a new staged bundle; restore to a separate directory. Exact logical
rows and SQLite DDL are preserved, not byte-identical SQLite database files.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile

try:
    from .fin19_standardize import canonical,digest,sources
except ImportError:
    from fin19_standardize import canonical,digest,sources

VERSION=1
CHUNK=2048
LAYERS={
    "ledger":("financial.sqlite3","ledger_sha256","ledger_bytes",2,
      "standardized_staging_not_pit_certified",
      ("sources","versions","lineage","calendar","calendar_origin","source_field_quality","group_quality","missing_input"),
      "02_standardized_data"),
    "metrics":("metrics.sqlite3","metrics_sha256","metrics_bytes",1,
      "financial_metrics_research_not_pit_certified",
      ("field_groups","period_selection","metrics","provider_checks"),"02_standardized_data"),
    "quality":("quality.sqlite3","quality_sha256","quality_bytes",1,
      "financial_quality_research_not_pit_certified",
      ("market_period","bvps","accounting_checks","comparison","strategy_quality"),"04_quality_reports"),
}
TYPES={"INTEGER":"BIGINT","REAL":"DOUBLE","TEXT":"VARCHAR"}


def quote(s):
    return '"'+s.replace('"','""')+'"'


def literal(s):
    return "'"+str(s).replace("'","''")+"'"


def separate(output,*inputs):
    if output.exists() or any(p==output or p in output.parents or output in p.parents for p in inputs):
        raise ValueError("output must be new and separate from all inputs")


def safe(root,relative):
    p=Path(relative)
    if p.is_absolute() or not p.parts or ".." in p.parts or "." in p.parts:
        raise ValueError("unsafe relative path")
    target=root/p
    if target.resolve()!=target or not target.is_relative_to(root):
        raise ValueError("unsafe or symlink path")
    return target


def file_info(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError("missing or symlink file: "+str(path))
    return {"bytes":path.stat().st_size,"sha256":digest(path)}


def check(path,info):
    if file_info(path)!={k:info[k] for k in ("bytes","sha256")}:
        raise ValueError("hash/size mismatch: "+str(path))


def ro(path):
    db=sqlite3.connect(path.as_uri()+"?mode=ro",uri=True)
    db.execute("PRAGMA query_only=ON")
    db.execute("PRAGMA cache_size=-16384")
    db.execute("PRAGMA temp_store=FILE")
    return db


def layer_input(root,layer):
    filename,hkey,bkey,fmt,state,_,_=LAYERS[layer]
    path=root/filename; mp=root/"manifest.json"
    mf=file_info(mp)
    m=json.loads(mp.read_text())
    if (m.get("format_version")!=fmt or m.get("state")!=state or m.get("pit_certified") is not False
        or m.get("production_published") is not False):
        raise ValueError("unaccepted input state: "+layer)
    info={"sha256":m[hkey],"bytes":m[bkey]}
    check(path,info)
    return path,m,info,mf


def copy_evidence(src,root,relative,inventory,expected=None):
    info=file_info(src)
    if expected is not None and info!=expected:
        raise ValueError("evidence mismatch: "+str(src))
    dst=safe(root,relative)
    dst.parent.mkdir(parents=True,exist_ok=True)
    if dst.exists():
        raise ValueError("duplicate evidence destination")
    shutil.copyfile(src,dst)
    check(dst,info)
    check(src,info)
    inventory.append({"path":relative,**info})
    return info


def raw_evidence(ledger,ledger_manifest,weekend,old,root,inventory):
    """Preserve exact new compressed responses and original control reports.

    Existing managed raw objects remain in place; their paths/hashes are
    referenced relative to 01_raw_data and verified against V5 lineage.
    """
    prefix="01_raw_data/financial_weekend"
    expected={}
    with closing(ro(ledger)) as db:
        for klass,api,path,h,n,mh,lines,rows in db.execute(
            "SELECT source_class,api,path,sha256,bytes,manifest_sha256,input_lines,accepted_rows FROM sources"):
            key=(klass,api,str(Path(path).resolve()))
            if key in expected:
                raise ValueError("duplicate ledger source")
            expected[key]=(h,n,mh,lines,rows)
    old_refs={}
    weekend_refs=[]
    seen=set()
    for klass,api,path,n,h,mp,mh,lines,rows in sources(weekend,old):
        key=(klass,api,str(path.resolve()))
        if key in seen or expected.get(key)!=(h,n,mh,lines,rows):
            raise ValueError("V5 source lineage mismatch: "+str(path))
        seen.add(key)
        info={"bytes":n,"sha256":h}
        check(path,info)
        mi=file_info(mp)
        if mi["sha256"]!=mh:
            raise ValueError("source manifest mismatch")
        if klass=="weekend":
            relative=prefix+"/archive/"+path.name
            copy_evidence(path,root,relative,inventory,info)
            weekend_refs.append({"api":api,"path":relative,"rows":rows})
        else:
            for p,i in ((path,info),(mp,mi)):
                rel=p.relative_to(old).as_posix()
                if rel in old_refs and old_refs[rel]!=i:
                    raise ValueError("inconsistent raw reference")
                old_refs[rel]=i
    if seen!=set(expected):
        raise ValueError("incomplete ledger source set")
    for name in ("manifest.json","missing.csv","unfinished.csv"):
        p=weekend/name
        info=file_info(p)
        if name=="missing.csv" and info["sha256"]!=ledger_manifest["missing_input_sha256"]:
            raise ValueError("missing report mismatch")
        if name=="manifest.json" and any(v[2]!=info["sha256"] for k,v in expected.items() if k[0]=="weekend"):
            raise ValueError("weekend manifest changed")
        copy_evidence(p,root,prefix+"/"+name,inventory,info)
    return [{"path":p,**i} for p,i in sorted(old_refs.items())],weekend_refs


def market_refs(quality,old):
    result={}
    for e in quality["market_periods"]:
        if e["status"]!="verified_market_session":
            continue
        for p,h in [(e["path"],e["sha256"]),*e["manifest_hashes"].items()]:
            path=Path(p)
            rel=path.relative_to(old).as_posix()
            path=safe(old,rel)
            info=file_info(path)
            if info["sha256"]!=h:
                raise ValueError("V7 market evidence mismatch")
            result[rel]=info
    return [{"path":p,**i} for p,i in sorted(result.items())]


def schema(db,layer):
    expected=LAYERS[layer][5]
    actual={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    if actual!=set(expected):
        raise ValueError("unrecognized table set: "+layer)
    tables={}
    for name in expected:
        ddl=db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone()[0]
        columns=db.execute("PRAGMA table_info("+quote(name)+")").fetchall()
        if not columns or any(c[2] not in TYPES for c in columns):
            raise ValueError("unsupported SQLite type")
        tables[name]={"ddl":ddl,"columns":[{"name":c[1],"type":c[2],"pk":c[5]} for c in columns]}
    extras=[{"type":t,"name":n,"ddl":sql} for t,n,sql in db.execute(
        "SELECT type,name,sql FROM sqlite_master WHERE type IN ('index','view') AND sql IS NOT NULL ORDER BY type,name")]
    return {"tables":tables,"extras":extras}


def partition_expr(cols):
    if "period" not in [c["name"] for c in cols]:
        return "'all'"
    return """CASE WHEN substr(period,1,4) GLOB '[0-9][0-9][0-9][0-9]'
        THEN substr(period,1,4) ELSE 'unknown' END"""


def scan(db,name,cols):
    names=[quote(c["name"]) for c in cols]
    pk=[quote(c["name"]) for c in sorted(cols,key=lambda x:x["pk"]) if c["pk"]]
    order=",".join(pk or ["rowid"])
    part=partition_expr(cols)
    return db.execute("SELECT "+part+","+",".join(names)+" FROM "+quote(name)+" ORDER BY 1,"+order)


def row_bytes(row):
    return (canonical(list(row))+"\n").encode("utf-8")


def add_rows(hasher,rows):
    for row in rows:
        hasher.update(row_bytes(row))


def validate_row(row,cols):
    for value,c in zip(row,cols):
        if value is None:
            continue
        if type(value)!={"INTEGER":int,"REAL":float,"TEXT":str}[c["type"]]:
            raise ValueError("unexpected SQLite dynamic type: "+c["name"])


def parquet_rows(con,path,cols):
    cursor=con.execute("SELECT "+",".join(quote(c["name"]) for c in cols)+
        " FROM read_parquet(?,hive_partitioning=false)",[str(path)])
    while True:
        rows=cursor.fetchmany(CHUNK)
        if not rows:
            break
        yield from rows


def write_chunk(con,path,rows,cols):
    path.parent.mkdir(parents=True,exist_ok=True)
    select=",".join("unnest(CAST(? AS "+TYPES[c["type"]]+"[])) AS "+quote(c["name"]) for c in cols)
    params=[list(values) for values in zip(*rows)]
    con.execute("CREATE OR REPLACE TEMP TABLE fin20_chunk AS SELECT "+select,params)
    con.execute("COPY fin20_chunk TO "+literal(path)+" (FORMAT PARQUET, COMPRESSION ZSTD)")
    expected=hashlib.sha256(); actual=hashlib.sha256()
    add_rows(expected,rows)
    count=0
    for row in parquet_rows(con,path,cols):
        actual.update(row_bytes(row));count+=1
    if count!=len(rows) or expected.hexdigest()!=actual.hexdigest():
        raise ValueError("Parquet logical row mismatch: "+str(path))
    return {"rows":count,"logical_sha256":actual.hexdigest(),**file_info(path)}


def export_table(db,con,root,layer,name,definition):
    cols=definition["columns"]; partition=None; buffer=[]; parts=[]
    h=None; n=0; chunk=0; total=0
    base=LAYERS[layer][6]+"/"+layer+"/"+name
    def flush():
        nonlocal chunk
        if not buffer:
            return
        relative=f"{base}/year={partition}/part-{chunk:06d}.parquet"
        info=write_chunk(con,safe(root,relative),buffer,cols)
        parts[-1]["files"].append({"path":relative,**info})
        buffer.clear();chunk+=1
    def end_part():
        flush()
        parts[-1].update({"rows":n,"logical_sha256":h.hexdigest()})
    for item in scan(db,name,cols):
        p,row=item[0],item[1:]
        if p!=partition:
            if partition is not None:
                end_part()
            partition=p;h=hashlib.sha256();n=chunk=0
            parts.append({"partition":p,"files":[]})
        validate_row(row,cols)
        h.update(row_bytes(row));n+=1;total+=1
        buffer.append(row)
        if len(buffer)>=CHUNK:
            flush()
    if partition is not None:
        end_part()
    if total!=db.execute("SELECT count(*) FROM "+quote(name)).fetchone()[0]:
        raise ValueError("source row count mismatch")
    return {"rows":total,"partitions":parts}


def verify_table(con,root,definition,table,db=None,name=None):
    cols=definition["columns"];total=0
    for part in table["partitions"]:
        h=hashlib.sha256();n=0
        for f in part["files"]:
            path=safe(root,f["path"]);check(path,f)
            fh=hashlib.sha256();fn=0;batch=[]
            for row in parquet_rows(con,path,cols):
                validate_row(row,cols)
                raw=row_bytes(row);h.update(raw);fh.update(raw);n+=1;fn+=1
                if db is not None:
                    batch.append(row)
                    if len(batch)>=CHUNK:
                        db.executemany("INSERT INTO "+quote(name)+" VALUES ("+",".join("?" for _ in cols)+")",batch)
                        batch.clear()
            if batch:
                db.executemany("INSERT INTO "+quote(name)+" VALUES ("+",".join("?" for _ in cols)+")",batch)
            if fn!=f["rows"] or fh.hexdigest()!=f["logical_sha256"]:
                raise ValueError("chunk row reconciliation failed")
        if n!=part["rows"] or h.hexdigest()!=part["logical_sha256"]:
            raise ValueError("partition row reconciliation failed")
        total+=n
    if total!=table["rows"]:
        raise ValueError("table row reconciliation failed")


def verify_source(db,name,definition,table):
    actual={}
    for p,*row in scan(db,name,definition["columns"]):
        if p not in actual:
            actual[p]=[0,hashlib.sha256()]
        actual[p][0]+=1
        actual[p][1].update(row_bytes(row))
    observed={p:(n,h.hexdigest()) for p,(n,h) in actual.items()}
    expected={p["partition"]:(p["rows"],p["logical_sha256"]) for p in table["partitions"]}
    if observed!=expected or sum(n for n,h in observed.values())!=table["rows"]:
        raise ValueError("SQLite/Parquet logical mismatch: "+name)


def connection():
    import duckdb
    con=duckdb.connect(":memory:")
    con.execute("SET threads=1")
    con.execute("SET memory_limit='256MB'")
    con.execute("SET preserve_insertion_order=true")
    return con


def build(ledger,metrics,quality,weekend,old,output):
    roots={k:p.resolve() for k,p in (("ledger",ledger),("metrics",metrics),("quality",quality))}
    weekend,old,output=(p.resolve() for p in (weekend,old,output))
    separate(output,*roots.values(),weekend,old)
    inputs={k:layer_input(p,k) for k,p in roots.items()}
    lm,mm,qm=(inputs[k][1] for k in LAYERS)
    if (mm["input_ledger_sha256"]!=lm["ledger_sha256"] or mm["input_manifest_sha256"]!=inputs["ledger"][3]["sha256"]
        or qm["input_ledger_sha256"]!=lm["ledger_sha256"] or qm["input_metrics_sha256"]!=mm["metrics_sha256"]
        or len({m["asof"] for m in (lm,mm,qm)})!=1):
        raise ValueError("input lineage mismatch")
    output.parent.mkdir(parents=True,exist_ok=True)
    tmp=Path(tempfile.mkdtemp(prefix=".fin20-",dir=output.parent))
    inventory=[]
    old_refs,weekend_refs=raw_evidence(inputs["ledger"][0],lm,weekend,old,tmp,inventory)
    mr=market_refs(qm,old)
    layers={}
    with closing(connection()) as con:
        for layer in LAYERS:
            path,m,info,mf=inputs[layer]
            rel="04_quality_reports/input_manifests/"+layer+".json"
            copy_evidence(roots[layer]/"manifest.json",tmp,rel,inventory,mf)
            with closing(ro(path)) as db:
                definition=schema(db,layer)
                tables={}
                for name in LAYERS[layer][5]:
                    t=export_table(db,con,tmp,layer,name,definition["tables"][name])
                    tables[name]=t
                    print("FIN20_TABLE",layer,name,t["rows"],flush=True)
                layers[layer]={"source":info,"manifest":rel,"schema":definition,"tables":tables}
            check(path,info);check(roots[layer]/"manifest.json",mf)
    for ref in old_refs+mr:
        check(safe(old,ref["path"]),ref)
    for item in inventory:
        check(safe(tmp,item["path"]),item)
    doc={"format_version":VERSION,"state":"financial_snapshot_staged_not_pit_certified",
         "asof":lm["asof"],"pit_certified":False,"production_published":False,
         "layers":layers,"evidence_files":inventory,"weekend_sources":weekend_refs,
         "existing_raw_references":old_refs,"market_references":mr,
         "limitations":["local staged snapshot; not managed object storage",
           "not historical PIT data","SQLite logical reconstruction, not byte-identical database",
           "existing managed raw files referenced, not duplicated","collection state.sqlite3 retained separately"]}
    (tmp/"manifest.json").write_text(json.dumps(doc,indent=2,ensure_ascii=False)+"\n")
    # Re-read all archived chunks before making the bundle visible.
    verify(tmp,old)
    if output.exists():
        raise ValueError("output appeared")
    tmp.rename(output)
    print("FIN20_STAGED",output,flush=True)
    print("FIN20_MANIFEST_SHA256",digest(output/"manifest.json"),flush=True)


def manifest(root):
    doc=json.loads((root/"manifest.json").read_text())
    if (doc.get("format_version")!=VERSION or doc.get("state")!="financial_snapshot_staged_not_pit_certified"
        or doc.get("pit_certified") is not False or doc.get("production_published") is not False
        or set(doc.get("layers",{}))!=set(LAYERS)):
        raise ValueError("invalid snapshot manifest")
    return doc


def verify(root,old):
    root,old=root.resolve(),old.resolve()
    doc=manifest(root)
    for item in doc["evidence_files"]:
        check(safe(root,item["path"]),item)
    for item in doc["existing_raw_references"]+doc["market_references"]:
        check(safe(old,item["path"]),item)
    with closing(connection()) as con:
        for layer,d in doc["layers"].items():
            if set(d["tables"])!=set(LAYERS[layer][5]) or set(d["schema"]["tables"])!=set(d["tables"]):
                raise ValueError("snapshot table set mismatch")
            for name,table in d["tables"].items():
                verify_table(con,root,d["schema"]["tables"][name],table)
    print("FIN20_VERIFY_PASS",root,flush=True)
    return doc


def restore(root,old,output):
    root,old,output=(p.resolve() for p in (root,old,output))
    separate(output,root,old)
    doc=verify(root,old)
    output.parent.mkdir(parents=True,exist_ok=True)
    tmp=Path(tempfile.mkdtemp(prefix=".fin20-restore-",dir=output.parent))
    with closing(connection()) as con:
        for layer,d in doc["layers"].items():
            path=tmp/LAYERS[layer][0]
            with closing(sqlite3.connect(path)) as db:
                db.execute("PRAGMA foreign_keys=ON")
                db.execute("PRAGMA cache_size=-32768")
                db.execute("PRAGMA temp_store=FILE")
                for name in LAYERS[layer][5]:
                    definition=d["schema"]["tables"][name]
                    db.execute(definition["ddl"])
                    verify_table(con,root,definition,d["tables"][name],db,name)
                    db.commit()
                for extra in d["schema"]["extras"]:
                    db.execute(extra["ddl"])
                db.commit()
                if db.execute("PRAGMA integrity_check").fetchone()[0]!="ok" or db.execute("PRAGMA foreign_key_check").fetchone():
                    raise ValueError("restored SQLite integrity failure")
                for name in LAYERS[layer][5]:
                    verify_source(db,name,d["schema"]["tables"][name],d["tables"][name])
                print("FIN20_RESTORE_PASS",layer,flush=True)
    report={"state":"logical_reconstruction_verified","pit_certified":False,"production_published":False,
            "snapshot_manifest_sha256":digest(root/"manifest.json"),
            "databases":{LAYERS[k][0]:file_info(tmp/LAYERS[k][0]) for k in LAYERS}}
    (tmp/"manifest.json").write_text(json.dumps(report,indent=2)+"\n")
    if output.exists():
        raise ValueError("restore output appeared")
    tmp.rename(output)
    print("FIN20_RESTORED",output,flush=True)


def publish(root,old,restored,archive,approve):
    """Explicit local publication after successful restore. Never updates managed keys."""
    root,old,restored,archive=(p.resolve() for p in (root,old,restored,archive))
    if old!=archive/"a-share-backtest/01_raw_data":
        raise ValueError("unexpected canonical archive root")
    if digest(root/"manifest.json")!=approve:
        raise ValueError("approval does not match staged manifest SHA256")
    doc=verify(root,old)
    receipt=json.loads((restored/"manifest.json").read_text())
    if (receipt.get("state")!="logical_reconstruction_verified"
        or receipt.get("snapshot_manifest_sha256")!=approve
        or receipt.get("pit_certified") is not False or receipt.get("production_published") is not False
        or set(receipt.get("databases",{}))!={v[0] for v in LAYERS.values()}):
        raise ValueError("restore receipt mismatch")
    for name,info in receipt["databases"].items():
        check(safe(restored,name),info)
    # Independently reconcile restored records before permitting publication.
    for layer,d in doc["layers"].items():
        with closing(ro(restored/LAYERS[layer][0])) as db:
            if schema(db,layer)!=d["schema"]:
                raise ValueError("restored schema mismatch")
            if db.execute("PRAGMA integrity_check").fetchone()[0]!="ok" or db.execute("PRAGMA foreign_key_check").fetchone():
                raise ValueError("restored integrity failure")
            for name,table in d["tables"].items():
                verify_source(db,name,d["schema"]["tables"][name],table)
    parent=archive/"a-share-backtest/financial_snapshots"
    target=parent/("snapshot="+doc["asof"]+"-"+approve[:16])
    if safe(archive,target.relative_to(archive).as_posix())!=target:
        raise ValueError("unsafe publication path")
    separate(target,root,old,restored)
    parent.mkdir(parents=True,exist_ok=True)
    tmp=Path(tempfile.mkdtemp(prefix=".fin20-publish-",dir=parent))
    # Copy only manifest-listed evidence and Parquet files, never arbitrary data.
    items=list(doc["evidence_files"])
    for d in doc["layers"].values():
        for table in d["tables"].values():
            for part in table["partitions"]:
                items.extend(part["files"])
    inventory=[]
    for item in items:
        copy_evidence(safe(root,item["path"]),tmp,item["path"],inventory,
                      {k:item[k] for k in ("bytes","sha256")})
    copy_evidence(root/"manifest.json",tmp,"manifest.json",inventory)
    if digest(tmp/"manifest.json")!=approve:
        raise ValueError("copied manifest mismatch")
    verify(tmp,old)
    if target.exists():
        raise ValueError("publication target appeared")
    tmp.rename(target)
    print("FIN20_LOCAL_ARCHIVE_PASS",target,flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest="action",required=True)
    for action,names in (("build",("ledger","metrics","quality","weekend","old","output")),
                         ("verify",("root","old")),("restore",("root","old","output"))):
        parser=sub.add_parser(action)
        for name in names:
            parser.add_argument("--"+name,type=Path,required=True)
    pub=sub.add_parser("publish")
    for name in ("root","old","restored","archive"):
        pub.add_argument("--"+name,type=Path,required=True)
    pub.add_argument("--approve",required=True)
    a=p.parse_args()
    if a.action=="build":
        build(a.ledger,a.metrics,a.quality,a.weekend,a.old,a.output)
    elif a.action=="verify":
        verify(a.root,a.old)
    elif a.action=="restore":
        restore(a.root,a.old,a.output)
    else:
        publish(a.root,a.old,a.restored,a.archive,a.approve)


if __name__=="__main__":
    main()
