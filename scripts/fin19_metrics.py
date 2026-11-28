"""FIN19-V5C/V6 research-only field consensus and financial metrics.

Reads the accepted V5 ledger without mutation; no provider calls or PIT claims.
Only mainboard code-pattern candidates, all historical periods >=2018. Each
metric retains exact contributing source-version IDs. No synthetic statement
assembled from complementary versions. Cross-period metrics use the latest
observed version per period at the ledger capture cutoff, NOT historical as-of.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date
import itertools
import json
from pathlib import Path
import re
import sqlite3
import tempfile

try:
    from .fin19_standardize import canonical, digest, day, num, ratio
except ImportError:
    from fin19_standardize import canonical, digest, day, num, ratio

VERSION = "fin19-metrics-v1"
QUARTERS = ("0331", "0630", "0930", "1231")
FIELDS = {
    "balancesheet": ("total_assets","total_liab","total_cur_assets","total_cur_liab",
        "total_hldr_eqy_exc_min_int","total_hldr_eqy_inc_min_int","inventories","total_share"),
    "income": ("revenue","oper_cost","n_income","n_income_attr_p","total_profit",
        "fin_exp_int_exp","ebit","ebitda"),
    "cashflow": ("n_cashflow_act","c_pay_acq_const_fiolta","free_cashflow"),
}
# Explicit applicability: standard industrial/commercial definitions.
INDUSTRIAL = "1"
BAD_FLAGS = {"invalid_period","invalid_code","invalid_version_date",
             "non_primary_report_type","negative_assets_or_liabilities"}
BLOCKED = {"conflicting_non_null","invalid_numeric","no_value"}
SCHEMA = """
CREATE TABLE field_groups(
 id INTEGER PRIMARY KEY,api TEXT,code TEXT,period TEXT,comp_type TEXT,end_type TEXT,
 candidate_date TEXT,version_ids TEXT,decisions TEXT,quality TEXT,
 UNIQUE(api,code,period,comp_type,end_type,candidate_date));
CREATE TABLE period_selection(
 api TEXT,code TEXT,period TEXT,comp_type TEXT,group_id INTEGER,status TEXT,
 PRIMARY KEY(api,code,period,comp_type));
CREATE TABLE metrics(
 code TEXT,period TEXT,comp_type TEXT,metric TEXT,value REAL,unit TEXT,
 status TEXT,candidate_date_max TEXT,inputs TEXT,formula_version TEXT,
 PRIMARY KEY(code,period,comp_type,metric));
CREATE TABLE provider_checks(
 code TEXT,period TEXT,comp_type TEXT,metric TEXT,provider_field TEXT,
 calculated REAL,provider REAL,difference REAL,status TEXT,inputs TEXT,
 PRIMARY KEY(code,period,comp_type,metric,provider_field));
