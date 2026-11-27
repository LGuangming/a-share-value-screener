"""BT05-B: reproducible execution-parameter grid and held-out comparison.

Uses one fixed BT04 signal run. Select per strategy on development interval
only. Independent out-of-sample cash accounts; no chaining of period returns.
All outputs are engineering-only and require reviewed research inputs.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict
from datetime import date
import fcntl
import itertools
from pathlib import Path
from types import SimpleNamespace
from . import bt05_research as bt05
from .backtest_engine import BacktestConfig
from .technical_research import sha256,write_new

STATUS="BT05_GRID_ENGINEERING_ONLY_NOT_CERTIFIED"

def put(path,doc):
    if path.exists():
        if bt05.bt04.json_file(path)!=doc:raise ValueError("Grid identity changed; use new directory")
    else:write_new(path,doc)

def select(rows,strategy,min_trades,max_drawdown):
    candidates=[]
    for row in rows:
        m=row["metrics"][strategy]
        if m["closed_trades"]>=min_trades and m["max_drawdown"]>=-max_drawdown:
            candidates.append(row)
    return min(candidates,key=lambda x:(-x["metrics"][strategy]["total_return"],
                      -x["metrics"][strategy]["max_drawdown"],x["id"])) if candidates else None

def run(a):
    root=a.output.resolve(strict=True)
    sources=[getattr(a,k).resolve(strict=True) for k in ("market","security","signals","scratch")]
    if any(root==r or root.is_relative_to(r) or r.is_relative_to(root) for r in sources):
        raise ValueError("Grid output overlaps input or scratch")
    if not a.train_start<a.train_end<a.test_start<a.test_end:
        raise ValueError("Development and held-out dates must be ordered and disjoint")
    if not 0<a.max_drawdown<=1 or a.min_trades<1:
        raise ValueError("Invalid selection criteria")
    if not a.holdings or not a.stops or len(a.holdings)*len(a.stops)>24:
        raise ValueError("Grid must have 1..24 configurations")
    base=bt05.bt04.json_file(a.config) if a.config else {}
    configs=[]
    for h,s in itertools.product(a.holdings,a.stops):
        cfg=BacktestConfig(**{**base,"holding_sessions":h,"stop_loss_rate":s})
        doc=asdict(cfg);cid=bt05.identity(doc)
        configs.append((cid,doc))
    if len({x[0] for x in configs})!=len(configs):raise ValueError("Duplicate grid parameters")
    lock=root/".grid.lock"
    if lock.is_symlink():raise ValueError("Invalid grid lock")
    with lock.open("a") as f:
        fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        source=bt05.bt04.json_file(a.signals/"run.json")
        contract={"status":STATUS,"formal_backtest_ready":False,
            "bt04_run_id":source["run_id"],"bt04_summary_sha256":sha256(a.signals/"summary.json"),
            "calendar_sha256":sha256(a.calendar),"fees_sha256":sha256(a.fees) if a.fees else None,
            "actions_sha256":sha256(a.actions) if a.actions else None,
            "train":[a.train_start.isoformat(),a.train_end.isoformat()],
            "test":[a.test_start.isoformat(),a.test_end.isoformat()],
            "min_trades":a.min_trades,"max_drawdown":a.max_drawdown,
            "selection":"max_training_total_return_then_smaller_drawdown_then_config_id",
            "configs":[{"id":i,"config":c} for i,c in configs],
            "code_sha256":sha256(Path(__file__))}
        put(root/"grid.json",contract)
        def one(cid,cfg,phase,start,end):
            d=root/phase/cid
            d.mkdir(parents=True,exist_ok=True)
            conf=d/"config.json";put(conf,cfg)
            result=bt05.run(SimpleNamespace(market=a.market,security=a.security,signals=a.signals,
                scratch=a.scratch,output=d,calendar=a.calendar,start=start,end=end,
                config=conf,fees=a.fees,actions=a.actions))
            return {"id":cid,"run_id":result["run_id"],"config":cfg,
                "metrics":{k:result["results"][k]["metrics"] for k in bt05.STRATEGIES}}
        train=[one(i,c,"train",a.train_start,a.train_end) for i,c in configs]
        chosen={k:select(train,k,a.min_trades,a.max_drawdown) for k in bt05.STRATEGIES}
        # Persist selection before any held-out execution, never reselect on test.
        selection={"status":STATUS,"training":train,
                   "selected":{k:(v["id"] if v else None) for k,v in chosen.items()}}
        put(root/"selection.json",selection)
        held={}
        for k,v in chosen.items():
            if v is None:held[k]=None;continue
            cid=v["id"]
            if cid not in held:
                held[cid]=one(cid,v["config"],"test",a.test_start,a.test_end)
        results={}
        for k,v in chosen.items():
            results[k]=None if v is None else {"id":v["id"],"config":v["config"],
              "training_metrics":v["metrics"][k],"held_out_metrics":held[v["id"]]["metrics"][k],
              "held_out_run_id":held[v["id"]]["run_id"]}
        for path,key in ((a.signals/"summary.json","bt04_summary_sha256"),(a.calendar,"calendar_sha256"),
                         (a.actions,"actions_sha256"),(a.fees,"fees_sha256")):
            if path and sha256(path)!=contract[key]:raise ValueError("Grid input changed")
        result={"status":STATUS,"formal_backtest_ready":False,
                "grid_id":bt05.identity(contract),"selected_results":results,
                "limitations":["execution_parameters_only_fixed_BT04_signal_config",
                   "independent_training_and_test_initial_cash",
                   "held_out_portfolio_starts_without_positions",
                   "no_automatic_reselection_or_benchmark",
                   "corporate_actions_and_eligibility_not_certified"]}
        put(root/"summary.json",result)
        return result

def parse_stop(s):
    return None if s.lower()=="none" else float(s)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for k in ("market","security","signals","scratch","output","calendar"):
        p.add_argument("--"+k,type=Path,required=True)
    for k in ("train-start","train-end","test-start","test-end"):
        p.add_argument("--"+k,type=date.fromisoformat,required=True)
    p.add_argument("--config",type=Path)
    p.add_argument("--fees",type=Path)
    p.add_argument("--actions",type=Path)
    p.add_argument("--holdings",nargs="+",type=int,default=[5,10,20])
    p.add_argument("--stops",nargs="+",type=parse_stop,default=[.03,.05,.07,None])
    p.add_argument("--min-trades",type=int,default=10)
    p.add_argument("--max-drawdown",type=float,default=.30)
    print(run(p.parse_args()))

if __name__=="__main__":main()
