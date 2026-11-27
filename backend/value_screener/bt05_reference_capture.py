"""BT05-E bounded official calendar/index capture; explicit review before use.

No scheduler, database, relay or automatic network access in CI. Retain
annual raw responses with immutable receipts, compare both exchange calendars,
and require exact benchmark coverage. Provider agreement is not independent
historical certification.
"""
from __future__ import annotations
import argparse
from datetime import date,timedelta,datetime,timezone
import fcntl
import json
import os
from math import isfinite
from pathlib import Path
import re
from .bt04_research import json_file
from .technical_research import sha256,write_new
from .bt05_dividend_capture import datestr,parse_day
from .tushare_client import TushareClient

STATUS="BT05_REFERENCE_CANDIDATE_NOT_REVIEWED"
INDEXES=("000300.SH","000905.SH","000852.SH")
CAL_FIELDS="exchange,cal_date,is_open,pretrade_date"
INDEX_FIELDS="ts_code,trade_date,close,pre_close"

def dates(start,end):
    return [start+timedelta(days=i) for i in range((end-start).days+1)]

def request(kind,key,start,end):
    if kind=="calendar" and key in ("SSE","SZSE"):
        return "trade_cal",{"exchange":key,"start_date":datestr(start),"end_date":datestr(end)},CAL_FIELDS
    if kind=="index" and key in INDEXES:
        return "index_daily",{"ts_code":key,"start_date":datestr(start),"end_date":datestr(end)},INDEX_FIELDS
    raise ValueError("Unsupported reference")

def validate(kind,key,start,end,rows):
    if not isinstance(rows,list) or not 0<len(rows)<=366:
        raise ValueError("Invalid reference response size")
    observed={}
    for row in rows:
        if kind=="calendar":
            d=parse_day(row["cal_date"])
            if row["exchange"]!=key or str(row["is_open"]) not in ("0","1"):
                raise ValueError("Invalid calendar identity/state")
            parse_day(row["pretrade_date"])
            v=str(row["is_open"])=="1"
        else:
            d=parse_day(row["trade_date"]);v=row["close"]
            if row["ts_code"]!=key or type(v) not in (int,float) or not isfinite(v) or v<=0:
                raise ValueError("Invalid index observation")
            prior=row["pre_close"]
            if type(prior) not in (int,float) or not isfinite(prior) or prior<=0:
                raise ValueError("Invalid prior close")
        if d is None or d<start or d>end or d in observed:
            raise ValueError("Duplicate/out-of-range reference day")
        observed[d]=v
    if kind=="calendar" and set(observed)!=set(dates(start,end)):
        raise ValueError("Calendar missing civil dates")
    return observed

def slice_paths(root,kind,key,start,end):
    base=root/f"{kind}-{key}-{start.isoformat()}-{end.isoformat()}"
    return base.with_suffix(base.suffix+".json"),base.with_suffix(base.suffix+".manifest.json")

def read_slice(root,kind,key,start,end):
    raw,marker=slice_paths(root,kind,key,start,end)
    if not marker.exists():return None
    if any(x.is_symlink() for x in (raw,marker)):raise ValueError("Symlink reference")
    doc=json_file(marker);api,params,fields=request(kind,key,start,end)
    if (doc.get("source")!="Tushare official" or doc.get("api")!=api or doc.get("params")!=params
            or doc.get("fields")!=fields or doc.get("file")!=raw.name or
            doc.get("sha256")!=sha256(raw) or doc.get("bytes")!=raw.stat().st_size):
        raise ValueError("Reference receipt mismatch")
    rows=json_file(raw)
    if len(rows)!=doc["rows"]:raise ValueError("Reference row count mismatch")
    return validate(kind,key,start,end,rows),{"receipt":sha256(marker),"data":doc["sha256"],"file":raw.name}

def slices(start,end):
    for y in range(start.year,end.year+1):
        yield max(start,date(y,1,1)),min(end,date(y,12,31))

def capture(root,start,end,codes,client,budget):
    if client.source!="official" or not 1<=budget<=50:raise ValueError("Explicit official reference budget required")
    calls=reused=0
    for lo,hi in slices(start,end):
        for kind,key in [("calendar","SSE"),("calendar","SZSE")]+[("index",c) for c in codes]:
            if read_slice(root,kind,key,lo,hi) is not None:
                reused+=1;continue
            if calls>=budget:return {"new_requests":calls,"reused":reused,"complete":False}
            raw,marker=slice_paths(root,kind,key,lo,hi)
            if raw.exists():raise ValueError("Orphan reference data; inspect before retry")
            api,params,fields=request(kind,key,lo,hi);calls+=1
            rows=client.query(api,params,fields,retries=1)
            validate(kind,key,lo,hi,rows)
            write_new(raw,rows)
            write_new(marker,{"source":"Tushare official","api":api,"params":params,"fields":fields,
                "file":raw.name,"sha256":sha256(raw),"bytes":raw.stat().st_size,"rows":len(rows),
                "captured_at_utc":datetime.now(timezone.utc).isoformat()})
    return {"new_requests":calls,"reused":reused,"complete":True}

