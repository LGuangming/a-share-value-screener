"""Incremental raw archive for completed sessions from 2026 onward.

Daily immutable Parquet partitions plus verified per-day manifests. No mutation
of completed-year archives, no change to website/NCAV publication. Each
successful market-refresh event is bound to its actual recorded trading date.
"""
from __future__ import annotations
import argparse
import csv
from datetime import date, datetime, timedelta, timezone
import gzip
import hashlib
import json
from math import ceil
import os
from pathlib import Path
import tempfile

from .archive_paths import RAW_PREFIX
from .history_parquet import FIELDS as PRICE_FIELDS, valid_daily
from .market_session import CN_TZ, MARKET_CLOSE
from .neon_archive import StorageConfig, s3_client, preflight, verify_object
from .phase2_market import DATASETS, TEXT_FIELDS, API_CAP, validate_row, valuation_quality, get_optional_json, put_manifest
from .raw_consolidation import publish_file
from .tushare_client import TushareClient

MARKET = ("daily_prices", "adj_factor", "daily_basic", "stk_limit", "suspend_d")
FIELDS = {"daily_prices": tuple(PRICE_FIELDS), **DATASETS}
ENDPOINTS = {"daily_prices": "daily", **{k:k for k in DATASETS}}


def day_stem(dataset, day):
    return f"{RAW_PREFIX}/{dataset}/year={day.year}/month={day.month:02d}/day={day:%Y%m%d}"


def daily_manifest(s3, bucket, dataset, day, path):
    stem = day_stem(dataset, day)
    doc = get_optional_json(s3,bucket,stem+".manifest.json")
    if doc is None:
        return None
    if (doc.get("status") != "verified_daily_partition" or doc.get("dataset") != dataset
            or doc.get("trade_date") != day.isoformat() or doc.get("fields") != list(FIELDS[dataset])
            or doc.get("object_key") != stem+".parquet" or type(doc.get("rows")) is not int):
        raise RuntimeError("Invalid existing daily manifest")
    verify_object(s3,bucket,doc["object_key"],doc["bytes"],doc["sha256"],path)
    return doc


def collect_rows(dataset, records, day):
    ds = day.strftime("%Y%m%d")
    cap = 6000 if dataset == "daily_prices" else API_CAP[dataset]
    if len(records) >= cap:
        raise RuntimeError(f"Possible API truncation: {dataset} {ds}")
    output, seen = [], set()
    for row in records:
        values = valid_daily(row,ds) if dataset == "daily_prices" else validate_row(dataset,row,ds)
        if values is None:
            raise RuntimeError(f"Invalid raw OHLCV row on {ds}")
        key = (values[0], values[1], values[2]) if dataset == "suspend_d" else (values[0], values[1])
        if key in seen:
            raise RuntimeError(f"Duplicate raw key in {dataset} {ds}")
        seen.add(key)
        output.append(values)
    return output


def code_coverage(dataset, codes, baseline, minimum):
    if dataset == "daily_prices":
        if len(codes) < minimum:
            raise RuntimeError(f"Incomplete daily_prices: {len(codes)} < {minimum}")
    elif dataset != "suspend_d" and len(codes & baseline) < ceil(len(baseline)*0.95):
        raise RuntimeError(f"Incomplete overlap: {dataset} {len(codes & baseline)}/{len(baseline)}")


def write_parquet(dataset, values, path):
    import duckdb
    fields = FIELDS[dataset]
    csv_path = path.with_suffix(".csv")
    def dtype(col):
        return "DATE" if col=="trade_date" else "VARCHAR" if col in TEXT_FIELDS else "DOUBLE"
    with csv_path.open("w",encoding="utf-8",newline="") as fp:
        w=csv.writer(fp); w.writerow(fields); w.writerows(values)
    selects=[]
    for col in fields:
        selects.append(f"CAST(strptime(trade_date,'%Y%m%d') AS DATE) AS trade_date" if col=="trade_date" else f"CAST({col} AS {dtype(col)}) AS {col}")
    src=str(csv_path).replace("'","''")
    dst=str(path).replace("'","''")
    query=(f"SELECT {', '.join(selects)} FROM read_csv_auto('{src}',header=true,all_varchar=true)"
           if values else "SELECT "+", ".join(f"CAST(NULL AS {dtype(c)}) AS {c}" for c in fields)+" WHERE FALSE")
    with duckdb.connect() as db:
        db.execute(f"COPY ({query} ORDER BY ts_code,trade_date) TO '{dst}' (FORMAT PARQUET, COMPRESSION ZSTD)")
        rows=db.execute("SELECT COUNT(*) FROM read_parquet(?)",[str(path)]).fetchone()[0]
    if rows!=len(values):
        raise RuntimeError("Daily Parquet row count mismatch")


