"""Read-only BT03 security/market integrity and provisional evidence audit.

Fixed local maintenance plan, never remote paths. Not a raw archive audit,
independent calendar validation, historical-universe certification or backtest.
"""
from __future__ import annotations
import fcntl
import hashlib
from pathlib import Path
import re
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parent))
import bridge
import maintenance_runner as maint
from worker import store

STATUS="BT03_SECURITY_AUDIT_V1"
METRICS=("market_rows","security_rows","mainboard_rows","provisional_eligible_rows",
         "st_unresolved_rows","st_unknown_rows","listing_anomaly_rows","invalid_factor_rows",
         "invalid_limit_rows","duplicate_keys","key_mismatch_rows","invalid_evidence_rows")
STATES=("historical_announcement_unresolved_or_same_day","historical_merged_not_certified","historical_ST_unknown")
LISTING=("snapshot_missing","snapshot_invalid_dates","before_snapshot_listing_date",
         "at_or_after_snapshot_delisting_date","within_snapshot_listing_dates_not_certified")

def digest(path):
    with __import__("os").fdopen(bridge.open_local(path),"rb") as f:
        return hashlib.file_digest(f,"sha256").hexdigest()

def verified_file(folder,item,prefix):
    h=item["sha256"]
    if not isinstance(h,str) or not re.fullmatch(r"[0-9a-f]{64}",h) or item["file"]!=f"{prefix}-{h}.parquet":
        raise ValueError("Invalid output identity")
    p=folder/item["file"]
    if digest(p)!=h or ("bytes" in item and p.stat().st_size!=item["bytes"]):
        raise ValueError("Checksum or size mismatch")
    return p

def partition(db,mfolder,sfolder,year,month,snapshot_sha):
    mm=bridge.bounded_json(mfolder/"manifest.json")
    sm=bridge.bounded_json(sfolder/"manifest.json")
    if (mm.get("version")!=1 or mm.get("year")!=year or mm.get("month")!=month
        or mm.get("status")!="research_data_not_backtest_ready" or sm.get("version")!=1
        or sm.get("status")!="security_evidence_not_certified"):
        raise ValueError("Manifest identity mismatch")
    mi=mm["outputs"]["market"]
    if sm["inputs"]!={"market_sha256":mi["sha256"],"snapshot_sha256":snapshot_sha,"capture_day":None,"namechange_hashes":{}}:
        raise ValueError("Security source identity mismatch")
    mp=verified_file(mfolder,mi,"market");sp=verified_file(sfolder,sm,"security")
    db.execute("CREATE OR REPLACE TEMP VIEW m AS SELECT * FROM read_parquet("+sql_literal(str(mp))+",hive_partitioning=false)")
    db.execute("CREATE OR REPLACE TEMP VIEW s AS SELECT * FROM read_parquet("+sql_literal(str(sp))+",hive_partitioning=false)")
    m,nm,dm=db.execute("SELECT count(*),count(*) FILTER (WHERE ts_code IS NULL OR trade_date IS NULL OR year(trade_date)<>? OR month(trade_date)<>?),count(*)-count(DISTINCT(ts_code,trade_date)) FROM m",[year,month]).fetchone()
    s,ns,ds=db.execute("SELECT count(*),count(*) FILTER (WHERE ts_code IS NULL OR trade_date IS NULL OR year(trade_date)<>? OR month(trade_date)<>?),count(*)-count(DISTINCT(ts_code,trade_date)) FROM s",[year,month]).fetchone()
    if not m or m!=mm["quality"]["rows"] or s!=sm["quality"]["rows"]:
        raise ValueError("Manifest row count mismatch")
    actual_counts=dict(db.execute("SELECT st_evidence,count(*) FROM s GROUP BY st_evidence").fetchall())
    if actual_counts!=sm["quality"]["st_evidence_counts"]:
        raise ValueError("ST evidence receipt count mismatch")
    missing=db.execute("SELECT count(*) FROM (SELECT ts_code,trade_date FROM m EXCEPT SELECT ts_code,trade_date FROM s)").fetchone()[0]
    extra=db.execute("SELECT count(*) FROM (SELECT ts_code,trade_date FROM s EXCEPT SELECT ts_code,trade_date FROM m)").fetchone()[0]
    result=dict.fromkeys(METRICS,0)
    result.update(market_rows=m,security_rows=s,duplicate_keys=dm+ds,key_mismatch_rows=missing+extra)
    # Never aggregate a many-to-many or incomplete join as a valid population.
    if nm or ns or dm or ds or missing or extra or m!=s:
        result["invalid_evidence_rows"]=nm+ns
        return result,False
    invalid=db.execute("""SELECT count(*) FROM m JOIN s USING(ts_code,trade_date)
        WHERE s.st_certified IS DISTINCT FROM false
           OR s.namechange_source_sha256 IS NOT NULL
           OR s.historical_st_type IS DISTINCT FROM m.st_type
           OR s.listing_evidence IS NULL OR s.listing_evidence NOT IN (SELECT unnest(?))
           OR s.st_evidence IS DISTINCT FROM CASE
                WHEN m.st_ann_date IS NULL OR m.st_ann_date>=m.trade_date
                  THEN 'historical_announcement_unresolved_or_same_day'
                WHEN m.st_type IS NOT NULL AND m.st_type<>'' THEN 'historical_merged_not_certified'
                ELSE 'historical_ST_unknown' END""",[list(LISTING)]).fetchone()[0]
    result["invalid_evidence_rows"]=invalid
    row=db.execute("""SELECT count(*),
       count(*) FILTER (WHERE m.st_normal_evidence AND m.st_ann_date<m.trade_date
          AND s.historical_st_type='NORMAL' AND s.st_evidence='historical_merged_not_certified'
          AND s.listing_evidence='within_snapshot_listing_dates_not_certified' AND m.limits_valid),
       count(*) FILTER (WHERE s.st_evidence='historical_announcement_unresolved_or_same_day'),
       count(*) FILTER (WHERE s.st_evidence='historical_ST_unknown'),
       count(*) FILTER (WHERE s.listing_evidence IS DISTINCT FROM 'within_snapshot_listing_dates_not_certified'),
       count(*) FILTER (WHERE NOT coalesce(m.factor_valid,false)),
       count(*) FILTER (WHERE NOT coalesce(m.limits_valid,false))
       FROM m JOIN s USING(ts_code,trade_date) WHERE m.is_mainboard_code""").fetchone()
    result.update(zip(METRICS[2:9],row))
    return result,invalid==0

