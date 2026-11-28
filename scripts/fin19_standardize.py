"""FIN19-A/G: offline financial version ledger; not a PIT or production dataset.

Run on verified local archives only. SQLite is a disk-backed staging ledger.
No external dependencies, network, credentials, or production database.
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter
from datetime import date, datetime
import gzip
import hashlib
import itertools
import json
import math
from pathlib import Path
import re
import sqlite3
import tempfile

KINDS = ("balancesheet", "income", "cashflow", "fina_indicator",
         "disclosure_date", "stock_basic", "fina_audit", "forecast", "express")
CORE = {
    "balancesheet": ("total_assets", "total_liab", "total_hldr_eqy_inc_min_int"),
    "income": ("n_income_attr_p",),
    "cashflow": ("n_cashflow_act",),
    "fina_indicator": ("roe",),
}
META = {"ts_code", "end_date", "ann_date", "f_ann_date", "report_type",
        "comp_type", "end_type", "update_flag"}
STATEMENTS = set(CORE) - {"fina_indicator"}
FIELDS = {
    "balancesheet": ("total_assets", "total_liab", "total_cur_assets", "total_cur_liab",
                     "total_hldr_eqy_inc_min_int", "total_hldr_eqy_exc_min_int", "inventories"),
    "income": ("revenue", "oper_cost", "n_income", "n_income_attr_p"),
    "cashflow": ("n_cashflow_act", "c_pay_acq_const_fiolta"),
    "fina_indicator": ("roe", "roa", "roic"),
}


def canonical(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False)


def sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024*1024), b""):
            h.update(block)
    return h.hexdigest()


def day(v):
    if not isinstance(v, str) or not re.fullmatch(r"\d{8}", v):
        return None
    try:
        return datetime.strptime(v, "%Y%m%d").date()
    except ValueError:
        return None


def num(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    try:
        return v if math.isfinite(v) else None
    except OverflowError:
        return None


def token(v):
    if isinstance(v, int) and not isinstance(v, bool):
        return str(v)
    return v if isinstance(v, str) else ""


def ratio(a, b):
    if a is None or b is None or b <= 0:
        return None
    try:
        return num(a/b)
    except (OverflowError, ZeroDivisionError):
        return None


def normalize(kind, row, asof):
    code = token(row.get("ts_code"))
    period = token(row.get("end_date"))
    p = day(period)
    actual = day(row.get("f_ann_date"))
    announced = day(row.get("ann_date"))
    rt, ct, et = (token(row.get(k)) for k in ("report_type", "comp_type", "end_type"))
    flags = []
    effective = ""
    if kind in CORE:
        if not p or period[4:] not in {"0331","0630","0930","1231"} or p > asof:
            flags.append("invalid_period")
        if kind in STATEMENTS:
            if rt != "1":
                flags.append("non_primary_report_type")
            if (p and actual and p <= actual <= asof and
                (row.get("ann_date") in (None, "") or announced and p <= announced <= asof)):
                effective = max(actual, announced or actual).strftime("%Y%m%d")
            else:
                flags.append("invalid_version_date")
            if token(row.get("update_flag")) == "1":
                flags.append("updated_version_lineage_unverified")
        else:
            flags.append("indicator_lineage_unverified")
        if any(num(row.get(k)) is None for k in CORE[kind]):
            flags.append("missing_core")
    if not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", code):
        flags.append("invalid_code")
    typed = {k: num(row.get(k)) for k in FIELDS.get(kind, ())}
    derived = {}
    if kind == "balancesheet":
        a, l, e = (typed[k] for k in CORE[kind])
        if a is not None and l is not None and (a < 0 or l < 0):
            flags.append("negative_assets_or_liabilities")
        if None not in (a,l,e) and abs(a-l-e) > max(1,abs(a)*1e-6):
            flags.append("balance_equation_anomaly")
        ca, cl, inv = (typed[k] for k in ("total_cur_assets","total_cur_liab","inventories"))
        derived["debt_to_assets_ratio"] = ratio(l,a)
        derived["current_ratio"] = ratio(ca,cl)
        derived["ncav_cny"] = num(ca-l) if ca is not None and l is not None else None
    elif kind == "income":
        r,c = typed["revenue"],typed["oper_cost"]
        derived["gross_margin_ratio_ytd"] = ratio(r-c,r) if r is not None and c is not None else None
    elif kind == "cashflow":
        op,capex = typed["n_cashflow_act"],typed["c_pay_acq_const_fiolta"]
        derived["operating_cash_less_capex_cny_ytd"] = num(op-capex) if op is not None and capex is not None and capex >= 0 else None
    # Ratios are diagnostic candidates, never published strategy features.
    if kind in STATEMENTS and rt != "1":
        derived = {}
    return code,period,rt,ct,et,effective,flags,typed,derived


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE sources(id INTEGER PRIMARY KEY, source_class TEXT NOT NULL,
 api TEXT NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL,
 bytes INTEGER NOT NULL, manifest_sha256 TEXT NOT NULL,
 input_lines INTEGER, accepted_rows INTEGER, excluded_rows INTEGER,
 UNIQUE(source_class,api,path));
CREATE TABLE versions(id INTEGER PRIMARY KEY, api TEXT NOT NULL,
 row_hash TEXT NOT NULL, code TEXT NOT NULL, period TEXT NOT NULL,
 report_type TEXT NOT NULL, comp_type TEXT NOT NULL, end_type TEXT NOT NULL,
 candidate_date TEXT NOT NULL, payload TEXT NOT NULL, flags TEXT NOT NULL,
 typed_fields TEXT NOT NULL, derived_candidates TEXT NOT NULL,
 UNIQUE(api,row_hash));
CREATE TABLE lineage(version_id INTEGER NOT NULL REFERENCES versions(id),
 source_id INTEGER NOT NULL REFERENCES sources(id),
 line_no INTEGER NOT NULL, row_no INTEGER NOT NULL,
 PRIMARY KEY(source_id,line_no,row_no));
CREATE TABLE calendar(code TEXT NOT NULL, period TEXT NOT NULL, actual_date TEXT NOT NULL,
 PRIMARY KEY(code,period,actual_date)) WITHOUT ROWID;
CREATE TABLE calendar_origin(code TEXT NOT NULL, period TEXT NOT NULL, actual_date TEXT NOT NULL,
 source_class TEXT NOT NULL, PRIMARY KEY(code,period,actual_date,source_class)) WITHOUT ROWID;
CREATE TABLE source_field_quality(source_id INTEGER NOT NULL REFERENCES sources(id),
 field TEXT NOT NULL, state TEXT NOT NULL, occurrences INTEGER NOT NULL,
 PRIMARY KEY(source_id,field,state)) WITHOUT ROWID;
CREATE TABLE group_quality(api TEXT,code TEXT,period TEXT,report_type TEXT,
 comp_type TEXT,end_type TEXT,candidate_date TEXT,versions INTEGER,
 classification TEXT, differing_fields TEXT,
 PRIMARY KEY(api,code,period,report_type,comp_type,end_type,candidate_date));
CREATE TABLE missing_input(code TEXT, period TEXT, api TEXT, reason TEXT);
"""


