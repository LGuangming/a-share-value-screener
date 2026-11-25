"""Offline archive validation; never contacts source or mutates archived files."""
import argparse
import json
from pathlib import Path
from datetime import date, datetime
import duckdb
import pyarrow.parquet as pq
from common import digest, load, main_guard, write_json
from storage import safe_path, verify

RAW="a-share-backtest/01_raw_data/"
def iso(v):
    return v.isoformat() if isinstance(v,(date,datetime)) else str(v) if v is not None else None

def inspect(root):
    root=Path(root)
    manifest=load(root/"inventory-storage.json")
    verify(manifest,root)
    state=load(root/"checkpoint.json")
    objects={(x["bucket"],x["key"]):x for x in manifest["objects"]}
    files={}
    references=set()
    unclassified=[]
    with duckdb.connect(":memory:",config={"threads":"1","memory_limit":"256MB","enable_external_access":"true","autoinstall_known_extensions":"false","autoload_known_extensions":"false"}) as db:
        for (b,k),o in objects.items():
            if not k.endswith(".parquet"):
                continue
            path=safe_path(root/"files",b,k)
            pf=pq.ParquetFile(path)
            # Read each column and row group, not merely footer metadata.
            count=sum(batch.num_rows for batch in pf.iter_batches(batch_size=16384))
            names=pf.schema_arrow.names
            if len(names)!=len(set(names)) or count!=pf.metadata.num_rows:
                raise ValueError("Parquet schema or row count invalid")
            sql_count=db.execute("SELECT count(*) FROM read_parquet(?, hive_partitioning=false)",[str(path)]).fetchone()[0]
            if sql_count!=count:
                raise ValueError("DuckDB row count mismatch")
            info={"rows":count,"fields":names,"schema":str(pf.schema_arrow),"coverage":{}}
            for col in ("trade_date","end_date","ann_date","f_ann_date","disclosed_at"):
                if col in names:
                    q='SELECT min("'+col+'"),max("'+col+'"),count("'+col+'") FROM read_parquet(?, hive_partitioning=false)'
                    a,z,n=db.execute(q,[str(path)]).fetchone()
                    info["coverage"][col]=[iso(a),iso(z),n]
            if "trade_date" in names and "ts_code" in names:
                info["unique_stock_days"]=db.execute("SELECT count(DISTINCT (ts_code,trade_date)) FROM read_parquet(?, hive_partitioning=false)",[str(path)]).fetchone()[0]
            files[(b,k)]=info

    def check_ref(b,key,entry):
        ident=(b,key)
        if ident not in objects:
            raise ValueError("manifest target missing")
        references.add(ident)
        expected=entry.get("sha256")
        actual=state[b+"/"+key]["sha256"]
        if expected is not None and expected!=actual:
            raise ValueError("manifest SHA256 mismatch")
        sizes=[entry[x] for x in ("bytes","parquet_bytes","compressed_bytes") if x in entry]
        if any(type(s) is not int or s!=objects[ident]["size"] for s in sizes):
            raise ValueError("manifest size mismatch")
        if ident in files:
            info=files[ident]
            if "rows" in entry and (type(entry["rows"]) is not int or entry["rows"]!=info["rows"]):
                raise ValueError("manifest rows mismatch")
            if "fields" in entry and entry["fields"]!=info["fields"]:
                raise ValueError("manifest fields mismatch")
            if entry.get("dataset") in ("daily_prices","daily_basic","adj_factor") and info.get("unique_stock_days")!=info["rows"]:
                raise ValueError("duplicate stock/day rows")
            coverage=info["coverage"].get("trade_date")
            if coverage and entry.get("trade_date") and coverage[:2]!=[entry["trade_date"]]*2:
                raise ValueError("manifest trade date mismatch")
        return ident

    def walk(b,node,manifest_key):
        hits=0
        if isinstance(node,dict):
            if isinstance(node.get("object_key"),str):
                check_ref(b,node["object_key"],node)
                hits+=1
            if "partition_files" in node:
                parts=node["partition_files"]
                if not isinstance(parts,list):
                    raise ValueError("invalid partition list")
                for part in parts:
                    # Legacy OHLCV manifests use paths relative to RAW_PREFIX.
                    key=RAW+part["path"]
                    check_ref(b,key,part)
                    hits+=1
                if "rows" in node and sum(p["rows"] for p in parts)!=node["rows"]:
                    raise ValueError("partition sum mismatch")
            for key,val in node.items():
                if key!="partition_files":
                    hits+=walk(b,val,manifest_key)
        elif isinstance(node,list):
            for val in node:
                hits+=walk(b,val,manifest_key)
        return hits

    for b,k in objects:
        if not (k.endswith("manifest.json") or k.endswith(".manifest.json")):
            continue
        doc=load(safe_path(root/"files",b,k))
        if not isinstance(doc,dict):
            raise ValueError("manifest must be a JSON object")
        if not walk(b,doc,k):
            # No silent success on unsupported catalog, provenance or pointer shapes.
            unclassified.append(b+"/"+k)
    unreferenced=sorted(b+"/"+k for b,k in files if (b,k) not in references)
    report={"passed":not unclassified and not unreferenced and bool(files),"parquet_files":len(files),
            "parquet_rows":sum(x["rows"] for x in files.values()),"manifest_references":len(references),
            "unclassified_manifests":sorted(unclassified),"unreferenced_parquet":unreferenced,
            "files":[{"bucket":b,"key":k,**v} for (b,k),v in sorted(files.items())]}
    write_json(root/"verify-archive.json",report)
    if not report["passed"]:
        raise ValueError("unclassified manifests or unreferenced files require review")
    return report

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",required=True)
    a=p.parse_args()
    (Path(a.root)/"verify-archive.json").unlink(missing_ok=True)
    report=inspect(a.root)
    print("Parquet and supported manifest validation passed:",report["parquet_files"])

if __name__=="__main__":
    main_guard(main)