def read_codes(path, expected_rows):
    import duckdb
    with duckdb.connect() as db:
        count=db.execute("SELECT COUNT(*) FROM read_parquet(?)",[str(path)]).fetchone()[0]
        codes={x[0] for x in db.execute("SELECT DISTINCT ts_code FROM read_parquet(?)",[str(path)]).fetchall()}
    if count!=expected_rows:
        raise RuntimeError("Restored daily Parquet row count mismatch")
    return codes


def archive_session(s3,bucket,client,day,minimum):
    reports=[]
    baseline=set()
    with tempfile.TemporaryDirectory(prefix="raw-daily-") as tmp:
        for dataset in MARKET:
            path=Path(tmp)/f"{dataset}.parquet"
            stem=day_stem(dataset,day)
            doc=daily_manifest(s3,bucket,dataset,day,path)
            if doc:
                codes=read_codes(path,doc["rows"])
                code_coverage(dataset,codes,baseline,minimum)
                mode="reused_verified"
            else:
                records=client.query(ENDPOINTS[dataset],{"trade_date":day.strftime("%Y%m%d")},",".join(FIELDS[dataset]))
                values=collect_rows(dataset,records,day)
                codes={v[0] for v in values}
                code_coverage(dataset,codes,baseline,minimum)
                write_parquet(dataset,values,path)
                sha=hashlib.sha256(path.read_bytes()).hexdigest()
                doc={"format_version":1,"status":"verified_daily_partition","dataset":dataset,
                     "trade_date":day.isoformat(),"source":"Tushare official","fields":list(FIELDS[dataset]),
                     "rows":len(values),"object_key":stem+".parquet","bytes":path.stat().st_size,"sha256":sha}
                if dataset=="daily_basic":
                    mv=[r["ts_code"] for r in records if r.get("total_mv") in (None,"")]
                    shares=[r["ts_code"] for r in records if r.get("total_share") in (None,"")]
                    doc["valuation_quality"]=valuation_quality(day.strftime("%Y%m%d"),len(records),mv,shares)
                if dataset=="suspend_d":
                    doc["empty_response_is_provider_evidence_only"]=not bool(values)
                publish_file(s3,bucket,doc["object_key"],path,sha,doc["bytes"],"application/octet-stream")
                put_manifest(s3,bucket,stem+".manifest.json",doc)
                mode="copied_verified"
            if dataset=="daily_prices":
                baseline=codes
            reports.append(doc)
            print(f"RAW_INCREMENT_PART date={day} dataset={dataset} rows={doc['rows']} status={mode}",flush=True)
        # Current names are collected only as contemporaneous snapshot evidence.
        # Never backfill old ST by applying today's stock names.
        if day==datetime.now(CN_TZ).date():
            key=day_stem("stock_basic",day)+".json"
            if get_optional_json(s3,bucket,key) is None:
                stocks=client.stocks()
                if len({r["ts_code"] for r in stocks})<minimum:
                    raise RuntimeError("Incomplete current stock_basic snapshot")
                put_manifest(s3,bucket,key,{"format_version":1,"dataset":"stock_basic","status":"observed_current_snapshot_not_historical_st",
                            "trade_date":day.isoformat(),"captured_at_utc":datetime.now(timezone.utc).isoformat(),
                            "source":"Tushare official stock_basic L","rows":len(stocks),"records":stocks})
    day_key=day_stem("trade_calendar",day)+".json"
    if get_optional_json(s3,bucket,day_key) is None:
        put_manifest(s3,bucket,day_key,{"trade_date":day.isoformat(),"is_open":True,"exchange":"SSE","source":"Tushare trade_cal"})
    day_doc={"status":"verified_market_day","trade_date":day.isoformat(),"datasets":reports}
    key=f"{RAW_PREFIX}/_incremental/market/year={day.year}/day={day:%Y%m%d}.json"
    existing=get_optional_json(s3,bucket,key)
    if existing and existing!=day_doc:
        raise RuntimeError("Published day manifest changed")
    if not existing:
        put_manifest(s3,bucket,key,day_doc)
    print(f"RAW_INCREMENT_DAY date={day} datasets={len(reports)} status=verified",flush=True)


