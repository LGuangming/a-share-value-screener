"""Guarded maintenance of the live quote cache and disposable reader snapshots.

No Tushare calls. Preserve the original daily Parquet sources and all finance,
screen results and job histories. A failed archive or live-reader gate prevents
every destructive operation. Database rows are archived byte-for-byte before
deletion; each date is an independent transaction.
"""
from __future__ import annotations

import argparse
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.request import urlopen, Request, build_opener, HTTPRedirectHandler, ProxyHandler
from urllib.parse import urlsplit
from uuid import uuid4
from .archive_storage import archive_driver

from .archive_paths import RAW_PREFIX
from .neon_archive import StorageConfig, s3_client, verify_object
from .phase2_market import get_optional_json, put_manifest
from .raw_consolidation import publish_file
from .raw_incremental import MARKET, FIELDS, day_stem
from .raw_month_reader import CHART_DATASETS, month_root

FULL_DAYS = 30
COMPACT_SESSIONS = 120
READER_GRACE = timedelta(days=7)
MAX_DB_DATES = 60
MAX_READER_DELETES = 50
SHA = re.compile(r"^[a-f0-9]{64}$")


class NotReady(RuntimeError):
    """A legitimate incomplete archive; no deletion is permitted."""


def retention_cutoffs(latest, observed_sessions):
    ordered = sorted(set(d for d in observed_sessions if d <= latest))
    if len(ordered) < COMPACT_SESSIONS:
        raise NotReady(f"Only {len(ordered)} distinct cached sessions; need {COMPACT_SESSIONS}")
    return latest - timedelta(days=FULL_DAYS-1), ordered[-COMPACT_SESSIONS]


def pointer(s3, bucket, dataset, year, month):
    root = month_root(dataset, year, month)
    doc = get_optional_json(s3,bucket,root+"/reader.manifest.json")
    if doc is None:
        raise NotReady(f"Missing reader manifest {dataset} {year}-{month:02d}")
    dates = doc.get("dates", [])
    expected = doc.get("expected_dates", [])
    sha = doc.get("sha256", "")
    through = date.fromisoformat(doc["through"])
    if (doc.get("status") != "verified_month_reader" or doc.get("format_version") != 1
            or doc.get("dataset") != dataset or doc.get("year") != year
            or doc.get("month") != f"{month:02d}" or not SHA.fullmatch(str(sha))
            or doc.get("object_key") != root+f"/reader/sha256={sha}.parquet"
            or doc.get("fields") != list(FIELDS[dataset])
            or type(doc.get("rows")) is not int or doc["rows"] <= 0
            or type(doc.get("bytes")) is not int or doc["bytes"] <= 0
            or through.year != year or through.month != month
            or not dates or dates != sorted(set(dates)) or expected != sorted(set(expected))
            or any(not d.startswith(f"{year}-{month:02d}-") or date.fromisoformat(d)>through for d in expected)
            or not set(dates).issubset(expected)
            or doc.get("missing_dates") != sorted(set(expected)-set(dates))
            or len(doc.get("source_sha256",[])) != len(dates)
            or any(not SHA.fullmatch(str(s)) for s in doc["source_sha256"])):
        raise RuntimeError("Invalid month reader pointer: "+root)
    return doc


def complete_year_gate(s3,bucket,latest):
    """Require full current-year month coverage through latest successful close."""
    if latest.year < 2026:
        raise NotReady("No current-year incremental archive")
    pointers={}
    for month in range(1,latest.month+1):
        through = latest if month==latest.month else date(latest.year,month,monthrange(latest.year,month)[1])
        for dataset in CHART_DATASETS:
            doc=pointer(s3,bucket,dataset,latest.year,month)
            if doc["missing_dates"] or date.fromisoformat(doc["through"])<through:
                raise NotReady(f"Archive still incomplete {dataset} {latest.year}-{month:02d}")
            pointers[(dataset,latest.year,month)]=doc
        if pointers[("daily_prices",latest.year,month)]["dates"] != pointers[("adj_factor",latest.year,month)]["dates"]:
            raise NotReady("OHLCV/factor session coverage differs")
    if latest.isoformat() not in pointers[("daily_prices",latest.year,latest.month)]["dates"]:
        raise NotReady("Latest market day not included in reader")
    return pointers


