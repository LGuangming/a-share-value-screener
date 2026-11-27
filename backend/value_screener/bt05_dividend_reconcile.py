"""Reconcile archived dividend rows to BT03 factor changes, then review explicitly.

Candidate generation never produces a BT05-consumable ledger. Each candidate
must be accepted by a separately supplied review bound to the candidate hash.
Unknown/conflicting/unsupported events remain blocked, never filled with zeros.
"""
from __future__ import annotations
import argparse
from datetime import date
import json
from math import isfinite,isclose
from pathlib import Path
from . import bt04_research as bt04
from . import bt05_dividend_capture as capture
from .technical_strategies import MAINBOARD
from .technical_research import sha256,write_new
from .bt05_actions import Actions

STATUS="DIVIDEND_CANDIDATES_NOT_REVIEWED"

def number(x):
    if type(x) not in (int,float) or not isfinite(x) or x<0:
        raise ValueError("Missing or invalid nonnegative distribution field")
    return float(x)

def candidate(code,day,prior,current,rows,withholding,source):
    """Only narrow, explicit gross cash/share events with compatible factors."""
    def blocked(reason):
        return {"code":code,"ex":day.isoformat(),"kind":"blocked","reason":reason,"source_ref":source}
    if prior is None or current is None:return blocked("missing_prior_or_current_market_bar")
    before_day,before_factor,prev_close=prior
    _,after_factor,_=current
    if before_factor is None or after_factor is None:return blocked("missing_factor")
    # Preserve conflicts. Exact duplicate rows can be deduplicated for interpretation.
    impl={capture.identity(r):r for r in rows if r["div_proc"]=="实施"}
    if len(impl)!=1:return blocked("missing_or_conflicting_implemented_dividend")
    row=next(iter(impl.values()))
    try:
        record=capture.parse_day(row["record_date"])
        imp=capture.parse_day(row["imp_ann_date"])
        pay=capture.parse_day(row["pay_date"])
        share_day=capture.parse_day(row["div_listdate"])
        gross=number(row["cash_div_tax"]);shares=number(row["stk_div"])
        if shares>0:
            if not isclose(shares,number(row["stk_bo_rate"])+number(row["stk_co_rate"]),abs_tol=1e-8):
                return blocked("share_components_disagree")
        elif any(row[k] is not None and number(row[k])!=0 for k in ("stk_bo_rate","stk_co_rate")):
            return blocked("share_components_disagree")
        if record!=before_day or imp is None or imp>record or (gross==0 and shares==0):
            return blocked("record_or_announcement_or_zero_event")
        if gross>0 and (pay is None or pay<day):return blocked("missing_or_invalid_payment_date")
        if shares>0 and share_day!=day:return blocked("delayed_or_unknown_share_listing")
        if gross>=prev_close:return blocked("invalid_ex_price")
        # Gross theoretical ex-price, not actual market open. This is a
        # reconciliation heuristic, not proof of complete corporate actions.
        expected=prev_close/((prev_close-gross)/(1+shares))
        observed=after_factor/before_factor
        if isclose(observed,1.,rel_tol=1e-9) or not isclose(observed,expected,rel_tol=.005,abs_tol=1e-6):
            return blocked("factor_ratio_not_reconciled")
        return {"code":code,"ex":day.isoformat(),"kind":"cash_bonus_same_day" if shares else "cash_only",
            "record":record.isoformat(),"pay":(pay or day).isoformat(),
            "shares_tradable_on":share_day.isoformat() if shares else None,
            "cash_per_old_share_gross":gross,"cash_per_old_share_net":gross*(1-withholding),
            "share_multiplier":1+shares,"factor_before":before_factor,"factor_after":after_factor,
            "factor_ratio_expected":expected,"factor_ratio_observed":observed,
            "source_ref":source,"raw_row_sha256":capture.identity(row)}
    except (KeyError,TypeError,ValueError,ZeroDivisionError):
        return blocked("invalid_or_missing_distribution_fields")