def market_run(trigger):
    import psycopg
    with psycopg.connect(os.environ["DATABASE_URL"],connect_timeout=15) as db:
        query="""SELECT trade_date, (metrics->>'quoted_stocks')::int FROM job_runs
                 WHERE job_type='market_refresh' AND status='success'
                   AND metrics->>'quoted_stocks' IS NOT NULL AND trade_date >= '2026-01-01'"""
        params=()
        if trigger:
            query+=" AND metrics->>'github_run_id'=%s";params=(str(trigger),)
        return db.execute(query+" ORDER BY trade_date DESC, finished_at DESC LIMIT 1",params).fetchone()


def sessions(client,start,end):
    rows=client.query("trade_cal",{"exchange":"SSE","start_date":start.strftime("%Y%m%d"),"end_date":end.strftime("%Y%m%d")},"cal_date,is_open")
    days={}
    for row in rows:
        d=datetime.strptime(str(row["cal_date"]),"%Y%m%d").date()
        if not start<=d<=end or d in days or str(row["is_open"]) not in ("0","1"):
            raise RuntimeError("Invalid calendar response")
        days[d]=str(row["is_open"])=="1"
    if len(days)!=(end-start).days+1:
        raise RuntimeError("Calendar range incomplete")
    return sorted(d for d,opened in days.items() if opened)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--trigger-run-id")
    parser.add_argument("--backfill-2026", action="store_true", help="Backfill completed 2026 sessions, newest month first")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    args=parser.parse_args()
    now=datetime.now(CN_TZ)
    bound=now.date()-timedelta(days=int((now.hour,now.minute)<MARKET_CLOSE))
    if args.backfill_2026 and (args.start_date or args.end_date or args.trigger_run_id):
        parser.error("Backfill cannot be combined with date range or trigger")
    if args.backfill_2026:
        latest=market_run(None)
        if latest is None or latest[0].year!=2026 or latest[0]>bound:
            raise RuntimeError("No eligible successful 2026 market refresh")
        start,end=date(2026,1,1),latest[0]
        minimum=5000
    elif args.start_date or args.end_date:
        if not args.start_date or not args.end_date or args.trigger_run_id:
            parser.error("Supply both range dates, or a successful trigger run")
        start,end=date.fromisoformat(args.start_date),date.fromisoformat(args.end_date)
        if not date(2026,1,1)<=start<=end<=bound or (end-start).days>30:
            parser.error("Range must be completed sessions from 2026 onward, at most 31 calendar days")
        if end==now.date():
            latest=market_run(None)
            if latest is None or latest[0]!=end:
                raise RuntimeError("Today's market refresh not yet successful")
        minimum=5000
    else:
        result=market_run(args.trigger_run_id)
        if not result:
            print("RAW_INCREMENT_SKIP no_successful_market_refresh",flush=True)
            return
        start=end=result[0]
        if end>bound:
            raise RuntimeError("Market refresh date not completed")
        minimum=max(4000,ceil(result[1]*0.95))
    client=TushareClient.from_environment()
    if client.source!="official":
        raise RuntimeError("Raw archive uses official API only")
    cfg=StorageConfig.from_environment()
    s3=s3_client(cfg);preflight(s3,cfg.bucket)
    from calendar import monthrange
    from .raw_month_reader import publish_month
    ranges=[]
    cursor=start.replace(day=1)
    while cursor<=end:
        month_end=cursor.replace(day=monthrange(cursor.year,cursor.month)[1])
        ranges.append((max(start,cursor),min(end,month_end)))
        cursor=month_end+timedelta(days=1)
    if args.backfill_2026:
        ranges.reverse()
        # Publish current existing daily evidence immediately for the website.
        publish_month(s3,cfg.bucket,client,end)
    total=0
    for first,last in ranges:
        dates=sessions(client,first,last)
        if not dates and start==end:
            raise RuntimeError("Successful market refresh must reference an open session")
        for day in dates:
            archive_session(s3,cfg.bucket,client,day,minimum)
        publish_month(s3,cfg.bucket,client,last)
        total+=len(dates)
        print(f"RAW_INCREMENT_MONTH_DONE through={last} sessions={len(dates)}",flush=True)
    print(f"RAW_INCREMENT_STATUS start={start} end={end} sessions={total} status=complete",flush=True)


if __name__=="__main__":
    main()