"""


def mainboard(code):
    return bool(re.fullmatch(r"(?:60[0135]\d{3}\.SH|00[0123]\d{3}\.SZ)",code))


def decisions(rows, fields):
    """Rows are (id,payload,flags). A consensus is NOT a canonical version.

    Any invalid non-null input blocks that field. Missing/explicit null values
    are counted separately. Only finite, exactly equal non-null values agree.
    """
    result = {}
    for f in fields:
        absent = null = invalid = 0
        vals = {}
        for vid,payload,flags in rows:
            if f not in payload:
                absent += 1
            elif payload[f] is None:
                null += 1
            elif num(payload[f]) is None or BAD_FLAGS.intersection(flags):
                invalid += 1
            else:
                vals[vid] = payload[f]
        if len(set(vals.values())) > 1:
            status = "conflicting_non_null"
        elif invalid:
            status = "invalid_numeric"
        elif not vals:
            status = "no_value"
        elif absent or null:
            status = "partial_consensus"
        else:
            status = "consensus"
        result[f] = {"state":status,"value":next(iter(vals.values())) if status in
                     ("consensus","partial_consensus") else None,
                     "version_ids":sorted(vals),"absent":absent,"null":null,"invalid":invalid,
                     "conflict_observations":[[vid,val] for vid,val in sorted(vals.items())]
                         if status=="conflicting_non_null" else []}
    return result


def group_record(key, rows):
    api,period,comp,end,eff = key
    flags = set().union(*(set(r[2]) for r in rows))
    quality = "eligible_research_group"
    if not day(period) or not day(eff) or day(eff)<day(period) or BAD_FLAGS.intersection(flags):
        quality = "invalid_group"
    elif not comp:
        quality = "unknown_company_type"
    return {"key":key,"ids":[r[0] for r in rows],"fields":decisions(rows,FIELDS[api]),
            "quality":quality,"flags":sorted(flags)}


def choose(groups):
    # Old archive omitted end_type. Do not silently map unknown end_type onto
    # a populated type; choose explicit groups only if latest date is unique
    # and no same-date contradictory legacy field is observed.
    if any(not g["key"][-1] for g in groups):
        return None,"unresolved_version_date"
    latest = max(g["key"][-1] for g in groups)
    candidates = [g for g in groups if g["key"][-1]==latest]
    if any(g["quality"]!="eligible_research_group" for g in candidates):
        return None,"invalid_latest_group"
    explicit = [g for g in candidates if g["key"][3]]
    legacy = [g for g in candidates if not g["key"][3]]
    if len(explicit)>1 or len(legacy)>1:
        return None,"ambiguous_report_period_type"
    if explicit:
        chosen = explicit[0]
        if legacy:
            # Compare legacy overlap, without filling unknown-type fields.
            for f,d in legacy[0]["fields"].items():
                e = chosen["fields"][f]
                if (d["state"] in ("conflicting_non_null","invalid_numeric") or
                    (d["value"] is not None and e["value"] is not None and d["value"]!=e["value"])):
                    return None,"legacy_overlap_conflict"
        return chosen,"selected_explicit_type"
    if len(legacy)==1:
        return legacy[0],"selected_unknown_type_diagnostic"
    return None,"no_group"


def extract(g, names):
    if not g:
        return None,[],"missing_or_blocked_period"
    # Unknown end_type kept for diagnostics, not for cross-period or derived metrics.
    if not g["key"][3]:
        return None,[],"unknown_report_period_type"
    ds = [g["fields"][f] for f in names]
    bad = next((d["state"] for d in ds if d["state"] in BLOCKED),None)
    if bad:
        return None,[],bad
    common = set(ds[0]["version_ids"])
    for d in ds[1:]:
        common.intersection_update(d["version_ids"])
    if not common:
        return None,[],"no_coherent_source_version"
    return [d["value"] for d in ds],sorted(common),"ok"


def prev_year(period):
    return str(int(period[:4])-1)+period[4:]


def prev_quarter(period):
    q=QUARTERS.index(period[4:])
    return period[:4]+QUARTERS[q-1] if q else None


def metric_builder(selected, code, period, comp):
    """Evaluate metric recipes with coherent per-statement inputs.

    Candidate-date max is diagnostic, not an availability date. All prior-period
    versions are chosen at the same ledger snapshot cutoff, so later revisions
    may be used. No historical signal date is accepted.
    """
    outputs = []
    def get(api,p,fields):
        g = selected.get((api,p,comp))
        values,ids,status = extract(g,fields)
        return values,{"api":api,"period":p,"fields":list(fields),
            "group_id":g.get("id") if g else None,"version_ids":ids,
            "candidate_date":g["key"][-1] if g else None,"status":status},status
    def emit(name,unit,requests,fn,applicable=True):
        inputs=[]; values=[]; statuses=[]
        for api,p,fields in requests:
            vals,info,status=get(api,p,fields)
            inputs.append(info);values.append(vals);statuses.append(status)
        status="ok"
        value=None
        if not applicable:
            status="industry_not_applicable_or_unverified"
        elif any(s!="ok" for s in statuses):
            status=next(s for s in statuses if s!="ok")
        else:
            try:
                value=num(fn(*values))
            except (ArithmeticError,ValueError,TypeError):
                value=None
            if value is None:
                status="invalid_denominator_or_value"
        # At a given period, do not mix incompatible explicit report-period types.
        if status=="ok":
            types=defaultdict(set)
            for api,p,fields in requests:
                types[p].add(selected[(api,p,comp)]["key"][3])
            if any(len(t)>1 for t in types.values()):
                value=None;status="report_period_type_mismatch"
        dates=[i["candidate_date"] for i in inputs if i["candidate_date"]]
        outputs.append((code,period,comp,name,value,unit,status,max(dates,default=""),
                        canonical(inputs),VERSION))
    b,i,c="balancesheet","income","cashflow"
    industrial=comp==INDUSTRIAL
    def req(api,fields,p=period):
        return (api,p,tuple(fields))
    def ytd(name,api,field):
        emit(name+"_ytd","CNY",[req(api,[field])],lambda v:v[0])
        previous=prev_quarter(period)
        rr=[req(api,[field])]
        if previous:
            rr.append(req(api,[field],previous))
        emit(name+"_quarter","CNY",rr,lambda *v:v[0][0]-(v[1][0] if len(v)>1 else 0))
        rr=ttm_requests(api,[field])
        emit(name+"_ttm","CNY",rr,lambda *v:ttm_values(v)[0])
    def ttm_requests(api,fields):
        rr=[req(api,fields)]
        if period[4:]!="1231":
            rr.extend([req(api,fields,prev_year(period)[:4]+"1231"),
                       req(api,fields,prev_year(period))])
        return rr
    def ttm_values(v):
        if len(v)==1:
            return v[0]
        return [a+b-c for a,b,c in zip(*v)]
    def book_ratio(a,b):
        return ratio(a,b)
    def nonnegative_ratio(a,b):
        return ratio(a,b) if a>=0 else None
    emit("ncav","CNY",[req(b,["total_cur_assets","total_liab"])],
         lambda v:v[0]-v[1] if min(v)>=0 else None,industrial)
    emit("book_value_per_share","CNY/share",[req(b,["total_hldr_eqy_exc_min_int","total_share"])],
         lambda v:book_ratio(*v))
    emit("debt_to_assets","ratio",[req(b,["total_liab","total_assets"])],
         lambda v:nonnegative_ratio(*v))
    emit("current_ratio","ratio",[req(b,["total_cur_assets","total_cur_liab"])],
         lambda v:nonnegative_ratio(*v),industrial)
    emit("quick_ratio","ratio",[req(b,["total_cur_assets","inventories","total_cur_liab"])],
         lambda v:nonnegative_ratio(v[0]-v[1],v[2]) if v[1]>=0 else None,industrial)
    emit("gross_margin_ytd","ratio",[req(i,["revenue","oper_cost"])],
         lambda v:ratio(v[0]-v[1],v[0]),industrial)
    for name,api,field in [("revenue",i,"revenue"),("parent_profit",i,"n_income_attr_p"),
                           ("net_profit",i,"n_income"),("operating_cashflow",c,"n_cashflow_act")]:
        ytd(name,api,field)
    cf=("n_cashflow_act","c_pay_acq_const_fiolta")
    def fcf(v):
        return v[0]-v[1] if v[1]>=0 else None
    emit("fcf_ytd","CNY",[req(c,cf)],fcf,industrial)
    rr=[req(c,cf)]
    if prev_quarter(period):
        rr.append(req(c,cf,prev_quarter(period)))
    emit("fcf_quarter","CNY",rr,lambda *v:fcf(v[0])-(fcf(v[1]) if len(v)>1 else 0)
         if all(x[1]>=0 for x in v) and (len(v)==1 or v[0][1]>=v[1][1]) else None,industrial)
    emit("fcf_ttm","CNY",ttm_requests(c,cf),
         lambda *v:fcf(ttm_values(v)) if all(x[1]>=0 for x in v) else None,industrial)
    for metric,profit,denom in [("roe_ttm","n_income_attr_p","total_hldr_eqy_exc_min_int"),
                                 ("roa_ttm","n_income","total_assets"),
                                 ("asset_turnover_ttm","revenue","total_assets")]:
        rr=ttm_requests(i,[profit])
        n=len(rr)
        rr.extend([req(b,[denom]),req(b,[denom],prev_year(period))])
        emit(metric,"ratio",rr,lambda *v,n=n:ratio(ttm_values(v[:n])[0],(v[n][0]+v[n+1][0])/2)
             if v[n][0]>0 and v[n+1][0]>0 else None)
    for name,field in [("revenue_yoy","revenue"),("parent_profit_yoy","n_income_attr_p")]:
        emit(name,"ratio",[req(i,[field]),req(i,[field],prev_year(period))],
             lambda a,b:ratio(a[0]-b[0],b[0]))
    # Signed profit transitions remain explicit in the underlying profit metrics;
    # no percentage growth from a nonpositive base.
    emit("cash_to_profit_ytd","ratio",[req(c,["n_cashflow_act"]),req(i,["n_income"])],
         lambda a,b:ratio(a[0],b[0]),industrial)
    # Industrial interest expense in finance costs, not int_exp (interest
    # expenditure on the financial-company income statement). This is a proxy.
    emit("ebit_pbt_plus_interest_ytd","CNY",[req(i,["total_profit","fin_exp_int_exp"])],
         lambda v:v[0]+v[1] if v[1]>=0 else None,industrial)
    return outputs


def process_code(src,out,code,asof,counts):
    selected={}
    periods=set()
    for api in FIELDS:
        rows=src.execute("""SELECT period,comp_type,end_type,candidate_date,id,payload,flags
            FROM versions WHERE api=? AND code=? AND report_type='1'
            AND period>='20180101' AND period<=?
            ORDER BY period,comp_type,end_type,candidate_date,id""",(api,code,asof))
        grouped=defaultdict(list)
        for key,items in itertools.groupby(rows,key=lambda r:r[:4]):
            p,comp,end,eff=key
            if not day(p) or p[4:] not in QUARTERS:
                counts["invalid_period_groups"]+=1
                continue
            versions=[(r[4],json.loads(r[5]),json.loads(r[6])) for r in items]
            g=group_record((api,p,comp,end,eff),versions)
            if eff and eff>asof:
                g["quality"]="invalid_group"
            g["id"]=out.execute("INSERT INTO field_groups(api,code,period,comp_type,end_type,candidate_date,version_ids,decisions,quality) VALUES (?,?,?,?,?,?,?,?,?)",
                (api,code,p,comp,end,eff,canonical(g["ids"]),canonical(g["fields"]),g["quality"])).lastrowid
            grouped[(p,comp)].append(g)
            for d in g["fields"].values():
                counts["field:"+d["state"]]+=1
        for (p,comp),groups in grouped.items():
            g,status=choose(groups)
            out.execute("INSERT INTO period_selection VALUES (?,?,?,?,?,?)",
                        (api,code,p,comp,g["id"] if g else None,status))
            counts["selection:"+status]+=1
            selected[(api,p,comp)]=g
            periods.add((p,comp))
    for p,comp in sorted(periods):
        # Never mix different company types; multiple company types are separate
        # diagnostics, not duplicate usable observations.
        metrics=metric_builder(selected,code,p,comp)
        out.executemany("INSERT INTO metrics VALUES (?,?,?,?,?,?,?,?,?,?)",metrics)
        for row in metrics:
            counts["metric:"+row[6]]+=1
        for metric,api,field in [("fcf_ytd","cashflow","free_cashflow"),
                                 ("ebit_pbt_plus_interest_ytd","income","ebit")]:
            calculated=next(x for x in metrics if x[3]==metric)
            g=selected.get((api,p,comp))
            provider,ids,state=extract(g,[field])
            pv=provider[0] if provider else None
            cv=calculated[4]
            diff=num(cv-pv) if cv is not None and pv is not None else None
            observations=(g["fields"][field]["conflict_observations"] if g else [])
            variants=[{"version_id":vid,"provider_value":val,"difference":num(cv-val) if cv is not None else None}
                      for vid,val in observations]
            status="compared_distinct_definitions" if diff is not None else (
                "compared_conflicting_provider_variants" if cv is not None and variants else
                "calculation_unavailable" if cv is None else state)
            out.execute("INSERT INTO provider_checks VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (code,p,comp,metric,field,cv,pv,diff,status,
                         canonical({"calculation":json.loads(calculated[8]),"provider_version_ids":ids,
                                    "provider_group_id":g["id"] if g else None,"provider_conflict_variants":variants})))
    return len(periods)


def build(ledger,output):
    ledger,output=ledger.resolve(),output.resolve()
    if output.exists() or ledger in output.parents or output in ledger.parents or output==ledger:
        raise ValueError("output must be new and outside input ledger")
    mp=ledger/"manifest.json"; source=ledger/"financial.sqlite3"
    mh=digest(mp)
    manifest=json.loads(mp.read_text())
    if (manifest.get("format_version")!=2 or manifest.get("state")!="standardized_staging_not_pit_certified"
        or manifest.get("pit_certified") is not False or manifest.get("production_published") is not False
        or source.stat().st_size!=manifest["ledger_bytes"] or digest(source)!=manifest["ledger_sha256"]):
        raise ValueError("unverified V5 input")
    asof=date.fromisoformat(manifest["asof"]).strftime("%Y%m%d")
    output.parent.mkdir(parents=True,exist_ok=True)
    tmp=Path(tempfile.mkdtemp(prefix=".fin19-metrics-",dir=output.parent))
    src=sqlite3.connect(source.as_uri()+"?mode=ro",uri=True)
    out=sqlite3.connect(tmp/"metrics.sqlite3")
    try:
        src.execute("PRAGMA query_only=ON")
        src.execute("PRAGMA cache_size=-32768")
        out.execute("PRAGMA cache_size=-32768")
        out.execute("PRAGMA temp_store=FILE")
        out.executescript(SCHEMA)
        codes=set()
        for api in FIELDS:
            codes.update(r[0] for r in src.execute("SELECT DISTINCT code FROM versions WHERE api=?",(api,))
                         if mainboard(r[0]))
        counts=Counter()
        if not codes:
            raise ValueError("no mainboard candidates")
        for n,code in enumerate(sorted(codes),1):
            counts["company_period_type_keys"]+=process_code(src,out,code,asof,counts)
            if n%50==0:
                out.commit()
                print("FIN19_METRICS_PROGRESS",n,len(codes),flush=True)
        out.commit()
        if out.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
            raise ValueError("output integrity failed")
        if out.execute("SELECT COUNT(*) FROM metrics WHERE (status='ok') != (value IS NOT NULL)").fetchone()[0]:
            raise ValueError("metric status reconciliation failed")
        if out.execute("SELECT COUNT(*) FROM metrics WHERE candidate_date_max>?",(asof,)).fetchone()[0]:
            raise ValueError("future candidate date")
        summary=out.execute("SELECT metric,status,COUNT(*) FROM metrics GROUP BY metric,status ORDER BY metric,status").fetchall()
        comparisons=out.execute("SELECT metric,status,COUNT(*) FROM provider_checks GROUP BY metric,status").fetchall()
        out.close();out=None
        src.close();src=None
        if digest(source)!=manifest["ledger_sha256"] or digest(mp)!=mh:
            raise ValueError("input changed during calculation")
        db=tmp/"metrics.sqlite3"
        doc={"state":"financial_metrics_research_not_pit_certified","format_version":1,
             "formula_version":VERSION,"input_ledger_sha256":manifest["ledger_sha256"],
             "input_manifest_sha256":mh,"asof":manifest["asof"],"scope":"mainboard_code_pattern",
             "codes":len(codes),"counts":dict(counts),"pit_certified":False,"production_published":False,
             "metrics_sha256":digest(db),"metrics_bytes":db.stat().st_size,
             "metric_status":[{"metric":m,"status":s,"rows":n} for m,s,n in summary],
             "provider_checks":[{"metric":m,"status":s,"rows":n} for m,s,n in comparisons],
             "limitations":["latest observed versions at collection cutoff; not historical as-of",
               "unknown end_type never used for derived metrics","no fallback to older date",
               "no complementary-version synthesis","no historical universe certification",
               "no ROIC EBITDA or market-cap/share-date joins","no independent revision certification",
               "provider FCF and EBIT definitions may differ","cross-period comparability not certified"]}
        (tmp/"manifest.json").write_text(json.dumps(doc,indent=2)+"\n")
        if output.exists():
            raise ValueError("output already exists")
        tmp.rename(output)
        print(json.dumps(doc,indent=2),flush=True)
    finally:
        if src is not None: src.close()
        if out is not None: out.close()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ledger",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args()
    build(a.ledger,a.output)


if __name__=="__main__":
    main()
