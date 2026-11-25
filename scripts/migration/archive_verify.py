"""Offline archive validation; never contacts source or mutates archived files."""
import argparse
import json
import gzip
import hashlib
from pathlib import Path
from datetime import date, datetime
import duckdb
import pyarrow.parquet as pq
from common import digest, load, main_guard, write_json
from storage import safe_path, verify

RAW="a-share-backtest/01_raw_data/"
FIN_FIELDS={
"balancesheet":"ts_code,ann_date,f_ann_date,end_date,report_type,comp_type,total_cur_assets,total_liab,total_assets,money_cap,accounts_receiv,inventories,prepayment,oth_cur_assets,minority_int",
"income":"ts_code,ann_date,f_ann_date,end_date,report_type,comp_type,revenue,n_income,n_income_attr_p,operate_profit",
"cashflow":"ts_code,ann_date,f_ann_date,end_date,report_type,comp_type,n_cashflow_act",
"fina_indicator":"ts_code,ann_date,end_date,roe,roe_dt,roa"}
MAX_EXPANDED=256*1024*1024

def read_gzip(path,key):
    def no_constant(x):
        raise ValueError("nonfinite JSON")
    count=0
    fields=None
    codes=set()
    missing_pub=0
    expanded=0
    h=hashlib.sha256()
    periods=set()
    with gzip.open(path,"rb") as stream:
        if key.endswith(".jsonl.gz"):
            while True:
                line=stream.readline(8*1024*1024+1)
                if not line: break
                expanded+=len(line)
                if len(line)>8*1024*1024 or expanded>MAX_EXPANDED:
                    raise ValueError("gzip bounds")
                h.update(line)
                row=json.loads(line,parse_constant=no_constant)
                if not isinstance(row,dict):
                    raise ValueError("invalid JSONL row")
                count+=1
                fields=set(row) if fields is None else fields & set(row)
                if row.get("ts_code"): codes.add(row["ts_code"])
                if not row.get("f_ann_date"): missing_pub+=1
                if row.get("end_date"): periods.add(str(row["end_date"]))
        else:
            # Existing historical ST raw batch is a bounded JSON array.
            raw=stream.read(MAX_EXPANDED+1)
            if len(raw)>MAX_EXPANDED: raise ValueError("gzip bounds")
            expanded=len(raw)
            h.update(raw)
            arr=json.loads(raw,parse_constant=no_constant)
            if not isinstance(arr,list) or any(not isinstance(row,dict) for row in arr):
                raise ValueError("unsupported gzip array")
            for row in arr:
                count+=1
                fields=set(row) if fields is None else fields & set(row)
                if row.get("ts_code"): codes.add(row["ts_code"])
    return {"rows":count,"common_fields":sorted(fields or []),"codes":sorted(codes),"missing_f_ann_date":missing_pub,
            "periods":sorted(periods),"expanded_bytes":expanded,"uncompressed_sha256":h.hexdigest()}

def iso(v):
    return v.isoformat() if isinstance(v,(date,datetime)) else str(v) if v is not None else None