def check_file(path, size, checksum):
    if path.is_symlink() or not path.is_file() or path.stat().st_size != size or digest(path) != checksum:
        raise ValueError("source verification failed: "+str(path))


def safe_child(root, name):
    if not isinstance(name,str) or Path(name).name != name or name in ("",".",".."):
        raise ValueError("unsafe manifest filename")
    return root/name


def sources(weekend, old):
    manifest = weekend/"manifest.json"
    doc = json.loads(manifest.read_text(encoding="utf-8"))
    if doc.get("state") != "requests_finished_not_certified" or doc.get("incomplete_request_tasks") != 0:
        raise ValueError("weekend collection not complete")
    if set(doc["archive"]) != set(KINDS):
        raise ValueError("weekend archive kinds mismatch")
    mh = digest(manifest)
    for api, info in sorted(doc["archive"].items()):
        path = safe_child(weekend/"archive",info["file"])
        yield "weekend",api,path,info["bytes"],info["sha256"],manifest,mh,info["responses"],doc["tasks"][api]["done"]["rows"]
    for api in CORE:
        root = old/"financial"/api
        files = sorted(root.glob("*.jsonl.gz"))
        manifests = sorted(root.glob("*.manifest.json"))
        if not files or {p.name[:-9] for p in files} != {p.name[:-14] for p in manifests}:
            raise ValueError("old archive pairs missing: "+str(root))
        for path in files:
            mp = path.with_name(path.name.replace(".jsonl.gz",".manifest.json"))
            m = json.loads(mp.read_text(encoding="utf-8"))
            if (m.get("kind") != api or m.get("format_version") != 1 or
                m.get("source") != "Tushare official per-symbol" or
                m.get("currency") != "CNY" or m.get("financial_amount_unit") != "RMB yuan"):
                raise ValueError("unsupported old manifest: "+str(mp))
            yield "old",api,path,m["compressed_bytes"],m["sha256"],mp,digest(mp),m["rows"],m["rows"]

    # Reuse BT03's existing disclosure archive contract; do not glob arbitrary JSON.
    directory = old/"disclosures"
    payloads = sorted(directory.glob("period=*.jsonl.gz"))
    markers = sorted(directory.glob("period=*.manifest.json"))
    if (not markers or {p.name[:-9] for p in payloads} != {p.name[:-14] for p in markers}):
        raise ValueError("old disclosure archive pairs missing: "+str(directory))
    for mp in markers:
        if mp.is_symlink():
            raise ValueError("symlink disclosure manifest")
        mh = digest(mp)
        m = json.loads(mp.read_text(encoding="utf-8"))
        period = m.get("report_period")
        if (not day(period) or mp.name != f"period={period}.manifest.json"):
            raise ValueError("invalid disclosure manifest period")
        path = directory/f"period={period}.jsonl.gz"
        if (m.get("format_version") != 1 or m.get("source") != "Tushare official disclosure_date"
            or m.get("status") != "source_evidence_only"
            or m.get("object_key") != "a-share-backtest/01_raw_data/"+path.relative_to(old).as_posix()
            or not 0 < m.get("compressed_bytes",0) <= 8*1024*1024
            or m.get("actual_date_populated",-1) < 0 or m.get("actual_date_missing",-1) < 0
            or m.get("rows") != m.get("actual_date_populated")+m.get("actual_date_missing")):
            raise ValueError("unsupported disclosure manifest: "+str(mp))
        yield "old_disclosure","disclosure_date",path,m["compressed_bytes"],m["sha256"],mp,mh,m["rows"],m["rows"]


