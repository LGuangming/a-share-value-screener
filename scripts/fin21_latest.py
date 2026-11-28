"""FIN21: current research view of a pinned FIN20 local archive.

Not point-in-time, not a historical universe, not a trading eligibility screen.
Read only manifest-listed Parquet; no SQLite source database or network needed.
"""
from __future__ import annotations
import argparse
from contextlib import closing
import json
from pathlib import Path
import tempfile
import re

try:
    from . import fin20_archive as a
    from . import fin19_market_shares as market
    from .fin19_standardize import day,digest
except ImportError:
    import fin20_archive as a
    import fin19_market_shares as market
    from fin19_standardize import day,digest

VERSION="fin21-latest-v1"
CODE_PATTERN="(60[0135][0-9]{3}[.]SH|00[0123][0-9]{3}[.]SZ)"
METRICS=("ncav","book_value_per_share","debt_to_assets","current_ratio","quick_ratio",
         "gross_margin_ytd",*[base+"_"+suffix for base in
         ("revenue","parent_profit","net_profit","operating_cashflow","fcf")
         for suffix in ("ytd","quarter","ttm")],"roe_ttm","roa_ttm","asset_turnover_ttm",
         "revenue_yoy","parent_profit_yoy","cash_to_profit_ytd","ebit_pbt_plus_interest_ytd")
APIS=("balancesheet","income","cashflow")
TABLES=("financial_statements","statement_versions","financial_metric_detail",
        "financial_metrics","valuation_metrics","financial_quality","strategy_inputs")


def snapshot(root,raw,approve):
    if not re.fullmatch("[0-9a-f]{64}",approve) or digest(root/"manifest.json")!=approve:
        raise ValueError("snapshot manifest approval mismatch")
    doc=a.manifest(root)
    if root.parent!=raw.parent/"financial_snapshots" or root.name!="snapshot="+doc["asof"]+"-"+approve[:16]:
        raise ValueError("not a published local FIN20 snapshot")
    # All evidence and logical rows, not just selected partitions, are validated.
    a.verify(root,raw)
    return doc


def view(con,root,doc,layer,name):
    t=doc["layers"][layer]["tables"][name]
    cols=doc["layers"][layer]["schema"]["tables"][name]["columns"]
    files=[str(a.safe(root,f["path"])) for p in t["partitions"] for f in p["files"]]
    if files:
        con.execute("CREATE TEMP VIEW "+a.quote(name)+" AS SELECT "+
                    ",".join(a.quote(c["name"]) for c in cols)+" FROM read_parquet(["+
                    ",".join(a.literal(f) for f in files)+"],hive_partitioning=false)")
    else:
        con.execute("CREATE TEMP VIEW "+a.quote(name)+" AS SELECT "+
            ",".join("NULL::"+a.TYPES[c["type"]]+" AS "+a.quote(c["name"]) for c in cols)+" WHERE false")


def market_day(raw,asof,requested):
    cutoff=day(asof.replace("-",""))
    if not cutoff:
        raise ValueError("invalid snapshot cutoff")
    if requested:
        selected=day(requested.replace("-",""))
        if not selected or selected>cutoff:
            raise ValueError("market day after snapshot cutoff or invalid")
        return selected
    dates=[]
    folder=raw/"_incremental/market"
    for path in folder.glob("year=*/day=*.json"):
        d=day(path.stem.removeprefix("day="))
        if d and d<=cutoff and path.parent.name=="year="+str(d.year):
            dates.append(d)
    if not dates:
        raise ValueError("no committed market day at or before snapshot cutoff")
    # Never silently fall back if newest observed marker is corrupt.
    return max(dates)