def inspect(root,memory_limit="256MB"):
    root=Path(root)
    (root/"verify-archive.json").unlink(missing_ok=True)
    manifest=load(root/"inventory-storage.json")
    verify(manifest,root)
    state=load(root/"checkpoint.json")
    objects={(x["bucket"],x["key"]):x for x in manifest["objects"]}
    files={}
    references=set()
    gzfiles={}
    catalogs=[]
    json_docs={}
    for (b,k),obj in objects.items():
        if k.endswith((".jsonl.gz",".json.gz")):
            gzfiles[(b,k)]=read_gzip(safe_path(root/"files",b,k),k)
        elif k.endswith(".json"):
            json_docs[(b,k)]=load(safe_path(root/"files",b,k))
    unclassified=[]
    with duckdb.connect(":memory:",config={"threads":"1","memory_limit":memory_limit,"enable_external_access":"true","autoinstall_known_extensions":"false","autoload_known_extensions":"false"}) as db:
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
        if ident in gzfiles:
            info=gzfiles[ident]
            if "rows" in entry and entry["rows"]!=info["rows"]:
                raise ValueError("gzip rows mismatch")
            if entry.get("uncompressed_sha256") and entry["uncompressed_sha256"]!=info["uncompressed_sha256"]:
                raise ValueError("uncompressed SHA256 mismatch")
            if "codes" in entry and sorted(entry["codes"])!=info["codes"]:
                raise ValueError("historical ST codes mismatch")
            kind=entry.get("kind")
            if kind in FIN_FIELDS:
                if info["rows"] and not set(FIN_FIELDS[kind].split(","))<=set(info["common_fields"]):
                    raise ValueError("financial schema mismatch")
                if "missing_f_ann_date_rows" in entry and entry["missing_f_ann_date_rows"]!=info["missing_f_ann_date"]:
                    raise ValueError("financial missing dates mismatch")
                # Frozen source universe determines selected symbols, not current stock names.
                snapshot=json_docs.get((b,RAW+"stock_basic/stock_basic_snapshot.json"))
                if snapshot is None:
                    raise ValueError("frozen financial universe missing")
                universe=sorted({r["ts_code"] for r in snapshot["records"]})
                start=entry["start_index"]
                selected=universe[start:start+entry["requested_codes"]]
                if len(selected)!=entry["requested_codes"] or hashlib.sha256(json.dumps(selected,ensure_ascii=False).encode()).hexdigest()!=entry["codes_sha256"]:
                    raise ValueError("financial universe digest mismatch")
                missing=set(selected)-set(info["codes"])
                if not set(info["codes"])<=set(selected) or missing!=set(entry["missing_codes"]) or len(missing)!=entry["missing_codes_count"]:
                    raise ValueError("financial symbol coverage mismatch")
            if "report_period" in entry and info["rows"] and info["periods"]!=[str(entry["report_period"])]:
                raise ValueError("disclosure period mismatch")
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
            if isinstance(node.get("months"),list) and type(node.get("rows")) is int and all(isinstance(m,dict) and type(m.get("rows")) is int for m in node["months"]):
                if sum(m["rows"] for m in node["months"])!=node["rows"]:
                    raise ValueError("annual rows mismatch")
            for key,val in node.items():
                if key!="partition_files":
                    hits+=walk(b,val,manifest_key)
        elif isinstance(node,list):
            for val in node:
                hits+=walk(b,val,manifest_key)
        return hits

    for (b,k),doc in json_docs.items():
        if not isinstance(doc,dict):
            if k.endswith("manifest.json"): raise ValueError("manifest must be object")
            continue
        status=doc.get("status")
        if status=="consolidated_verified" and k.endswith("dataset_catalog.json"):
            groups=doc["datasets"]
            if len({g["dataset"] for g in groups})!=len(groups):
                raise ValueError("duplicate catalog group")
            for group in groups:
                key=group["inventory"]
                receipt=json_docs.get((b,key))
                if not receipt or receipt.get("status")!="copied_and_verified" or receipt.get("dataset")!=group["dataset"]:
                    raise ValueError("catalog receipt missing or wrong")
                references.add((b,key))
                entries=receipt["entries"]
                if len({x["object_key"] for x in entries})!=len(entries):
                    raise ValueError("duplicate receipt object")
                for field in ("objects","bytes","source_bytes"):
                    if receipt[field]!=group[field]:
                        raise ValueError("catalog group totals mismatch")
                if len(entries)!=receipt["objects"] or sum(x["bytes"] for x in entries)!=receipt["bytes"] or sum(x["source_bytes"] for x in entries)!=receipt["source_bytes"]:
                    raise ValueError("receipt totals mismatch")
                for entry in entries:
                    if not entry["object_key"].startswith(group["prefix"]):
                        raise ValueError("receipt prefix mismatch")
                    check_ref(b,entry["object_key"],entry)
            for field in ("objects","bytes","source_bytes"):
                if sum(g[field] for g in groups)!=doc[field]:
                    raise ValueError("catalog totals mismatch")
            catalogs.append(b+"/"+k)
        elif status=="copied_and_verified":
            # Individual receipt validated via catalog and references.
            walk(b,doc,k)
        elif k.endswith("manifest.json") or status=="verified_market_day":
            if not walk(b,doc,k):
                unclassified.append(b+"/"+k)
        elif doc.get("dataset") in ("stock_basic","stock_basic_snapshot") and isinstance(doc.get("records"),list):
            if doc.get("rows")!=len(doc["records"]) or len({r["ts_code"] for r in doc["records"]})!=len(doc["records"]):
                raise ValueError("stock universe count/uniqueness mismatch")
    unreferenced=sorted(b+"/"+k for b,k in set(files)|set(gzfiles) if (b,k) not in references)
    report={"passed":not unclassified and not unreferenced and bool(files or gzfiles),"parquet_files":len(files),
            "parquet_rows":sum(x["rows"] for x in files.values()),"gzip_files":len(gzfiles),
            "gzip_rows":sum(x["rows"] for x in gzfiles.values()),"catalogs":catalogs,"manifest_references":len(references),
            "unclassified_manifests":sorted(unclassified),"unreferenced_parquet":unreferenced,
            "files":[{"bucket":b,"key":k,**v} for (b,k),v in sorted(files.items())]}
    write_json(root/"verify-archive.json",report)
    if not report["passed"]:
        raise ValueError("unclassified manifests or unreferenced files require review")
    return report

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",required=True)
    p.add_argument("--memory-limit",choices=["256MB","512MB"],default="256MB")
    a=p.parse_args()
    (Path(a.root)/"verify-archive.json").unlink(missing_ok=True)
    report=inspect(a.root,a.memory_limit)
    print("Parquet and supported manifest validation passed:",report["parquet_files"])

if __name__=="__main__":
    main_guard(main)
