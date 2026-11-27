"""Read-only BT05-E preflight. Reports blockers, never certifies a backtest."""
from __future__ import annotations
import argparse,json,resource,time,shutil
from datetime import date
from pathlib import Path
from . import bt04_research as bt04,bt05_dividend_capture as dividends,bt05_walk_forward as wf
from .bt05_actions import Actions
from .technical_research import sha256,write_new

def run(a):
    started=time.monotonic()
    roots=[getattr(a,k).resolve(strict=True) for k in ("market","security","dividend","scratch")]
    if any(x==y or x.is_relative_to(y) or y.is_relative_to(x) for i,x in enumerate(roots) for y in roots[i+1:]):
        raise ValueError("Preflight roots must be disjoint")
    if not a.warmup<a.start<=a.end:raise ValueError("Invalid preflight interval")
    report={"status":"BT05_PREFLIGHT_RESEARCH_ONLY","formal_backtest_ready":False,
            "checks":{},"blockers":[],"inputs":{}}
    def check(name,fn):
        try:
            result=fn();report["checks"][name]={"ok":True,**result}
            return result
        except Exception as e:
            report["checks"][name]={"ok":False,"error":str(e)}
            report["blockers"].append(name)
            return None
    sessions=[]
    def calendar():
        nonlocal sessions
        doc=bt04.json_file(a.calendar)
        if doc.get("status")!="OPERATOR_REVIEWED_CALENDAR_NOT_CERTIFIED":
            raise ValueError("Reviewed calendar evidence required")
        sessions=bt04.read_calendar(a.calendar,a.warmup,a.end)
        if not any(d<a.start for d in sessions) or sum(d>=a.start for d in sessions)<2:
            raise ValueError("Insufficient warmup or execution sessions")
        report["inputs"]["calendar"]=sha256(a.calendar)
        return {"sessions":len(sessions),"execution_sessions":sum(d>=a.start for d in sessions)}
    if check("calendar",calendar):
        selected=[d for d in sessions if d>=a.start]
        def market():
            db=bt04.connect(roots[3])
            try:
                mf,sf,refs=bt04.receipts(db,roots[0],roots[1],sessions)
                actual=[x[0] for x in db.execute("SELECT DISTINCT trade_date FROM read_parquet(?) WHERE trade_date BETWEEN ? AND ? ORDER BY trade_date",[mf,a.warmup,a.end]).fetchall()]
                if actual!=sessions:raise ValueError("Market dates differ from calendar")
                rows=eligible=missing_factor=0
                for m,s in zip(mf,sf):
                    counts=db.execute("""SELECT count(*),count(*) FILTER (WHERE m.st_normal_evidence
                         AND m.st_ann_date<m.trade_date AND s.historical_st_type='NORMAL'
                         AND s.st_evidence='historical_merged_not_certified'
                         AND s.listing_evidence='within_snapshot_listing_dates_not_certified'
                         AND m.limits_valid),count(*) FILTER (WHERE NOT coalesce(m.factor_valid,false))
                         FROM read_parquet(?) m JOIN read_parquet(?) s USING(ts_code,trade_date)
                         WHERE regexp_full_match(m.ts_code,?) AND m.trade_date BETWEEN ? AND ?""",
                         [m,s,bt04.MAIN_SQL,a.start,a.end]).fetchone()
                    rows+=counts[0];eligible+=counts[1];missing_factor+=counts[2]
                report["inputs"]["market_security"]=refs
                if not rows or not eligible or missing_factor:
                    raise ValueError(f"No usable provisional rows or missing factors: rows={rows}, provisional_evidence_rows={eligible}, missing_factors={missing_factor}")
                return {"partitions":len(refs),"mainboard_rows":rows,"provisional_evidence_rows":eligible,"missing_factors":missing_factor}
            finally:db.close()
        check("market_security",market)
        def dividend():
            x=dividends.audit(roots[2],selected)
            report["inputs"]["dividend"]=x["inputs"]
            if x["missing_sessions"]:raise ValueError(f"Missing dividend sessions: {len(x['missing_sessions'])}")
            return {"verified_sessions":x["verified_sessions"],"raw_rows":x["rows"],"empty_responses":x["empty_responses"]}
        check("dividend_capture",dividend)
        def actions():
            if a.actions is None:raise ValueError("Reviewed action ledger required")
            events,h=Actions.load(a.actions,sessions,a.start,a.end)
            doc=bt04.json_file(a.actions)
            if not doc.get("candidate_sha256") or not doc.get("review_sha256"):
                raise ValueError("Candidate-bound review provenance required")
            report["inputs"]["actions"]=h
            blocked=sum(len(v) for v in events.blocked.values())
            return {"supported":sum(len(v) for v in events.by_ex.values()),"blocked":blocked}
        check("actions",actions)
        def benchmark():
            _,ref=wf.benchmark(a.benchmark,selected)
            report["inputs"]["benchmark"]=ref
            return {"code":ref["code"],"kind":ref["kind"],"sessions":len(selected)}
        check("benchmark",benchmark)
    else:report["blockers"].extend(["market_security","dividend_capture","actions","benchmark"])
    report["research_preflight_pass"]=not report["blockers"]
    report["resource_observation"]={"elapsed_seconds":round(time.monotonic()-started,3),
        "process_peak_rss_kib":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "scratch_free_bytes":shutil.disk_usage(roots[3]).free}
    report["limitations"]=["preflight_only_not_backtest","blocked_actions_may_stop_execution",
        "historical_universe_ST_calendar_fees_not_certified","no_actual_portfolio_or_container_peak_measurement"]
    return report

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for k in ("market","security","dividend","scratch","calendar","benchmark","output"):
        p.add_argument("--"+k,type=Path,required=True)
    p.add_argument("--actions",type=Path)
    for k in ("warmup","start","end"):p.add_argument("--"+k,type=date.fromisoformat,required=True)
    a=p.parse_args()
    if any(a.output.resolve().is_relative_to(getattr(a,k).resolve()) for k in ("market","security","dividend","scratch")):
        p.error("Report must be outside inputs and scratch")
    if a.output.exists():p.error("New report required")
    report=run(a);write_new(a.output,report)
    print(json.dumps({k:v for k,v in report.items() if k!="inputs"},ensure_ascii=False))
    if not report["research_preflight_pass"]:raise SystemExit(2)
if __name__=="__main__":main()