def web_gate(latest):
    origin="https://stock.guangming.blog"
    opener=None
    headers={}
    if archive_driver()=="local":
        origin=os.getenv("ARCHIVE_LOCAL_WEB_ORIGIN","")
        parsed=urlsplit(origin)
        if (parsed.scheme!="http" or parsed.hostname not in ("web","127.0.0.1","localhost","::1")
                or parsed.port!=3000 or parsed.username or parsed.password or parsed.path not in ("","/")
                or parsed.query or parsed.fragment):
            raise NotReady("Local web health origin not configured")
        origin=origin.rstrip("/")
        cookie=os.getenv("ARCHIVE_LOCAL_WEB_COOKIE","")
        if cookie:
            if "\r" in cookie or "\n" in cookie:
                raise NotReady("Invalid local web health credential")
            headers["Cookie"]=cookie
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self,*args,**kwargs):
                return None
        opener=build_opener(ProxyHandler({}),NoRedirect())
    def request(route,timeout):
        if opener is None:
            return urlopen(origin+route,timeout=timeout)
        try:
            return opener.open(Request(origin+route,headers=headers),timeout=timeout)
        except Exception as exc:
            raise NotReady("Local web health check failed") from exc
    for route,field in (
        (f"/api/quotes/600000.SH?year={latest.year}","bars"),
        (f"/api/quotes/600000.SH/factors?year={latest.year}","factors"),
    ):
        with request(route,75) as response:
            doc=json.load(response)
            coverage=doc.get("coverage",{})
            if (response.status!=200 or doc.get("archive_prefix")!=RAW_PREFIX
                    or not doc.get(field) or coverage.get("partial") is not False
                    or coverage.get("latest","")<latest.isoformat()):
                raise NotReady(f"Production {field} reader not fully current")
    with request("/api/health",30) as response:
        if response.status!=200 or json.load(response).get("status")!="ok":
            raise NotReady("Production health not ready")


def verified_day(s3,bucket,day,pointers,verified_months):
    """Verify all five raw datasets plus their published chart references."""
    if day.year<2026:
        raise NotReady("Pre-2026 SQL cache requires separate historical reconciliation")
    key=f"{RAW_PREFIX}/_incremental/market/year={day.year}/day={day:%Y%m%d}.json"
    doc=get_optional_json(s3,bucket,key)
    if not doc or doc.get("status")!="verified_market_day" or doc.get("trade_date")!=day.isoformat():
        raise NotReady("No complete daily archive")
    parts={x["dataset"]:x for x in doc["datasets"]}
    if set(parts)!=set(MARKET) or len(doc["datasets"])!=len(MARKET):
        raise RuntimeError("Invalid daily archive receipt")
    for dataset,p in parts.items():
        stem=day_stem(dataset,day)
        if (p.get("status")!="verified_daily_partition" or p.get("trade_date")!=day.isoformat()
                or p.get("fields")!=list(FIELDS[dataset]) or p.get("object_key")!=stem+".parquet"
                or type(p.get("rows")) is not int or p["rows"]<0
                or (dataset!="suspend_d" and p["rows"]<=0)
                or type(p.get("bytes")) is not int or p["bytes"]<=0
                or not SHA.fullmatch(str(p.get("sha256","")))):
            raise RuntimeError("Invalid daily partition receipt")
        if get_optional_json(s3,bucket,stem+".manifest.json")!=p:
            raise RuntimeError("Daily partition receipt differs")
        verify_object(s3,bucket,p["object_key"],p["bytes"],p["sha256"])
        if dataset in CHART_DATASETS:
            idx=(dataset,day.year,day.month)
            if idx not in pointers:
                pointers[idx]=pointer(s3,bucket,*idx)
            month=pointers[idx]
            if day.isoformat() not in month["dates"]:
                raise NotReady("Day absent from published chart reader")
            if month["source_sha256"][month["dates"].index(day.isoformat())]!=p["sha256"]:
                raise RuntimeError("Monthly reader source hash mismatch")
            if idx not in verified_months:
                verify_object(s3,bucket,month["object_key"],month["bytes"],month["sha256"])
                verified_months.add(idx)
    return parts