def build(market,security,raw,scratch,calendar,warmup,start,end,withholding):
    if not warmup<start<=end or not isfinite(withholding) or not 0<=withholding<=1:
        raise ValueError("Explicit prior warmup and withholding assumption required")
    calhash=sha256(calendar)
    sessions=bt04.read_calendar(calendar,warmup,end)
    wanted=[d for d in sessions if start<=d<=end]
    if not wanted or sessions[0]>=wanted[0]:raise ValueError("Need preceding exchange session")
    archived=capture.audit(raw,wanted)
    if archived["missing_sessions"]:raise ValueError("Dividend capture incomplete; missing sessions")
    refs={date.fromisoformat(x["day"]):x for x in archived["inputs"]}
    db=bt04.connect(scratch)
    try:
        mf,sf,market_refs=bt04.receipts(db,market,security,sessions)
        previous={};events=[];counts={"factor_changes":0,"implemented_rows":0,"candidate":0,"blocked":0}
        prior_session=None
        for i,(_,days) in enumerate(bt04.expected(sessions).items()):
            # Retrieve one monthly/daily partition, then consume in session order.
            cur=db.execute("""SELECT ts_code,trade_date,adj_factor,"close",factor_valid,ohlcv_valid
                FROM read_parquet(?,hive_partitioning=false)
                WHERE regexp_full_match(ts_code,?) AND trade_date BETWEEN ? AND ?
                ORDER BY trade_date,ts_code""",[mf[i],bt04.MAIN_SQL,days[0],days[-1]])
            def stream():
                while batch:=cur.fetchmany(4096):yield from batch
            it=iter(stream());row=next(it,None)
            for day in days:
                today={}
                while row is not None and row[1]==day:
                    code,d,f,c,fok,ok=row
                    if code in today or not ok or not isfinite(c) or c<=0:
                        raise ValueError("Invalid market row")
                    today[code]=(d,f if fok and f is not None and isfinite(f) and f>0 else None,c)
                    row=next(it,None)
                if day>=start:
                    doc,rows=capture.receipt(raw,day)
                    ref=refs[day]
                    if doc["sha256"]!=ref["data"] or sha256(capture.folder(raw,day)/"manifest.json")!=ref["receipt"]:
                        raise ValueError("Dividend inputs changed")
                    by_code={}
                    for item in rows:
                        if MAINBOARD.fullmatch(item["ts_code"]) and item["div_proc"]=="实施":
                            by_code.setdefault(item["ts_code"],[]).append(item)
                            counts["implemented_rows"]+=1
                    changed=set()
                    for code,now in today.items():
                        old=previous.get(code)
                        if old and old[1] is not None and now[1] is not None and not isclose(old[1],now[1],rel_tol=1e-9):
                            changed.add(code)
                    counts["factor_changes"]+=len(changed)
                    for code in sorted(changed|by_code.keys()):
                        old=previous.get(code)
                        source=f"{capture.SOURCE} {day.isoformat()} sha256={ref['data']}; BT03 market sha256={market_refs[i]['market']}"
                        item=candidate(code,day,old if old and old[0]==prior_session else None,today.get(code),
                                       by_code.get(code,[]),withholding,source)
                        item["id"]=capture.identity(item)
                        events.append(item);counts["blocked" if item["kind"]=="blocked" else "candidate"]+=1
                previous.update(today);prior_session=day
            if row is not None:raise ValueError("Unexpected partition date")
        _,_,checked=bt04.receipts(db,market,security,sessions)
        if checked!=market_refs or capture.audit(raw,wanted)!=archived or sha256(calendar)!=calhash:
            raise ValueError("Source inputs changed during candidate construction")
        return {"status":STATUS,"formal_backtest_ready":False,"from":start.isoformat(),"through":end.isoformat(),
            "warmup":warmup.isoformat(),"calendar_sha256":calhash,"withholding_assumption":withholding,
            "market_inputs":market_refs,"dividend_inputs":archived["inputs"],"counts":counts,"events":events,
            "limitations":["provider_capture_not_independently_certified","fixed_withholding_not_holding_period_tax",
                "factor_reconciliation_heuristic","unsupported_actions_blocked","not_all_actions_change_factor",
                "no_rights_delisting_fractional_or_delayed_shares"]}
    finally:db.close()

