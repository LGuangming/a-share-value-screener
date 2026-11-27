"""BT04 offline adapter for BT03 published market/security receipts.

Evidence-only research. No raw archive modification, network or production DB.
DuckDB reads exact receipt-named files, sorts per code shard with bounded memory.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict
from datetime import date
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import time
from tempfile import NamedTemporaryFile
from .backtest_engine import Bar, BacktestConfig, run_backtest
from .technical_strategies import (Observation, TechnicalConfig, MAINBOARD, TREND,
                                   REVERSAL, generate_signals, signal_record)
from .technical_research import sha256, write_new

VERSION = 1
STATUS = "BT04_RESEARCH_ONLY_NOT_CERTIFIED"
HEX = re.compile(r"[0-9a-f]{64}\Z")
MAIN_SQL = r"(600|601|603|605)[0-9]{3}[.]SH|(000|001|002|003)[0-9]{3}[.]SZ"

def json_file(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Missing or symlink receipt: {path}")
    return json.loads(path.read_text(encoding="utf-8"))

def under(root, path):
    if path.resolve()!=path.absolute() or not path.resolve().is_relative_to(root):
        raise ValueError(f"Unexpected path or symlink: {path}")
    return path

def output_file(root, folder, item, prefix):
    h=item["sha256"]
    if not isinstance(h,str) or not HEX.fullmatch(h) or item["file"]!=f"{prefix}-{h}.parquet":
        raise ValueError("Invalid output identity")
    p=under(root,folder/item["file"])
    if not p.is_file() or sha256(p)!=h or ("bytes" in item and p.stat().st_size!=item["bytes"]):
        raise ValueError(f"Output checksum/size mismatch: {p}")
    return p

def read_calendar(path, warmup, end):
    doc=json_file(path)
    if not isinstance(doc,dict) or not isinstance(doc.get("sessions"),list):
        raise ValueError("Calendar must contain an ordered sessions array")
    sessions=[date.fromisoformat(x) for x in doc["sessions"]]
    if not sessions or sessions!=sorted(set(sessions)):
        raise ValueError("Calendar sessions must be unique and sorted")
    if date.fromisoformat(doc["from"])>warmup or date.fromisoformat(doc["to"])<end:
        raise ValueError("Calendar does not cover requested interval")
    if sessions[0]<date.fromisoformat(doc["from"]) or sessions[-1]>date.fromisoformat(doc["to"]):
        raise ValueError("Session outside calendar declared coverage")
    selected=[d for d in sessions if warmup<=d<=end]
    if not selected:
        raise ValueError("No calendar sessions")
    return selected

def expected(sessions):
    groups={}
    for d in sessions:
        rel=Path(f"year={d.year}/month={d.month:02d}")
        if d.year>=2026:
            rel=rel/f"day={d:%Y%m%d}"
        groups.setdefault(rel,[]).append(d)
    return groups

def connect(scratch):
    import duckdb
    return duckdb.connect(config={"threads":"1","memory_limit":"384MB",
                                  "temp_directory":str(scratch),"preserve_insertion_order":"false"})

def receipts(db, market, security, sessions):
    market_files=[];security_files=[];identities=[]
    for rel,days in expected(sessions).items():
        mf=under(market,market/rel/"manifest.json")
        sf=under(security,security/rel/"manifest.json")
        mh,sh=sha256(mf),sha256(sf)
        m,s=json_file(mf),json_file(sf)
        first=days[0]
        if (m.get("version")!=1 or m.get("status")!="research_data_not_backtest_ready"
                or m.get("year")!=first.year or m.get("month")!=first.month
                or (first.year>=2026 and m.get("trade_date")!=first.isoformat())
                or (first.year<2026 and "trade_date" in m)
                or s.get("version")!=1 or s.get("status")!="security_evidence_not_certified"
                or s["inputs"]["market_sha256"]!=m["outputs"]["market"]["sha256"]):
            raise ValueError(f"BT03 receipt mismatch: {rel}")
        mp=output_file(market,mf.parent,m["outputs"]["market"],"market")
        sp=output_file(security,sf.parent,s,"security")
        ep=output_file(market,mf.parent,m["outputs"]["suspension_events"],"suspension_events")
        for file,rows in ((mp,m["quality"]["rows"]),(sp,s["quality"]["rows"])):
            n,u,bad=db.execute("""SELECT count(*),count(DISTINCT(ts_code,trade_date)),
               count(*) FILTER (WHERE ts_code IS NULL OR trade_date IS NULL
               OR year(trade_date)!=? OR month(trade_date)!=?)
               FROM read_parquet(?,hive_partitioning=false)""",
               [first.year,first.month,str(file)]).fetchone()
            if n!=u or n!=rows or bad or n<=0:
                raise ValueError(f"Invalid partition keys/count: {file}")
            if first.year>=2026 and db.execute("SELECT count(*) FROM read_parquet(?) WHERE trade_date!=?",[str(file),first]).fetchone()[0]:
                raise ValueError("Incremental date mismatch")
        if m["quality"]["rows"]!=s["quality"]["rows"] or db.execute("""
           SELECT count(*) FROM (SELECT ts_code,trade_date FROM read_parquet(?)
            EXCEPT SELECT ts_code,trade_date FROM read_parquet(?))""",[str(mp),str(sp)]).fetchone()[0]:
            raise ValueError("Market/security keys do not reconcile")
        if db.execute("SELECT count(*) FROM read_parquet(?) WHERE st_certified IS DISTINCT FROM false",[str(sp)]).fetchone()[0]:
            raise ValueError("Unexpected certified-state claim in BT03 research evidence")
        if db.execute("SELECT count(*) FROM read_parquet(?)",[str(ep)]).fetchone()[0]!=m["quality"]["suspension_event_rows"]:
            raise ValueError("Suspension-event row count mismatch")
        lo,hi=days[0],days[-1]
        actual=[r[0] for r in db.execute("""SELECT DISTINCT trade_date FROM read_parquet(?)
                       WHERE trade_date BETWEEN ? AND ? ORDER BY trade_date""",[str(mp),lo,hi]).fetchall()]
        if actual!=days:
            raise ValueError(f"Market/calendar dates mismatch: {rel}")
        if sha256(mf)!=mh or sha256(sf)!=sh:
            raise ValueError("Receipt changed during verification")
        market_files.append(str(mp));security_files.append(str(sp))
        identities.append({"partition":rel.as_posix(),"market_receipt":mh,"security_receipt":sh,
                           "market":m["outputs"]["market"]["sha256"],"security":s["sha256"],
                           "events":m["outputs"]["suspension_events"]["sha256"]})
    return market_files,security_files,identities

def query(db, market_files, security_files, sessions, shard, shards, start, end, provisional):
    # The listing age is a conservative lower bound: count observed traded bars
    # since warmup, not an invented listing date or a current-name lookup.
    # Entire calendar gaps reset the factor state in generate_signals.
    indexes={d:i for i,d in enumerate(sessions)}
    cur=db.execute("""
      WITH m AS (
        SELECT ts_code,trade_date,open,high,low,"close",vol,amount,adj_factor,
          up_limit,down_limit,factor_valid,limits_valid,ohlcv_valid,
          suspend_event_count,st_normal_evidence,st_ann_date
        FROM read_parquet(?,hive_partitioning=false)
        WHERE regexp_full_match(ts_code,?) AND
          cast(substr(ts_code,1,6) AS BIGINT)%?=? AND trade_date BETWEEN ? AND ?
      ), s AS (
        SELECT ts_code,trade_date,listing_evidence,historical_st_type,st_evidence
        FROM read_parquet(?,hive_partitioning=false)
        WHERE regexp_full_match(ts_code,?) AND
          cast(substr(ts_code,1,6) AS BIGINT)%?=? AND trade_date BETWEEN ? AND ?
      )
      SELECT m.ts_code,m.trade_date,m.open,m.high,m.low,m."close",m.vol,m.amount,
             m.adj_factor,m.up_limit,m.down_limit,m.factor_valid,m.limits_valid,
             m.ohlcv_valid,m.suspend_event_count,m.st_normal_evidence,m.st_ann_date,
             s.listing_evidence,s.historical_st_type,s.st_evidence
      FROM m JOIN s USING(ts_code,trade_date)
      ORDER BY m.ts_code,m.trade_date
    """,[market_files,MAIN_SQL,shards,shard,start,end,
          security_files,MAIN_SQL,shards,shard,start,end])
    last=None;age=0
    while rows:=cur.fetchmany(4096):
        for row in rows:
            (code,d,o,h,l,c,vol,amount,factor,up,down,factor_ok,limit_ok,ohlcv_ok,
             events,normal,ann,listing,st,st_evidence)=row
            if d not in indexes or not ohlcv_ok or events is None or events<0:
                raise ValueError(f"Invalid normalized observation: {code} {d}")
            if code!=last:
                age=0;last=code
            # Any same-day suspension/resume event is conservatively excluded.
            suspended=events>0 or vol<=0
            if not suspended:
                age+=1
            evidence=(listing=="within_snapshot_listing_dates_not_certified" and
                      st=="NORMAL" and st_evidence=="historical_merged_not_certified" and
                      normal is True and ann is not None and ann<d)
            eligible=True if provisional and evidence and limit_ok else None
            bar=Bar(code,d,o,h,l,c,vol,up if limit_ok else None,down if limit_ok else None,suspended)
            # Tushare daily amount is in thousands of CNY.
            yield Observation(bar,factor if factor_ok else None,amount*1000,eligible,age,indexes[d])

def append_shard(db, folder, identity, shard, rows, cfg, start, end):
    marker=folder/f"shard-{shard:03d}.json"
    data=folder/f"shard-{shard:03d}.jsonl"
    if marker.exists():
        doc=json_file(marker)
        if (doc.get("run_id")!=identity or doc.get("shard")!=shard
                or doc.get("file")!=data.name or not data.is_file()
                or data.is_symlink() or sha256(data)!=doc.get("sha256")):
            raise ValueError("Existing shard receipt differs or output corrupted")
        return doc
    # Orphan data from a crash is not silently overwritten. Operator can
    # explicitly remove it after inspection, or select a new output directory.
    if data.exists():
        raise ValueError(f"Uncommitted shard output: {data}")
    counts={TREND:0,REVERSAL:0};seen=eligible=0
    began=time.monotonic()
    def stream():
        nonlocal seen,eligible
        for row in rows:
            seen+=1
            if row.eligible is True: eligible+=1
            yield row
    with NamedTemporaryFile(mode="w",encoding="utf-8",dir=folder,delete=False) as f:
        tmp=Path(f.name)
        try:
            for sig in generate_signals(stream(),cfg):
                if start<=sig.day<=end:
                    f.write(json.dumps(signal_record(sig),allow_nan=False,separators=(",",":"))+"\n")
                    counts[sig.strategy_id]+=1
            f.flush();os.fsync(f.fileno())
            h=sha256(tmp)
            os.link(tmp,data)
        finally:
            tmp.unlink(missing_ok=True)
    doc={"run_id":identity,"shard":shard,"file":data.name,"sha256":h,
         "rows":seen,"provisional_eligible_rows":eligible,"signal_counts":counts,
         "elapsed_seconds":round(time.monotonic()-began,3),
         "process_peak_rss_kib":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
    write_new(marker,doc)
    return doc

def prepare(args):
    market,security,scratch,folder=(getattr(args,k).resolve(strict=True) for k in
                                    ("market","security","scratch","output"))
    if any(a==b or a.is_relative_to(b) or b.is_relative_to(a)
           for i,a in enumerate((market,security,scratch,folder))
           for b in (market,security,scratch,folder)[i+1:]):
        raise ValueError("Market, security, scratch and result directories must be disjoint")
    if not date(2020,1,1)<=args.warmup<=args.start<=args.end<=date.today():
        raise ValueError("Invalid dates")
    if not 1<=args.shards<=64 or (args.shard is not None and not 0<=args.shard<args.shards):
        raise ValueError("Invalid shard selection")
    cfg=TechnicalConfig(**(json_file(args.config) if args.config else {}))
    calendar_hash=sha256(args.calendar)
    sessions=read_calendar(args.calendar,args.warmup,args.end)
    if sha256(args.calendar)!=calendar_hash:
        raise ValueError("Calendar changed")
    db=connect(scratch)
    try:
        market_files,security_files,identities=receipts(db,market,security,sessions)
        contract={"version":VERSION,"status":STATUS,"formal_backtest_ready":False,
                  "calendar_sha256":calendar_hash,"warmup":args.warmup.isoformat(),
                  "start":args.start.isoformat(),"end":args.end.isoformat(),
                  "shards":args.shards,"eligibility_policy":"provisional_evidence" if args.provisional else "strict_uncertified_blocked",
                  "technical_config":asdict(cfg),"inputs":identities,
                  "limitations":["calendar_supplied_by_operator_not_independently_certified",
                    "BT03_ST_and_listing_evidence_not_certified","listing_age_observed_bar_lower_bound",
                    "suspension_event_absence_not_certified","corporate_actions_not_accounted"]}
        identity=hashlib.sha256(json.dumps(contract,sort_keys=True,separators=(",",":")).encode()).hexdigest()
        manifest=folder/"run.json"
        expected_doc={"run_id":identity,**contract}
        if manifest.exists():
            if json_file(manifest)!=expected_doc:
                raise ValueError("Research run parameters or inputs changed; use new output directory")
        else:
            write_new(manifest,expected_doc)
        return db,folder,cfg,sessions,market_files,security_files,identity
    except BaseException:
        db.close();raise

def run(args):
    folder=args.output.resolve(strict=True)
    # Validate paths before creating even the lock.
    roots=[args.market.resolve(strict=True),args.security.resolve(strict=True),args.scratch.resolve(strict=True)]
    if any(folder==r or folder.is_relative_to(r) or r.is_relative_to(folder) for r in roots):
        raise ValueError("Result directory overlaps source or scratch")
    lock_path=folder/".bt04.lock"
    if lock_path.is_symlink():
        raise ValueError("Invalid result lock")
    with lock_path.open("a") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        db,folder,cfg,sessions,mf,sf,identity=prepare(args)
        try:
            docs=[]
            for shard in ([args.shard] if args.shard is not None else range(args.shards)):
                rows=query(db,mf,sf,sessions,shard,args.shards,args.warmup,args.end,args.provisional)
                doc=append_shard(db,folder,identity,shard,rows,cfg,args.start,args.end)
                docs.append(doc)
                print("BT04_SHARD "+json.dumps(doc,sort_keys=True),flush=True)
            if args.shard is None:
                summary={"run_id":identity,"status":STATUS,"shards_completed":len(docs),
                         "rows":sum(d["rows"] for d in docs),
                         "signal_counts":{k:sum(d["signal_counts"][k] for d in docs) for k in (TREND,REVERSAL)}}
                p=folder/"summary.json"
                if p.exists():
                    if json_file(p)!=summary: raise ValueError("Summary mismatch")
                else: write_new(p,summary)
                print("BT04_SUMMARY "+json.dumps(summary),flush=True)
        finally:
            db.close()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ("market","security","scratch","output","calendar"):
        p.add_argument("--"+name,type=Path,required=True)
    for name in ("warmup","start","end"):
        p.add_argument("--"+name,type=date.fromisoformat,required=True)
    p.add_argument("--shards",type=int,default=8)
    p.add_argument("--shard",type=int)
    p.add_argument("--config",type=Path)
    p.add_argument("--provisional",action="store_true",help="Explicit research-only use of non-certified historical evidence")
    run(p.parse_args())

if __name__=="__main__":
    main()