def market_views(con,raw,selected):
    stamp=selected.strftime("%Y%m%d")
    suffix=f"year={selected.year}/month={selected.month:02d}/day={stamp}"
    commit=raw/"_incremental/market"/f"year={selected.year}/day={stamp}.json"
    cal=raw/"trade_calendar"/(suffix+".json")
    evidence={}
    def read(path):
        info=a.file_info(path);doc=json.loads(path.read_text());a.check(path,info)
        evidence[path.relative_to(raw).as_posix()]=info
        return doc
    d,c=read(commit),read(cal)
    if (d.get("status")!="verified_market_day" or d.get("trade_date")!=selected.isoformat()
        or c.get("trade_date")!=selected.isoformat() or c.get("exchange")!="SSE" or c.get("is_open") is not True):
        raise ValueError("uncommitted session")
    records=d.get("datasets",[])
    if len(records)!=5 or {x.get("dataset") for x in records}!=market.DATASETS:
        raise ValueError("incomplete market commit")
    for meta in records:
        ds=meta["dataset"]
        path=raw/ds/(suffix+".parquet")
        relative=path.relative_to(raw).as_posix()
        if (meta.get("object_key")!=market.PREFIX+"/"+relative or meta.get("trade_date")!=selected.isoformat()
            or meta.get("status")!="verified_daily_partition" or meta.get("format_version")!=1
            or read(path.with_suffix(".manifest.json"))!=meta):
            raise ValueError("daily partition manifest mismatch")
        market.verified(path,meta)
        evidence[relative]=a.file_info(path)
        if evidence[relative]["sha256"]!=meta["sha256"]:
            raise ValueError("market source changed")
        if ds not in ("daily_prices","daily_basic"):
            continue
        con.execute("CREATE TEMP TABLE "+ds+" AS SELECT * FROM read_parquet(?,hive_partitioning=false)",[str(path)])
        n,unique,bad=con.execute(f"""SELECT count(*),count(DISTINCT ts_code),
            count(*) FILTER(WHERE ts_code IS NULL OR NOT regexp_full_match(ts_code,'[0-9]{{6}}[.](SH|SZ|BJ)')
              OR trade_date IS NULL OR CAST(trade_date AS DATE)!=?) FROM {ds}""",[selected]).fetchone()
        if not n or n!=meta["rows"] or n!=unique or bad:
            raise ValueError("market grain/count mismatch")
    return evidence


def select_latest(con,asof):
    cutoff=asof.replace("-","")
    # Scope is observed financial mainboard codes plus current-session mainboard
    # market codes. Latest *observed* period, not latest complete period.
    con.execute("""CREATE TEMP TABLE latest AS
        WITH periods AS (SELECT DISTINCT code,period,comp_type FROM metrics
            WHERE period BETWEEN '20200101' AND ? AND regexp_full_match(code,?)),
        maxima AS (SELECT code,max(period) period FROM periods GROUP BY code),
        latest_types AS (SELECT p.code,p.period,count(*) AS type_count,
            CASE WHEN count(*)=1 THEN min(comp_type) END comp_type
            FROM periods p JOIN maxima m USING(code,period) GROUP BY p.code,p.period),
        universe AS (SELECT code FROM maxima UNION
            SELECT ts_code FROM daily_prices WHERE regexp_full_match(ts_code,?) UNION
            SELECT ts_code FROM daily_basic WHERE regexp_full_match(ts_code,?))
        SELECT u.code,t.period,t.comp_type,coalesce(t.type_count,0) type_count,
          CASE WHEN t.period IS NULL THEN 'no_observed_financial_period'
               WHEN t.type_count!=1 OR t.comp_type IS NULL OR t.comp_type='' THEN 'ambiguous_or_unknown_company_type'
               ELSE 'latest_observed_period' END selection_status
        FROM universe u LEFT JOIN latest_types t USING(code)""",[cutoff,CODE_PATTERN,CODE_PATTERN,CODE_PATTERN])


