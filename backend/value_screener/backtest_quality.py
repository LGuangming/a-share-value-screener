"""BT03-D: verify published research partitions, not strategy readiness."""
from __future__ import annotations
import argparse
from datetime import date
import json
from pathlib import Path

from .archive_paths import RAW_PREFIX
from .backtest_dataset import inputs, incremental_inputs, read_json, digest, atomic_json


def expected(raw, year):
    if year <= 2025:
        return [(Path(f"year={year}/month={m:02d}"),year,m,None) for m in range(1,13)]
    result=[]
    for p in sorted((raw/"_incremental"/"market"/f"year={year}").glob("day=*.json")):
        value=p.stem.removeprefix("day=")
        d=date(int(value[:4]),int(value[4:6]),int(value[6:]))
        if d.year!=year or value!=d.strftime("%Y%m%d"):
            raise ValueError("Invalid committed day")
        result.append((Path(f"year={year}/month={d.month:02d}/day={value}"),year,d.month,d))
    return result


def verify_output(folder, item, prefix):
    name=item["file"]
    if name!=f"{prefix}-{item['sha256']}.parquet" or len(item["sha256"])!=64:
        raise ValueError("Invalid output identity")
    p=folder/name
    if p.is_symlink() or not p.is_file() or digest(p)!=item["sha256"]:
        raise ValueError("Output checksum mismatch")
    if "bytes" in item and p.stat().st_size!=item["bytes"]:
        raise ValueError("Output size mismatch")
    return p


def key_stats(db,path):
    return db.execute("SELECT count(*),count(DISTINCT(ts_code,trade_date)) FROM read_parquet(?,hive_partitioning=false)",[str(path)]).fetchone()


def compare_keys(db, market_path, other):
    return db.execute("""SELECT count(*) FROM (
      SELECT ts_code,trade_date FROM read_parquet(?,hive_partitioning=false)
      EXCEPT SELECT ts_code,trade_date FROM read_parquet(?,hive_partitioning=false)
      )""",[str(market_path),str(other)]).fetchone()[0]


def audit(archive,market,financial,security,scratch,year):
    import duckdb
    raw=archive/RAW_PREFIX
    partitions=expected(raw,year)
    if not partitions:
        raise ValueError("No expected source partitions")
    db=duckdb.connect(config={"threads":"1","memory_limit":"512MB","temp_directory":str(scratch)})
    report={"year":year,"expected_partitions":len(partitions),"market_verified":0,
            "financial_verified":0,"security_verified":0,"market_rows":0,"missing":[],"errors":[],
            "quality_totals":{},"formal_backtest_ready":False,
            "limitations":["revision_history_not_certified","historical_universe_not_certified",
              "corporate_actions_and_execution_not_certified","calendar_completeness_not_independently_verified"]}
    try:
        for rel,y,m,d in partitions:
            folder=market/rel
            marker=folder/"manifest.json"
            if not marker.is_file():
                report["missing"].append({"layer":"market","partition":str(rel)})
                continue
            try:
                src=incremental_inputs(raw,d) if d else inputs(raw,y,m)
                meta=read_json(marker)
                if (meta.get("version")!=1 or meta.get("year")!=y or meta.get("month")!=m
                    or (d and meta.get("trade_date")!=d.isoformat())
                    or meta.get("status")!="research_data_not_backtest_ready"):
                    raise ValueError("Market receipt identity mismatch")
                fingerprint={ds:{"sha256":info["sha256"],"rows":info["rows"]} for ds,(_,info) in src.items()}
                if meta["inputs"]!=fingerprint:
                    raise ValueError("Market source fingerprint mismatch")
                item=meta["outputs"]["market"]
                mp=verify_output(folder,item,"market")
                ep=verify_output(folder,meta["outputs"]["suspension_events"],"suspension_events")
                n,u=key_stats(db,mp)
                events=db.execute("SELECT count(*) FROM read_parquet(?)",[str(ep)]).fetchone()[0]
                if n!=u or n!=src["daily_prices"][1]["rows"] or n!=meta["quality"]["rows"] or events!=src["suspend_d"][1]["rows"]:
                    raise ValueError("Market grain/source counts mismatch")
                if compare_keys(db,src["daily_prices"][0],mp):
                    raise ValueError("Market source keys lost")
                report["market_verified"]+=1
                report["market_rows"]+=n
                for k,v in meta["quality"].items():
                    if type(v) is int:
                        report["quality_totals"][k]=report["quality_totals"].get(k,0)+v
            except Exception as exc:
                report["errors"].append({"layer":"market","partition":str(rel),"error":str(exc)})
                continue
            for layer,root,prefix,status in (("financial",financial,"financial","research_not_pit_certified"),
                    ("security",security,"security","security_evidence_not_certified")):
                if layer=="financial" and year>=2026:
                    continue
                folder=root/rel
                marker=folder/"manifest.json"
                if not marker.is_file():
                    report["missing"].append({"layer":layer,"partition":str(rel)})
                    continue
                try:
                    extra=read_json(marker)
                    if extra.get("status")!=status or extra["inputs"]["market_sha256"]!=item["sha256"]:
                        raise ValueError("Downstream receipt source mismatch")
                    other=verify_output(folder,extra,prefix)
                    count,unique=key_stats(db,other)
                    if count!=unique or count!=n or count!=extra["quality"]["rows"] or compare_keys(db,mp,other):
                        raise ValueError("Downstream keys/count mismatch")
                    if layer=="financial":
                        bad=db.execute("""SELECT count(*) FROM read_parquet(?)
                            WHERE pit_certified IS DISTINCT FROM false OR balance_disclosed>=trade_date
                            OR income_disclosed>=trade_date OR cashflow_disclosed>=trade_date""",[str(other)]).fetchone()[0]
                    else:
                        bad=db.execute("SELECT count(*) FROM read_parquet(?) WHERE st_certified IS DISTINCT FROM false",[str(other)]).fetchone()[0]
                    if bad:
                        raise ValueError("Uncertified evidence or future disclosure gate violated")
                    report[layer+"_verified"]+=1
                except Exception as exc:
                    report["errors"].append({"layer":layer,"partition":str(rel),"error":str(exc)})
    finally:
        db.close()
    report["status"]="integrity_failed" if report["errors"] else "incomplete_research_data" if report["missing"] else "research_partitions_verified_not_backtest_ready"
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ("archive","market","financial","security","scratch","output"):
        p.add_argument("--"+name,type=Path,required=True)
    p.add_argument("--year",type=int,required=True,choices=range(2020,2100))
    a=p.parse_args()
    target=a.output.resolve()
    roots=[getattr(a,k).resolve(strict=True) for k in ("archive","market","financial","security","scratch")]
    if target.exists() or not target.parent.is_dir() or any(x==target or x in target.parents for x in roots):
        p.error("Output must be a new file outside data directories")
    report=audit(*roots,a.year)
    atomic_json(target,report)
    print("BT03_DATA_QUALITY "+json.dumps({k:v for k,v in report.items() if k not in ("missing","errors")},sort_keys=True),flush=True)
    if report["errors"]:
        raise SystemExit(2)


if __name__=="__main__":
    main()
