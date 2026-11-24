"""Reuse verified archived statements for current live screening, not PIT backtests.

Only insert missing/newer consolidated statement periods. Never replace a live
statement from the same/newer period, infer missing publication dates, or use
a later archival capture to run a backdated historical import.
"""
from __future__ import annotations
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import gzip
import hashlib
import json
from math import isfinite
from pathlib import Path
import re
import tempfile

from .archive_paths import RAW_PREFIX
from .neon_archive import StorageConfig, s3_client, verify_object
from .phase2_financial import BASE, FIELDS
from .market_session import CN_TZ
from .financial_schedule import recent_closed_quarters

KINDS=("balancesheet","income","cashflow")
CORE={"balancesheet":("total_cur_assets","total_liab"),
      "income":("n_income",),"cashflow":("n_cashflow_act",)}
SHA=re.compile(r"^[a-f0-9]{64}$")
CODE=re.compile(r"^\d{6}\.(SH|SZ|BJ)$")
BATCH=50
MAX_JSON=8*1024*1024
MAX_GZIP=8*1024*1024
MAX_EXPANDED=64*1024*1024


def parsed(value):
    if not isinstance(value,str) or not re.fullmatch(r"\d{8}",value):
        return None
    try:
        return date(int(value[:4]),int(value[4:6]),int(value[6:]))
    except ValueError:
        return None


def verified_json(s3,bucket,key):
    h=s3.head_object(Bucket=bucket,Key=key)
    size=int(h["ContentLength"]);sha=(h.get("Metadata") or {}).get("sha256","")
    if not 0<size<=MAX_JSON or not SHA.fullmatch(sha):
        raise RuntimeError("Unverified archive JSON metadata: "+key)
    with tempfile.TemporaryDirectory() as tmp:
        path=Path(tmp)/"doc.json"
        verify_object(s3,bucket,key,size,sha,path)
        return json.loads(path.read_bytes())


def finite(v):
    if isinstance(v,bool) or v in (None,""):
        return False
    try:
        return isfinite(float(v))
    except (TypeError,ValueError,OverflowError):
        return False


def select_latest(rows,kind,quote_day,stored_period=None):
    """Choose the latest unambiguous eligible report, not an older substitute."""
    choices=defaultdict(dict)
    rejected=0
    for row in rows:
        if str(row.get("report_type"))!="1":
            continue
        end=parsed(row.get("end_date"))
        actual=parsed(row.get("f_ann_date"))
        ann=parsed(row.get("ann_date"))
        if not end or not actual or actual<end or (row.get("ann_date") not in (None,"") and ann is None):
            rejected+=1;continue
        disclosed=max(actual,ann or actual)
        if end>quote_day or disclosed>=quote_day or end<quote_day-timedelta(days=540):
            continue
        if stored_period and end<=stored_period:
            continue
        key=(end,disclosed)
        canonical=json.dumps(row,sort_keys=True,ensure_ascii=False,separators=(",",":"),allow_nan=False)
        choices[key][canonical]=row
    if not choices:
        return None,rejected,"no_eligible_new_period"
    end,disclosed=max(choices)
    if len(choices[(end,disclosed)])!=1:
        return None,rejected,"conflicting_latest_version"
    row=next(iter(choices[(end,disclosed)].values()))
    if any(not finite(row.get(field)) for field in CORE[kind]):
        return None,rejected,"missing_core_metric"
    if kind=="balancesheet" and any(float(row[k])<0 for k in CORE[kind]):
        return None,rejected,"invalid_balance_metric"
    return (end,disclosed,row),rejected,"selected"