def db_state(conn):
    result={}
    for table in ("daily_prices","price_history"):
        count,first,last=conn.execute(f"SELECT count(*),min(trade_date),max(trade_date) FROM {table}").fetchone()
        size=conn.execute("SELECT pg_total_relation_size(%s::regclass)",(table,)).fetchone()[0]
        result[table]={"rows":count,"first":str(first) if first else None,"last":str(last) if last else None,"bytes":size}
    return result


def storage_state(s3,bucket):
    count=size=0
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket,Prefix=RAW_PREFIX+"/"):
        for obj in page.get("Contents",[]):
            count+=1; size+=obj["Size"]
    return {"objects":count,"bytes":size}


def backup_rows(s3,bucket,table,day,rows):
    """Exact PostgreSQL JSON representation; content addressed and read verified."""
    if not rows:
        return None
    if table not in ("daily_prices","price_history"):
        raise RuntimeError("Unexpected table")
    payload=("\n".join(r[0] for r in rows)+"\n").encode("utf-8")
    raw_sha=hashlib.sha256(payload).hexdigest()
    zipped=gzip.compress(payload,mtime=0)
    sha=hashlib.sha256(zipped).hexdigest()
    base=f"{RAW_PREFIX}/_maintenance/postgres_backup/{table}/year={day.year}/month={day.month:02d}/day={day:%Y%m%d}/sha256={sha}"
    key=base+".jsonl.gz"
    with tempfile.TemporaryDirectory() as tmp:
        path=Path(tmp)/"backup.jsonl.gz"; path.write_bytes(zipped)
        publish_file(s3,bucket,key,path,sha,len(zipped),"application/gzip")
    doc={"status":"verified_exact_sql_row_backup","table":table,"trade_date":day.isoformat(),"rows":len(rows),
         "object_key":key,"bytes":len(zipped),"sha256":sha,"uncompressed_sha256":raw_sha}
    manifest=base+".manifest.json"
    old=get_optional_json(s3,bucket,manifest)
    if old is not None and old!=doc:
        raise RuntimeError("SQL backup manifest mismatch")
    if old is None:
        put_manifest(s3,bucket,manifest,doc)
    return doc


def preserve_recent_closes(conn,compact_cutoff):
    # Copy full-table closes to the compact cache BEFORE any deletion. Existing
    # daily_prices has priority in the live calculation, so retain that value.
    with conn.transaction():
        conn.execute("SET LOCAL lock_timeout='15s'")
        conn.execute("""
            INSERT INTO price_history (ts_code,trade_date,close)
            SELECT ts_code,trade_date,close FROM daily_prices WHERE trade_date >= %s
            ON CONFLICT (ts_code,trade_date) DO UPDATE SET close=excluded.close
            WHERE price_history.close IS DISTINCT FROM excluded.close
        """,(compact_cutoff,))
        missing=conn.execute("""
            SELECT count(*) FROM daily_prices d LEFT JOIN price_history p
            USING(ts_code,trade_date) WHERE d.trade_date >= %s
            AND p.close IS DISTINCT FROM d.close
        """,(compact_cutoff,)).fetchone()[0]
        if missing:
            raise RuntimeError("Recent close cache reconciliation failed")


