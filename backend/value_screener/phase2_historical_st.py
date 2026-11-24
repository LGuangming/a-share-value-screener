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

PREFIX = "a-share-backtest/v1/phase2/historical_st/v2"
FIELDS = "ts_code,name,start_date,end_date,ann_date,change_reason"
CODE = re.compile(r"^\d{6}\.(SH|SZ|BJ)$")
BATCH = 20
COLUMNS = ("ts_code", "trade_date", "is_st", "baostock_is_st",
           "namechange_is_st", "resolution", "namechange_name",
           "namechange_ann_date", "namechange_conflict", "st_type")


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


def classify_st(value, resolution, historical_name):
    """Binary ST evidence is distinct from detailed risk-warning classification."""
    if resolution == "conflict":
        return "CONFLICT"
    if value is None:
        return "UNKNOWN"
    name = unicodedata.normalize("NFKC", str(historical_name or "")).strip().upper()
    if value == 1:
        if re.match(r"^(?:S\*ST|\*ST)", name):
            return "STAR_ST"
        if re.match(r"^(?:SST|ST)", name):
            return "ST"
        return "ST_UNCLASSIFIED"
    if name and (name.startswith("退市") or name.endswith("退")):
        return "DELISTING"
    # Without a historical name, non-ST does not prove non-delisting.
    return "NORMAL" if name else "NON_ST_UNCLASSIFIED"


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
            login_bao(bs)
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
            names = fetch_names(client,code) if client is not None and CODE.fullmatch(code) else []
            bao = fetch_bao(bs,code,first,last) if bs is not None else {"status":"not_collected","rows":[]}
            records.append({"ts_code":code,"namechange":names,"baostock":bao})
        raw = gzip.compress(json.dumps(records,ensure_ascii=False,allow_nan=False,
                                       separators=(",",":")).encode(),mtime=0)
        meta = save_bytes(s3,bucket,key,raw)
        put_manifest(s3,bucket,manifest_key,{**meta,"codes":codes,"first":first,"last":last})
        path.write_bytes(raw)
    print(f"ST_RAW_BATCH index={index} symbols={len(codes)} status={'reused_verified' if existing is not None else 'uploaded_verified'}",flush=True)
    return path