def build_views(con,asof,selected):
    select_latest(con,asof)
    con.execute("""CREATE TEMP TABLE financial_statements AS
        SELECT l.code,l.period,l.comp_type,x.api,l.selection_status,
          CASE WHEN l.selection_status!='latest_observed_period' THEN l.selection_status
               ELSE coalesce(s.status,'missing_statement') END statement_status,
          s.group_id,g.end_type,g.candidate_date,g.version_ids,g.decisions
        FROM latest l CROSS JOIN (VALUES ('balancesheet'),('income'),('cashflow')) x(api)
        LEFT JOIN period_selection s ON l.selection_status='latest_observed_period'
          AND (l.code,l.period,l.comp_type,x.api)=(s.code,s.period,s.comp_type,s.api)
        LEFT JOIN field_groups g ON s.group_id=g.id""")
    # Preserve actual version rows, never synthesize a complete report from
    # different source versions. Unknown-type groups remain diagnostic only.
    con.execute("""CREATE TEMP TABLE statement_versions AS
        SELECT s.code,s.period,s.comp_type,s.api,s.statement_status,s.group_id,
               v.id version_id,v.row_hash,v.report_type,v.end_type,v.candidate_date,v.payload,v.flags
        FROM financial_statements s CROSS JOIN LATERAL json_each(s.version_ids) j
        JOIN versions v ON v.id=CAST(j.value AS BIGINT)
        WHERE (v.code,v.period,v.comp_type,v.api)=(s.code,s.period,s.comp_type,s.api)
          AND v.report_type='1' AND v.end_type=s.end_type AND v.candidate_date=s.candidate_date""")
    expected=con.execute("SELECT coalesce(sum(json_array_length(version_ids)),0) FROM financial_statements").fetchone()[0]
    if con.execute("SELECT count(*) FROM statement_versions").fetchone()[0]!=expected:
        raise ValueError("statement version lineage mismatch")
    con.execute("""CREATE TEMP TABLE financial_metric_detail AS
        SELECT l.code,l.period,l.comp_type,x.metric,
            CASE WHEN m.status='ok' THEN m."value" END AS "value",m.unit,
            CASE WHEN l.selection_status!='latest_observed_period' THEN l.selection_status
                 ELSE coalesce(m.status,'metric_missing') END status,
            m.candidate_date_max,m.inputs,m.formula_version
        FROM latest l CROSS JOIN (SELECT unnest(?::VARCHAR[]) metric) x
        LEFT JOIN metrics m ON l.selection_status='latest_observed_period'
          AND (l.code,l.period,l.comp_type,x.metric)=(m.code,m.period,m.comp_type,m.metric)""",[list(METRICS)])
    fields=[]
    for metric in METRICS:
        fields += [f"max(d.\"value\") FILTER(WHERE d.metric='{metric}') AS {metric}",
                   f"max(d.status) FILTER(WHERE d.metric='{metric}') AS {metric}_status"]
    con.execute("CREATE TEMP TABLE financial_metrics AS SELECT l.*,"+",".join(fields)+
        " FROM latest l JOIN financial_metric_detail d USING(code) GROUP BY ALL")
    # Market valuation uses the exact same requested day; no prior-security-day
    # fill. All ratios compare latest observed report with market date, not PIT.
    con.execute("""CREATE TEMP TABLE valuation_metrics AS WITH market_data AS (
        SELECT l.code,l.period,l.comp_type,?::DATE market_date,
          CASE WHEN isfinite(p."close") AND p."close">0 THEN p."close" END close_cny,
          CASE WHEN isfinite(b.total_mv) AND b.total_mv>0 THEN b.total_mv*10000 END market_cap_cny,
          CASE WHEN isfinite(b.total_share) AND b.total_share>0 THEN b.total_share*10000 END market_shares,
          CASE WHEN isfinite(b.pb) THEN b.pb END provider_pb,
          CASE WHEN isfinite(b.pe_ttm) THEN b.pe_ttm END provider_pe_ttm,
          p.ts_code IS NOT NULL has_price,b.ts_code IS NOT NULL has_daily_basic,
          q.reported reported_bvps,q.reported_status,
          q.market_candidate period_market_bvps,q.market_status period_market_bvps_status,
          q.source_session period_share_session,
          CASE WHEN s.statement_status='selected_explicit_type'
            AND json_extract_string(s.decisions,'$.total_hldr_eqy_exc_min_int.state') IN ('consensus','partial_consensus')
            THEN try_cast(json_extract(s.decisions,'$.total_hldr_eqy_exc_min_int.value') AS DOUBLE) END parent_equity_cny,
          json_extract_string(s.decisions,'$.total_hldr_eqy_exc_min_int.state') parent_equity_field_status,
          json_extract(s.decisions,'$.total_hldr_eqy_exc_min_int.version_ids') parent_equity_version_ids,
          f.ncav,f.parent_profit_ttm,f.fcf_ttm
        FROM latest l JOIN financial_metrics f USING(code)
        LEFT JOIN daily_prices p ON p.ts_code=l.code
        LEFT JOIN daily_basic b ON b.ts_code=l.code
        LEFT JOIN bvps q ON l.selection_status='latest_observed_period' AND
          (l.code,l.period,l.comp_type)=(q.code,q.period,q.comp_type)
        LEFT JOIN financial_statements s ON s.code=l.code AND s.api='balancesheet')
        SELECT *,date_diff('day',try_strptime(period,'%Y%m%d')::DATE,market_date) report_age_days,
          CASE WHEN market_cap_cny>0 AND parent_equity_cny>0
              THEN market_cap_cny/parent_equity_cny END pb_cap_to_reported_equity,
          CASE WHEN market_cap_cny>0 AND parent_profit_ttm>0
              THEN market_cap_cny/parent_profit_ttm END pe_latest_ttm,
          CASE WHEN market_cap_cny>0 AND ncav>0 THEN market_cap_cny/ncav END cap_to_positive_ncav,
          CASE WHEN market_cap_cny>0 AND fcf_ttm IS NOT NULL THEN fcf_ttm/market_cap_cny END fcf_yield,
          CASE WHEN NOT has_price THEN 'no_same_day_price'
               WHEN NOT has_daily_basic THEN 'no_same_day_valuation'
               WHEN close_cny IS NULL OR market_cap_cny IS NULL OR market_shares IS NULL THEN 'invalid_market_inputs'
               ELSE 'market_inputs_present' END market_status
        FROM market_data""",[selected])
    con.execute("""CREATE TEMP TABLE financial_quality AS WITH checks AS (
        SELECT l.code,count(c.check_name) check_count,
            count(*) FILTER(WHERE c.status='outside_tolerance') outside_tolerance,
            count(*) FILTER(WHERE c.status='within_tolerance') within_tolerance,
            count(*) FILTER(WHERE c.status NOT IN ('within_tolerance','outside_tolerance')) unavailable,
            list(struct_pack(check_name:=c.check_name,status:=c.status,residual:=c.residual,tolerance:=c.tolerance)
              ORDER BY c.check_name) FILTER(WHERE c.check_name IS NOT NULL) check_details
        FROM latest l LEFT JOIN accounting_checks c ON l.selection_status='latest_observed_period'
          AND (l.code,l.period,l.comp_type)=(c.code,c.period,c.comp_type) GROUP BY l.code)
        SELECT l.*,c.check_count,c.outside_tolerance,c.within_tolerance,c.unavailable,c.check_details
        FROM latest l JOIN checks c USING(code)""")
    con.execute("""CREATE TEMP TABLE strategy_inputs AS
        SELECT l.code,l.period,l.comp_type,x.strategy,
          coalesce(s.status,CASE WHEN l.selection_status='latest_observed_period' THEN 'quality_record_missing' ELSE l.selection_status END) financial_status,s.missing_metrics,
          v.market_date,v.market_status,
          CASE WHEN x.strategy='net_net' THEN v.cap_to_positive_ncav IS NOT NULL
               WHEN x.strategy='low_pb_financial' THEN v.pb_cap_to_reported_equity IS NOT NULL
               WHEN x.strategy='fcf_financial' THEN v.fcf_yield IS NOT NULL
               ELSE v.market_status='market_inputs_present' END valuation_inputs_present,
          q.outside_tolerance,q.unavailable,
          false AS eligibility_certified
        FROM latest l CROSS JOIN (VALUES ('net_net'),('low_pb_financial'),('fcf_financial'),('quality_momentum_financial')) x(strategy)
        LEFT JOIN strategy_quality s ON l.selection_status='latest_observed_period'
          AND (l.code,l.period,l.comp_type,x.strategy)=(s.code,s.period,s.comp_type,s.strategy)
        JOIN valuation_metrics v USING(code) JOIN financial_quality q USING(code)""")
    n=con.execute("SELECT count(*) FROM latest").fetchone()[0]
    if not n:
        raise ValueError("empty latest universe")
    for name,mult,key in (("financial_statements",3,"code,api"),("financial_metric_detail",len(METRICS),"code,metric"),
       ("financial_metrics",1,"code"),("valuation_metrics",1,"code"),("financial_quality",1,"code"),
       ("strategy_inputs",4,"code,strategy")):
        count,unique=con.execute(f"SELECT count(*),count(DISTINCT ({key})) FROM {name}").fetchone()
        if count!=n*mult or count!=unique:
            raise ValueError("output grain mismatch: "+name)
    return n