def delete_date(conn,s3,bucket,day,full_cutoff,compact_cutoff):
    result={"date":day.isoformat(),"daily_prices":0,"price_history":0,"backup_bytes":0}
    with conn.transaction():
        conn.execute("SET LOCAL lock_timeout='15s'")
        # Serialize external database writers as well as Actions writers.
        conn.execute("LOCK TABLE daily_prices, price_history, price_history_fetches IN SHARE ROW EXCLUSIVE MODE")
        for table,cutoff in (("daily_prices",full_cutoff),("price_history",compact_cutoff)):
            if day>=cutoff:
                continue
            rows=conn.execute(f"SELECT to_jsonb(p)::text FROM {table} p WHERE trade_date=%s ORDER BY ts_code",(day,)).fetchall()
            if not rows:
                continue
            if table=="daily_prices" and day>=compact_cutoff:
                missing=conn.execute("""
                    SELECT count(*) FROM daily_prices d LEFT JOIN price_history p USING(ts_code,trade_date)
                    WHERE d.trade_date=%s AND p.close IS DISTINCT FROM d.close
                """,(day,)).fetchone()[0]
                if missing:
                    raise RuntimeError("Refusing to delete closes absent from compact cache")
            backup=backup_rows(s3,bucket,table,day,rows)
            deleted=conn.execute(f"DELETE FROM {table} WHERE trade_date=%s",(day,)).rowcount
            if deleted!=backup["rows"]:
                raise RuntimeError("SQL backup/delete row count differs")
            if table=="price_history":
                conn.execute("DELETE FROM price_history_fetches WHERE trade_date=%s",(day,))
            result[table]=deleted
            result["backup_bytes"]+=backup["bytes"]
    return result


def reader_candidates(objects,active,now):
    """Keep current pointer and newest previous version, then apply grace."""
    ordered=sorted(objects,key=lambda o:o["LastModified"],reverse=True)
    previous=next((o["Key"] for o in ordered if o["Key"]!=active),None)
    return [o for o in ordered if o["Key"] not in (active,previous)
            and now-o["LastModified"]>=READER_GRACE]


def clean_reader_versions(s3,bucket,pointers,now,apply):
    count=size=0
    for (dataset,year,month),doc in sorted(pointers.items()):
        if count>=MAX_READER_DELETES:
            break
        # Only fully covered readers. Keep original daily partitions forever.
        if doc["missing_dates"]:
            continue
        root=month_root(dataset,year,month)
        prefix=root+"/reader/"
        objects=[]
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket,Prefix=prefix):
            for obj in page.get("Contents",[]):
                if re.fullmatch(re.escape(prefix)+r"sha256=[a-f0-9]{64}\.parquet",obj["Key"]):
                    objects.append(obj)
        if doc["object_key"] not in {o["Key"] for o in objects}:
            raise RuntimeError("Active reader snapshot missing")
        candidates=reader_candidates(objects,doc["object_key"],now)[:MAX_READER_DELETES-count]
        if not candidates:
            continue
        verify_object(s3,bucket,doc["object_key"],doc["bytes"],doc["sha256"])
        # Ensure every original partition needed to reconstruct this reader survives.
        for ds,sha in zip(doc["dates"],doc["source_sha256"]):
            d=date.fromisoformat(ds)
            key=day_stem(dataset,d)
            source=get_optional_json(s3,bucket,key+".manifest.json")
            if not source or source.get("sha256")!=sha or source.get("object_key")!=key+".parquet":
                raise RuntimeError("Reader source is not reconstructible")
            verify_object(s3,bucket,key+".parquet",source["bytes"],sha)
        if pointer(s3,bucket,dataset,year,month)!=doc:
            raise RuntimeError("Active reader changed during maintenance")
        for obj in candidates:
            if apply:
                head=s3.head_object(Bucket=bucket,Key=obj["Key"])
                expected_sha=obj["Key"].rsplit("=",1)[1].removesuffix(".parquet")
                if (head.get("ETag")!=obj.get("ETag") or head["ContentLength"]!=obj["Size"]
                        or (head.get("Metadata") or {}).get("sha256")!=expected_sha):
                    raise RuntimeError("Old reader object identity changed")
                s3.delete_object(Bucket=bucket,Key=obj["Key"])
            count+=1;size+=obj["Size"]
    return {"objects":count,"bytes":size}


