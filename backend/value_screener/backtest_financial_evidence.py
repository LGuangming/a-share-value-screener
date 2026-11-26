"""Offline financial archive evidence audit, not certified PIT financial data."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime
import gzip
import hashlib
import json
import math
from pathlib import Path

from .archive_paths import RAW_PREFIX
from .backtest_dataset import atomic_json, digest
from .financial_archive_import import parsed, finite
from .phase2_financial import FIELDS

CORE = {"balancesheet": ("total_cur_assets","total_liab"),
        "income": ("n_income",), "cashflow": ("n_cashflow_act",),
        "fina_indicator": ("roe",)}
BATCH = 50


def evidence(row, kind):
    """Retain uncertainty rather than invent an actual announcement date."""
    period = parsed(row.get("end_date"))
    actual = parsed(row.get("f_ann_date"))
    announced = parsed(row.get("ann_date"))
    if not period:
        return None, None, "invalid_period"
    if kind == "fina_indicator":
        # Indicator API lacks consolidated-report and actual-announcement lineage.
        return period, announced, "indicator_lineage_unverified"
    if str(row.get("report_type")) != "1":
        return period, None, "non_consolidated_or_unknown"
    if (not actual or actual < period or
            (row.get("ann_date") not in (None,"") and
             (not announced or announced < period))):
        return period, None, "invalid_disclosure"
    disclosed = max(actual, announced or actual)
    if any(not finite(row.get(k)) for k in CORE[kind]):
        return period, disclosed, "missing_core"
    if kind == "balancesheet" and any(float(row[k]) < 0 for k in CORE[kind]):
        return period, disclosed, "invalid_balance"
    return period, disclosed, "candidate_not_pit_certified"


def canonical(row):
    return json.dumps(row,sort_keys=True,ensure_ascii=False,separators=(",",":"),allow_nan=False)


def select_candidate(rows, kind, signal_day):
    """Latest eligible consolidated report period/version strictly before signal.

    Reject conflicting same-day versions and invalid latest metrics instead of
    silently falling back. Actual historical revision completeness remains unknown.
    """
    if kind == "fina_indicator":
        return None, "indicator_lineage_unverified"
    groups = defaultdict(dict)
    unresolved = []
    for row in rows:
        period, disclosed, status = evidence(row, kind)
        if status == "non_consolidated_or_unknown":
            continue
        if not period or period > signal_day:
            continue
        if not disclosed:
            unresolved.append(period)
            continue
        if disclosed >= signal_day:
            continue
        groups[(period,disclosed)][canonical(row)] = (row,status)
    if not groups:
        return None, "no_eligible_report"
    period, disclosed = max(groups)
    if any(p >= period for p in unresolved):
        return None, "unresolved_disclosure_version"
    if len(groups[(period,disclosed)]) != 1:
        return None, "conflicting_latest_version"
    row,status = next(iter(groups[(period,disclosed)].values()))
    if status != "candidate_not_pit_certified":
        return None,status
    return row,status


def batch_rows(raw, kind, codes, start):
    selected = codes[start:start+BATCH]
    stem = raw / "financial" / kind / f"start={start:05d}_count={BATCH:03d}"
    manifest_path = stem.with_suffix(".manifest.json")
    path = stem.with_suffix(".jsonl.gz")
    doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    code_hash = hashlib.sha256(json.dumps(selected,ensure_ascii=False).encode()).hexdigest()
    captured = datetime.fromisoformat(doc["captured_at_utc"])
    key = RAW_PREFIX + "/" + path.relative_to(raw).as_posix()
    if (doc.get("format_version") != 1 or doc.get("kind") != kind or
        doc.get("start_index") != start or doc.get("requested_codes") != len(selected)
        or doc.get("codes_sha256") != code_hash or doc.get("object_key") != key
        or doc.get("source") != "Tushare official per-symbol"
        or doc.get("status") != "research_not_pit_certified"
        or doc.get("currency") != "CNY" or doc.get("financial_amount_unit") != "RMB yuan"
        or captured.tzinfo is None or path.is_symlink()
        or not 0 < path.stat().st_size <= 8*1024*1024
        or doc.get("compressed_bytes") != path.stat().st_size or doc.get("sha256") != digest(path)):
        raise ValueError(f"Unverified financial batch: {key}")
    rows = []
    expanded = 0
    allowed = set(selected)
    fields = set(FIELDS[kind].split(","))
    with gzip.open(path,"rb") as f:
        for line in f:
            expanded += len(line)
            if expanded > 64*1024*1024:
                raise ValueError(f"Financial batch exceeds decompressed cap: {key}")
            row = json.loads(line)
            if not isinstance(row,dict) or row.get("ts_code") not in allowed or not fields.issubset(row):
                raise ValueError(f"Invalid financial row: {key}")
            rows.append(row)
    missing = allowed - {row["ts_code"] for row in rows}
    if (len(rows) != doc.get("rows") or missing != set(doc.get("missing_codes",[]))
        or len(missing) != doc.get("missing_codes_count") or doc["sha256"] != digest(path)):
        raise ValueError(f"Financial batch reconciliation failed: {key}")
    return rows,doc


def audit(raw):
    snapshot = raw/"stock_basic"/"stock_basic_snapshot.json"
    snapshot_sha = digest(snapshot)
    doc = json.loads(snapshot.read_text(encoding="utf-8"))
    if doc.get("status") != "current_snapshot_not_historical":
        raise ValueError("Unexpected universe snapshot")
    codes = sorted({x["ts_code"] for x in doc["records"]})
    if not codes:
        raise ValueError("Empty financial archive universe")
    report = {"status":"research_not_pit_certified","universe_sha256":snapshot_sha,
              "symbols":len(codes),"kinds":{},"limitations":[
              "historical_revision_coverage_unknown","no_daily_financial_join",
              "no_disclosure_calendar_cross_check","no_production_database_reads"]}
    for kind in CORE:
        counts = Counter()
        periods = Counter()
        conflicts = 0
        sources = []
        for start in range(0,len(codes),BATCH):
            rows, meta = batch_rows(raw,kind,codes,start)
            sources.append({"object_key":meta["object_key"],"sha256":meta["sha256"],
                            "rows":len(rows),"captured_at_utc":meta["captured_at_utc"]})
            groups = defaultdict(set)
            counts["missing_symbols"] += meta["missing_codes_count"]
            counts["rows"] += len(rows)
            for row in rows:
                p,d,status = evidence(row,kind)
                counts[status] += 1
                if p:
                    periods[str(p.year)] += 1
                if p and d and str(row.get("report_type")) == "1":
                    groups[(row["ts_code"],p,d)].add(canonical(row))
            conflicts += sum(len(versions)>1 for versions in groups.values())
        report["kinds"][kind] = {"counts":dict(counts),"period_years":dict(periods),
                                 "same_day_conflicting_version_keys":conflicts,"sources":sources}
    if digest(snapshot) != snapshot_sha:
        raise ValueError("Universe snapshot changed during audit")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True,help="New audit JSON in independent directory")
    args=parser.parse_args()
    archive=args.archive.resolve(strict=True)
    target=args.output.resolve()
    if target == archive or archive in target.parents or target.exists() or not target.parent.is_dir():
        parser.error("Audit output must be a new file outside archive")
    report=audit(archive/RAW_PREFIX)
    atomic_json(target,report)
    print("BT03_FINANCIAL_EVIDENCE "+json.dumps({k:v["counts"] for k,v in report["kinds"].items()},sort_keys=True),flush=True)


if __name__ == "__main__":
    main()
