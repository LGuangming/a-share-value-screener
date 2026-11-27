"""BT05: run continuous portfolios from immutable BT04 signal shards and BT03.

Read one calendar partition at a time; retain only daily bars, listing-age
counters and portfolio state. Publish a hash-linked monthly checkpoint.
No production DB, network or formal performance publication.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict
from datetime import date
import fcntl
import hashlib
import json
from pathlib import Path
import resource
import time
from tempfile import TemporaryDirectory
from math import isfinite
from . import bt04_research as bt04, bt05_portfolio, backtest_engine, technical_strategies, technical_research
from .backtest_engine import Bar, BacktestConfig
from .bt05_portfolio import Portfolio,FeePeriod,FeeSchedule
from . import bt05_actions
from .bt05_actions import Actions
from .technical_strategies import TREND,REVERSAL,MAINBOARD
from .technical_research import sha256,write_new

STATUS="BT05_ENGINEERING_ONLY_NOT_CERTIFIED"
STRATEGIES=(TREND,REVERSAL)

def identity(doc):
    return hashlib.sha256(json.dumps(doc,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()

def signal_store(db,root,contract,sessions):
    summary=bt04.json_file(root/"summary.json")
    rid=contract["run_id"]
    if (summary.get("run_id")!=rid or summary.get("shards_completed")!=contract["shards"]
            or summary.get("status")!=bt04.STATUS):
        raise ValueError("BT04 signals are not complete")
    db.execute("CREATE TEMP TABLE signals(strategy VARCHAR,day DATE,code VARCHAR,score DOUBLE)")
    hashes=[];counts={k:0 for k in STRATEGIES};total_rows=0
    def insert(batch):
        if batch:
            columns=list(zip(*batch))
            db.execute("INSERT INTO signals SELECT unnest(?::VARCHAR[]),unnest(?::DATE[]),unnest(?::VARCHAR[]),unnest(?::DOUBLE[])",[list(c) for c in columns])
    allowed=set(sessions);start=date.fromisoformat(contract["start"]);end=date.fromisoformat(contract["end"])
    for shard in range(contract["shards"]):
        marker=root/f"shard-{shard:03d}.json";p=root/f"shard-{shard:03d}.jsonl"
        receipt_hash=sha256(marker);doc=bt04.json_file(marker)
        if (doc.get("run_id")!=rid or doc.get("shard")!=shard or doc.get("file")!=p.name or
                p.is_symlink() or sha256(p)!=doc["sha256"]):
            raise ValueError("Invalid BT04 shard receipt or data")
        seen={k:0 for k in STRATEGIES};batch=[]
        with p.open(encoding="utf-8") as f:
            for line in f:
                x=json.loads(line);day=date.fromisoformat(x["day"]);code=x["ts_code"];k=x["strategy_id"];score=x["score"]
                if (k not in STRATEGIES or not MAINBOARD.fullmatch(code) or
                    int(code[:6])%contract["shards"]!=shard or day not in allowed or not start<=day<=end
                    or type(score) not in (float,int) or not isfinite(score)):
                    raise ValueError("Invalid BT04 signal")
                seen[k]+=1;batch.append((k,day,code,score))
                if len(batch)>=4096:
                    insert(batch);batch.clear()
        if batch:insert(batch)
        if seen!=doc["signal_counts"] or sha256(p)!=doc["sha256"] or sha256(marker)!=receipt_hash:
            raise ValueError("BT04 shard changed or count mismatch")
        total_rows+=doc["rows"]
        for k in STRATEGIES:counts[k]+=seen[k]
        hashes.append({"shard":shard,"receipt":receipt_hash,"data":doc["sha256"]})
    if counts!=summary["signal_counts"] or total_rows!=summary["rows"]:
        raise ValueError("BT04 summary counts mismatch")
    n,u=db.execute("SELECT count(*),count(DISTINCT(strategy,day,code)) FROM signals").fetchone()
    if n!=u:raise ValueError("Duplicate signal")
    return hashes

def day_rows(db,mp,sp,lo,hi):
    # Bounded by one BT03 partition (month for historical data, day for incremental).
    cur=db.execute("""
      SELECT m.ts_code,m.trade_date,m.open,m.high,m.low,m."close",m.vol,
       m.adj_factor,m.up_limit,m.down_limit,m.factor_valid,m.limits_valid,m.ohlcv_valid,
       m.suspend_event_count,m.st_normal_evidence,m.st_ann_date,
       s.listing_evidence,s.historical_st_type,s.st_evidence
      FROM read_parquet(?,hive_partitioning=false) m
      JOIN read_parquet(?,hive_partitioning=false) s USING(ts_code,trade_date)
      WHERE regexp_full_match(m.ts_code,?) AND m.trade_date BETWEEN ? AND ?
      ORDER BY m.trade_date,m.ts_code
    """,[mp,sp,bt04.MAIN_SQL,lo,hi])
    while rows:=cur.fetchmany(4096):
        yield from rows

def partition_days(db,mp,sp,days,ages,min_age):
    iterator=iter(day_rows(db,mp,sp,days[0],days[-1]))
    row=next(iterator,None)
    for day in days:
        bars={};factors={};eligible=set()
        while row is not None and row[1]==day:
            (code,d,o,h,l,c,vol,f,up,down,factor_ok,limit_ok,ohlcv_ok,events,
             normal,ann,listing,st,evidence)=row
            if code in bars or not ohlcv_ok or events is None or events<0:
                raise ValueError("Invalid execution bar")
            suspended=events>0 or vol<=0
            # Open-time listing-age lower bound excludes today's traded bar.
            age=ages.get(code,0)
            if (age>=min_age and listing=="within_snapshot_listing_dates_not_certified" and
                    st=="NORMAL" and evidence=="historical_merged_not_certified" and
                    normal is True and ann is not None and ann<d and limit_ok and not suspended):
                eligible.add(code)
            if not suspended:ages[code]=age+1
            bars[code]=Bar(code,d,o,h,l,c,vol,up if limit_ok else None,down if limit_ok else None,suspended)
            factors[code]=f if factor_ok else None
            row=next(iterator,None)
        if row is not None and row[1]<day:raise ValueError("Unexpected market day")
        yield day,bars,eligible,factors
    if row is not None:raise ValueError("Unconsumed market rows")

def rankings(db,lo,hi):
    result={}
    for k,day,code in db.execute("""SELECT strategy,day,code FROM signals
        WHERE day BETWEEN ? AND ? ORDER BY day,strategy,score DESC,code""",[lo,hi]).fetchall():
        result.setdefault(day,{}).setdefault(k,[]).append(code)
    return result

def load_fees(path,cfg,start,end):
    if path is None:return FeeSchedule.fixed(cfg),None
    h=sha256(path);doc=bt04.json_file(path)
    if doc.get("status")!="OPERATOR_ASSUMPTIONS_NOT_CERTIFIED" or date.fromisoformat(doc["through"])<end:
        raise ValueError("Fee assumptions lack declared coverage")
    fees=FeeSchedule([FeePeriod(**{**p,"start":date.fromisoformat(p["start"])}) for p in doc["periods"]])
    fees.at(start)
    if sha256(path)!=h:raise ValueError("Fee input changed")
    return fees,h

def verify_checkpoint(path,expected,prior):
    doc=bt04.json_file(path)
    body=doc["body"]
    if doc["sha256"]!=identity(body) or body["run_id"]!=expected or body["previous_hash"]!=prior:
        raise ValueError("Checkpoint hash or chain mismatch")
    return body,doc["sha256"]

def run(args):
    market,security,signals,scratch,out=(getattr(args,k).resolve(strict=True) for k in
            ("market","security","signals","scratch","output"))
    roots=(market,security,signals,scratch,out)
    if any(a==b or a.is_relative_to(b) or b.is_relative_to(a) for i,a in enumerate(roots) for b in roots[i+1:]):
        raise ValueError("BT05 source, scratch, signal and output roots must be disjoint")
    if not args.start<args.end:raise ValueError("Invalid execution interval")
    cfg=BacktestConfig(**(bt04.json_file(args.config) if args.config else {}))
    lock=out/".bt05.lock"
    if lock.is_symlink():raise ValueError("Invalid lock")
    with lock.open("a") as handle:
        fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return run_locked(args,market,security,signals,scratch,out,cfg)

def run_locked(args,market,security,signals,scratch,out,cfg):
    source_hash=sha256(signals/"run.json");summary_hash=sha256(signals/"summary.json")
    source=bt04.json_file(signals/"run.json")
    if (source.get("status")!=bt04.STATUS or source.get("eligibility_policy")!="provisional_evidence"
            or source.get("formal_backtest_ready") is not False):
        raise ValueError("BT05 requires completed provisional BT04 research signals")
    if source["run_id"]!=identity({k:v for k,v in source.items() if k!="run_id"}):
        raise ValueError("BT04 run identity mismatch")
    warmup=date.fromisoformat(source["warmup"]);source_end=date.fromisoformat(source["end"])
    if not date.fromisoformat(source["start"])<=args.start<args.end<=source_end:
        raise ValueError("Execution outside BT04 signal interval")
    if sha256(args.calendar)!=source["calendar_sha256"]:raise ValueError("BT04 calendar mismatch")
    sessions=bt04.read_calendar(args.calendar,warmup,source_end)
    fees,fee_hash=load_fees(args.fees,cfg,args.start,args.end)
    action_path=getattr(args,'actions',None)
    actions,action_hash=Actions.load(action_path,sessions,args.start,args.end)
    import duckdb
    staging=TemporaryDirectory(dir=scratch,prefix="bt05-")
    try:
        db=duckdb.connect(str(Path(staging.name)/"signals.duckdb"),
            config={"threads":"1","memory_limit":"384MB","temp_directory":staging.name,
                    "preserve_insertion_order":"false"})
    except BaseException:
        staging.cleanup();raise
    try:
        mf,sf,inputs=bt04.receipts(db,market,security,sessions)
        if inputs!=source["inputs"]:raise ValueError("BT03 inputs differ from BT04")
        hashes=signal_store(db,signals,source,sessions)
        contract={"version":1,"status":STATUS,"formal_backtest_ready":False,
            "bt04_run_id":source["run_id"],"signals":hashes,"inputs":inputs,
            "start":args.start.isoformat(),"end":args.end.isoformat(),"config":asdict(cfg),
            "fee_periods":fees.record(),"fee_input_sha256":fee_hash,
            "action_input_sha256":action_hash,"action_policy":"explicit_net_cash_and_integer_shares_only" if action_path else "factor_gate_only",
            "code_sha256":{Path(p).name:sha256(Path(p)) for p in
                (__file__,bt05_portfolio.__file__,bt05_actions.__file__,bt04.__file__,backtest_engine.__file__,technical_strategies.__file__,technical_research.__file__)},
            "limitations":["provisional_historical_universe_and_tradeability","operator_fee_assumptions_not_certified",
              "unsupported_or_unreconciled_corporate_action_blocks_execution","net_dividend_assumptions_not_certified",
              "no_rights_fractional_delayed_shares_or_delisting_accounting",
              "no_independent_calendar_certification"]}
        rid=identity(contract);manifest=out/"run.json";record={"run_id":rid,**contract}
        if manifest.exists():
            if bt04.json_file(manifest)!=record:raise ValueError("BT05 run identity changed; use new directory")
        else:write_new(manifest,record)
        groups=list(bt04.expected(sessions).items())
        # Checkpoint each calendar month, even when the incremental source is daily.
        months={}
        for i,(rel,days) in enumerate(groups):
            selected=[d for d in days if d<=args.end]
            if selected:months.setdefault(selected[0].strftime("%Y-%m"),[]).append((i,selected))
        portfolios={k:Portfolio(cfg,fees,actions) for k in STRATEGIES}
        ages={};previous_hash=None;completed=0;rows_seen=0;checkpoint_refs=[]
        for month,parts in months.items():
            marker=out/f"month-{month}.json"
            days=[d for _,ds in parts for d in ds]
            exec_days=[d for d in days if args.start<=d<=args.end]
            if marker.exists():
                body,h=verify_checkpoint(marker,rid,previous_hash)
                if body["month"]!=month or body["sessions"]!=[d.isoformat() for d in exec_days]:
                    raise ValueError("Checkpoint period mismatch")
                ages=dict(body["ages"])
                portfolios={k:Portfolio.restore(cfg,fees,body["portfolios"][k],actions) for k in STRATEGIES}
                rows_seen=body["rows_seen"]
                if any(p.index+1!=completed+len(exec_days) for p in portfolios.values()):
                    raise ValueError("Checkpoint session count mismatch")
            else:
                # Later checkpoints cannot be reused after a missing predecessor.
                if any(p.name>marker.name for p in out.glob("month-*.json")):
                    raise ValueError("Missing earlier checkpoint")
                started=time.monotonic();records={k:[] for k in STRATEGIES}
                ranked=rankings(db,days[0],days[-1])
                for i,ds in parts:
                    for day,bars,eligible,factors in partition_days(db,mf[i],sf[i],ds,ages,
                                                        source["technical_config"]["min_listing_sessions"]):
                        rows_seen+=len(bars)
                        if day<args.start:continue
                        for k,p in portfolios.items():
                            records[k].append(p.step(day,bars,eligible,factors,ranked.get(day,{}).get(k,[])))
                body={"run_id":rid,"month":month,"previous_hash":previous_hash,
                    "sessions":[d.isoformat() for d in exec_days],"rows_seen":rows_seen,
                    "ages":ages,"portfolios":{k:p.checkpoint() for k,p in portfolios.items()},
                    "records":records}
                h=identity(body);write_new(marker,{"body":body,"sha256":h})
                print("BT05_MONTH "+json.dumps({"month":month,"sessions":len(exec_days),
                       "seconds":round(time.monotonic()-started,3),
                       "peak_rss_kib":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}),flush=True)
            previous_hash=h;completed+=len(exec_days);checkpoint_refs.append({"file":marker.name,"sha256":h})
        expected_days=[d for d in sessions if args.start<=d<=args.end]
        if completed!=len(expected_days) or completed<2:raise ValueError("Execution session coverage mismatch")
        _,_,checked=bt04.receipts(db,market,security,sessions)
        if (checked!=inputs or sha256(args.calendar)!=source["calendar_sha256"] or (args.fees and sha256(args.fees)!=fee_hash)
                or (action_path and sha256(action_path)!=action_hash) or sha256(signals/"run.json")!=source_hash or sha256(signals/"summary.json")!=summary_hash):
            raise ValueError("Source inputs changed during execution")
        for sh in hashes:
            n=sh["shard"]
            if sha256(signals/f"shard-{n:03d}.json")!=sh["receipt"] or sha256(signals/f"shard-{n:03d}.jsonl")!=sh["data"]:
                raise ValueError("Signal inputs changed during execution")
        summary={"run_id":rid,"status":STATUS,"formal_backtest_ready":False,
            "sessions":completed,"rows_seen_including_warmup":rows_seen,"checkpoints":checkpoint_refs,
            "results":{k:{"metrics":p.metrics(),"final_positions":p.checkpoint()["holdings"],
                          "final_cash":p.cash,"final_dividend_receivables":p.checkpoint()["receivables"]} for k,p in portfolios.items()}}
        dest=out/"summary.json"
        if dest.exists():
            if bt04.json_file(dest)!=summary:raise ValueError("BT05 summary differs")
        else:write_new(dest,summary)
        print("BT05_SUMMARY "+json.dumps({"status":STATUS,"sessions":completed,"rows_seen":rows_seen}),flush=True)
        return summary
    finally:
        db.close();staging.cleanup()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ("market","security","signals","scratch","output","calendar"):
        p.add_argument("--"+name,type=Path,required=True)
    for name in ("start","end"):p.add_argument("--"+name,type=date.fromisoformat,required=True)
    p.add_argument("--config",type=Path)
    p.add_argument("--fees",type=Path)
    p.add_argument("--actions",type=Path)
    run(p.parse_args())

if __name__=="__main__":main()