def main():
    import psycopg
    parser=argparse.ArgumentParser()
    parser.add_argument("--apply",action="store_true")
    args=parser.parse_args()
    if archive_driver()=="local" and args.apply:
        if os.getenv("ARCHIVE_LOCAL_ALLOW_MAINTENANCE_APPLY")!="true" or not os.getenv("PIPELINE_RUN_ID"):
            raise RuntimeError("Local cleanup disabled; explicit opt-in and locked pipeline required")
    cfg=StorageConfig.from_environment()
    s3=s3_client(cfg);bucket=cfg.bucket
    now=datetime.now(timezone.utc)
    report={"format_version":1,"mode":"apply" if args.apply else "audit",
            "captured_at_utc":now.isoformat(),"full_quote_calendar_days":FULL_DAYS,
            "compact_trading_sessions":COMPACT_SESSIONS,"deleted_daily_prices":0,
            "deleted_price_history":0,"backup_bytes":0,"blocked_dates":[],
            "status":"audit_only"}
    with psycopg.connect(os.environ["DATABASE_URL"],connect_timeout=15,autocommit=True) as conn:
        report["database_before"]=db_state(conn)
        report["storage_before"]=storage_state(s3,bucket)
        latest=conn.execute("SELECT max(trade_date) FROM daily_prices").fetchone()[0]
        report["latest_quote_date"]=str(latest) if latest else None
        if not latest:
            raise RuntimeError("No live quote data")
        try:
            successful=conn.execute("SELECT max(trade_date) FROM job_runs WHERE job_type='market_refresh' AND status='success'").fetchone()[0]
            if successful!=latest:
                raise NotReady("Latest SQL quote date is not a successful market refresh")
            observed=[r[0] for r in conn.execute("SELECT DISTINCT trade_date FROM daily_prices UNION SELECT DISTINCT trade_date FROM price_history").fetchall()]
            full,compact=retention_cutoffs(latest,observed)
            report.update(full_cutoff=str(full),compact_cutoff=str(compact))
            candidates=conn.execute("""
                SELECT trade_date, sum(full_rows),sum(compact_rows) FROM (
                    SELECT trade_date,count(*) AS full_rows,0 AS compact_rows FROM daily_prices
                    WHERE trade_date < %s GROUP BY trade_date
                    UNION ALL
                    SELECT trade_date,0 AS full_rows,count(*) AS compact_rows FROM price_history
                    WHERE trade_date < %s GROUP BY trade_date
                ) q GROUP BY trade_date ORDER BY trade_date
            """,(full,compact)).fetchall()
            report["candidate_dates"]=len(candidates)
            report["candidate_daily_prices"]=sum(int(r[1]) for r in candidates)
            report["candidate_price_history"]=sum(int(r[2]) for r in candidates)
            pointers=complete_year_gate(s3,bucket,latest)
            web_gate(latest)
            report["archive_and_web_gate"]="passed"
            if args.apply:
                preserve_recent_closes(conn,compact)
            verified_months=set()
            eligible=0
            for day,full_rows,compact_rows in candidates:
                try:
                    verified_day(s3,bucket,day,pointers,verified_months)
                except NotReady as exc:
                    report["blocked_dates"].append({"date":day.isoformat(),"reason":str(exc)})
                    continue
                eligible+=1
                if args.apply:
                    deleted=delete_date(conn,s3,bucket,day,full,compact)
                    report["deleted_daily_prices"]+=deleted["daily_prices"]
                    report["deleted_price_history"]+=deleted["price_history"]
                    report["backup_bytes"]+=deleted["backup_bytes"]
                if eligible>=MAX_DB_DATES:
                    break
            report["verified_eligible_dates"]=eligible
            report["reader_cleanup"]=clean_reader_versions(s3,bucket,pointers,now,args.apply)
            report["status"]="completed" if args.apply else "audit_only"
        except NotReady as exc:
            report["status"]="blocked_no_cleanup"
            report["reason"]=str(exc)
        report["database_after"]=db_state(conn)
        report["storage_after"]=storage_state(s3,bucket)
    run=os.environ.get("GITHUB_RUN_ID") or os.environ.get("PIPELINE_RUN_ID") or ("local-"+uuid4().hex if archive_driver()=="local" else "manual")
    attempt=os.environ.get("GITHUB_RUN_ATTEMPT","1")
    key=f"{RAW_PREFIX}/_maintenance/reports/year={now.year}/run={run}_{attempt}.json"
    put_manifest(s3,bucket,key,report)
    print("RAW_MAINTENANCE_STATUS "+json.dumps(report,ensure_ascii=False,sort_keys=True),flush=True)


if __name__=="__main__":
    main()