def load_batch(s3,bucket,codes,start,kind,asof):
    selected=codes[start:start+BATCH]
    base=f"{BASE}/{kind}/start={start:05d}_count={BATCH:03d}"
    key=base+".jsonl.gz"
    doc=verified_json(s3,bucket,base+".manifest.json")
    digest=hashlib.sha256(json.dumps(selected,ensure_ascii=False).encode("utf-8")).hexdigest()
    captured=datetime.fromisoformat(doc["captured_at_utc"])
    if captured.tzinfo is None:
        raise RuntimeError("Financial capture timestamp missing timezone")
    size=doc.get("compressed_bytes");sha=doc.get("sha256","")
    if (doc.get("format_version")!=1 or doc.get("kind")!=kind or doc.get("start_index")!=start
            or doc.get("requested_codes")!=len(selected) or doc.get("codes_sha256")!=digest
            or doc.get("object_key")!=key or doc.get("status")!="research_not_pit_certified"
            or doc.get("source")!="Tushare official per-symbol" or doc.get("currency")!="CNY"
            or doc.get("financial_amount_unit")!="RMB yuan"
            or type(size) is not int or not 0<size<=MAX_GZIP or not SHA.fullmatch(str(sha))
            or type(doc.get("rows")) is not int or doc["rows"]<0
            or captured.astimezone(CN_TZ).date()>asof or captured>datetime.now(timezone.utc)):
        raise RuntimeError("Financial batch manifest failed identity/date/unit checks: "+key)
    records=defaultdict(list);count=expanded=0;allowed=set(selected)
    with tempfile.TemporaryDirectory() as tmp:
        path=Path(tmp)/"batch.gz"
        verify_object(s3,bucket,key,size,sha,path)
        with gzip.open(path,"rb") as fp:
            for line in fp:
                expanded+=len(line)
                if expanded>MAX_EXPANDED:
                    raise RuntimeError("Financial batch exceeds expanded size cap")
                row=json.loads(line)
                if not isinstance(row,dict) or row.get("ts_code") not in allowed:
                    raise RuntimeError("Unexpected financial security in verified batch")
                if not set(FIELDS[kind].split(",")).issubset(row):
                    raise RuntimeError("Financial batch field mismatch")
                records[row["ts_code"]].append(row);count+=1
    if count!=doc["rows"] or set(doc.get("missing_codes",[]))!=allowed-set(records) or doc.get("missing_codes_count")!=len(allowed-set(records)):
        raise RuntimeError("Financial batch rows or missing symbols do not reconcile")
    return records,{"object_key":key,"sha256":sha,"captured_at_utc":doc["captured_at_utc"],"status":"research_not_pit_certified"}


def import_archive(store,quote_day,asof):
    if asof!=datetime.now(CN_TZ).date():
        raise RuntimeError("Archive reuse is restricted to current live screening, not backdated research")
    cutoff=recent_closed_quarters(asof)[0]
    targets=store.archive_financial_targets(quote_day,asof,cutoff)
    stats={"archive_target_companies":len(targets),"archive_verified_batches":0,
           "archive_rows_selected":0,"archive_rows_inserted":0,"archive_companies_inserted":0,
           "archive_unmapped_companies":0,"archive_rejected_dates":0,"archive_outcomes":{}}
    if not targets:
        return stats
    cfg=StorageConfig.from_environment()
    s3=s3_client(cfg);bucket=cfg.bucket
    try:
        snapshot=verified_json(s3,bucket,RAW_PREFIX+"/stock_basic/stock_basic_snapshot.json")
        if snapshot.get("status")!="current_snapshot_not_historical" or not isinstance(snapshot.get("records"),list):
            raise RuntimeError("Invalid frozen financial universe")
        codes=sorted({x["ts_code"] for x in snapshot["records"]})
        if not codes or any(not CODE.fullmatch(c) for c in codes):
            raise RuntimeError("Invalid frozen archive securities")
        positions={code:i for i,code in enumerate(codes)}
        groups=defaultdict(set)
        for code in targets:
            if code in positions:
                groups[positions[code]//BATCH*BATCH].add(code)
            else:
                stats["archive_unmapped_companies"]+=1
        inserts=[]
        for start,selected in sorted(groups.items()):
            for kind in KINDS:
                needed={code for code in selected if targets[code].get(kind) is None or targets[code][kind]<cutoff}
                if not needed:
                    continue
                records,provenance=load_batch(s3,bucket,codes,start,kind,asof)
                stats["archive_verified_batches"]+=1
                for code in sorted(needed):
                    chosen,rejected,outcome=select_latest(records.get(code,[]),kind,quote_day,targets[code].get(kind))
                    stats["archive_rejected_dates"]+=rejected
                    stats["archive_outcomes"][outcome]=stats["archive_outcomes"].get(outcome,0)+1
                    if chosen:
                        end,disclosed,row=chosen
                        inserts.append((code,kind,end,disclosed,row,provenance))
            if stats["archive_verified_batches"] and start%500==0:
                print(f"FINANCIAL_ARCHIVE_PROGRESS start={start} batches={stats['archive_verified_batches']} selected={len(inserts)}",flush=True)
        # All selected batches are fully verified before the first SQL write.
        stats["archive_rows_selected"]=len(inserts)
        count,companies=store.insert_archive_financials(inserts,asof)
        stats["archive_rows_inserted"]=count
        stats["archive_companies_inserted"]=companies
        return stats
    finally:
        s3.close()