def read_source(db, spec, asof):
    source_class,api,path,size,checksum,mp,mh,expected_lines,expected_rows = spec
    check_file(path,size,checksum)
    sid = db.execute("INSERT INTO sources(source_class,api,path,sha256,bytes,manifest_sha256) VALUES (?,?,?,?,?,?)",
                     (source_class,api,str(path),checksum,size,mh)).lastrowid
    accepted = excluded = lines = populated = expanded = 0
    field_counts = Counter()
    old_disclosure = source_class == "old_disclosure"
    old_meta = json.loads(mp.read_text(encoding="utf-8")) if old_disclosure else None
    with gzip.open(path,"rt",encoding="utf-8") as f:
        for lines,line in enumerate(f,1):
            if old_disclosure:
                expanded += len(line.encode("utf-8"))
                if expanded > 64*1024*1024:
                    raise ValueError("old disclosure expansion limit")
            obj = json.loads(line)
            if not isinstance(obj,dict):
                raise ValueError("non-object source line")
            if source_class == "weekend":
                records = obj["rows"]
                if not isinstance(records,list):
                    raise ValueError("invalid response rows")
                if obj.get("status") != "done":
                    excluded += len(records)
                    continue
            else:
                records = [obj]
            for row_no,row in enumerate(records,1):
                if not isinstance(row,dict):
                    raise ValueError("non-object financial row")
                if old_disclosure:
                    if row.get("end_date") != old_meta["report_period"] or not row.get("ts_code"):
                        raise ValueError("old disclosure row identity mismatch")
                    populated += row.get("actual_date") not in (None,"")
                for field in sorted(set(CORE.get(api, ())) | set(FIELDS.get(api, ()))):
                    if field not in row:
                        state = "absent_in_payload"
                    elif row[field] is None:
                        state = "explicit_null"
                    elif num(row[field]) is None:
                        state = "invalid_numeric"
                    else:
                        state = "valid_numeric"
                    field_counts[(field,state)] += 1
                payload = canonical(row)
                code,p,rt,ct,et,eff,flags,typed,derived = normalize(api,row,asof)
                rh = sha(payload)
                db.execute("INSERT OR IGNORE INTO versions(api,row_hash,code,period,report_type,comp_type,end_type,candidate_date,payload,flags,typed_fields,derived_candidates) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                           (api,rh,code,p,rt,ct,et,eff,payload,canonical(flags),canonical(typed),canonical(derived)))
                vid,stored = db.execute("SELECT id,payload FROM versions WHERE api=? AND row_hash=?",(api,rh)).fetchone()
                if stored != payload:
                    raise ValueError("hash collision")
                db.execute("INSERT INTO lineage VALUES (?,?,?,?)",(vid,sid,lines,row_no))
                accepted += 1
                if api == "disclosure_date":
                    pd,ad = day(p),day(row.get("actual_date"))
                    if pd and ad and pd <= ad <= asof and "invalid_code" not in flags:
                        actual = ad.strftime("%Y%m%d")
                        db.execute("INSERT OR IGNORE INTO calendar VALUES (?,?,?)",(code,p,actual))
                        db.execute("INSERT OR IGNORE INTO calendar_origin VALUES (?,?,?,?)",
                                   (code,p,actual,source_class))
            if lines % 250 == 0:
                db.commit()
    # The manifest is authoritative for response lines and completed-row count.
    if lines != expected_lines or accepted != expected_rows:
        raise ValueError(f"source counts differ: {path} lines={lines}/{expected_lines} accepted={accepted}/{expected_rows} excluded={excluded}")
    if old_disclosure and (populated != old_meta["actual_date_populated"]
                           or accepted-populated != old_meta["actual_date_missing"]):
        raise ValueError("old disclosure actual-date counts mismatch")
    db.executemany("INSERT INTO source_field_quality VALUES (?,?,?,?)",
                   [(sid,field,state,n) for (field,state),n in sorted(field_counts.items())])
    check_file(path,size,checksum)
    if digest(mp) != mh:
        raise ValueError("manifest changed during read")
    db.execute("UPDATE sources SET input_lines=?,accepted_rows=?,excluded_rows=? WHERE id=?",(lines,accepted,excluded,sid))
    db.commit()
    print("FIN19_SOURCE",source_class,api,path.name,accepted,excluded,flush=True)


def classify_payloads(payloads):
    """Distinguish metadata/schema/null differences from unequal non-null values.

    Never merge complementary payloads: they may be different historical versions.
    Numeric int/float equality is exact under Python numeric semantics; no fuzzy
    tolerance or unit conversions are applied. Missing and explicit null differ
    in source JSON but neither proves a conflicting non-null value.
    """
    values = {}
    nonnull = {}
    schemas = set()
    null_sets = set()
    conflict = set()
    for payload in payloads:
        row = json.loads(payload)
        financial = {k:v for k,v in row.items() if k not in META}
        schemas.add(frozenset(financial))
        null_sets.add(frozenset(k for k,v in financial.items() if v is None))
        for k,v in financial.items():
            if v is None:
                continue
            if k in nonnull and (type(v) is bool or type(nonnull[k]) is bool) and type(v) is not type(nonnull[k]):
                conflict.add(k)
            elif k in nonnull and nonnull[k] != v:
                conflict.add(k)
            else:
                nonnull[k] = v
        values[canonical(financial)] = True
    if conflict:
        return "non_null_value_conflict", sorted(conflict)
    if len(values) == 1:
        return "metadata_only", []
    if len(schemas) > 1:
        return "schema_or_complementary_difference", []
    if len(null_sets) > 1:
        return "null_population_difference", []
    return "numeric_representation_difference", []


def group_audit(db):
    db.execute("CREATE INDEX version_groups ON versions(api,code,period,report_type,comp_type,end_type,candidate_date)")
    sql = """SELECT api,code,period,report_type,comp_type,end_type,candidate_date,payload
             FROM versions WHERE api IN ('balancesheet','income','cashflow')
             ORDER BY api,code,period,report_type,comp_type,end_type,candidate_date"""
    counts = Counter()
    for key,items in itertools.groupby(db.execute(sql),key=lambda x:x[:7]):
        payloads = [r[7] for r in items]
        if not key[-1]:
            status,fields = "unresolved_date",[]
        elif len(payloads) == 1:
            status,fields = "single_distinct_payload",[]
        else:
            status,fields = classify_payloads(payloads)
        db.execute("INSERT INTO group_quality VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (*key,len(payloads),status,canonical(fields)))
        counts[(key[0], "2018-2019" if key[2] < "20200101" else "2020+", status)] += 1
    db.commit()
    return [{"api":k[0],"scope":k[1],"classification":k[2],"groups":v} for k,v in sorted(counts.items())]


def build(weekend,old,output,asof):
    weekend,old,output = (p.resolve() for p in (weekend,old,output))
    if (output.exists() or any(output == p or p in output.parents or output in p.parents for p in (weekend,old))):
        raise ValueError("output must be new and outside both source roots")
    output.parent.mkdir(parents=True,exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".fin19-staging-",dir=output.parent))
    db = None
    try:
        db = sqlite3.connect(tmp/"financial.sqlite3")
        db.executescript(SCHEMA)
        db.execute("PRAGMA cache_size=-32768")
        db.execute("PRAGMA temp_store=FILE")
        for spec in sources(weekend,old):
            read_source(db,spec,asof)
        missing = weekend/"missing.csv"
        missing_sha = digest(missing)
        with missing.open(encoding="utf-8-sig",newline="") as f:
            for row in csv.DictReader(f):
                db.execute("INSERT INTO missing_input VALUES (?,?,?,?)",
                           (row["ts_code"],row["period"],row["api"],row["reason"]))
        if digest(missing) != missing_sha:
            raise ValueError("missing list changed")
        db.commit()
        groups = group_audit(db)
        counts = []
        for api in KINDS:
            total = db.execute("SELECT COALESCE(SUM(accepted_rows),0) FROM sources WHERE api=?",(api,)).fetchone()[0]
            unique = db.execute("SELECT COUNT(*) FROM versions WHERE api=?",(api,)).fetchone()[0]
            linked = db.execute("SELECT COUNT(*) FROM lineage l JOIN sources s ON l.source_id=s.id WHERE s.api=?",(api,)).fetchone()[0]
            if total != linked:
                raise ValueError("lineage reconciliation failed: "+api)
            flags = Counter()
            for (raw,) in db.execute("SELECT flags FROM versions WHERE api=?",(api,)):
                flags.update(json.loads(raw))
            counts.append({"api":api,"accepted_rows":total,"distinct_payloads":unique,
                           "exact_duplicate_occurrences":total-unique,"flags":dict(flags)})
        # Calendar matches are row-date evidence only, not proof of version history.
        db.execute("""CREATE VIEW statement_date_evidence AS SELECT v.id AS version_id,v.api,v.code,v.period,v.candidate_date, CASE
              WHEN v.candidate_date='' THEN 'no_valid_statement_date'
              WHEN EXISTS(SELECT 1 FROM calendar c WHERE c.code=v.code AND c.period=v.period AND c.actual_date=v.candidate_date) THEN 'calendar_exact_match'
              WHEN EXISTS(SELECT 1 FROM calendar c WHERE c.code=v.code AND c.period=v.period) THEN 'calendar_other_date'
              ELSE 'statement_date_only' END AS evidence
              FROM versions v WHERE api IN ('balancesheet','income','cashflow') AND report_type='1'
              """)
        date_counts = db.execute("SELECT api,evidence,COUNT(*) FROM statement_date_evidence GROUP BY api,evidence").fetchall()
        origin_counts = db.execute("SELECT source_class,COUNT(*) FROM calendar_origin GROUP BY source_class").fetchall()
        overlap = db.execute("""SELECT COUNT(*) FROM (
            SELECT code,period,actual_date FROM calendar_origin
            GROUP BY code,period,actual_date HAVING COUNT(*) > 1)""").fetchone()[0]
        field_counts = db.execute("""SELECT s.source_class,s.api,q.field,q.state,SUM(q.occurrences)
            FROM source_field_quality q JOIN sources s ON s.id=q.source_id
            GROUP BY s.source_class,s.api,q.field,q.state
            ORDER BY s.source_class,s.api,q.field,q.state""").fetchall()
        db.commit()
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("ledger integrity failed")
        db.close()
        db = None
        ledger = tmp/"financial.sqlite3"
        report = {"state":"standardized_staging_not_pit_certified","format_version":2,
                  "asof":asof.isoformat(),"formal_start":"2020-01-01","warmup_start":"2018-01-01",
                  "pit_certified":False,"production_published":False,"parquet_published":False,
                  "missing_input_sha256":missing_sha,"ledger_sha256":digest(ledger),
                  "ledger_bytes":ledger.stat().st_size,"counts":counts,"version_groups":groups,
                  "calendar_origin_counts":[{"source_class":s,"valid_distinct_date_keys":n} for s,n in origin_counts],
                  "calendar_shared_date_keys":overlap,
                  "field_quality_by_source":[{"source_class":s,"api":a,"field":f,"state":k,"occurrences":n}
                      for s,a,f,k,n in field_counts],
                  "date_evidence":[{"api":a,"classification":e,"rows":n} for a,e,n in date_counts],
                  "limitations":["no version release certification","no TTM or ROE derivation",
                    "no conflict winner selection","no automatic publication","no historical universe certification",
                    "derived values are diagnostics only","source manifests checked locally, not signed"]}
        (tmp/"manifest.json").write_text(json.dumps(report,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
        if output.exists():
            raise ValueError("output appeared during build")
        tmp.rename(output)
        print(json.dumps(report,indent=2,ensure_ascii=False),flush=True)
    except BaseException:
        if db is not None:
            db.close()
        # Preserve failed staging evidence for operator inspection. Never publish it.
        print("FIN19_FAILED_STAGING",tmp,flush=True)
        raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--weekend",type=Path,required=True)
    p.add_argument("--old-raw",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--asof",type=date.fromisoformat,required=True)
    a = p.parse_args()
    build(a.weekend,a.old_raw,a.output,a.asof)


if __name__ == "__main__":
    main()
