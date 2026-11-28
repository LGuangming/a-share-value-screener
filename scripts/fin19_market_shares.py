"""FIN19 V7 verified quarter-end market share evidence (offline).

Annual months must be committed and match their annual manifest. Current-year
daily inputs require open/closed calendar evidence through the period end.
Never forward-fill an individual security; a previous session is only a proxy.
"""
from datetime import date, timedelta
import json
from pathlib import Path

try:
    from .fin19_standardize import digest, num, day
except ImportError:
    from fin19_standardize import digest, num, day

PREFIX="a-share-backtest/01_raw_data"
DATASETS={"daily_prices","daily_basic","adj_factor","stk_limit","suspend_d"}


def read(path):
    if path.is_symlink():
        raise ValueError("symlink manifest: "+str(path))
    return json.loads(path.read_text(encoding="utf-8"))


def verified(path,meta):
    if (path.is_symlink() or not path.is_file() or type(meta.get("rows")) is not int
        or meta["rows"]<0 or path.stat().st_size!=meta.get("parquet_bytes",meta.get("bytes"))
        or digest(path)!=meta.get("sha256")):
        raise ValueError("invalid market archive: "+str(path))


def annual_source(raw,p):
    root=raw/"daily_basic"/f"year={p.year}"
    annual=root/"manifest.json"
    if not annual.exists():
        return None
    a=read(annual)
    if (a.get("format_version")!=1 or a.get("dataset")!="daily_basic"
        or a.get("year")!=p.year or len(a.get("months",[]))!=12
        or {m.get("month") for m in a["months"]}!={f"{i:02d}" for i in range(1,13)}):
        raise ValueError("invalid annual daily_basic manifest")
    month=f"{p.month:02d}"
    meta=next(m for m in a["months"] if m["month"]==month)
    path=root/f"month={month}.parquet"
    marker=root/f"month={month}.manifest.json"
    if (read(marker)!=meta or meta.get("format_version")!=1
        or meta.get("source")!="Tushare official" or meta.get("dataset")!="daily_basic"
        or meta.get("year")!=p.year or meta.get("object_key")!=PREFIX+"/"+path.relative_to(raw).as_posix()
        or "total_share" not in meta.get("fields",[])):
        raise ValueError("invalid monthly daily_basic manifest")
    session=day(meta.get("max_session"))
    minimum=day(meta.get("min_session"))
    if (not session or not minimum or minimum.year!=p.year or minimum.month!=p.month
        or session.year!=p.year or session.month!=p.month or not minimum<=session<=p
        or (p-session).days>10):
        raise ValueError("invalid quarter-end session")
    verified(path,meta)
    return path,meta,session,{str(annual):digest(annual),str(marker):digest(marker)}


def incremental_source(raw,p):
    evidence={}
    for offset in range(11):
        session=p-timedelta(days=offset)
        stamp=session.strftime("%Y%m%d")
        cal=raw/"trade_calendar"/f"year={session.year}"/f"month={session.month:02d}"/f"day={stamp}.json"
        if not cal.exists():
            return None # no evidence that all subsequent days were closed
        c=read(cal)
        if (c.get("trade_date")!=session.isoformat() or c.get("exchange")!="SSE"
            or type(c.get("is_open")) is not bool):
            raise ValueError("invalid market calendar: "+str(cal))
        evidence[str(cal)]=digest(cal)
        if not c["is_open"]:
            continue
        commit=raw/"_incremental"/"market"/f"year={session.year}"/f"day={stamp}.json"
        if not commit.exists():
            return None
        d=read(commit)
        records=d.get("datasets",[])
        if (d.get("status")!="verified_market_day" or d.get("trade_date")!=session.isoformat()
            or len(records)!=5 or {m.get("dataset") for m in records}!=DATASETS):
            raise ValueError("invalid daily commit: "+str(commit))
        meta=next(m for m in records if m["dataset"]=="daily_basic")
        path=raw/"daily_basic"/f"year={session.year}"/f"month={session.month:02d}"/f"day={stamp}.parquet"
        marker=path.with_suffix(".manifest.json")
        if (read(marker)!=meta or meta.get("format_version")!=1
            or meta.get("status")!="verified_daily_partition" or meta.get("trade_date")!=session.isoformat()
            or meta.get("object_key")!=PREFIX+"/"+path.relative_to(raw).as_posix()):
            raise ValueError("invalid daily partition")
        verified(path,meta)
        evidence.update({str(commit):digest(commit),str(marker):digest(marker)})
        return path,meta,session,evidence
    return None


def load_period(raw,period,con):
    p=day(period)
    if not p or p.strftime("%m%d") not in ("0331","0630","0930","1231"):
        raise ValueError("invalid report period")
    spec=annual_source(raw,p) or incremental_source(raw,p)
    if not spec:
        return {},{"period":period,"status":"market_period_unverified"}
    path,meta,session,evidence=spec
    rows,distinct,bad,min_day,max_day=con.execute("""
      SELECT count(*),count(DISTINCT (ts_code,trade_date)),
       count(*) FILTER (WHERE ts_code IS NULL OR trade_date IS NULL OR
          NOT regexp_full_match(ts_code,'[0-9]{6}\\.(SH|SZ|BJ)')),
       min(CAST(trade_date AS DATE)),max(CAST(trade_date AS DATE))
      FROM read_parquet(?,hive_partitioning=false)""",[str(path)]).fetchone()
    if (rows!=meta["rows"] or rows!=distinct or bad or max_day!=session
        or (meta.get("max_session") is not None and
           (min_day!=day(meta["min_session"]) or
            con.execute("SELECT count(DISTINCT trade_date) FROM read_parquet(?,hive_partitioning=false)",[str(path)]).fetchone()[0]!=meta["sessions"]))
        or (meta.get("trade_date") is not None and min_day!=session)):
        raise ValueError("market rows/calendar reconciliation failed")
    result={}
    for code,share in con.execute("""SELECT ts_code,total_share FROM
        read_parquet(?,hive_partitioning=false) WHERE CAST(trade_date AS DATE)=?""",
        [str(path),session]).fetchall():
        v=num(share)
        shares=num(v*10000) if v is not None and v>0 else None
        result[code]=shares
    if digest(path)!=meta["sha256"] or any(digest(Path(k))!=v for k,v in evidence.items()):
        raise ValueError("market input changed")
    return result,{"period":period,"status":"verified_market_session","session":session.strftime("%Y%m%d"),
        "session_relation":"same_calendar_day" if session==p else "previous_market_session_proxy",
        "path":str(path),"sha256":meta["sha256"],"manifest_hashes":evidence,
        "share_unit_source":"10000 shares","conversion":10000,"rows_on_session":len(result)}