def review(candidate_path,review_path,calendar):
    h=sha256(candidate_path);doc=bt04.json_file(candidate_path)
    rev=bt04.json_file(review_path);rh=sha256(review_path)
    if (doc.get("status")!=STATUS or rev.get("status")!="OPERATOR_REVIEWED_RESEARCH_NOT_CERTIFIED"
            or rev.get("candidate_sha256")!=h or not isinstance(rev.get("reviewer"),str)
            or not rev["reviewer"].strip() or not isinstance(rev.get("accepted_ids"),list)):
        raise ValueError("Explicit candidate-bound operator review required")
    if sha256(calendar)!=doc["calendar_sha256"]:raise ValueError("Calendar mismatch")
    accepted=set(rev["accepted_ids"])
    if len(accepted)!=len(rev["accepted_ids"]):raise ValueError("Duplicate accepted IDs")
    events=[];seen=set();keys=set()
    for row in doc["events"]:
        eid=row["id"];body={k:v for k,v in row.items() if k!="id"}
        if capture.identity(body)!=eid or eid in seen or (row["code"],row["ex"]) in keys:
            raise ValueError("Invalid candidate identity or grain")
        seen.add(eid);keys.add((row["code"],row["ex"]))
        if eid in accepted:
            if row["kind"] not in ("cash_only","cash_bonus_same_day"):
                raise ValueError("Blocked event cannot be approved")
            events.append({k:v for k,v in body.items() if k not in ("factor_ratio_expected","factor_ratio_observed")})
        else:
            events.append({"kind":"blocked","code":row["code"],"ex":row["ex"],
                "source_ref":row["source_ref"],"reason":row.get("reason","not_approved_by_operator")})
    if not accepted<=seen:raise ValueError("Unknown accepted candidate")
    result={"status":"OPERATOR_REVIEWED_RESEARCH_NOT_CERTIFIED","from":doc["from"],"through":doc["through"],
        "candidate_sha256":h,"review_sha256":rh,"reviewer":rev["reviewer"],
        "withholding_assumption":doc["withholding_assumption"],"events":events,
        "accepted_count":len(accepted),"blocked_count":len(events)-len(accepted)}
    if sha256(candidate_path)!=h or sha256(review_path)!=rh:raise ValueError("Review inputs changed")
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest="task",required=True)
    b=sub.add_parser("build")
    for name in ("market","security","raw","scratch","calendar","output"):
        b.add_argument("--"+name,type=Path,required=True)
    for name in ("warmup","start","end"):b.add_argument("--"+name,type=date.fromisoformat,required=True)
    b.add_argument("--withholding",type=float,required=True,help="Explicit fixed research assumption; not certified historical tax")
    v=sub.add_parser("review")
    for name in ("candidates","review","calendar","output"):v.add_argument("--"+name,type=Path,required=True)
    a=p.parse_args()
    dest=a.output.resolve()
    if a.task=="build":
        roots=[getattr(a,k).resolve(strict=True) for k in ("market","security","raw","scratch")]
        if any(dest.is_relative_to(r) for r in roots):p.error("Output overlaps source/scratch")
        result=build(*roots,a.calendar,a.warmup,a.start,a.end,a.withholding)
    else:
        result=review(a.candidates,a.review,a.calendar)
    write_new(dest,result)
    print(json.dumps({k:v for k,v in result.items() if k in ("status","counts","accepted_count","blocked_count")}))
if __name__=="__main__":main()
