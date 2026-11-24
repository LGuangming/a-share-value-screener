"""Private historical ST evidence from BaoStock and Tushare namechange.

The immutable phase-1 OHLCV keys define the universe. Never infer ST from
today's name, a missing observation, or price limits. Conflicts are NULL.
Raw per-symbol evidence is checkpointed in verified batches.
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter
from datetime import date, datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import tempfile
import time
import unicodedata

from .neon_archive import StorageConfig, preflight, s3_client, verify_archive, verify_object
from .phase2_market import get_optional_json, put_manifest
from .tushare_client import TushareClient

PREFIX = "a-share-backtest/v1/phase2/historical_st/v1"
FIELDS = "ts_code,name,start_date,end_date,ann_date,change_reason"
CODE = re.compile(r"^\d{6}\.(SH|SZ|BJ)$")
BATCH = 20
COLUMNS = ("ts_code", "trade_date", "is_st", "baostock_is_st",
           "namechange_is_st", "resolution", "namechange_name",
           "namechange_ann_date", "namechange_conflict")


def iso(value):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        return None


def name_status(name):
    name = unicodedata.normalize("NFKC", str(name or "")).strip().upper()
    if not name:
        return None
    # Explicit risk-warning abbreviations, including historical S*ST/SST.
    if re.match(r"^(?:S\*ST|SST|\*ST|ST)", name):
        return 1
    return 0


def intervals(records):
    valid = [(iso(r.get("start_date")), iso(r.get("end_date")), r)
             for r in records]
    valid = [(a,b,r) for a,b,r in valid if a and (not r.get("end_date") or b)
             and (not b or b >= a)]
    starts = sorted({a for a,_,_ in valid})
    result = []
    for a,b,r in valid:
        if not b:
            following = next((d for d in starts if d > a), None)
            if following:
                from datetime import timedelta
                b = (date.fromisoformat(following) - timedelta(days=1)).isoformat()
        result.append((a,b,r))
    return result


def name_evidence(periods, day):
    candidates = [r for a,b,r in periods if a <= day and (not b or day <= b)]
    states = {name_status(r.get("name")) for r in candidates}
    if not candidates or None in states:
        return None, None, None, False
    if len(states) != 1:
        return None, None, None, True
    names = {r.get("name") for r in candidates}
    announcements = {iso(r.get("ann_date")) for r in candidates}
    return (next(iter(states)), next(iter(names)) if len(names)==1 else None,
            next(iter(announcements)) if len(announcements)==1 else None, False)


def resolve(bao, name, internal_conflict=False):
    if internal_conflict or (bao is not None and name is not None and bao != name):
        return None, "conflict"
    if bao is not None and name is not None:
        return bao, "both_agree"
    if bao is not None:
        return bao, "baostock_only"
    if name is not None:
        return name, "namechange_only"
    return None, "unknown"


def fetch_names(client, code):
    # No date filter: provider date parameters have been reported to filter
    # announcement rather than effective dates. Retain full per-code history.
    rows = client.query("namechange", {"ts_code": code}, FIELDS)
    if len(rows) >= 1000 or any(r.get("ts_code") != code for r in rows):
        raise RuntimeError("namechange truncated or returned unexpected code")
    return rows


def fetch_bao(bs, code, first, last):
    if not CODE.fullmatch(code) or code.endswith(".BJ"):
        return {"status":"unsupported_code", "rows":[]}
    number, market = code.split(".")
    symbol = market.lower() + "." + number
    for attempt in range(3):
        rs = bs.query_history_k_data_plus(symbol, "date,code,isST,tradestatus",
                                           start_date=first, end_date=last,
                                           frequency="d", adjustflag="3")
        if rs.error_code == "0":
            rows = []
            while rs.next():
                d,c,st,trade = rs.get_row_data()
                if c != symbol or not iso(d) or not first <= d <= last:
                    raise RuntimeError("BaoStock returned unexpected key")
                rows.append([d,st,trade])
            if rs.error_code == "0":
                if len({r[0] for r in rows}) != len(rows):
                    raise RuntimeError("Duplicate BaoStock daily keys")
                return {"status":"ok" if rows else "empty", "rows":rows}
        if attempt < 2:
            time.sleep(3 * (attempt+1))
            bs.logout()
            if bs.login().error_code != "0":
                raise RuntimeError("BaoStock reconnect failed")
    raise RuntimeError("BaoStock daily query failed after retries")


def save_bytes(s3,bucket,key,raw):
    sha = hashlib.sha256(raw).hexdigest()
    s3.put_object(Bucket=bucket,Key=key,Body=raw,Metadata={"sha256":sha},
                  ContentType="application/octet-stream")
    verify_object(s3,bucket,key,len(raw),sha)
    return {"object_key":key,"bytes":len(raw),"sha256":sha}


def load_bytes(s3,bucket,meta,key,dest):
    if meta.get("object_key") != key:
        raise RuntimeError("Unexpected archived key")
    verify_object(s3,bucket,key,meta["bytes"],meta["sha256"],dest)
    return dest.read_bytes()


def collect_batch(s3,bucket,client,bs,codes,first,last,root,namespace,index):
    key = f"{namespace}/raw/batch={index:05d}.json.gz"
    manifest_key = key + ".manifest.json"
    existing = get_optional_json(s3,bucket,manifest_key)
    path = root / f"batch-{index:05d}.json.gz"
    if existing is not None:
        if existing.get("codes") != codes or existing.get("first") != first or existing.get("last") != last:
            raise RuntimeError("Raw batch identity mismatch")
        load_bytes(s3,bucket,existing,key,path)
    else:
        records = []
        for code in codes:
            names = fetch_names(client,code) if CODE.fullmatch(code) else []
            bao = fetch_bao(bs,code,first,last)
            records.append({"ts_code":code,"namechange":names,"baostock":bao})
        raw = gzip.compress(json.dumps(records,ensure_ascii=False,allow_nan=False,
                                       separators=(",",":")).encode(),mtime=0)
        meta = save_bytes(s3,bucket,key,raw)
        put_manifest(s3,bucket,manifest_key,{**meta,"codes":codes,"first":first,"last":last})
        path.write_bytes(raw)
    print(f"ST_RAW_BATCH index={index} symbols={len(codes)}",flush=True)
    return path


def publish_year(con,s3,bucket,root,namespace,year,paths,baseline):
    year_prefix = f"{namespace}/year={year}"
    annual_key = f"{year_prefix}/manifest.json"
    existing = get_optional_json(s3,bucket,annual_key)
    if existing:
        months = existing.get("months",[])
        if (existing.get("year") != year or len(months)!=12 or
                existing.get("rows") != baseline["rows"] or
                sum(m["rows"] for m in months)!=baseline["rows"]):
            raise RuntimeError("Invalid ST year manifest")
        for i,m in enumerate(months,1):
            key=f"{year_prefix}/month={i:02d}.parquet"
            if m["object_key"] != key or m["month"] != i:
                raise RuntimeError("Invalid ST partition")
            verify_object(s3,bucket,key,m["bytes"],m["sha256"])
        print(f"ST_YEAR year={year} status=reused_verified",flush=True)
        return existing
    files = [str(root / "baseline" / p["path"]) for p in baseline["partition_files"]]
    by_code = {}
    for code,day in con.execute("SELECT ts_code,trade_date FROM read_parquet(?) ORDER BY ts_code,trade_date",
                                [files]).fetchall():
        by_code.setdefault(code,[]).append(day.isoformat())
    csvpath = root / f"st-{year}.csv"
    counts = Counter()
    covered = set()
    with csvpath.open("w",encoding="utf-8",newline="") as fp:
        writer = csv.writer(fp)
        writer.writerow(COLUMNS)
        for path in paths:
            for record in json.loads(gzip.decompress(path.read_bytes())):
                code = record["ts_code"]
                if code in covered:
                    raise RuntimeError("Duplicate raw symbol")
                covered.add(code)
                bao = {d:int(s) for d,s,_ in record["baostock"]["rows"] if s in ("0","1")}
                periods = intervals(record["namechange"])
                for day in by_code.get(code,[]):
                    a = bao.get(day)
                    b,name,ann,conflict = name_evidence(periods,day)
                    value,reason = resolve(a,b,conflict)
                    writer.writerow((code,day,value,a,b,reason,name,ann,int(conflict)))
                    counts[reason]+=1
    if not set(by_code).issubset(covered) or sum(counts.values()) != baseline["rows"]:
        raise RuntimeError("ST join does not reconcile to OHLCV")
    con.execute("CREATE OR REPLACE TEMP TABLE st AS SELECT ts_code,CAST(trade_date AS DATE) trade_date,"
                "CAST(is_st AS TINYINT) is_st,CAST(baostock_is_st AS TINYINT) baostock_is_st,"
                "CAST(namechange_is_st AS TINYINT) namechange_is_st,resolution,namechange_name,"
                "CAST(namechange_ann_date AS DATE) namechange_ann_date,"
                "CAST(namechange_conflict AS BOOLEAN) namechange_conflict "
                "FROM read_csv(?,header=true,all_varchar=true)",[str(csvpath)])
    total,unique = con.execute("SELECT count(*),count(DISTINCT (ts_code,trade_date)) FROM st").fetchone()
    if total != unique or total != baseline["rows"]:
        raise RuntimeError("ST output keys not unique or incomplete")
    months=[]
    for month in range(1,13):
        dest = root / f"st-{year}-{month:02d}.parquet"
        con.execute(f"COPY (SELECT * FROM st WHERE month(trade_date)={month} ORDER BY trade_date,ts_code) "
                    "TO '"+str(dest).replace("'","''")+"' (FORMAT PARQUET, COMPRESSION ZSTD)")
        stats=dict(con.execute("SELECT resolution,count(*) FROM st WHERE month(trade_date)=? GROUP BY resolution",
                               [month]).fetchall())
        expected=next(p["rows"] for p in baseline["partition_files"] if p["path"].endswith(f"month={month:02d}.parquet"))
        if sum(stats.values())!=expected:
            raise RuntimeError("ST month count mismatch")
        key=f"{year_prefix}/month={month:02d}.parquet"
        meta=save_bytes(s3,bucket,key,dest.read_bytes())
        entry={**meta,"month":month,"rows":expected,"resolution_counts":stats}
        put_manifest(s3,bucket,key+".manifest.json",entry)
        months.append(entry)
    doc={"year":year,"rows":total,"resolution_counts":dict(counts),"months":months,
         "status":"archived_with_explicit_unknowns","research_only":True,
         "captured_at_utc":datetime.now(timezone.utc).isoformat()}
    put_manifest(s3,bucket,annual_key,doc)
    print(f"ST_YEAR year={year} rows={total} resolution_counts={dict(counts)}",flush=True)
    return doc


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--start-year",type=int,default=2025)
    parser.add_argument("--end-year",type=int,default=2020)
    parser.add_argument("--probe-only",action="store_true")
    args=parser.parse_args()
    if not 2020<=args.end_year<=args.start_year<date.today().year:
        parser.error("Use descending completed years from 2020")
    import baostock as bs
    import duckdb
    cfg=StorageConfig.from_environment()
    s3=s3_client(cfg)
    preflight(s3,cfg.bucket)
    client=TushareClient.from_environment()
    if not fetch_names(client,"000001.SZ"):
        raise RuntimeError("namechange preflight returned no history")
    if bs.login().error_code != "0":
        raise RuntimeError("BaoStock login failed")
    try:
        probe=fetch_bao(bs,"000001.SZ","2025-01-01","2025-01-10")
        if not any(r[1] in ("0","1") for r in probe["rows"]):
            raise RuntimeError("BaoStock isST preflight returned no usable values")
        print("ST_PROVIDER_PREFLIGHT both_sources_responded",flush=True)
        if args.probe_only:
            return
        first=f"{args.end_year}-01-01"
        last=f"{args.start_year}-12-31"
        with tempfile.TemporaryDirectory(prefix="historical-st-") as temp:
            root=Path(temp)
            baselines={}
            years=range(args.start_year,args.end_year-1,-1)
            for year in years:
                dest=root/"baseline"
                baselines[year]=verify_archive(s3,cfg.bucket,year,dest)
            con=duckdb.connect()
            try:
                files=[str(root/"baseline"/p["path"]) for m in baselines.values() for p in m["partition_files"]]
                codes=[r[0] for r in con.execute("SELECT DISTINCT ts_code FROM read_parquet(?) ORDER BY ts_code",[files]).fetchall()]
                identity=hashlib.sha256(json.dumps(codes,separators=(",",":")).encode()).hexdigest()
                namespace=f"{PREFIX}/range={args.end_year}-{args.start_year}/universe={identity}"
                paths=[collect_batch(s3,cfg.bucket,client,bs,codes[i:i+BATCH],first,last,root,namespace,i//BATCH)
                       for i in range(0,len(codes),BATCH)]
                docs=[publish_year(con,s3,cfg.bucket,root,namespace,y,paths,baselines[y]) for y in years]
                put_manifest(s3,cfg.bucket,namespace+"/manifest.json",{
                    "years":list(years),"universe_sha256":identity,"symbols":len(codes),
                    "rows":sum(d["rows"] for d in docs),"year_manifests":docs,
                    "status":"archived_with_explicit_unknowns"})
                print("ST_ARCHIVE_STATUS all_requested_years_verified_with_explicit_unknowns",flush=True)
            finally:
                con.close()
    finally:
        bs.logout()


if __name__=="__main__":
    main()
