"""Read-only diagnosis of pending financial refresh and published DATA_GAP."""
from collections import Counter, defaultdict
from datetime import datetime
import json

from .store import Store
from .rules import NETNET_IGNORED_INDUSTRIES
from .financial_archive_import import (KINDS, CORE, BATCH, load_batch, verified_json,
    parsed, finite, select_latest, StorageConfig, s3_client, RAW_PREFIX)
from .market_session import CN_TZ
import os


def run():
    day=datetime.now(CN_TZ).date()
    store=Store(os.environ["DATABASE_URL"])
    quote=store.latest_quote_date(day)
    if not quote:
        raise RuntimeError("No market quotes")
    backlog=store.financial_backlog(quote,day,10000)
    # Include cooling companies as well; derive exact pending list with the
    # original query logic, not the cooldown-filtered work queue.
    with store.connect() as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        pending=conn.execute("""
            SELECT p.ts_code
            FROM daily_prices p JOIN stocks s USING(ts_code)
            LEFT JOIN financial_reports r ON r.ts_code=p.ts_code
              AND r.disclosed_at<=%s AND r.end_date<=%s
            WHERE p.trade_date=%s AND p.close<=10 AND (p.pb<=1.2 OR p.pb IS NULL)
              AND NOT s.is_st AND s.market NOT IN ('科创板','创业板','北交所')
              AND NOT (btrim(coalesce(s.industry,'')) = ANY(%s::text[]))
            GROUP BY p.ts_code
            HAVING MAX(r.end_date) FILTER (WHERE r.kind='balancesheet') IS NULL
              OR MAX(r.end_date) FILTER (WHERE r.kind='balancesheet') < %s::date-280
              OR MAX(r.end_date) FILTER (WHERE r.kind='income') IS DISTINCT FROM MAX(r.end_date) FILTER (WHERE r.kind='balancesheet')
              OR MAX(r.end_date) FILTER (WHERE r.kind='cashflow') IS DISTINCT FROM MAX(r.end_date) FILTER (WHERE r.kind='balancesheet')
        """,(day,day,quote,list(NETNET_IGNORED_INDUSTRIES),day)).fetchall()
        gaps=conn.execute("""
            SELECT ts_code,flags,report_date,disclosed_at FROM screen_results
            WHERE trade_date=%s AND strategy='netnet_v1' AND status='DATA_GAP'
        """,(quote,)).fetchall()
        pending={x["ts_code"] for x in pending}
        gap_map={x["ts_code"]:x for x in gaps}
        codes=sorted(pending|set(gap_map))
        if len(pending)!=backlog["remaining"]:
            raise RuntimeError("Backlog counts differ")
        stocks={x["ts_code"]:x for x in conn.execute("SELECT ts_code,name,industry,market,is_st FROM stocks WHERE ts_code=ANY(%s)",(codes,)).fetchall()}
        dbrows=conn.execute("""
            SELECT DISTINCT ON (ts_code,kind) ts_code,kind,end_date,disclosed_at,metrics,raw->>'comp_type' AS comp_type
            FROM financial_reports WHERE ts_code=ANY(%s) AND disclosed_at<%s AND end_date<=%s AND report_type='1'
            ORDER BY ts_code,kind,end_date DESC,disclosed_at DESC
        """,(codes,quote,quote)).fetchall()
    stored={(x["ts_code"],x["kind"]):x for x in dbrows}
    cfg=StorageConfig.from_environment()
    s3=s3_client(cfg)
    try:
        snapshot=verified_json(s3,cfg.bucket,RAW_PREFIX+"/stock_basic/stock_basic_snapshot.json")
        universe=sorted({x["ts_code"] for x in snapshot["records"]})
        positions={c:i for i,c in enumerate(universe)}
        groups=defaultdict(set)
        for c in codes:
            if c in positions:
                groups[positions[c]//BATCH*BATCH].add(c)
        archived={};batches=0
        for start,selected in sorted(groups.items()):
            for kind in KINDS:
                data,prov=load_batch(s3,cfg.bucket,universe,start,kind,day)
                batches+=1
                for c in selected:
                    archived[(c,kind)]=data.get(c,[])
        categories=Counter();pending_categories=Counter();gap_categories=Counter()
        missing_fields=Counter();industries=Counter();archive_comp_types=Counter();flags=Counter()
        for c in codes:
            s=stocks[c]; rec={"code":c,"name":s["name"],"industry":s["industry"],"pending":c in pending,"data_gap":c in gap_map,"reports":{}}
            reasons=[]
            for kind in KINDS:
                db=stored.get((c,kind))
                rows=archived.get((c,kind),[])
                eligible=[]
                for row in rows:
                    end=parsed(row.get("end_date"));actual=parsed(row.get("f_ann_date"));ann=parsed(row.get("ann_date"))
                    if str(row.get("report_type"))=="1" and end and actual and end<=actual and max(actual,ann or actual)<quote:
                        eligible.append((end,max(actual,ann or actual),row))
                latest=max(((a,b) for a,b,_ in eligible),default=None)
                latest_rows=[row for a,b,row in eligible if (a,b)==latest]
                chosen,rejected,outcome=select_latest(rows,kind,quote,db["end_date"] if db else None)
                miss=sorted({f for row in latest_rows for f in CORE[kind] if not finite(row.get(f))})
                comp=sorted({str(row.get("comp_type")) for row in latest_rows})
                rec["reports"][kind]={"db_period":str(db["end_date"]) if db else None,
                   "archive_period":str(latest[0]) if latest else None,"archive_comp_type":comp,
                   "archive_rows":len(rows),"missing_fields":miss,
                   "db_missing_fields":[f for f in CORE[kind] if db and not finite(db["metrics"].get(f))],
                   "outcome":outcome}
                if kind=="balancesheet":
                    for t in comp:archive_comp_types[t]+=1
                if (not db or (latest and latest[0]>db["end_date"])) and miss:
                    reasons.extend(kind+":"+f for f in miss)
                    missing_fields.update(kind+":"+f for f in miss)
                elif not db and not latest:
                    reasons.append(kind+":no_eligible_report")
                elif not db and outcome=="conflicting_latest_version":
                    reasons.append(kind+":conflicting_latest_version")
            fs=gap_map.get(c,{}).get("flags",[])
            rec["flags"]=fs
            flags.update(fs)
            bs=rec["reports"]["balancesheet"]
            # Do not infer applicability from a missing field alone: comp_type
            # and industry are separately printed as observed source evidence.
            financial_structure=("total_cur_assets" in bs["missing_fields"] and any(t in ("2","3","4") for t in bs["archive_comp_type"]))
            if financial_structure:
                category="financial_statement_structure"
            elif "INVALID_MARKET_DATA" in fs:
                category="invalid_market_data"
            elif "BALANCE_INCONSISTENCY" in fs:
                category="balance_inconsistency"
            elif reasons:
                category="missing_core_or_eligible_report"
            elif "MISSING_MATERIAL_BALANCE_ITEM" in fs:
                category="existing_balance_missing_core"
            else:
                category="other_requires_review"
            rec["category"]=category;rec["reasons"]=reasons
            categories[category]+=1
            if c in pending:pending_categories[category]+=1
            if c in gap_map:gap_categories[category]+=1;industries[s["industry"] or "unknown"]+=1
            print("FIN_DIAG_ROW "+json.dumps(rec,ensure_ascii=False,default=str),flush=True)
        summary={"quote_day":str(quote),"asof":str(day),"pending":len(pending),"data_gap":len(gap_map),
            "overlap":len(pending&set(gap_map)),"unique":len(codes),"verified_batches":batches,
            "categories":dict(categories),"pending_categories":dict(pending_categories),"gap_categories":dict(gap_categories),
            "missing_fields":dict(missing_fields),"gap_industries":dict(industries),"archive_balance_comp_types":dict(archive_comp_types),
            "gap_flags":dict(flags),"database_writes":0,"tushare_calls":0}
        print("FIN_DIAG_SUMMARY "+json.dumps(summary,ensure_ascii=False),flush=True)
    finally:
        s3.close()


if __name__=="__main__":
    run()
