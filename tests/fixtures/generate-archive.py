"""Synthetic archive fixtures only; never access production data or network."""
import calendar
import datetime as dt
import gzip
import hashlib
import json
import sys
from pathlib import Path
import duckdb

root = Path(sys.argv[1]) / "a-share-backtest/01_raw_data"
prefix = "a-share-backtest/01_raw_data"
code = "000001.SZ"
db = duckdb.connect()
db.execute("SET threads=1")

def write(rel, value):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(value if isinstance(value, bytes) else json.dumps(value).encode())
    return p

def parquet(rel, dates, dataset):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    db.execute("DROP TABLE IF EXISTS fixture")
    if dataset == "daily_prices":
        fields = ["ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount"]
        db.execute("CREATE TABLE fixture(ts_code VARCHAR, trade_date VARCHAR, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, vol DOUBLE, amount DOUBLE)")
        db.executemany("INSERT INTO fixture VALUES (?,?,?,?,?,?,?,?)", [(code,d,10,12,9,11,100,1100) for d in dates])
    else:
        fields = ["ts_code", "trade_date", "adj_factor"]
        db.execute("CREATE TABLE fixture(ts_code VARCHAR, trade_date VARCHAR, adj_factor DOUBLE)")
        db.executemany("INSERT INTO fixture VALUES (?,?,?)", [(code,d,2) for d in dates])
    db.execute("COPY fixture TO '" + str(p).replace("'", "''") + "' (FORMAT PARQUET, COMPRESSION ZSTD)")
    raw = p.read_bytes()
    return len(raw), hashlib.sha256(raw).hexdigest(), fields

def days(year, month, end=None):
    limit=calendar.monthrange(year,month)[1]
    return [d.isoformat() for n in range(1,limit+1) if (d:=dt.date(year,month,n)).weekday()<5 and (end is None or d<=end)]

annual_prices=[]
annual_factors=[]
for month in range(1,13):
    m=f"{month:02d}"
    dates=days(2025,month)
    for dataset in ("daily_prices","adj_factor"):
        rel=f"{dataset}/year=2025/month={m}.parquet"
        size,sha,fields=parquet(rel,dates,dataset)
        if dataset=="daily_prices":
            annual_prices.append(dict(path=rel,month=m,bytes=size,sha256=sha,rows=len(dates)))
        else:
            annual_factors.append(dict(format_version=1,dataset=dataset,year=2025,month=m,object_key=f"{prefix}/{rel}",fields=fields,parquet_bytes=size,rows=len(dates),sessions=len(dates),sha256=sha))
write("daily_prices/year=2025/manifest.json",dict(format_version=1,dataset="a_share_daily",adjustment="unadjusted",year=2025,partition_files=annual_prices,rows=sum(x["rows"] for x in annual_prices),sessions=sum(x["rows"] for x in annual_prices)))
write("adj_factor/year=2025/manifest.json",dict(format_version=1,dataset="adj_factor",year=2025,months=annual_factors,rows=sum(x["rows"] for x in annual_factors),sessions=sum(x["sessions"] for x in annual_factors)))

today=dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date()
# Most recent eight months, plus January 2026 for the fixed-year test.
months={(2026,1)}
for i in range(8):
    index=today.year*12+today.month-1-i
    year,month=divmod(index,12)
    if year>=2026: months.add((year,month+1))
for year,month in sorted(months):
    dates=days(year,month,today)
    if not dates: continue
    m=f"{month:02d}"
    for dataset in ("daily_prices","adj_factor"):
        stem=f"{dataset}/year={year}/month={m}"
        tmp=f"{stem}/reader/fixture.parquet"
        size,sha,fields=parquet(tmp,dates,dataset)
        rel=f"{stem}/reader/sha256={sha}.parquet"
        (root/tmp).rename(root/rel)
        write(f"{stem}/reader.manifest.json",dict(format_version=1,status="verified_month_reader",dataset=dataset,year=year,month=m,through=dates[-1],dates=dates,expected_dates=dates,missing_dates=[],rows=len(dates),object_key=f"{prefix}/{rel}",bytes=size,sha256=sha,fields=fields,source_sha256=["a"*64]*len(dates)))

write("stock_basic/stock_basic_snapshot.json",dict(status="current_snapshot_not_historical",records=[dict(ts_code=code)]))
digest=hashlib.sha256(json.dumps([code]).encode()).hexdigest()
for kind,extra in [("balancesheet",dict(total_assets=100,total_liab=40,total_cur_assets=60)),("income",dict(revenue=80,n_income=10)),("cashflow",dict(n_cashflow_act=12)),("fina_indicator",dict(roe=10,ann_date="20241030"))]:
    rel=f"financial/{kind}/start=00000_count=050"
    record=dict(ts_code=code,end_date="20240930",f_ann_date="20241030",report_type="1",**extra)
    raw=gzip.compress((json.dumps(record)+"\n").encode())
    write(rel+".jsonl.gz",raw)
    write(rel+".manifest.json",dict(format_version=1,kind=kind,start_index=0,requested_codes=1,codes_sha256=digest,object_key=f"{prefix}/{rel}.jsonl.gz",status="research_not_pit_certified",rows=1,compressed_bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest()))
hash_="b"*64
write(f"historical_st/v2/range=2020-2025/universe={hash_}/merged/manifest.json",dict(source="merged",universe_sha256=hash_,rows=2,year_manifests=[dict(year=2025,rows=2,resolution_counts=dict(normal=1,unknown=1))]))
db.close()
