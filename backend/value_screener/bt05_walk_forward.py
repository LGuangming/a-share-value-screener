"""BT05-D: independently reset walk-forward experiments, no production publication.

Train-only signal generation and selection; test signals are calculated only
for frozen configurations. Explicit, precommitted fold/parameter plan.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict
from datetime import date
import fcntl
import itertools
import json
from math import isfinite,sqrt
from pathlib import Path
from types import SimpleNamespace
from . import bt04_research as bt04,bt05_research as bt05,bt05_grid,bt05_portfolio,bt05_actions,technical_strategies,backtest_engine,technical_research
from .bt05_grid import put,select
from .technical_strategies import TechnicalConfig
from .backtest_engine import BacktestConfig
from .technical_research import sha256

STATUS="BT05_WALK_FORWARD_ENGINEERING_ONLY"

def plan(doc,sessions):
    if not isinstance(doc,dict):raise ValueError("Invalid experiment plan")
    folds=doc["folds"]
    if not 1<=len(folds)<=12:raise ValueError("Expected 1..12 folds")
    normalized=[];previous=None
    for row in folds:
        dates=[date.fromisoformat(row[k]) for k in ("train_start","train_end","test_start","test_end")]
        a,b,c,d=dates
        if not a<b<c<d or (previous and (a<previous[0] or b<=previous[1] or c<=previous[3])):
            raise ValueError("Fold dates must advance with nonoverlapping test windows")
        if any(sum(lo<=x<=hi for x in sessions)<2 for lo,hi in ((a,b),(c,d))):
            raise ValueError("Each window needs at least two sessions")
        normalized.append(dates);previous=dates
    signal=[asdict(TechnicalConfig(**x)) for x in doc["signal_configs"]]
    execution=[asdict(BacktestConfig(**x)) for x in doc["execution_configs"]]
    if not 1<=len(signal)<=8 or not 1<=len(execution)<=12 or len(signal)*len(execution)*len(folds)>96:
        raise ValueError("Grid cap: 8 signal, 12 execution, 96 total training trials")
    combos=[]
    for s,e in itertools.product(signal,execution):
        body={"signal":s,"execution":e}
        combos.append({"id":bt05.identity(body),**body})
    if len({x["id"] for x in combos})!=len(combos):raise ValueError("Duplicate configuration")
    min_trades=doc.get("min_trades",10);max_dd=doc.get("max_drawdown",.30)
    if type(min_trades) is not int or min_trades<1 or not isfinite(max_dd) or not 0<max_dd<=1:
        raise ValueError("Invalid training criteria")
    return normalized,combos,min_trades,max_dd

def benchmark(path,sessions):
    h=sha256(path);doc=bt04.json_file(path)
    if (doc.get("status")!="OPERATOR_REVIEWED_BENCHMARK_NOT_CERTIFIED"
            or doc.get("kind") not in ("price_index","total_return_index")
            or not all(isinstance(doc.get(k),str) and doc[k].strip() for k in ("code","source_ref"))):
        raise ValueError("Explicit benchmark type and provenance required")
    values={}
    for row in doc["levels"]:
        day=date.fromisoformat(row["date"]);v=row["close"]
        if day in values or type(v) not in (int,float) or not isfinite(v) or v<=0:
            raise ValueError("Invalid or duplicate benchmark observation")
        values[day]=float(v)
    if not set(sessions)<=values.keys():raise ValueError("Benchmark session coverage missing")
    if sha256(path)!=h:raise ValueError("Benchmark changed")
    return values,{"code":doc["code"],"kind":doc["kind"],"source_ref":doc["source_ref"],"sha256":h}

def comparison(directory,result,strategy,levels,sessions,initial):
    # Verify exact checkpoint chain and daily records instead of trusting a summary alone.
    equity=[];previous=None
    for ref in result["checkpoints"]:
        p=directory/ref["file"]
        body,h=bt05.verify_checkpoint(p,result["run_id"],previous)
        if h!=ref["sha256"]:raise ValueError("Checkpoint summary mismatch")
        previous=h
        equity.extend((date.fromisoformat(x["equity"]["day"]),x["equity"]["equity"])
                      for x in body["records"][strategy])
    if [d for d,v in equity]!=sessions:raise ValueError("Portfolio/benchmark session mismatch")
    if not isfinite(initial) or initial<=0 or not equity or abs(equity[0][1]-initial)>1e-6 or any(not isfinite(v) or v<0 for _,v in equity):
        raise ValueError("Invalid independent portfolio baseline")
    first=levels[sessions[0]];base=initial;prior=first;peak=1.;drawdown=0.;excess=[]
    curve=[]
    for d,v in equity:
        level=levels[d];nav=v/initial;bn=level/first
        peak=max(peak,bn);drawdown=min(drawdown,bn/peak-1)
        excess.append((v/base-1 if base else 0.)-(level/prior-1))
        curve.append({"date":d.isoformat(),"strategy_nav":nav,"benchmark_nav":bn})
        base=v;prior=level
    # First day is a common close baseline; compare subsequent close-to-close returns.
    x=excess[1:];n=len(x);mean=sum(x)/n if n else 0.
    var=sum((v-mean)**2 for v in x)/(n-1) if n>1 else 0.
    sr=equity[-1][1]/initial-1;br=levels[sessions[-1]]/first-1
    return {"strategy_return":sr,"benchmark_return":br,"excess_return_difference":sr-br,
            "benchmark_max_drawdown":drawdown,"information_ratio":sqrt(252)*mean/sqrt(var) if var>0 else None,
            "cash_benchmark_return":0.,"curve":curve}

def run(a):
    root=a.output.resolve(strict=True)
    sources=[getattr(a,k).resolve(strict=True) for k in ("market","security","scratch")]
    if any(root==r or root.is_relative_to(r) or r.is_relative_to(root) for r in sources):
        raise ValueError("Experiment output overlaps data or scratch")
    paths={k:getattr(a,k) for k in ("plan","calendar","benchmark","fees","actions")}
    hashes={k:sha256(p) if p else None for k,p in paths.items()}
    doc=bt04.json_file(a.plan)
    warmup=date.fromisoformat(doc["warmup"])
    end=max(date.fromisoformat(f["test_end"]) for f in doc["folds"])
    sessions=bt04.read_calendar(a.calendar,warmup,end)
    folds,combos,min_trades,max_dd=plan(doc,sessions)
    if warmup>=folds[0][0] or not 1<=a.shards<=64:raise ValueError("Prior warmup and valid shards required")
    test_sessions=[d for _,_,c,e in folds for d in sessions if c<=d<=e]
    levels,bench=benchmark(a.benchmark,test_sessions)
    db=bt04.connect(sources[2])
    try:
        _,_,inputs=bt04.receipts(db,sources[0],sources[1],sessions)
    finally:db.close()
    inputs_by_partition={x["partition"]:x for x in inputs}
    contract={"status":STATUS,"formal_backtest_ready":False,"inputs":inputs,
        "file_hashes":hashes,"benchmark":bench,"shards":a.shards,
        "code_sha256":{Path(p).name:sha256(Path(p)) for p in (__file__,bt04.__file__,bt05.__file__,bt05_grid.__file__,bt05_portfolio.__file__,bt05_actions.__file__,technical_strategies.__file__,backtest_engine.__file__,technical_research.__file__)},
        "selection":"training_total_return_then_smaller_drawdown_then_config_id",
        "limitations":["independent_reset_each_test_fold","no_compounded_cross_fold_return",
            "benchmark_operator_evidence_not_certified","provisional_universe_and_company_actions",
            "price_index_not_total_return_if_selected","no_benchmark_trading_costs"]}
    lock=root/".walk.lock"
    if lock.is_symlink():raise ValueError("Invalid lock")
    with lock.open("a") as f:
        fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        put(root/"experiment.json",contract)
        def unchanged():
            if any(p and sha256(p)!=hashes[k] for k,p in paths.items()):raise ValueError("Experiment inputs changed")
        def execute(folder,phase,combo,start,stop,strategies=None):
            unchanged()
            signal_id=bt05.identity(combo["signal"])
            signal_dir=folder/phase/"signals"/signal_id
            signal_dir.mkdir(parents=True,exist_ok=True)
            sc=signal_dir/"config.json";put(sc,combo["signal"])
            # Deliberately limit source end to this phase, not the eventual test end.
            bt04.run(SimpleNamespace(market=a.market,security=a.security,scratch=a.scratch,
                output=signal_dir,calendar=a.calendar,warmup=warmup,start=start,end=stop,
                shards=a.shards,shard=None,config=sc,provisional=True,execute_start=None,execute_end=None))
            source=bt04.json_file(signal_dir/"run.json")
            expected=[inputs_by_partition[p.as_posix()] for p in bt04.expected([d for d in sessions if d<=stop])]
            if source["inputs"]!=expected:raise ValueError("Fold source differs from frozen experiment inputs")
            suffix="-".join(strategies) if strategies else "both"
            dest=folder/phase/"portfolios"/combo["id"]/suffix
            dest.mkdir(parents=True,exist_ok=True)
            config=dest/"config.json";put(config,combo["execution"])
            report=bt05.run(SimpleNamespace(market=a.market,security=a.security,signals=signal_dir,
                scratch=a.scratch,output=dest,calendar=a.calendar,start=start,end=stop,
                config=config,fees=a.fees,actions=a.actions,strategies=strategies))
            return dest,report
        results=[]
        for n,(ta,tb,sa,sb) in enumerate(folds):
            folder=root/f"fold-{n+1:02d}";folder.mkdir(exist_ok=True)
            train=[]
            for combo in combos:
                _,report=execute(folder,"train",combo,ta,tb)
                train.append({**combo,"run_id":report["run_id"],
                              "metrics":{k:report["results"][k]["metrics"] for k in bt05.STRATEGIES}})
            chosen={k:select(train,k,min_trades,max_dd) for k in bt05.STRATEGIES}
            selection={"status":STATUS,"fold":n+1,"train":train,"selected":{k:x["id"] if x else None for k,x in chosen.items()}}
            put(folder/"selection.json",selection)
            held={}
            for k,x in chosen.items():
                if x is None:held[k]=None;continue
                dest,report=execute(folder,"test",x,sa,sb,[k])
                days=[d for d in sessions if sa<=d<=sb]
                cmp=comparison(dest,report,k,levels,days,x["execution"]["initial_cash"])
                put(dest/"benchmark-comparison.json",cmp)
                held[k]={"id":x["id"],"signal_config":x["signal"],"execution_config":x["execution"],
                    "train_metrics":x["metrics"][k],"test_metrics":report["results"][k]["metrics"],
                    "test_run_id":report["run_id"],"comparison":{k:v for k,v in cmp.items() if k!="curve"}}
            fold_result={"fold":n+1,"train":[ta.isoformat(),tb.isoformat()],
                "test":[sa.isoformat(),sb.isoformat()],"results":held}
            put(folder/"summary.json",fold_result);results.append(fold_result)
        unchanged()
        db=bt04.connect(sources[2])
        try:
            _,_,verified=bt04.receipts(db,sources[0],sources[1],sessions)
            if verified!=inputs:raise ValueError("Experiment market inputs changed")
        finally:db.close()
        aggregates={}
        for k in bt05.STRATEGIES:
            rows=[f["results"][k] for f in results if f["results"][k] is not None]
            x=[v["comparison"]["excess_return_difference"] for v in rows]
            aggregates[k]={"total_folds":len(results),"selected_folds":len(rows),
                "unselected_folds":len(results)-len(rows),
                "positive_excess_folds":sum(v>0 for v in x),
                "mean_fold_excess_return":sum(x)/len(x) if x else None}
        result={"status":STATUS,"formal_backtest_ready":False,"experiment_id":bt05.identity(contract),
                "folds":results,"aggregate":aggregates}
        put(root/"summary.json",result)
        return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for k in ("market","security","scratch","output","calendar","plan","benchmark"):
        p.add_argument("--"+k,type=Path,required=True)
    p.add_argument("--fees",type=Path);p.add_argument("--actions",type=Path)
    p.add_argument("--shards",type=int,default=8)
    args=p.parse_args()
    if not args.plan.is_file():p.error("Plan file required")
    print(json.dumps(run(args),ensure_ascii=False))
if __name__=="__main__":main()
