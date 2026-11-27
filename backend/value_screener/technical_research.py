"""Offline research entrypoint. Never writes production tables or publishes returns.

Input: ordered normalized JSONL (code, session), supplied by the operator.
The BT03 evidence-to-input adapter and historical universe certification are
separate gates. A hash identifies the exact input, not its market-data quality.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from dataclasses import asdict
from datetime import date
from pathlib import Path
from tempfile import NamedTemporaryFile
from .backtest_engine import Bar, BacktestConfig, run_backtest
from .technical_strategies import (Observation, TechnicalConfig, TREND, REVERSAL,
                                  generate_signals, ranked_close_signals, signal_record)

def sha256(path: Path):
    digest=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""):
            digest.update(chunk)
    return digest.hexdigest()

def normalized_observations(path: Path):
    with path.open(encoding="utf-8") as f:
        for line_no,line in enumerate(f,1):
            try:
                row=json.loads(line)
                code=row["ts_code"]
                if not isinstance(code,str) or len(code)!=9 or not code[:6].isdigit() or code[6:] not in (".SH",".SZ"):
                    raise ValueError("Invalid code")
                eligible=row["eligible"]
                if eligible is not None and type(eligible) is not bool:
                    raise ValueError("Invalid eligibility")
                # Never silently treat missing suspension status as tradable.
                if type(row["suspended"]) is not bool:
                    raise ValueError("Unknown suspension status")
                day=date.fromisoformat(row["trade_date"])
                bar=Bar(code,day,row["open"],row["high"],row["low"],row["close"],
                        row["volume"],row["up_limit"],row["down_limit"],row["suspended"])
                yield Observation(bar,row["adj_factor"],row["amount_yuan"],eligible,
                                  row["listing_sessions"],row["session"])
            except (KeyError,TypeError,ValueError) as exc:
                raise ValueError(f"Invalid normalized row {line_no}: {exc}") from exc

def evaluate(path: Path, cfg: TechnicalConfig, start: date, end: date,
             backtest: bool=False, holding: int=10, stop: float|None=None, max_rows: int=250000):
    if start>end or max_rows<1:
        raise ValueError("Invalid research interval or row cap")
    identity=sha256(path)
    signals=[]
    count=0
    bars=[]
    day_sessions={}
    session_days={}
    def stream():
        nonlocal count
        for row in normalized_observations(path):
            count+=1
            day=row.bar.day
            if day>end:
                continue
            if day_sessions.setdefault(day,row.session)!=row.session or session_days.setdefault(row.session,day)!=day:
                raise ValueError("Inconsistent market calendar index")
            if backtest and start<=day<=end:
                if len(bars)>=max_rows:
                    raise ValueError("Engineering execution exceeds configured row cap")
                bars.append(row.bar)
            yield row
    for signal in generate_signals(stream(),cfg):
        if start<=signal.day<=end:
            signals.append(signal)
    if identity!=sha256(path):
        raise ValueError("Research input changed during execution")
    ordered=sorted(signals,key=lambda s:(s.day,s.strategy_id,-s.score,s.ts_code))
    result={"status":"RESEARCH_ONLY_NOT_CERTIFIED","input_sha256":identity,
            "rows_seen":count,"start":start.isoformat(),"end":end.isoformat(),
            "strategy_version":"0.1.1","technical_config":asdict(cfg),
            "signal_counts":{k:sum(s.strategy_id==k for s in ordered) for k in (TREND,REVERSAL)},
            "signals":[signal_record(s) for s in ordered]}
    if backtest:
        days=sorted({b.day for b in bars})
        if any(day_sessions[b]!=day_sessions[a]+1 for a,b in zip(days,days[1:])):
            raise ValueError("Missing market sessions in execution interval")
        bc=BacktestConfig(holding_sessions=holding,stop_loss_rate=stop)
        result["execution_config"]=asdict(bc)
        result["engineering_execution"]={k:run_backtest(bars,ranked_close_signals(ordered,k),bc)
                                          for k in (TREND,REVERSAL)}
        result["limitations"]="Raw execution prices; no certified corporate-action cash/share accounting, historical universe, or date-specific fees. Engineering results must not be published."
    return result

def write_new(path: Path, result: dict):
    # Exclusive final creation; never replace a previous result or input.
    path.parent.mkdir(parents=True,exist_ok=True)
    with NamedTemporaryFile(mode="w",encoding="utf-8",dir=path.parent,delete=False) as f:
        tmp=Path(f.name)
        try:
            json.dump(result,f,ensure_ascii=False,allow_nan=False)
            f.flush();os.fsync(f.fileno())
            os.link(tmp,path)
        finally:
            tmp.unlink(missing_ok=True)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--start",type=date.fromisoformat,required=True)
    p.add_argument("--end",type=date.fromisoformat,required=True)
    p.add_argument("--config",type=Path,help="JSON object of TechnicalConfig overrides")
    p.add_argument("--engineering-backtest",action="store_true")
    p.add_argument("--holding",type=int,default=10)
    p.add_argument("--stop",type=float)
    p.add_argument("--max-rows",type=int,default=250000)
    a=p.parse_args()
    cfg=TechnicalConfig(**(json.loads(a.config.read_text()) if a.config else {}))
    result=evaluate(a.input,cfg,a.start,a.end,a.engineering_backtest,a.holding,a.stop,a.max_rows)
    write_new(a.output,result)
    print(json.dumps({k:result[k] for k in ("status","rows_seen","signal_counts","input_sha256")}))
if __name__=="__main__":
    main()
