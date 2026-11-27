"""BT05-C dividend archive: official Tushare ex-date requests, offline verification.

One request per exchange session (no per-stock duplication); preserve all returned
rows, including conflicting versions. Empty responses are evidence, not proof
that no action occurred. Explicit bounded online CLI only; no scheduler/DB.
"""
from __future__ import annotations
import argparse
from datetime import date,datetime,timezone
import fcntl
import hashlib
import json
from pathlib import Path
import os
from .bt04_research import read_calendar,json_file
from .technical_research import sha256,write_new
from .tushare_client import TushareClient

FIELDS="ts_code,end_date,ann_date,div_proc,stk_div,stk_bo_rate,stk_co_rate,cash_div,cash_div_tax,record_date,ex_date,pay_date,div_listdate,imp_ann_date,base_date,base_share"
CAP=2000
SOURCE="Tushare official dividend ex_date"
STATUS="DIVIDEND_CAPTURE_RESEARCH_NOT_CERTIFIED"

def identity(obj):
    return hashlib.sha256(json.dumps(obj,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False).encode()).hexdigest()

def datestr(day):return day.strftime("%Y%m%d")

def parse_day(value):
    if value is None or value=="":return None
    if not isinstance(value,str) or len(value)!=8 or not value.isdigit():
        raise ValueError("Invalid provider date")
    return date(int(value[:4]),int(value[4:6]),int(value[6:]))

def validate_rows(rows,day):
    if not isinstance(rows,list) or len(rows)>=CAP:
        raise ValueError("Dividend API response at cap or invalid; cannot certify complete capture")
    required=set(FIELDS.split(","))
    for row in rows:
        if not isinstance(row,dict) or not required<=row.keys():
            raise ValueError("Missing dividend fields")
        code=row["ts_code"]
        if not isinstance(code,str) or len(code)!=9 or not code[:6].isdigit() or code[6:] not in (".SH",".SZ",".BJ"):
            raise ValueError("Invalid dividend security key")
        if parse_day(row["ex_date"])!=day:
            raise ValueError("Dividend response ex-date mismatch")
        for key in ("end_date","ann_date","record_date","pay_date","div_listdate","imp_ann_date","base_date"):
            parse_day(row[key])
    # JSON serialization rejects NaN/Infinity rather than silently replacing values.
    identity(rows)

def folder(root,day):return root/f"year={day.year}"/f"month={day.month:02d}"/f"day={datestr(day)}"

def receipt(root,day):
    p=folder(root,day)/"manifest.json"
    if not p.exists():return None
    if p.resolve()!=p.absolute():raise ValueError("Symlink dividend receipt path")
    doc=json_file(p)
    if (doc.get("version")!=1 or doc.get("status")!=STATUS or doc.get("source")!=SOURCE
            or doc.get("ex_date")!=day.isoformat() or doc.get("fields")!=FIELDS
            or doc.get("request")!={"ex_date":datestr(day)}):
        raise ValueError("Dividend receipt identity mismatch")
    h=doc["sha256"]
    if not isinstance(h,str) or len(h)!=64 or any(c not in "0123456789abcdef" for c in h) or doc["file"]!=f"dividend-{h}.json":
        raise ValueError("Invalid dividend file name")
    data=p.parent/doc["file"]
    if data.is_symlink() or sha256(data)!=h or data.stat().st_size!=doc["bytes"]:
        raise ValueError("Dividend archive hash/size mismatch")
    rows=json_file(data);validate_rows(rows,day)
    if len(rows)!=doc["rows"]:raise ValueError("Dividend archive count mismatch")
    return doc,rows

def collect_day(root,day,client):
    old=receipt(root,day)
    if old is not None:return old[0],False
    target=folder(root,day)
    target.mkdir(parents=True,exist_ok=True)
    if target.resolve()!=target.absolute():raise ValueError("Symlink dividend target")
    if any(target.iterdir()):raise ValueError("Uncommitted dividend output; inspect or use new root")
    rows=client.query("dividend",{"ex_date":datestr(day)},FIELDS,retries=1)
    validate_rows(rows,day)
    # Preserve provider order and all duplicates in raw snapshot.
    payload=target/"payload.json"
    write_new(payload,rows)
    h=sha256(payload);dest=target/f"dividend-{h}.json"
    os.link(payload,dest);payload.unlink()
    doc={"version":1,"status":STATUS,"source":SOURCE,"ex_date":day.isoformat(),
        "request":{"ex_date":datestr(day)},"fields":FIELDS,"file":dest.name,
        "sha256":h,"bytes":dest.stat().st_size,"rows":len(rows),
        "empty_response":not rows,"captured_at_utc":datetime.now(timezone.utc).isoformat()}
    write_new(target/"manifest.json",doc)
    return doc,True

def collect(root,sessions,client,budget):
    if getattr(client,"source",None)!="official" or not 1<=budget<=500:
        raise ValueError("Official source and bounded 1..500 request budget required")
    calls=0;reused=0;rows=0;empty=0
    for d in sessions:
        old=receipt(root,d)
        if old is not None:
            doc=old[0];reused+=1
        else:
            if calls>=budget:break
            calls+=1
            doc,_=collect_day(root,d,client)
            print("DIVIDEND_DAY "+json.dumps({"day":d.isoformat(),"rows":doc["rows"]}),flush=True)
        rows+=doc["rows"];empty+=int(doc["empty_response"])
    return {"status":STATUS,"expected_sessions":len(sessions),"verified_sessions":calls+reused,
            "new_requests":calls,"reused_sessions":reused,"rows":rows,"empty_responses":empty}

def audit(root,sessions):
    missing=[];inputs=[];count=empty=0
    for d in sessions:
        old=receipt(root,d)
        if old is None:missing.append(d.isoformat());continue
        doc,_=old;count+=doc["rows"];empty+=int(doc["empty_response"])
        inputs.append({"day":d.isoformat(),"receipt":sha256(folder(root,d)/"manifest.json"),"data":doc["sha256"]})
    return {"status":STATUS,"formal_backtest_ready":False,"expected_sessions":len(sessions),
        "verified_sessions":len(inputs),"missing_sessions":missing,"rows":count,"empty_responses":empty,"inputs":inputs}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("task",choices=("capture","audit"))
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--calendar",type=Path,required=True)
    p.add_argument("--start",type=date.fromisoformat,required=True)
    p.add_argument("--end",type=date.fromisoformat,required=True)
    p.add_argument("--budget",type=int,default=50)
    p.add_argument("--confirm")
    p.add_argument("--report",type=Path)
    a=p.parse_args()
    root=a.root.resolve(strict=True)
    if not date(2020,1,1)<=a.start<=a.end<=date.today():p.error("Invalid capture interval")
    sessions=read_calendar(a.calendar,a.start,a.end)
    if a.task=="capture":
        if a.confirm!="CAPTURE-DIVIDEND":p.error("Explicit CAPTURE-DIVIDEND confirmation required")
        # Never forward the official token to a relay.
        if os.getenv("TUSHARE_API_SOURCE","official")!="official":p.error("Official API only")
        client=TushareClient(os.environ.get("TUSHARE_TOKEN",""),api_source="official")
        lock=root/".capture.lock"
        if lock.is_symlink():p.error("Invalid capture lock")
        with lock.open("a") as f:
            fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
            result=collect(root,sessions,client,a.budget)
    else:
        result=audit(root,sessions)
        if a.report:
            path=a.report.resolve()
            if path.is_relative_to(root):p.error("Audit report must be outside archive")
            write_new(path,result)
    print(json.dumps(result,ensure_ascii=False),flush=True)

if __name__=="__main__":main()
