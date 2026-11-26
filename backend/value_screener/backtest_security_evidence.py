"""BT03-B2: bounded official namechange capture and offline security evidence.

No current name is used for a historical decision. Listing dates from a later
snapshot are descriptive evidence, not a historically certified stock universe.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from .archive_paths import RAW_PREFIX
from .backtest_dataset import atomic_json, digest, read_json, incremental_inputs, sync_dir, MAIN
from .financial_archive_import import parsed
from .phase2_historical_st import classify_st, name_status
from .market_session import CN_TZ

CODE = re.compile(r"^[0-9]{6}[.](SH|SZ|BJ)$")


def listing_evidence(record, day):
    if not record:
        return "snapshot_missing"
    start=parsed(record.get("list_date"))
    end=parsed(record.get("delist_date"))
    if not start or (record.get("delist_date") not in (None,"") and not end) or (end and end<start):
        return "snapshot_invalid_dates"
    if day<start:
        return "before_snapshot_listing_date"
    if end and day>=end:
        return "at_or_after_snapshot_delisting_date"
    return "within_snapshot_listing_dates_not_certified"


def name_evidence_asof(rows, day, captured):
    if captured<day:
        return None,"capture_before_day"
    candidates=[]
    for row in rows:
        start=parsed(row.get("start_date"))
        end=parsed(row.get("end_date"))
        ann=parsed(row.get("ann_date"))
        if not start or (row.get("end_date") not in (None,"") and not end) or (end and end<start):
            return None,"invalid_name_interval"
        if start<=day and (not end or day<=end):
            candidates.append((row,ann))
    if not candidates:
        return None,"no_name_interval"
    # Same-day announcement time is unavailable. Do not backdate.
    if any(not a or a>=day for _,a in candidates):
        return None,"name_announcement_unresolved_or_same_day"
    names={r.get("name") for r,_ in candidates}
    if len(names)!=1 or not next(iter(names)):
        return None,"name_interval_conflict"
    name=next(iter(names))
    st=name_status(name)
    if st is None:
        return None,"unknown_name"
    return classify_st(st,"namechange_only",name),"namechange_candidate_not_certified"


def source_record(path, code, capture_day):
    doc=read_json(path)
    if (doc.get("version")!=1 or doc.get("source")!="Tushare official namechange"
            or doc.get("ts_code")!=code or doc.get("capture_day")!=capture_day.isoformat()
            or not isinstance(doc.get("rows"),list)
            or any(row.get("ts_code")!=code for row in doc["rows"])):
        raise ValueError("Invalid namechange snapshot")
    ts=datetime.fromisoformat(doc["captured_at_utc"])
    if ts.tzinfo is None or ts.astimezone(CN_TZ).date()!=capture_day:
        raise ValueError("Invalid namechange capture timestamp")
    expected=hashlib.sha256(json.dumps(doc["rows"],sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
    if doc.get("rows_sha256")!=expected:
        raise ValueError("Namechange payload checksum mismatch")
    return doc


def collect(archive, evidence_dir, day, budget, client):
    import duckdb
    raw=archive/RAW_PREFIX
    sources=incremental_inputs(raw,day)
    db=duckdb.connect(config={"threads":"1","memory_limit":"128MB"})
    try:
        codes=[r[0] for r in db.execute("SELECT DISTINCT ts_code FROM read_parquet(?) WHERE regexp_matches(ts_code,?) ORDER BY ts_code",
            [str(sources["daily_prices"][0]),MAIN]).fetchall()]
    finally:
        db.close()
    capture=datetime.now(CN_TZ).date()
    target=evidence_dir/f"capture={capture:%Y%m%d}"
    target.mkdir(exist_ok=True)
    from .phase2_historical_st import fetch_names
    processed=0
    with (evidence_dir/".collect.lock").open("a") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        for code in codes:
            if not CODE.fullmatch(code):
                raise ValueError("Invalid security code")
            path=target/(code+".json")
            if path.exists():
                source_record(path,code,capture)
                continue
            if processed>=budget:
                break
            rows=fetch_names(client,code)
            now=datetime.now(timezone.utc)
            if now.astimezone(CN_TZ).date()!=capture:
                raise ValueError("Capture crossed local midnight; resume in a new capture")
            payload=json.dumps(rows,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False)
            atomic_json(path,{"version":1,"source":"Tushare official namechange","ts_code":code,
                "capture_day":capture.isoformat(),"captured_at_utc":now.isoformat(),"rows":rows,
                "rows_sha256":hashlib.sha256(payload.encode()).hexdigest()})
            processed+=1
    print("BT03_NAMECHANGE_CAPTURE "+json.dumps({"target_day":day.isoformat(),"capture_day":capture.isoformat(),
        "mainboard_symbols":len(codes),"new_captures":processed,"budget":budget}),flush=True)


def security_rows(db, market_path, snapshot, evidence_dir, capture):
    # One standardized partition at a time, including all market rows.
    db.execute("""CREATE OR REPLACE TEMP TABLE security_daily (
      ts_code VARCHAR,trade_date DATE,listing_evidence VARCHAR,
      historical_st_type VARCHAR,st_evidence VARCHAR,st_certified BOOLEAN,
      namechange_source_sha256 VARCHAR)""")
    q="""SELECT ts_code,trade_date,st_type,st_ann_date,st_resolution,is_mainboard_code
         FROM read_parquet(?,hive_partitioning=false) ORDER BY ts_code,trade_date"""
    rows=db.execute(q,[str(market_path)]).fetchall()
    batch=[]
    cache={}
    source_hashes={}
    for code,day,st,ann,resolution,main in rows:
        listing=listing_evidence(snapshot.get(code),day)
        state=st
        status="historical_merged_not_certified" if st else "historical_ST_unknown"
        h=None
        if day.year>=2026:
            state=None
            status="namechange_capture_missing"
            if main and capture:
                if code not in cache:
                    path=evidence_dir/f"capture={capture:%Y%m%d}"/(code+".json")
                    if path.is_file() and not path.is_symlink():
                        h=digest(path)
                        doc=source_record(path,code,capture)
                        if digest(path)!=h:
                            raise ValueError("Namechange snapshot changed")
                        cache[code]=(doc["rows"],h)
                        source_hashes[code]=h
                    else:
                        cache[code]=(None,None)
                names,h=cache[code]
                if names is not None:
                    state,status=name_evidence_asof(names,day,capture)
        elif ann is None or ann>=day:
            # Keep historical label as evidence but not an as-of eligibility decision.
            status="historical_announcement_unresolved_or_same_day"
        batch.append((code,day,listing,state,status,False,h))
        if len(batch)>=5000:
            db.executemany("INSERT INTO security_daily VALUES (?,?,?,?,?,?,?)",batch)
            batch.clear()
    if batch:
        db.executemany("INSERT INTO security_daily VALUES (?,?,?,?,?,?,?)",batch)
    n,u=db.execute("SELECT count(*),count(DISTINCT(ts_code,trade_date)) FROM security_daily").fetchone()
    if n!=u or n!=len(rows):
        raise ValueError("Security join grain mismatch")
    stats=dict(db.execute("SELECT st_evidence,count(*) FROM security_daily GROUP BY st_evidence").fetchall())
    return {"rows":n,"st_evidence_counts":stats},source_hashes


def build(archive, market, evidence_dir, output, scratch, year, month, capture):
    import duckdb
    snapshot_path=archive/RAW_PREFIX/"stock_basic"/"stock_basic_snapshot.json"
    snapshot_sha=digest(snapshot_path)
    doc=read_json(snapshot_path)
    if doc.get("status")!="current_snapshot_not_historical":
        raise ValueError("Invalid frozen stock snapshot")
    snapshot={r["ts_code"]:r for r in doc["records"]}
    if len(snapshot)!=len(doc["records"]):
        raise ValueError("Duplicate security snapshot")
    pattern=f"year={year}/month={month:02d}/"
    markers=sorted(market.glob(pattern+("manifest.json" if year<2026 else "day=*/manifest.json")))
    if not markers:
        raise ValueError("No standardized market partitions")
    with (output/".build.lock").open("a") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        db=duckdb.connect(config={"threads":"1","memory_limit":"512MB","temp_directory":str(scratch)})
        try:
            for marker in markers:
                meta=read_json(marker)
                item=meta["outputs"]["market"]
                src=marker.parent/item["file"]
                if (meta.get("version")!=1 or meta.get("year")!=year or meta.get("month")!=month
                    or meta.get("status")!="research_data_not_backtest_ready"
                    or item["file"]!=f"market-{item['sha256']}.parquet" or src.is_symlink()
                    or src.stat().st_size!=item["bytes"] or digest(src)!=item["sha256"]):
                    raise ValueError("Invalid market receipt")
                stats,hashes=security_rows(db,src,snapshot,evidence_dir,capture)
                if stats["rows"]!=meta["quality"]["rows"] or digest(src)!=item["sha256"] or digest(snapshot_path)!=snapshot_sha:
                    raise ValueError("Security input changed or row count mismatch")
                fingerprint={"market_sha256":item["sha256"],"snapshot_sha256":snapshot_sha,
                    "capture_day":capture.isoformat() if capture else None,"namechange_hashes":hashes}
                target=output/marker.parent.relative_to(market)
                target.mkdir(parents=True,exist_ok=True)
                receipt=target/"manifest.json"
                if receipt.exists():
                    old=read_json(receipt)
                    if (old["inputs"]!=fingerprint or old["file"]!=f"security-{old['sha256']}.parquet"
                        or digest(target/old["file"])!=old["sha256"]):
                        raise ValueError("Existing security partition differs; use a new output version")
                    continue
                with tempfile.TemporaryDirectory(dir=target) as tmp:
                    p=Path(tmp)/"data.parquet"
                    db.execute("COPY (SELECT * FROM security_daily ORDER BY trade_date,ts_code) TO ? (FORMAT PARQUET,COMPRESSION ZSTD)",[str(p)])
                    with p.open("rb") as f:
                        os.fsync(f.fileno())
                    h=digest(p);name=f"security-{h}.parquet"
                    os.replace(p,target/name);sync_dir(target)
                atomic_json(receipt,{"version":1,"status":"security_evidence_not_certified","inputs":fingerprint,
                    "file":name,"sha256":h,"quality":stats})
                print("BT03_SECURITY_PARTITION "+json.dumps(stats,sort_keys=True),flush=True)
        finally:
            db.close()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest="task",required=True)
    c=sub.add_parser("capture")
    c.add_argument("--archive",type=Path,required=True)
    c.add_argument("--evidence",type=Path,required=True)
    c.add_argument("--day",type=date.fromisoformat,required=True)
    c.add_argument("--budget",type=int,default=50,choices=range(1,201))
    c.add_argument("--confirm",required=True)
    b=sub.add_parser("build")
    for name in ("archive","market","evidence","output","scratch"):
        b.add_argument("--"+name,type=Path,required=True)
    b.add_argument("--year",type=int,required=True,choices=range(2020,2100))
    b.add_argument("--month",type=int,required=True,choices=range(1,13))
    b.add_argument("--capture-day",type=date.fromisoformat)
    a=p.parse_args()
    names=("archive","evidence") if a.task=="capture" else ("archive","market","evidence","output","scratch")
    paths=[getattr(a,k).resolve(strict=True) for k in names]
    if any(x==Path("/") for x in paths) or any(x==y or x in y.parents or y in x.parents for i,x in enumerate(paths) for y in paths[i+1:]):
        p.error("Use disjoint existing directories")
    os.umask(0o027)
    if a.task=="capture":
        if a.confirm!="CAPTURE-NAMECHANGE" or a.day.year<2026 or a.day>datetime.now(CN_TZ).date():
            p.error("Explicit confirmation and committed 2026+ day required")
        from .tushare_client import TushareClient
        # Explicit official endpoint only; separate token required by operator.
        collect(*paths,a.day,a.budget,TushareClient(os.environ.get("TUSHARE_TOKEN","")))
    else:
        build(*paths,a.year,a.month,a.capture_day)


if __name__=="__main__":
    main()
