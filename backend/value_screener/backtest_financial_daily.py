"""BT03-C2: disclosure-corroborated daily financial research candidates.

Not PIT certified: a later provider snapshot may omit original report versions.
No provider/DB access; original archive and market partitions remain immutable.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
from datetime import date, timedelta
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from .backtest_bulk import insert_columns, progress

from .archive_paths import RAW_PREFIX
from .backtest_dataset import atomic_json, digest, read_json, sync_dir
from .backtest_financial_evidence import batch_rows, evidence, select_candidate, canonical, BATCH
from .financial_archive_import import parsed, finite

KINDS = ("balancesheet", "income", "cashflow")
METRICS = {"balancesheet": ("total_cur_assets","total_liab"),
           "income": ("n_income","n_income_attr_p"),
           "cashflow": ("n_cashflow_act",)}
VERSION = 1
EVENT_CACHE_VERSION = 1


def disclosure_evidence(raw):
    result = defaultdict(set)
    provenance = {}
    directory = raw / "disclosures"
    for marker in sorted(directory.glob("period=*.manifest.json")):
        meta = read_json(marker)
        period = meta.get("report_period")
        if not parsed(period) or marker.name != f"period={period}.manifest.json":
            raise ValueError("Invalid disclosure period")
        path = directory / f"period={period}.jsonl.gz"
        if (meta.get("format_version") != 1 or meta.get("source") != "Tushare official disclosure_date"
            or meta.get("status") != "source_evidence_only" or meta.get("object_key") != RAW_PREFIX+"/"+path.relative_to(raw).as_posix()
            or path.is_symlink() or not 0 < path.stat().st_size <= 8*1024*1024
            or path.stat().st_size != meta.get("compressed_bytes") or digest(path) != meta.get("sha256")):
            raise ValueError("Unverified disclosure archive: "+str(path))
        count = expanded = populated = 0
        with gzip.open(path,"rb") as fp:
            for line in fp:
                expanded += len(line)
                if expanded > 64*1024*1024:
                    raise ValueError("Disclosure expansion limit")
                row = json.loads(line)
                if row.get("end_date") != period or not row.get("ts_code"):
                    raise ValueError("Disclosure row identity mismatch")
                actual = parsed(row.get("actual_date"))
                if row.get("actual_date") not in (None,""):
                    populated += 1
                result[(row["ts_code"],period)].add(actual)
                count += 1
        if (count != meta.get("rows") or populated != meta.get("actual_date_populated")
            or count-populated != meta.get("actual_date_missing") or digest(path) != meta["sha256"]):
            raise ValueError("Disclosure counts/hash mismatch")
        provenance[period] = meta["sha256"]
    if not provenance:
        raise ValueError("No verified disclosure calendars")
    return result, provenance


def candidate_events(rows, kind, calendar):
    """Changes in latest report availability, not every daily row.

    As-of join uses inclusive event date, which is disclosure date + 1 day.
    A newer unresolved disclosure or conflict creates an explicit null event.
    Calendar corroboration checks actual date; never uses scheduled pre_date.
    """
    days = set()
    releases = defaultdict(list)
    unresolved_dates = defaultdict(list)
    for row in rows:
        p,d,status = evidence(row,kind)
        if p:
            days.add(p)
        if d and d < date.max:
            days.add(d+timedelta(days=1))
        if not p or status == "non_consolidated_or_unknown":
            continue
        if not d:
            unresolved_dates[p].append(p)
        elif d < date.max:
            releases[d+timedelta(days=1)].append(((p,d),canonical(row),row,status))
    previous = None
    latest = None
    versions = {}
    unresolved = None
    for day in sorted(days):
        for p in unresolved_dates.get(day,()):
            unresolved = max(unresolved,p) if unresolved else p
        for key,signature,record,record_status in releases.get(day,()):
            if latest is None or key>latest:
                latest,versions=key,{}
            if key==latest:
                versions[signature]=(record,record_status)
        row=None
        if latest is None:
            status="no_eligible_report"
        elif unresolved is not None and unresolved>=latest[0]:
            status="unresolved_disclosure_version"
        elif len(versions)!=1:
            status="conflicting_latest_version"
        else:
            record,status=next(iter(versions.values()))
            if status=="candidate_not_pit_certified":
                row=record
        period = disclosed = version = None
        metrics = [None]*len(METRICS[kind])
        if row is not None:
            period, disclosed,_ = evidence(row,kind)
            dates = calendar.get((row["ts_code"],row["end_date"]),set())
            if not dates:
                status = "calendar_missing"
            elif dates != {parsed(row.get("f_ann_date"))}:
                status = "calendar_actual_conflict"
            else:
                status = "calendar_correlated_not_pit_certified"
                metrics = [float(row[k]) if finite(row.get(k)) else None for k in METRICS[kind]]
            version = hashlib.sha256(canonical(row).encode()).hexdigest()
        state = (period,disclosed,version,status,*metrics)
        if state != previous:
            yield (day,*state)
            previous = state


def event_types(kind):
    return ("VARCHAR","DATE","DATE","DATE","VARCHAR","VARCHAR",*("DOUBLE" for _ in METRICS[kind]))


def source_identity(raw):
    """Hash actual source bytes AND manifests before trusting a cached event set.

    Enumerate exactly the same frozen universe and periods as the cold reader.
    No mtime-only shortcuts. Cache invalidation also covers metadata-only edits.
    """
    snapshot=raw/"stock_basic"/"stock_basic_snapshot.json"
    doc=read_json(snapshot)
    if doc.get("status")!="current_snapshot_not_historical":
        raise ValueError("Invalid frozen universe")
    codes=sorted({r["ts_code"] for r in doc["records"]})
    if not codes:
        raise ValueError("Empty frozen universe")
    paths=[snapshot]
    markers=sorted((raw/"disclosures").glob("period=*.manifest.json"))
    if not markers:
        raise ValueError("No disclosure manifests")
    for marker in markers:
        paths.extend((marker,marker.with_name(marker.name.removesuffix(".manifest.json")+".jsonl.gz")))
    for kind in KINDS:
        for start in range(0,len(codes),BATCH):
            stem=raw/"financial"/kind/f"start={start:05d}_count={BATCH:03d}"
            paths.extend((stem.with_suffix(".manifest.json"),stem.with_suffix(".jsonl.gz")))
    identity={}
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise ValueError("Missing or symlink financial source: "+str(path))
        identity[path.relative_to(raw).as_posix()]=digest(path)
    return identity


def load_events(db, raw):
    calendar, provenance = disclosure_evidence(raw)
    snapshot = raw/"stock_basic"/"stock_basic_snapshot.json"
    snapshot_sha = digest(snapshot)
    doc = read_json(snapshot)
    if doc.get("status") != "current_snapshot_not_historical":
        raise ValueError("Invalid frozen universe")
    codes = sorted({r["ts_code"] for r in doc["records"]})
    if not codes:
        raise ValueError("Empty universe")
    sources = {"disclosures":provenance,"universe_sha256":snapshot_sha,"financial":{}}
    started=time.monotonic()
    total=len(KINDS)*((len(codes)+BATCH-1)//BATCH)
    done=0
    for kind in KINDS:
        columns = ",".join(f"{k} DOUBLE" for k in METRICS[kind])
        db.execute(f"CREATE TEMP TABLE {kind}_events (ts_code VARCHAR, available DATE, period DATE, disclosed DATE, version VARCHAR, status VARCHAR,{columns})")
        for start in range(0,len(codes),BATCH):
            rows,meta = batch_rows(raw,kind,codes,start)
            sources["financial"][meta["object_key"]] = meta["sha256"]
            grouped = defaultdict(list)
            for row in rows:
                grouped[row["ts_code"]].append(row)
            events = [(code,*event) for code,rs in grouped.items()
                      for event in candidate_events(rs,kind,calendar)]
            insert_columns(db,kind+"_events",event_types(kind),events)
            done+=1
            if done==1 or done%10==0 or done==total:
                progress("BT03_FINANCIAL_EVENTS_PROGRESS",started,kind=kind,batches_done=done,batches_total=total)
    if digest(snapshot) != snapshot_sha:
        raise ValueError("Universe changed")
    return sources


def cached_events(db,raw,cache_root):
    started=time.monotonic()
    progress("BT03_FINANCIAL_SOURCE_CHECK",started)
    identity=source_identity(raw)
    cache_key=hashlib.sha256(json.dumps({"version":EVENT_CACHE_VERSION,"files":identity},
        sort_keys=True,separators=(",",":")).encode()).hexdigest()
    cache=cache_root/cache_key
    cache.mkdir(parents=True,exist_ok=True)
    marker=cache/"manifest.json"
    if marker.exists():
        receipt=read_json(marker)
        if receipt.get("version")!=EVENT_CACHE_VERSION or receipt.get("source_identity")!=identity:
            raise ValueError("Event cache identity mismatch")
        for kind in KINDS:
            item=receipt["outputs"][kind]
            path=cache/f"{kind}-{item['sha256']}.parquet"
            if (item.get("file")!=path.name or path.is_symlink() or not path.is_file()
                or path.stat().st_size!=item["bytes"] or digest(path)!=item["sha256"]):
                raise ValueError("Corrupt event cache: "+str(path))
            db.execute(f"CREATE TEMP TABLE {kind}_events AS SELECT * FROM read_parquet(?,hive_partitioning=false)",[str(path)])
            n,u=db.execute(f"SELECT count(*),count(DISTINCT(ts_code,available)) FROM {kind}_events").fetchone()
            if n!=u or n!=item["rows"]:
                raise ValueError("Event cache row/key mismatch")
        if source_identity(raw)!=identity:
            raise ValueError("Financial sources changed while reading cache")
        progress("BT03_FINANCIAL_CACHE_REUSED",started,key=cache_key)
        return receipt["financial_sources"]
    sources=load_events(db,raw)
    outputs={}
    for kind in KINDS:
        n,u=db.execute(f"SELECT count(*),count(DISTINCT(ts_code,available)) FROM {kind}_events").fetchone()
        if n!=u:
            raise ValueError("Duplicate financial event keys")
        with tempfile.TemporaryDirectory(dir=cache) as tmp:
            path=Path(tmp)/"events.parquet"
            db.execute(f"COPY (SELECT * FROM {kind}_events ORDER BY ts_code,available) TO ? (FORMAT PARQUET,COMPRESSION ZSTD)",[str(path)])
            with path.open("rb") as f:
                os.fsync(f.fileno())
            h=digest(path)
            name=f"{kind}-{h}.parquet"
            os.replace(path,cache/name)
            sync_dir(cache)
            outputs[kind]={"file":name,"sha256":h,"rows":n,"bytes":(cache/name).stat().st_size}
    if source_identity(raw)!=identity:
        raise ValueError("Financial sources changed during event build")
    atomic_json(marker,{"version":EVENT_CACHE_VERSION,"source_identity":identity,
                        "financial_sources":sources,"outputs":outputs})
    progress("BT03_FINANCIAL_CACHE_BUILT",started,key=cache_key,events=sum(x["rows"] for x in outputs.values()))
    return sources


def daily_join(db, market_path):
    db.execute("""CREATE OR REPLACE TEMP TABLE financial_daily AS
        SELECT m.ts_code,m.trade_date,m.is_mainboard_code,m.total_mv_cny,
          b.period AS balance_period,b.disclosed AS balance_disclosed,b.version AS balance_version,
          coalesce(b.status,'no_eligible_report') AS balance_status,
          i.period AS income_period,i.disclosed AS income_disclosed,i.version AS income_version,
          coalesce(i.status,'no_eligible_report') AS income_status,
          c.period AS cashflow_period,c.disclosed AS cashflow_disclosed,c.version AS cashflow_version,
          coalesce(c.status,'no_eligible_report') AS cashflow_status,
          b.total_cur_assets,b.total_liab,i.n_income,i.n_income_attr_p,c.n_cashflow_act,
          CASE WHEN b.period >= m.trade_date-INTERVAL 540 DAY THEN b.total_cur_assets-b.total_liab END AS ncav_cny,
          CASE WHEN b.period >= m.trade_date-INTERVAL 540 DAY AND b.total_cur_assets>b.total_liab
                AND m.total_mv_cny>0 THEN m.total_mv_cny/(b.total_cur_assets-b.total_liab) END AS market_to_ncav,
          false AS pit_certified
        FROM read_parquet(?,hive_partitioning=false) m
        ASOF LEFT JOIN balancesheet_events b ON m.ts_code=b.ts_code AND m.trade_date>=b.available
        ASOF LEFT JOIN income_events i ON m.ts_code=i.ts_code AND m.trade_date>=i.available
        ASOF LEFT JOIN cashflow_events c ON m.ts_code=c.ts_code AND m.trade_date>=c.available
    """,[str(market_path)])
    count, unique, future, ncav = db.execute("""SELECT count(*),count(DISTINCT(ts_code,trade_date)),
      count(*) FILTER (WHERE balance_disclosed>=trade_date OR income_disclosed>=trade_date OR cashflow_disclosed>=trade_date),
      count(*) FILTER (WHERE is_mainboard_code AND ncav_cny IS NOT NULL) FROM financial_daily""").fetchone()
    if count != unique or future:
        raise ValueError("Financial join violates grain/date")
    return {"rows":count,"unique":unique,"future_disclosures":future,"mainboard_ncav_candidates":ncav}


def build(raw, market, output, scratch, year, month):
    import duckdb
    with (output/".build.lock").open("a") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        db = duckdb.connect(config={"threads":"1","memory_limit":"512MB","temp_directory":str(scratch)})
        try:
            started=time.monotonic()
            markers = sorted(market.glob(f"year={year}/month={month:02d}/manifest.json" if month else f"year={year}/month=*/manifest.json"))
            if not markers or (month is None and len(markers)!=12):
                raise ValueError("Incomplete standardized historical market partitions")
            sources=cached_events(db,raw,output/".event-cache")
            for index,marker in enumerate(markers,1):
                partition_started=time.monotonic()
                progress("BT03_FINANCIAL_PARTITION_START",started,partition=index,total=len(markers),month=marker.parent.name)
                meta = read_json(marker)
                item = meta["outputs"]["market"]
                src = marker.parent / item["file"]
                if (meta.get("version")!=1 or meta.get("year")!=year or marker.parent.name!=f"month={meta['month']:02d}"
                    or meta.get("status")!="research_data_not_backtest_ready"
                    or item["file"]!=f"market-{item['sha256']}.parquet" or src.is_symlink()
                    or src.stat().st_size!=item["bytes"] or digest(src)!=item["sha256"]):
                    raise ValueError("Invalid standardized market partition")
                target=output/marker.parent.parent.name/marker.parent.name
                target.mkdir(parents=True,exist_ok=True)
                receipt=target/"manifest.json"
                fingerprint={"version":VERSION,"market_sha256":item["sha256"],"financial_sources":sources}
                if receipt.exists():
                    old=read_json(receipt)
                    name=old["file"]
                    if (old["inputs"]!=fingerprint or name!=f"financial-{old['sha256']}.parquet"
                        or digest(target/name)!=old["sha256"]):
                        raise ValueError("Existing financial output differs; use new version")
                    progress("BT03_FINANCIAL_REUSED",partition_started,path=str(target))
                    continue
                stats=daily_join(db,src)
                if stats["rows"]!=meta["quality"]["rows"] or digest(src)!=item["sha256"]:
                    raise ValueError("Financial join row count/input hash mismatch")
                with tempfile.TemporaryDirectory(dir=target) as tmp:
                    path=Path(tmp)/"data.parquet"
                    db.execute("COPY (SELECT * FROM financial_daily ORDER BY trade_date,ts_code) TO ? (FORMAT PARQUET,COMPRESSION ZSTD)",[str(path)])
                    with path.open("rb") as f:
                        os.fsync(f.fileno())
                    h=digest(path); name=f"financial-{h}.parquet"
                    os.replace(path,target/name);sync_dir(target)
                atomic_json(receipt,{"inputs":fingerprint,"file":name,"sha256":h,"quality":stats,
                    "status":"research_not_pit_certified","limitations":["historical_revision_completeness_unknown",
                    "no_certified_ROE","no_2026_disclosure_archive","income_cashflow_YTD_not_TTM"]})
                progress("BT03_FINANCIAL_PARTITION",partition_started,**stats)
        finally:
            db.close()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for arg in ("archive","market","output","scratch"):
        p.add_argument("--"+arg,type=Path,required=True)
    p.add_argument("--year",type=int,required=True,choices=range(2020,2026))
    p.add_argument("--month",type=int,choices=range(1,13))
    a=p.parse_args()
    paths=[getattr(a,k).resolve(strict=True) for k in ("archive","market","output","scratch")]
    if any(x==Path("/") for x in paths) or any(x==y or x in y.parents or y in x.parents for i,x in enumerate(paths) for y in paths[i+1:]):
        p.error("Use disjoint existing directories")
    os.umask(0o027)
    build(paths[0]/RAW_PREFIX,*paths[1:],a.year,a.month)


if __name__=="__main__":
    main()
