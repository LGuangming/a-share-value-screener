"""FIN19 V7 quality sidecar: read-only V5/V6 plus verified market evidence.

Does not update original metrics, certify PIT, or publish data. Quarter-end
market share observations are separately named, not reported financial shares.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import date
import json
from pathlib import Path
import sqlite3
import tempfile

try:
    from .fin19_standardize import canonical,digest,num,ratio
    from .fin19_metrics import decisions,extract
    from .fin19_market_shares import load_period
except ImportError:
    from fin19_standardize import canonical,digest,num,ratio
    from fin19_metrics import decisions,extract
    from fin19_market_shares import load_period

VERSION="fin19-quality-v1"
SCHEMA="""
CREATE TABLE market_period(period TEXT PRIMARY KEY,evidence TEXT);
CREATE TABLE bvps(code TEXT,period TEXT,comp_type TEXT,reported REAL,market_candidate REAL,
 reported_status TEXT,market_status TEXT,shares REAL,source_session TEXT,inputs TEXT,
 PRIMARY KEY(code,period,comp_type));
CREATE TABLE accounting_checks(code TEXT,period TEXT,comp_type TEXT,check_name TEXT,
 status TEXT,residual REAL,tolerance REAL,inputs TEXT,PRIMARY KEY(code,period,comp_type,check_name));
CREATE TABLE comparison(code TEXT,period TEXT,comp_type TEXT,metric TEXT,provider_field TEXT,
 version_id INTEGER,calculated REAL,provider REAL,abs_difference REAL,relative_difference REAL,
 status TEXT,inputs TEXT,PRIMARY KEY(code,period,comp_type,metric,provider_field,version_id));
CREATE TABLE strategy_quality(code TEXT,period TEXT,comp_type TEXT,strategy TEXT,status TEXT,
 missing_metrics TEXT,PRIMARY KEY(code,period,comp_type,strategy));