def build(root,raw,approve,output,requested=None):
    root,raw,output=(p.resolve() for p in (root,raw,output))
    a.separate(output,root,raw)
    doc=snapshot(root,raw,approve)
    selected=market_day(raw,doc["asof"],requested)
    output.parent.mkdir(parents=True,exist_ok=True)
    tmp=Path(tempfile.mkdtemp(prefix=".fin21-",dir=output.parent))
    files={}
    with closing(a.connection()) as con:
        con.execute("SET temp_directory="+a.literal(tmp/"spill"))
        for layer,names in (("ledger",("versions",)),("metrics",("field_groups","period_selection","metrics")),
             ("quality",("bvps","accounting_checks","strategy_quality"))):
            for name in names:
                view(con,root,doc,layer,name)
        evidence=market_views(con,raw,selected)
        count=build_views(con,doc["asof"],selected)
        for name in TABLES:
            path=tmp/(name+".parquet")
            con.execute("COPY (SELECT * FROM "+name+" ORDER BY code) TO "+a.literal(path)+" (FORMAT PARQUET, COMPRESSION ZSTD)")
            rows=con.execute("SELECT count(*) FROM "+name).fetchone()[0]
            if con.execute("SELECT count(*) FROM read_parquet(?)",[str(path)]).fetchone()[0]!=rows:
                raise ValueError("output rows mismatch")
            # Full logical multiset reconciliation before publishing staged files.
            diff=con.execute("SELECT count(*) FROM ((SELECT * FROM "+name+" EXCEPT ALL SELECT * FROM read_parquet(?)) UNION ALL (SELECT * FROM read_parquet(?) EXCEPT ALL SELECT * FROM "+name+"))",[str(path),str(path)]).fetchone()[0]
            if diff:
                raise ValueError("output row mismatch")
            files[name]={"path":path.name,"rows":rows,**a.file_info(path)}
            print("FIN21_TABLE",name,rows,flush=True)
        stats={name:con.execute(sql).fetchall() for name,sql in {
            "selection":"SELECT selection_status,count(*) FROM latest GROUP BY 1 ORDER BY 1",
            "period":"SELECT period,count(*) FROM latest GROUP BY 1 ORDER BY 1",
            "market":"SELECT market_status,count(*) FROM valuation_metrics GROUP BY 1 ORDER BY 1",
            "metrics":"SELECT metric,status,count(*) FROM financial_metric_detail GROUP BY 1,2 ORDER BY 1,2",
            "strategy":"SELECT strategy,financial_status,valuation_inputs_present,count(*) FROM strategy_inputs GROUP BY 1,2,3 ORDER BY 1,2,3",
            "quality":"SELECT count(*) FILTER(WHERE outside_tolerance>0),sum(outside_tolerance),sum(unavailable) FROM financial_quality"
        }.items()}
    # Fail closed if any source changed during the run.
    if digest(root/"manifest.json")!=approve:
        raise ValueError("snapshot changed")
    for layer in doc["layers"].values():
        for table in layer["tables"].values():
            for part in table["partitions"]:
                for f in part["files"]:
                    a.check(a.safe(root,f["path"]),f)
    for f in doc["evidence_files"]:
        a.check(a.safe(root,f["path"]),f)
    for f in doc["existing_raw_references"]+doc["market_references"]:
        a.check(a.safe(raw,f["path"]),f)
    for relative,info in evidence.items():
        a.check(a.safe(raw,relative),info)
    report={"state":"latest_financial_research_staged","formula_version":VERSION,
       "asof":doc["asof"],"market_date":selected.isoformat(),
       "market_selection":"explicit_committed_day" if requested else "latest_observed_committed_day_at_or_before_snapshot",
       "snapshot_manifest_sha256":approve,"snapshot":str(root),"market_evidence":evidence,
       "pit_certified":False,"production_published":False,"historical_universe_certified":False,
       "codes":count,"files":files,"statistics":stats,
       "limitations":["latest observed period only, no fallback to earlier complete period",
         "not information available on the market date; later versions may be included",
         "observed code pattern universe; listing/ST/tradability not certified",
         "financial statements preserve selected raw versions and field decisions, not synthetic statements",
         "market-cap ratios use latest observed financial period, not provider-equivalent definitions",
         "no current price or shares forward fill","no production database or website reconciliation"]}
    (tmp/"manifest.json").write_text(json.dumps(report,indent=2,ensure_ascii=False)+"\n")
    if output.exists():
        raise ValueError("output appeared")
    tmp.rename(output)
    print("FIN21_STAGED",output,flush=True)
    print("FIN21_MANIFEST_SHA256",digest(output/"manifest.json"),flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ("snapshot","raw","output"):
        p.add_argument("--"+name,type=Path,required=True)
    p.add_argument("--approve",required=True)
    p.add_argument("--market-day",help="exact YYYY-MM-DD committed session; default latest observed commit <= snapshot cutoff")
    x=p.parse_args()
    build(x.snapshot,x.raw,x.approve,x.output,x.market_day)


if __name__=="__main__":
    main()
