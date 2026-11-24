"""Build versioned monthly market reader snapshots from verified daily raw partitions.

Daily sources and their manifests are retained. The monthly manifest is the
atomic publication pointer, never a completed-year historical manifest.
"""
from __future__ import annotations
from calendar import monthrange
from datetime import date
import hashlib
from pathlib import Path
import tempfile
from .archive_paths import RAW_PREFIX
from .neon_archive import verify_object
from .phase2_market import get_optional_json, put_manifest
from .raw_consolidation import publish_file

CHART_DATASETS = ("daily_prices", "adj_factor")


def month_root(dataset, year, month):
    return f"{RAW_PREFIX}/{dataset}/year={year}/month={month:02d}"


def publish_month(s3, bucket, client, through):
    import duckdb
    from .raw_incremental import MARKET, FIELDS, day_stem, sessions
    year, month = through.year, through.month
    first = date(year,month,1)
    previous = {}
    for dataset in CHART_DATASETS:
        key = month_root(dataset,year,month)+"/reader.manifest.json"
        doc = get_optional_json(s3,bucket,key)
        if doc:
            previous[dataset] = doc
            through = max(through,date.fromisoformat(doc["through"]))
    expected = sessions(client,first,through)
    day_docs = []
    for d in expected:
        key = f"{RAW_PREFIX}/_incremental/market/year={year}/day={d:%Y%m%d}.json"
        doc = get_optional_json(s3,bucket,key)
        if doc is None:
            continue
        if doc.get("status")!="verified_market_day" or doc.get("trade_date")!=d.isoformat():
            raise RuntimeError("Invalid daily source manifest")
        parts = {x["dataset"]:x for x in doc["datasets"]}
        if set(parts)!=set(MARKET) or len(doc["datasets"])!=len(MARKET):
            raise RuntimeError("Incomplete daily source manifest")
        day_docs.append((d,parts))
    if not day_docs:
        print(f"RAW_MONTH_SKIP year={year} month={month:02d} no_verified_days",flush=True)
        return
    dates=[d.isoformat() for d,_ in day_docs]
    expected_dates=[d.isoformat() for d in expected]
    missing=sorted(set(expected_dates)-set(dates))
    for dataset in CHART_DATASETS:
        root=month_root(dataset,year,month)
        pointer=root+"/reader.manifest.json"
        source=[parts[dataset] for _,parts in day_docs]
        for (d,_),p in zip(day_docs,source):
            if p.get("trade_date")!=d.isoformat() or p.get("status")!="verified_daily_partition" or p.get("object_key")!=day_stem(dataset,d)+".parquet" or p.get("fields")!=list(FIELDS[dataset]):
                raise RuntimeError("Invalid daily partition reference")
        source_hashes=[p["sha256"] for p in source]
        rows=sum(p["rows"] for p in source)
        old=previous.get(dataset)
        if old and old.get("source_sha256")==source_hashes and old.get("dates")==dates and old.get("expected_dates")==expected_dates and old.get("through")==through.isoformat():
            verify_object(s3,bucket,old["object_key"],old["bytes"],old["sha256"])
            print(f"RAW_MONTH dataset={dataset} year={year} month={month:02d} sessions={len(dates)} rows={rows} status=reused_verified",flush=True)
            continue
        with tempfile.TemporaryDirectory(prefix="raw-month-reader-") as tmp:
            paths=[]
            for i,p in enumerate(source):
                path=Path(tmp)/f"part-{i}.parquet"
                verify_object(s3,bucket,p["object_key"],p["bytes"],p["sha256"],path)
                paths.append(str(path))
            target=Path(tmp)/"month.parquet"
            quoted=str(target).replace("'","''")
            with duckdb.connect() as db:
                # Parameterize input paths via relation, not string interpolation.
                db.read_parquet(paths).order("ts_code,trade_date").write_parquet(str(target),compression="zstd")
                count,unique=db.execute("SELECT count(*),count(DISTINCT (ts_code,trade_date)) FROM read_parquet(?)",[str(target)]).fetchone()
                actual_dates=[x[0].isoformat() for x in db.execute("SELECT DISTINCT trade_date FROM read_parquet(?) ORDER BY trade_date",[str(target)]).fetchall()]
            if count!=rows or unique!=rows or actual_dates!=dates:
                raise RuntimeError("Monthly snapshot rows, keys or dates do not reconcile")
            sha=hashlib.sha256(target.read_bytes()).hexdigest()
            key=root+f"/reader/sha256={sha}.parquet"
            size=target.stat().st_size
            publish_file(s3,bucket,key,target,sha,size,"application/octet-stream")
            doc={"format_version":1,"status":"verified_month_reader","dataset":dataset,"year":year,
                 "month":f"{month:02d}","through":through.isoformat(),"dates":dates,
                 "expected_dates":expected_dates,"missing_dates":missing,"rows":rows,
                 "fields":list(FIELDS[dataset]),"object_key":key,"bytes":size,"sha256":sha,
                 "source_sha256":source_hashes}
            put_manifest(s3,bucket,pointer,doc)
        print(f"RAW_MONTH dataset={dataset} year={year} month={month:02d} sessions={len(dates)} expected={len(expected_dates)} rows={rows} status=published",flush=True)
