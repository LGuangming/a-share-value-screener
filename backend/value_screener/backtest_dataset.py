"""BT03 first slice: offline, manifest-verified completed-year market partitions."""
from __future__ import annotations
import argparse
from datetime import date
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile

from .archive_paths import RAW_PREFIX
from .neon_archive import validate_manifest

VERSION = 1
MARKET = ("daily_prices", "adj_factor", "daily_basic", "stk_limit", "suspend_d", "historical_st")
MAIN = r"^(600|601|603|605)[0-9]{3}[.]SH$|^(000|001|002|003)[0-9]{3}[.]SZ$"


def digest(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def verified(path, meta):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Missing or symlink source: {path}")
    size = meta.get("bytes", meta.get("parquet_bytes"))
    if path.stat().st_size != size or digest(path) != meta["sha256"]:
        raise ValueError(f"Source checksum/size mismatch: {path}")
    if type(meta.get("rows")) is not int or meta["rows"] < 0:
        raise ValueError(f"Invalid source row count: {path}")
    return path, meta


def inputs(raw, year, month):
    # Use published annual manifests, never wildcard historical Parquet readers.
    suffix = f"year={year}/month={month:02d}.parquet"
    p = raw / "daily_prices" / f"year={year}" / "manifest.json"
    doc = read_json(p)
    validate_manifest(doc, year)
    part = next(m for m in doc["partition_files"] if m["path"] == "daily_prices/" + suffix)
    result = {"daily_prices": verified(raw / part["path"], part)}
    for ds in MARKET[1:-1]:
        annual = read_json(raw / ds / f"year={year}" / "manifest.json")
        if (annual.get("dataset") != ds or annual.get("year") != year
                or annual.get("format_version") != 1 or len(annual.get("months", [])) != 12):
            raise ValueError(f"Invalid annual manifest: {ds}/{year}")
        key = f"{RAW_PREFIX}/{ds}/{suffix}"
        matches = [m for m in annual["months"] if m.get("object_key") == key]
        if len(matches) != 1:
            raise ValueError(f"Missing or repeated month: {key}")
        manifest = raw / ds / f"year={year}" / f"month={month:02d}.manifest.json"
        if read_json(manifest) != matches[0]:
            raise ValueError(f"Monthly/annual manifest mismatch: {key}")
        result[ds] = verified(raw / ds / suffix, matches[0])
    choices = sorted((raw / "historical_st" / "v2" / "range=2020-2025").glob(
        f"universe=*/merged/year={year}/manifest.json"))
    if len(choices) != 1:
        raise ValueError(f"Expected exactly one merged ST manifest for {year}")
    st = read_json(choices[0])
    if st.get("year") != year or st.get("classification_version") != 1 or len(st.get("months", [])) != 12:
        raise ValueError("Invalid merged ST manifest")
    matches = [m for m in st["months"] if m.get("month") == month]
    if len(matches) != 1:
        raise ValueError("Missing or repeated ST month")
    path = choices[0].parent / f"month={month:02d}.parquet"
    key = RAW_PREFIX + "/" + path.relative_to(raw).as_posix()
    if matches[0].get("object_key") != key or matches[0]["rows"] != part["rows"]:
        raise ValueError("ST manifest does not reconcile to OHLCV")
    result["historical_st"] = verified(path, matches[0])
    return result


def incremental_inputs(raw, day):
    """Only consume committed days; ignore reader snapshots and orphan files."""
    year, month = day.year, day.month
    stamp = day.strftime("%Y%m%d")
    commit = read_json(raw / "_incremental" / "market" / f"year={year}" / f"day={stamp}.json")
    calendar = read_json(raw / "trade_calendar" / f"year={year}" / f"month={month:02d}" / f"day={stamp}.json")
    if (commit.get("status") != "verified_market_day" or commit.get("trade_date") != day.isoformat()
            or calendar.get("trade_date") != day.isoformat() or calendar.get("is_open") is not True
            or calendar.get("exchange") != "SSE"):
        raise ValueError("Uncommitted market day or missing open-session evidence")
    records = commit.get("datasets", [])
    if len(records) != 5 or {m.get("dataset") for m in records} != set(MARKET[:-1]):
        raise ValueError("Incomplete day manifest")
    result = {}
    for meta in records:
        ds = meta["dataset"]
        path = raw / ds / f"year={year}" / f"month={month:02d}" / f"day={stamp}.parquet"
        key = RAW_PREFIX + "/" + path.relative_to(raw).as_posix()
        if (meta.get("object_key") != key or meta.get("trade_date") != day.isoformat()
                or meta.get("status") != "verified_daily_partition" or meta.get("format_version") != 1
                or read_json(path.with_suffix(".manifest.json")) != meta):
            raise ValueError(f"Daily manifest mismatch: {key}")
        result[ds] = verified(path, meta)
    return result


def build_table(db, sources, year, month, day=None):
    # Source schemas store trade_date as DATE. Explicitly reject invalid keys.
    if set(sources) != set(MARKET if day is None else MARKET[:-1]):
        raise ValueError("Unexpected source set")
    for ds in sources:
        path, meta = sources[ds]
        db.execute(f"CREATE OR REPLACE TEMP TABLE {ds} AS SELECT * FROM read_parquet(?, hive_partitioning=false)", [str(path)])
        n, bad = db.execute(f"""
            SELECT count(*), count(*) FILTER (WHERE ts_code IS NULL OR
                NOT regexp_full_match(ts_code, '[0-9]{{6}}[.](SH|SZ|BJ)')
                OR trade_date IS NULL OR year(trade_date) != ? OR month(trade_date) != ?)
            FROM {ds}""", [year, month]).fetchone()
        if day is not None:
            bad += db.execute(f"SELECT count(*) FROM {ds} WHERE trade_date != ?", [day]).fetchone()[0]
        if n != meta["rows"] or bad:
            raise ValueError(f"Source count/key mismatch: {ds}")
        if ds != "suspend_d":
            unique = db.execute(f"SELECT count(DISTINCT (ts_code, trade_date)) FROM {ds}").fetchone()[0]
            if unique != n:
                raise ValueError(f"Duplicate source keys: {ds}")
    if day is not None:
        # Never infer historical ST from current names. Explicit null evidence until a separate source is certified.
        db.execute("""CREATE OR REPLACE TEMP TABLE historical_st AS SELECT
          NULL::VARCHAR ts_code, NULL::DATE trade_date, NULL::TINYINT is_st,
          NULL::VARCHAR st_type, NULL::VARCHAR resolution, NULL::DATE namechange_ann_date WHERE false""")
    # Suspend/resume events may coexist. Preserve them without expanding daily grain.
    db.execute("""
        CREATE OR REPLACE TEMP TABLE market AS
        WITH events AS (
            SELECT ts_code, trade_date,
              count(*) AS event_count,
              count(*) FILTER (WHERE suspend_type = 'S') AS suspend_count,
              count(*) FILTER (WHERE suspend_type = 'R') AS resume_count
            FROM suspend_d GROUP BY ts_code, trade_date
        ), joined AS (
            SELECT p.*, regexp_matches(p.ts_code, ?) AS is_mainboard_code,
              a.adj_factor, b.total_mv, b.circ_mv, b.total_share,
              b.float_share, b.free_share, b.turnover_rate, b.pb, b.pe_ttm,
              l.up_limit, l.down_limit,
              s.is_st, s.st_type, s.resolution AS st_resolution,
              s.namechange_ann_date AS st_ann_date,
              coalesce(e.event_count,0) AS suspend_event_count,
              coalesce(e.suspend_count,0) AS suspend_count,
              coalesce(e.resume_count,0) AS resume_count,
              a.ts_code IS NOT NULL AS has_factor,
              b.ts_code IS NOT NULL AS has_valuation,
              l.ts_code IS NOT NULL AS has_limits,
              s.ts_code IS NOT NULL AS has_st
            FROM daily_prices p
            LEFT JOIN adj_factor a USING(ts_code,trade_date)
            LEFT JOIN daily_basic b USING(ts_code,trade_date)
            LEFT JOIN stk_limit l USING(ts_code,trade_date)
            LEFT JOIN historical_st s USING(ts_code,trade_date)
            LEFT JOIN events e USING(ts_code,trade_date)
        )
        SELECT *,
            coalesce(isfinite(adj_factor) AND adj_factor > 0,false) AS factor_valid,
            coalesce(isfinite(open) AND isfinite(high) AND isfinite(low)
              AND isfinite("close") AND isfinite(vol) AND isfinite(amount)
              AND open IS NOT NULL AND high IS NOT NULL AND low IS NOT NULL AND "close" IS NOT NULL
              AND least(open,high,low,"close")>0 AND high>=greatest(open,"close")
              AND low<=least(open,"close") AND vol>=0 AND amount>=0,false) AS ohlcv_valid,
            coalesce(isfinite(up_limit) AND isfinite(down_limit)
              AND down_limit>0 AND up_limit>=down_limit,false) AS limits_valid,
            coalesce(isfinite(total_mv) AND total_mv>0,false) AS market_value_valid,
            CASE WHEN isfinite(adj_factor) AND adj_factor>0 THEN "close"*adj_factor END AS factor_scaled_close,
            CASE WHEN isfinite(total_mv) AND total_mv>0 THEN total_mv*10000 END AS total_mv_cny,
            coalesce(st_type='NORMAL' AND is_st=0 AND st_resolution IN
              ('both_agree','namechange_only'),false) AS st_normal_evidence
        FROM joined
    """, [MAIN])
    # Full-market input coverage, no deletion of non-mainboard rows.
    row = db.execute("""
        SELECT count(*), count(DISTINCT (ts_code,trade_date)),
           count(*) FILTER (WHERE is_mainboard_code),
           count(*) FILTER (WHERE is_mainboard_code AND NOT(has_factor AND has_valuation AND has_limits)),
           count(*) FILTER (WHERE is_mainboard_code AND NOT has_st),
           count(*) FILTER (WHERE NOT ohlcv_valid),
           count(*) FILTER (WHERE is_mainboard_code AND NOT factor_valid),
           count(*) FILTER (WHERE is_mainboard_code AND NOT limits_valid),
           count(*) FILTER (WHERE is_mainboard_code AND NOT market_value_valid),
           count(*) FILTER (WHERE is_mainboard_code AND NOT st_normal_evidence),
           count(*) FILTER (WHERE is_mainboard_code AND st_normal_evidence AND st_ann_date>trade_date),
           count(*) FILTER (WHERE suspend_event_count>0)
        FROM market
    """).fetchone()
    names = ("rows","unique_keys","mainboard_rows","mainboard_missing_keys","mainboard_missing_st_keys","invalid_ohlcv",
             "mainboard_invalid_factor","mainboard_invalid_limits","mainboard_invalid_mv",
             "mainboard_not_confirmed_normal","mainboard_st_future_announcement","price_rows_with_suspend_events")
    stats = dict(zip(names,row))
    if stats["rows"] != sources["daily_prices"][1]["rows"] or stats["rows"] != stats["unique_keys"]:
        raise ValueError("Output grain changed")
    if stats["mainboard_missing_keys"] or stats["invalid_ohlcv"] or (day is None and stats["mainboard_missing_st_keys"]):
        raise ValueError(f"Blocking input quality: {stats}")
    # Events without bars are retained in their own output, including suspension-only days.
    db.execute("CREATE OR REPLACE TEMP TABLE suspension_events AS SELECT * FROM suspend_d")
    stats["suspension_event_rows"] = sources["suspend_d"][1]["rows"]
    return stats


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_json(path, obj):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as f:
        tmp = Path(f.name)
        try:
            json.dump(obj, f, sort_keys=True, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
            os.replace(tmp, path)
            sync_dir(path.parent)
        finally:
            tmp.unlink(missing_ok=True)


def write_partition(db, sources, out, year, month, day=None):
    target = out / f"year={year}" / f"month={month:02d}"
    if day is not None:
        target = target / f"day={day:%Y%m%d}"
    target.mkdir(parents=True, exist_ok=True)
    marker = target / "manifest.json"
    provenance = {ds: {"sha256": meta["sha256"], "rows": meta["rows"]}
                  for ds, (_, meta) in sources.items()}
    if marker.exists():
        old = read_json(marker)
        if old.get("version") != VERSION or old.get("inputs") != provenance:
            raise ValueError(f"Existing partition differs; use new dataset version: {target}")
        for name in ("market","suspension_events"):
            item = old["outputs"][name]
            expected = f"{name}-{item['sha256']}.parquet"
            if item["file"] != expected or not (target/expected).is_file() or digest(target/expected) != item["sha256"]:
                raise ValueError(f"Corrupt existing output: {target}")
        return old, "reused"
    stats = build_table(db, sources, year, month, day)
    outputs = {}
    for name in ("market", "suspension_events"):
        with tempfile.TemporaryDirectory(dir=target, prefix=".staging-") as staging:
            tmp = Path(staging) / "data.parquet"
            db.execute(f"COPY (SELECT * FROM {name} ORDER BY trade_date,ts_code) TO ? (FORMAT PARQUET, COMPRESSION ZSTD)", [str(tmp)])
            with tmp.open("rb") as f:
                os.fsync(f.fileno())
            h = digest(tmp)
            filename = f"{name}-{h}.parquet"
            os.replace(tmp, target / filename)
            sync_dir(target)
            outputs[name] = {"file":filename,"sha256":h,"bytes":(target/filename).stat().st_size}
    for path, meta in sources.values():
        verified(path, meta)
    manifest = {"version":VERSION,"year":year,"month":month,"inputs":provenance,
                "outputs":outputs,"quality":stats,"status":"research_data_not_backtest_ready",
                "limitations":["historical_board_code_only","suspension_events_not_execution_model",
                               "ST_announcement_lineage_pending","financial_PIT_pending",
                               "corporate_actions_pending","no_strategy_returns"]}
    if day is not None:
        manifest["trade_date"] = day.isoformat()
        manifest["limitations"].append("current_year_ST_unavailable")
    atomic_json(marker, manifest)
    return manifest, "built"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--start-year", type=int, default=2020)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument("--month", type=int, choices=range(1,13))
    parser.add_argument("--incremental", action="store_true", help="Read committed 2026+ days; ST remains unknown")
    parser.add_argument("--day", type=date.fromisoformat, help="Optional exact incremental day")
    args = parser.parse_args()
    if args.incremental:
        if not 2026 <= args.start_year <= args.end_year <= date.today().year:
            parser.error("Incremental mode requires explicit 2026+ year range")
        if args.day and not (args.start_year <= args.day.year <= args.end_year and
                             (args.month is None or args.day.month == args.month)):
            parser.error("Day outside selected range")
    elif args.day or not 2020 <= args.start_year <= args.end_year <= 2025:
        parser.error("Completed-year mode accepts 2020–2025 only")
    archive, output, scratch = (x.resolve(strict=True) for x in (args.archive,args.output,args.scratch))
    if any(x == Path("/") for x in (archive,output,scratch)) or any(
            a == b or a in b.parents or b in a.parents
            for a,b in ((archive,output),(archive,scratch),(output,scratch))):
        parser.error("Archive, output and scratch must be disjoint existing directories")
    raw = archive / RAW_PREFIX
    if not raw.is_dir():
        parser.error("Archive prefix missing")
    import duckdb
    os.umask(0o027)
    with (output/".build.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        db = duckdb.connect(config={"memory_limit":"512MB","threads":"1","temp_directory":str(scratch)})
        try:
            if args.incremental:
                days = []
                for y in range(args.start_year,args.end_year+1):
                    directory = raw / "_incremental" / "market" / f"year={y}"
                    for path in sorted(directory.glob("day=*.json")):
                        d = date.fromisoformat(path.stem.removeprefix("day=")) if "-" in path.stem else date(int(path.stem[4:8]),int(path.stem[8:10]),int(path.stem[10:12]))
                        if path.name != f"day={d:%Y%m%d}.json" or d.year != y or d > date.today():
                            raise ValueError("Invalid committed day name")
                        if (args.month is None or d.month == args.month) and (args.day is None or args.day == d):
                            days.append(d)
                if not days:
                    raise ValueError("No committed incremental days")
                for d in days:
                    manifest, action = write_partition(db, incremental_inputs(raw,d), output,d.year,d.month,d)
                    print("BT03_PARTITION "+json.dumps({"trade_date":d.isoformat(),"action":action,**manifest["quality"]},sort_keys=True),flush=True)
            else:
                for y in range(args.start_year,args.end_year+1):
                    for m in ([args.month] if args.month else range(1,13)):
                        manifest, action = write_partition(db, inputs(raw,y,m), output,y,m)
                        print("BT03_PARTITION "+json.dumps({"year":y,"month":m,"action":action,**manifest["quality"]},sort_keys=True),flush=True)
        finally:
            db.close()
    print("BT03_MARKET_DONE research_data_not_backtest_ready",flush=True)


if __name__ == "__main__":
    main()