"""
RULES={
 "balance_identity":("balancesheet",("total_assets","total_liab","total_hldr_eqy_inc_min_int"),(1,-1,-1)),
 "equity_identity":("balancesheet",("total_hldr_eqy_inc_min_int","total_hldr_eqy_exc_min_int","minority_int"),(1,-1,-1)),
 "cashflow_sections":("cashflow",("n_incr_cash_cash_equ","n_cashflow_act","n_cashflow_inv_act","n_cash_flows_fnc_act","eff_fx_flu_cash"),(1,-1,-1,-1,-1)),
 "cashflow_open_close":("cashflow",("c_cash_equ_end_period","c_cash_equ_beg_period","n_incr_cash_cash_equ"),(1,-1,-1)),
 "income_net_profit":("income",("n_income","total_profit","income_tax"),(1,-1,1)),
}
STRATEGIES={
 "net_net":("ncav",),
 "quality_momentum_financial":("roe_ttm","parent_profit_ttm","revenue_yoy","operating_cashflow_ttm"),
 "fcf_financial":("fcf_ttm","operating_cashflow_ttm"),
 "low_pb_financial":("book_value_per_share",),
}


def open_verified(root,name,hash_key,size_key,state,fmt):
    mp=root/"manifest.json"; path=root/name
    if mp.is_symlink() or path.is_symlink():
        raise ValueError("symlink input")
    mh=digest(mp)
    doc=json.loads(mp.read_text())
    if (doc.get("state")!=state or doc.get("format_version")!=fmt
        or doc.get("pit_certified") is not False or doc.get("production_published") is not False
        or path.stat().st_size!=doc[size_key] or digest(path)!=doc[hash_key]):
        raise ValueError("unverified input: "+str(root))
    db=sqlite3.connect(path.as_uri()+"?mode=ro",uri=True)
    db.execute("PRAGMA query_only=ON")
    db.execute("PRAGMA cache_size=-16384")
    return db,doc,{str(mp):mh,str(path):doc[hash_key]}


def check_identity(group,fields,signs):
    values,ids,status=extract(group,fields)
    if status!="ok":
        return status,None,None,ids
    residual=num(sum(v*s for v,s in zip(values,signs)))
    tol=max(1,max(abs(v) for v in values)*1e-6)
    if residual is None:
        return "invalid_numeric",None,tol,ids
    return ("within_tolerance" if abs(residual)<=tol else "outside_tolerance"),residual,tol,ids


def selected_group(src,metrics,api,code,period,comp,fields):
    r=metrics.execute("""SELECT g.id,g.end_type,g.candidate_date,g.version_ids
      FROM period_selection s JOIN field_groups g ON g.id=s.group_id
      WHERE s.api=? AND s.code=? AND s.period=? AND s.comp_type=?""",
      (api,code,period,comp)).fetchone()
    if not r:
        return None
    gid,end,eff,raw_ids=r
    rows=[]
    for vid in json.loads(raw_ids):
        item=src.execute("""SELECT api,code,period,comp_type,end_type,candidate_date,report_type,payload,flags
          FROM versions WHERE id=?""",(vid,)).fetchone()
        if not item or item[:7]!=(api,code,period,comp,end,eff,"1"):
            raise ValueError("sidecar version provenance mismatch")
        rows.append((vid,json.loads(item[7]),json.loads(item[8])))
    if not rows:
        raise ValueError("empty selected group")
    return {"key":(api,period,comp,end,eff),"id":gid,"fields":decisions(rows,fields)}


def share_candidate(group,shares,evidence):
    values,ids,status=extract(group,["total_hldr_eqy_exc_min_int"])
    if status!="ok":
        return None,status,ids
    if evidence["status"]!="verified_market_session":
        return None,"market_period_unverified",ids
    if shares is None:
        return None,"no_valid_share_on_market_session",ids
    value=ratio(values[0],shares)
    return value,(evidence["session_relation"] if value is not None else "invalid_denominator_or_value"),ids


def compare_values(calculated,provider):
    if calculated is None or provider is None:
        return None,None,"not_comparable"
    delta=num(abs(calculated-provider))
    relative=ratio(delta,abs(provider))
    return delta,relative,"diagnostic_difference"


def add_comparison(out,key,metric,field,vid,cv,pv,inputs,status=None):
    absolute,relative,state=compare_values(cv,pv)
    out.execute("INSERT INTO comparison VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
      (*key,metric,field,vid,cv,pv,absolute,relative,status or state,canonical(inputs)))


def process_code(src,metrics,out,code,market,counts):
    keys=metrics.execute("""SELECT DISTINCT period,comp_type FROM metrics WHERE code=?
      AND period>='20200101' ORDER BY period,comp_type""",(code,))
    for period,comp in keys:
        shares,evidence=market[period]
        key=(code,period,comp)
        groups={}
        for api in {r[0] for r in RULES.values()}:
            fields=set(f for a,fs,_ in RULES.values() if a==api for f in fs)
            if api=="balancesheet":
                fields.update(("total_hldr_eqy_exc_min_int","total_share"))
            groups[api]=selected_group(src,metrics,api,*key,sorted(fields))
        b=groups["balancesheet"]
        v,ids,status=extract(b,["total_hldr_eqy_exc_min_int","total_share"])
        reported=ratio(*v) if v is not None else None
        reported_status="ok" if reported is not None else ("invalid_denominator_or_value" if status=="ok" else status)
        market,market_status,market_ids=share_candidate(b,shares.get(code),evidence)
        out.execute("INSERT INTO bvps VALUES (?,?,?,?,?,?,?,?,?,?)",
          (*key,reported,market,reported_status,market_status,shares.get(code),evidence.get("session"),
           canonical({"reported_version_ids":ids,"equity_version_ids":market_ids,
             "group_id":b["id"] if b else None,"market_period":period})))
        counts["bvps_reported:"+reported_status]+=1
        counts["bvps_market:"+market_status]+=1
        for name,(api,fields,signs) in RULES.items():
            group=groups[api]
            status,residual,tol,ids=check_identity(group,fields,signs)
            out.execute("INSERT INTO accounting_checks VALUES (?,?,?,?,?,?,?,?)",
              (*key,name,status,residual,tol,canonical({"fields":fields,"signs":signs,"version_ids":ids,
                "group_id":group["id"] if group else None})))
            counts["check:"+name+":"+status]+=1
        values={r[0]:(r[1],r[2],r[3]) for r in metrics.execute(
          "SELECT metric,value,status,inputs FROM metrics WHERE code=? AND period=? AND comp_type=?",key)}
        for strategy,required in STRATEGIES.items():
            missing=[m for m in required if values.get(m,(None,"missing"))[1]!="ok"]
            status="financial_inputs_present" if not missing else "financial_inputs_incomplete"
            if strategy=="low_pb_financial" and missing and market is not None:
                status="market_share_candidate_only"
            out.execute("INSERT INTO strategy_quality VALUES (?,?,?,?,?,?)",
                        (*key,strategy,status,canonical(missing)))
            counts["strategy:"+strategy+":"+status]+=1
        # Existing FCF/EBIT comparisons include each conflicting provider variant.
        for metric,field,cv,pv,raw in metrics.execute(
          "SELECT metric,provider_field,calculated,provider,inputs FROM provider_checks WHERE code=? AND period=? AND comp_type=?",key):
            inputs=json.loads(raw)
            variants=inputs.get("provider_conflict_variants",[])
            if variants:
                for v in variants:
                    add_comparison(out,key,metric,field,v["version_id"],cv,v["provider_value"],inputs)
            else:
                add_comparison(out,key,metric,field,0,cv,pv,inputs)
        # Annual period only: compare provider ROE/ROA raw values under explicitly
        # labelled percentage assumption. Provider definitions are not equivalent.
        if period.endswith("1231"):
            for vid,raw in src.execute("""SELECT id,payload FROM versions
                WHERE api='fina_indicator' AND code=? AND period=?""",(code,period)):
                r=json.loads(raw)
                for metric,field in [("roe_ttm","roe"),("roa_ttm","roa"),("roa_ttm","npta")]:
                    cv=values.get(metric,(None,))[0]
                    pv=num(r.get(field))
                    add_comparison(out,key,metric,field,vid,cv*100 if cv is not None else None,pv,
                      {"unit":"percentage-point assumption; not definition matched",
                       "provider_ann_date":r.get("ann_date"),"provider_version_id":vid},
                      "annual_definition_unverified" if cv is not None and pv is not None else "not_comparable")


def build(ledger,metrics,raw,output):
    ledger,metrics,raw,output=(p.resolve() for p in (ledger,metrics,raw,output))
    if output.exists() or any(p==output or p in output.parents or output in p.parents for p in (ledger,metrics,raw)):
        raise ValueError("output must be new and separate from all inputs")
    # Only runtime dependency; no install or network access in the builder.
    import duckdb
    src,ld,hashes=open_verified(ledger,"financial.sqlite3","ledger_sha256","ledger_bytes",
                              "standardized_staging_not_pit_certified",2)
    md,m,other=open_verified(metrics,"metrics.sqlite3","metrics_sha256","metrics_bytes",
                            "financial_metrics_research_not_pit_certified",1)
    if m["input_ledger_sha256"]!=ld["ledger_sha256"] or m["asof"]!=ld["asof"] or m.get("formula_version")!="fin19-metrics-v1":
        raise ValueError("metric sidecar does not match V5 ledger")
    hashes.update(other)
    output.parent.mkdir(parents=True,exist_ok=True)
    tmp=Path(tempfile.mkdtemp(prefix=".fin19-quality-",dir=output.parent))
    out=sqlite3.connect(tmp/"quality.sqlite3")
    con=duckdb.connect(":memory:")
    try:
        con.execute("SET threads=1")
        con.execute("SET memory_limit='256MB'")
        out.execute("PRAGMA cache_size=-32768")
        out.executescript(SCHEMA)
        counts=Counter();market=[]
        periods=[r[0] for r in md.execute("SELECT DISTINCT period FROM metrics WHERE period>='20200101' ORDER BY period")]
        if not periods:
            raise ValueError("no formal periods")
        market_by_period={}
        for period in periods:
            shares,evidence=load_period(raw,period,con)
            market.append(evidence)
            market_by_period[period]=(shares,evidence)
            out.execute("INSERT INTO market_period VALUES (?,?)",(period,canonical(evidence)))
            print("FIN19_V7_MARKET",period,evidence["status"],flush=True)
        codes=[r[0] for r in md.execute("SELECT DISTINCT code FROM metrics ORDER BY code")]
        for n,code in enumerate(codes,1):
            process_code(src,md,out,code,market_by_period,counts)
            if n%50==0:
                out.commit()
                print("FIN19_V7_PROGRESS",n,len(codes),flush=True)
        out.commit()
        if out.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
            raise ValueError("quality database integrity failed")
        out.close();out=None
        for ev in market:
            if ev["status"]=="verified_market_session":
                hashes[ev["path"]]=ev["sha256"]
                hashes.update(ev["manifest_hashes"])
        if any(digest(Path(p))!=h for p,h in hashes.items()):
            raise ValueError("input changed during build")
        db=tmp/"quality.sqlite3"
        report={"state":"financial_quality_research_not_pit_certified","format_version":1,
          "formula_version":VERSION,"asof":m["asof"],"input_ledger_sha256":ld["ledger_sha256"],
          "input_metrics_sha256":m["metrics_sha256"],"pit_certified":False,"production_published":False,
          "quality_sha256":digest(db),"quality_bytes":db.stat().st_size,"counts":dict(counts),
          "market_periods":market,"limitations":["market session share is not reported quarter-end share",
          "no forward fill for missing securities","annual ROE/ROA differences are diagnostic, not equivalence",
          "strategy flags are financial input presence only, not eligibility or buy signals",
          "accounting checks use coherent selected groups only; no automatic repairs","no EBITDA or ROIC derivation"]}
        (tmp/"manifest.json").write_text(json.dumps(report,indent=2)+"\n")
        if output.exists():
            raise ValueError("output appeared")
        tmp.rename(output)
        print(json.dumps(report,indent=2),flush=True)
    finally:
        src.close();md.close();con.close()
        if out is not None: out.close()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ("ledger","metrics","raw","output"):
        p.add_argument("--"+name,type=Path,required=True)
    a=p.parse_args()
    build(a.ledger,a.metrics,a.raw,a.output)


if __name__=="__main__":
    main()