def publish_year(con,s3,bucket,root,namespace,year,paths,baseline):
    year_prefix = f"{namespace}/year={year}"
    annual_key = f"{year_prefix}/manifest.json"
    existing = get_optional_json(s3,bucket,annual_key)
    if existing:
        if namespace.endswith("/merged") and existing.get("classification_version") != 1:
            raise RuntimeError("Existing merged archive requires explicit version migration")
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
    type_counts = Counter()
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
                    st_type = classify_st(value, reason, name)
                    writer.writerow((code,day,value,a,b,reason,name,ann,int(conflict),st_type))
                    counts[reason]+=1
                    type_counts[st_type]+=1
    if not set(by_code).issubset(covered) or sum(counts.values()) != baseline["rows"]:
        raise RuntimeError("ST join does not reconcile to OHLCV")
    con.execute("CREATE OR REPLACE TEMP TABLE st AS SELECT ts_code,CAST(trade_date AS DATE) trade_date,"
                "CAST(is_st AS TINYINT) is_st,CAST(baostock_is_st AS TINYINT) baostock_is_st,"
                "CAST(namechange_is_st AS TINYINT) namechange_is_st,resolution,namechange_name,"
                "CAST(namechange_ann_date AS DATE) namechange_ann_date,"
                "CAST(namechange_conflict AS BOOLEAN) namechange_conflict,CAST(st_type AS VARCHAR) st_type "
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
        types=dict(con.execute("SELECT st_type,count(*) FROM st WHERE month(trade_date)=? GROUP BY st_type",
                               [month]).fetchall())
        if sum(types.values()) != expected:
            raise RuntimeError("ST classification totals mismatch")
        entry={**meta,"month":month,"rows":expected,"resolution_counts":stats,
               "st_type_counts":types}
        put_manifest(s3,bucket,key+".manifest.json",entry)
        months.append(entry)
    doc={"year":year,"rows":total,"resolution_counts":dict(counts),"months":months,
         "st_type_counts":dict(type_counts),"classification_version":1,
         "status":"archived_with_explicit_unknowns","research_only":True,
         "captured_at_utc":datetime.now(timezone.utc).isoformat()}
    put_manifest(s3,bucket,annual_key,doc)
    print(f"ST_YEAR year={year} rows={total} resolution_counts={dict(counts)} st_type_counts={dict(type_counts)}",flush=True)
    return doc


def login_bao(bs):
    for attempt in range(3):
        print(f"BAOSTOCK_LOGIN attempt={attempt+1}",flush=True)
        result=bs.login()
        if result.error_code=="0":
            print("BAOSTOCK_LOGIN status=connected",flush=True)
            return
        print(f"BAOSTOCK_LOGIN status=failed attempt={attempt+1}",flush=True)
        if attempt<2:
            time.sleep(10*(attempt+1))
    raise RuntimeError("BaoStock login failed after three attempts")


def restore_source_batch(s3,bucket,root,namespace,source,index,codes,first,last):
    key=f"{namespace}/{source}/raw/batch={index:05d}.json.gz"
    meta=get_optional_json(s3,bucket,key+".manifest.json")
    if meta is None:
        raise RuntimeError(f"Missing verified {source} batch {index}")
    if meta.get("codes")!=codes or meta.get("first")!=first or meta.get("last")!=last:
        raise RuntimeError("Source batch identity mismatch")
    path=root/f"{source}-{index:05d}.json.gz"
    records=json.loads(gzip.decompress(load_bytes(s3,bucket,meta,key,path)))
    if [r["ts_code"] for r in records]!=codes:
        raise RuntimeError("Source batch code mismatch")
    return records


def audit_baostock(con,s3,bucket,root,namespace,baselines,codes):
    """Audit the existing source against the exact frozen OHLCV universe."""
    snapshot = get_optional_json(s3,bucket,"a-share-backtest/v1/phase2/universe/stock_basic_snapshot.json")
    listing = {r["ts_code"]:r.get("list_status") for r in (snapshot or {}).get("records",[])}
    groups = Counter()
    symbol_totals = Counter()
    symbol_unknown = Counter()
    years = {}
    for year,baseline in baselines.items():
        prefix=f"{namespace}/baostock/year={year}"
        manifest=get_optional_json(s3,bucket,prefix+"/manifest.json")
        if not manifest or manifest.get("rows")!=baseline["rows"] or len(manifest.get("months",[]))!=12:
            raise RuntimeError(f"Missing or invalid BaoStock year {year}")
        files=[]
        for i,part in enumerate(manifest["months"],1):
            key=f"{prefix}/month={i:02d}.parquet"
            expected=next(p["rows"] for p in baseline["partition_files"] if p["path"].endswith(f"month={i:02d}.parquet"))
            if part.get("month")!=i or part.get("object_key")!=key or part.get("rows")!=expected:
                raise RuntimeError("BaoStock audit partition identity mismatch")
            dest=root/f"audit-{year}-{i:02d}.parquet"
            verify_object(s3,bucket,key,part["bytes"],part["sha256"],dest)
            files.append(str(dest))
        records=con.execute(
            "SELECT ts_code,count(*),count(*) FILTER (WHERE is_st IS NULL) FROM read_parquet(?) GROUP BY ts_code",
            [files]).fetchall()
        total=sum(r[1] for r in records)
        unknown=sum(r[2] for r in records)
        if total!=baseline["rows"] or unknown!=manifest["resolution_counts"].get("unknown",0):
            raise RuntimeError("BaoStock audit row counts do not reconcile")
        years[str(year)]={"rows":total,"unknown":unknown,"known":total-unknown}
        for code,n,u in records:
            symbol_totals[code]+=n
            symbol_unknown[code]+=u
            key=(year,code.rsplit(".",1)[-1],listing.get(code) or "UNKNOWN")
            groups[(key,"rows")]+=n
            groups[(key,"unknown")]+=u
        print(f"ST_COVERAGE_YEAR year={year} rows={total} unknown={unknown}",flush=True)
    if set(symbol_totals)!=set(codes):
        raise RuntimeError("BaoStock coverage universe does not reconcile")
    strata=[{"year":y,"exchange":e,"snapshot_list_status":s,"rows":groups[((y,e,s),"rows")],
             "unknown":groups[((y,e,s),"unknown")]} for y,e,s in sorted({k for k,_ in groups})]
    affected=[{"ts_code":c,"rows":symbol_totals[c],"unknown":u,
               "snapshot_list_status":listing.get(c) or "UNKNOWN",
               "gap_type":"UNSUPPORTED_EXCHANGE" if c.endswith(".BJ") else
                          "NO_VALID_DAILY_ST" if u==symbol_totals[c] else "PARTIAL_DAILY_GAP"}
              for c,u in sorted(symbol_unknown.items()) if u]
    doc={"source":"baostock","years":years,"rows":sum(symbol_totals.values()),
         "unknown":sum(symbol_unknown.values()),"symbols":len(codes),
         "affected_symbols":len(affected),"by_year_exchange_listing_status":strata,
         "affected_symbol_details":affected,
         "listing_status_caveat":"Frozen stock_basic snapshot status is not historical listing status.",
         "captured_at_utc":datetime.now(timezone.utc).isoformat()}
    put_manifest(s3,bucket,namespace+"/audit/baostock-coverage.json",doc)
    for item in strata:
        print("ST_COVERAGE_STRATUM "+json.dumps(item,sort_keys=True),flush=True)
    print("ST_COVERAGE_SUMMARY "+json.dumps({k:doc[k] for k in
          ("rows","unknown","symbols","affected_symbols")},sort_keys=True),flush=True)


def run_archive(args,s3,bucket,client,bs):
    import duckdb
    first=f"{args.end_year}-01-01"
    last=f"{args.start_year}-12-31"
    with tempfile.TemporaryDirectory(prefix="historical-st-") as temp:
        root=Path(temp)
        baselines={}
        years=range(args.start_year,args.end_year-1,-1)
        for year in years:
            dest=root/"baseline"
            baselines[year]=verify_archive(s3,bucket,year,dest)
        con=duckdb.connect()
        try:
            files=[str(root/"baseline"/p["path"]) for m in baselines.values() for p in m["partition_files"]]
            codes=[r[0] for r in con.execute("SELECT DISTINCT ts_code FROM read_parquet(?) ORDER BY ts_code",[files]).fetchall()]
            identity=hashlib.sha256(json.dumps(codes,separators=(",",":")).encode()).hexdigest()
            namespace=f"{PREFIX}/range={args.end_year}-{args.start_year}/universe={identity}"
            source=args.source
            if source=="audit":
                audit_baostock(con,s3,bucket,root,namespace,baselines,codes)
                return
            print(f"ST_UNIVERSE source={source} symbols={len(codes)}",flush=True)
            out=f"{namespace}/{source}"
            paths=[]
            for i in range(0,len(codes),BATCH):
                subset=codes[i:i+BATCH]
                index=i//BATCH
                if source=="merged":
                    bao=restore_source_batch(s3,bucket,root,namespace,"baostock",index,subset,first,last)
                    names=restore_source_batch(s3,bucket,root,namespace,"namechange",index,subset,first,last)
                    records=[{"ts_code":a["ts_code"],"baostock":a["baostock"],"namechange":b["namechange"]}
                             for a,b in zip(bao,names)]
                    path=root/f"merged-{index:05d}.json.gz"
                    path.write_bytes(gzip.compress(json.dumps(records,ensure_ascii=False,allow_nan=False).encode()))
                else:
                    path=collect_batch(s3,bucket,client,bs,subset,first,last,root,out,index)
                paths.append(path)
            docs=[publish_year(con,s3,bucket,root,out,y,paths,baselines[y]) for y in years]
            counts=Counter()
            types=Counter()
            for doc in docs:
                counts.update(doc["resolution_counts"])
                types.update(doc.get("st_type_counts",{}))
            put_manifest(s3,bucket,out+"/manifest.json",{
                "source":source,"years":list(years),"universe_sha256":identity,"symbols":len(codes),
                "rows":sum(d["rows"] for d in docs),"year_manifests":docs,
                "resolution_counts":dict(counts),"st_type_counts":dict(types),
                "status":"archived_with_explicit_unknowns"})
            print(f"ST_ARCHIVE_STATUS source={source} all_requested_years_verified_with_explicit_unknowns resolution_counts={dict(counts)} st_type_counts={dict(types)}",flush=True)
        finally:
            con.close()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--start-year",type=int,default=2025)
    parser.add_argument("--end-year",type=int,default=2020)
    parser.add_argument("--source",choices=("baostock","namechange","merged","audit"),required=True)
    parser.add_argument("--probe-only",action="store_true")
    args=parser.parse_args()
    if not 2020<=args.end_year<=args.start_year<date.today().year:
        parser.error("Use descending completed years from 2020")
    if args.source in ("merged","audit") and args.probe_only:
        parser.error("Merge is storage-only; no provider probe")
    cfg=StorageConfig.from_environment()
    s3=s3_client(cfg)
    preflight(s3,cfg.bucket)
    client=None
    bs=None
    if args.source=="namechange":
        client=TushareClient.from_environment()
        if not fetch_names(client,"000001.SZ"):
            raise RuntimeError("namechange preflight returned no history")
        print("ST_PROVIDER_PREFLIGHT source=namechange status=passed",flush=True)
    elif args.source=="baostock":
        import socket
        socket.setdefaulttimeout(30)
        import baostock as bs
        login_bao(bs)
    try:
        if bs is not None:
            probe=fetch_bao(bs,"000001.SZ","2025-01-01","2025-01-10")
            if not any(r[1] in ("0","1") for r in probe["rows"]):
                raise RuntimeError("BaoStock isST preflight returned no usable values")
            print("ST_PROVIDER_PREFLIGHT source=baostock status=passed",flush=True)
        if args.probe_only:
            return
        run_archive(args,s3,cfg.bucket,client,bs)
    finally:
        if bs is not None:
            bs.logout()


if __name__=="__main__":
    main()