def sql_literal(value):
    return "'"+value.replace("'","''")+"'"

def run(d,base=maint.BASE,publish=None):
    import duckdb
    if "existing_market" not in d:raise ValueError("Existing market plan required")
    expected={(y,m) for y in range(2020,2026) for m in range(1,13)}
    if any(b["stage"]!="security" for b in d["batches"]) or {(b["year"],b["month"]) for b in d["batches"]}!=expected:
        raise ValueError("Exact 72-month plan required")
    work=Path(base)/d["dataset"];market=Path(d["existing_market"]);security=work/"security"
    if work.resolve()!=work or security.resolve()!=security:raise ValueError("Invalid work directory")
    if publish is None:
        publish=lambda report:store(bridge.SPOOL/"research"/"results"/"bt03-security-audit.json",report)
    with (work/"maintenance.lock").open("a") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        checkpoint=bridge.bounded_json(work/"maintenance-state.json")
        if checkpoint["plan_sha256"]!=hashlib.sha256(bridge.dump(d).encode()).hexdigest() or checkpoint["completed"]!=72:
            raise ValueError("Incomplete or changed maintenance plan")
        snapshot=Path(d["archive"])/"a-share-backtest/01_raw_data/stock_basic/stock_basic_snapshot.json"
        sha=digest(snapshot);started=time.monotonic()
        report={"status":STATUS,"state":"running","expected_partitions":72,"checked_partitions":0,
                "verified_partitions":0,"failed_partitions":0,"missing_partitions":0,"years":{},
                "formal_backtest_ready":False}
        for y in range(2020,2026):
            report["years"][str(y)]={"checked_partitions":0,"verified_partitions":0,"failed_partitions":0,
                                     "missing_partitions":0,**dict.fromkeys(METRICS,0)}
        # Full report contains only finite counts and fixed error categories.
        def flush():
            report["elapsed_seconds"]=round(time.monotonic()-started,3)
            maint.atomic(work/"security-audit.json",report);publish(report)
        flush()
        db=duckdb.connect(config={"threads":"1","memory_limit":"512MB","temp_directory":str(work/"scratch")})
        try:
            for y,m in sorted(expected):
                rel=Path(f"year={y}/month={m:02d}");yr=report["years"][str(y)]
                try:
                    metrics,ok=partition(db,market/rel,security/rel,y,m,sha)
                    for k,v in metrics.items():yr[k]+=v
                    field="verified_partitions" if ok else "failed_partitions"
                except FileNotFoundError:field="missing_partitions"
                except Exception:field="failed_partitions"
                yr["checked_partitions"]+=1;yr[field]+=1
                report["checked_partitions"]+=1;report[field]+=1
                flush()
        finally:db.close()
        report["state"]="completed" if report["verified_partitions"]==72 and digest(snapshot)==sha else "failed"
        flush()
        return 0 if report["state"]=="completed" else 2

if __name__=="__main__":
    import os
    if os.geteuid()==0 or len(sys.argv)!=1:raise SystemExit("Fixed non-root audit only")
    raise SystemExit(run(maint.load()))