def build(root,start,end,codes):
    sessions=[];levels={k:[] for k in codes};refs=[]
    def load(kind,key,lo,hi):
        x=read_slice(root,kind,key,lo,hi)
        if x is None:raise ValueError(f"Missing reference {kind} {key} {lo}")
        values,ref=x;refs.append(ref);return values
    for lo,hi in slices(start,end):
        sh=load("calendar","SSE",lo,hi);sz=load("calendar","SZSE",lo,hi)
        if sh!=sz:raise ValueError("SSE/SZSE calendar disagreement")
        opened=sorted(d for d,v in sh.items() if v)
        sessions.extend(d.isoformat() for d in opened)
        for c in codes:
            values=load("index",c,lo,hi)
            if set(values)!=set(opened):raise ValueError(f"Benchmark calendar mismatch {c}")
            levels[c].extend({"date":d.isoformat(),"close":values[d]} for d in opened)
    return {"status":STATUS,"from":start.isoformat(),"through":end.isoformat(),
            "sessions":sessions,"indexes":levels,"inputs":refs,
            "limitations":["same_provider_exchange_calendars_not_independent","price_index_not_total_return",
                           "operator_review_required","not_certified_historical_coverage"]}

def review(candidate,review_path,output):
    h=sha256(candidate);doc=json_file(candidate);rev=json_file(review_path);rh=sha256(review_path)
    if (doc.get("status")!=STATUS or rev.get("status")!="OPERATOR_REVIEWED_REFERENCE_NOT_CERTIFIED"
            or rev.get("candidate_sha256")!=h or not str(rev.get("reviewer","")).strip()
            or not str(rev.get("independent_calendar_source","")).strip()
            or not str(rev.get("index_source_check","")).strip()):
        raise ValueError("Explicit candidate-bound source review required")
    if any(output.iterdir()):raise ValueError("Reference export requires empty directory")
    if sha256(candidate)!=h or sha256(review_path)!=rh:raise ValueError("Review inputs changed")
    common={"candidate_sha256":h,"review_sha256":rh,"reviewer":rev["reviewer"]}
    write_new(output/"calendar.json",{"from":doc["from"],"to":doc["through"],"sessions":doc["sessions"],
        "status":"OPERATOR_REVIEWED_CALENDAR_NOT_CERTIFIED","source_ref":rev["independent_calendar_source"],**common})
    for code,levels in doc["indexes"].items():
        if code not in INDEXES:raise ValueError("Unexpected benchmark code")
        write_new(output/f"benchmark-{code}.json",{"status":"OPERATOR_REVIEWED_BENCHMARK_NOT_CERTIFIED",
            "kind":"price_index","code":code,"source_ref":rev["index_source_check"],"levels":levels,**common})

def main():
    p=argparse.ArgumentParser(description=__doc__)
    s=p.add_subparsers(dest="task",required=True)
    for task in ("capture","build"):
        q=s.add_parser(task)
        q.add_argument("--root",type=Path,required=True)
        q.add_argument("--start",type=date.fromisoformat,required=True);q.add_argument("--end",type=date.fromisoformat,required=True)
        q.add_argument("--codes",nargs="+",choices=INDEXES,default=list(INDEXES))
        if task=="capture":
            q.add_argument("--budget",type=int,default=10);q.add_argument("--confirm")
        else:q.add_argument("--output",type=Path,required=True)
    q=s.add_parser("review")
    for k in ("candidate","review","output"):q.add_argument("--"+k,type=Path,required=True)
    a=p.parse_args()
    if a.task=="review":
        review(a.candidate,a.review,a.output.resolve(strict=True));return
    root=a.root.resolve(strict=True)
    if not date(2020,1,1)<=a.start<=a.end<=date.today() or len(set(a.codes))!=len(a.codes):
        p.error("Invalid reference dates or codes")
    if a.task=="capture":
        if a.confirm!="CAPTURE-REFERENCE" or os.getenv("TUSHARE_API_SOURCE","official")!="official":
            p.error("Explicit official capture confirmation required")
        lock=root/".reference.lock"
        if lock.is_symlink():p.error("Invalid reference lock")
        with lock.open("a") as f:
            fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
            result=capture(root,a.start,a.end,a.codes,TushareClient(os.environ.get("TUSHARE_TOKEN",""),api_source="official"),a.budget)
        print(json.dumps(result))
    else:
        if a.output.resolve().is_relative_to(root):p.error("Candidate must be outside archive")
        write_new(a.output,build(root,a.start,a.end,a.codes))
if __name__=="__main__":main()
