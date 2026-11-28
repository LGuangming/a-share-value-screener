"""FIN18-B: bounded, read-only BaoStock financial evidence probe.

Never writes to a database or source archive. Raw values and publication dates
are provider evidence only, not certified PIT or interchangeable Tushare values.
"""
import argparse
import csv
import datetime as dt
import importlib.metadata
import json
import math
import re
import time
from collections import Counter
from pathlib import Path

APIS = {
    "profit": ("query_profit_data", ("roeAvg", "npMargin", "gpMargin", "netProfit")),
    "balance": ("query_balance_data", ("currentRatio", "quickRatio", "liabilityToAsset")),
    "cashflow": ("query_cash_flow_data", ("CFOToOR", "ebitToInterest")),
    "growth": ("query_growth_data", ("YOYNI", "YOYPNI", "YOYAsset")),
    "operation": ("query_operation_data", ("NRTurnRatio", "INVTurnRatio", "AssetTurnRatio")),
    "dupont": ("query_dupont_data", ("dupontROE",)),
}
QUARTERS = {"0331": 1, "0630": 2, "0930": 3, "1231": 4}
REASONS = ("core_null_or_invalid", "no_indicator_row")


def parse_day(value):
    try:
        return dt.date.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def mainboard(code):
    return bool(re.fullmatch(r"(?:60[0135]\d{3}\.SH|00[0123]\d{3}\.SZ)", code))


def provider_code(code):
    if not mainboard(code):
        raise ValueError("not a supported mainboard symbol: " + code)
    number, exchange = code.split(".")
    return exchange.lower() + "." + number


def finite(value):
    try:
        return not isinstance(value, bool) and math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def choose(path, per_reason, asof):
    buckets = {r: set() for r in REASONS}
    counts = Counter()
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row["api"] != "fina_indicator" or row["reason"] not in buckets:
                continue
            code, period = row["ts_code"], row["period"]
            if not re.fullmatch(r"\d{8}", period):
                counts["invalid_period"] += 1
                continue
            day = parse_day(period[:4]+"-"+period[4:6]+"-"+period[6:])
            if not day or period[4:] not in QUARTERS or day > asof:
                counts["invalid_period"] += 1
                continue
            if not mainboard(code):
                counts["outside_mainboard"] += 1
                continue
            buckets[row["reason"]].add((code, period))
    selected = []
    for reason, keys in buckets.items():
        counts[reason] = len(keys)
        # Prefer formal scope, diversify symbols, and spread dates across the range.
        order = sorted(keys, key=lambda x: (x[1], x[0]))
        chosen = []
        for subset in ([k for k in order if k[1] >= "20200101"],
                       [k for k in order if k[1] < "20200101"]):
            if subset:
                indices = [round(i*(len(subset)-1)/max(1, per_reason-1))
                           for i in range(per_reason)]
                candidates = [subset[i] for i in indices] + subset
                for key in candidates:
                    if len(chosen) >= per_reason:
                        break
                    if key not in chosen and key[0] not in {k[0] for k in chosen}:
                        chosen.append(key)
                for key in candidates:
                    if len(chosen) >= per_reason:
                        break
                    if key not in chosen:
                        chosen.append(key)
        selected.extend((code, period, reason) for code, period in chosen)
    return selected, dict(counts)


def assess(row, code, period, fields, asof):
    stat = parse_day(row.get("statDate"))
    pub = parse_day(row.get("pubDate"))
    expected = parse_day(period[:4]+"-"+period[4:6]+"-"+period[6:])
    issues = []
    if row.get("code") != provider_code(code):
        issues.append("wrong_code")
    if not stat or stat != expected:
        issues.append("wrong_period")
    if not pub or not stat or not stat <= pub <= asof:
        issues.append("invalid_publication_date")
    usable = [f for f in fields if finite(row.get(f))]
    if not usable:
        issues.append("no_finite_metric")
    return {"issues": issues, "finite_fields": usable,
            "roe_candidate": not issues and "roeAvg" in usable,
            "pit_certified": False}


def probe(bs, api, code, period, reason, asof):
    method, fields = APIS[api]
    result = {"api": api, "method": method, "ts_code": code,
              "period": period, "reason": reason,
              "retrieved_at": dt.datetime.now(dt.timezone.utc).isoformat(),
              "source": "baostock", "pit_certified": False, "rows": []}
    try:
        rs = getattr(bs, method)(code=provider_code(code),
                                  year=int(period[:4]), quarter=QUARTERS[period[4:]])
        if str(rs.error_code) != "0":
            raise RuntimeError(str(rs.error_code)+": "+str(rs.error_msg))
        columns = list(rs.fields)
        result["fields"] = columns
        if len(columns) != len(set(columns)):
            raise ValueError("duplicate provider columns")
        while rs.next():
            values = rs.get_row_data()
            if len(values) != len(columns):
                raise ValueError("row width mismatch")
            raw = dict(zip(columns, values))
            result["rows"].append({"raw": raw, "assessment": assess(raw, code, period, fields, asof)})
            if len(result["rows"]) > 1000:
                raise ValueError("unexpected response size")
        if str(rs.error_code) != "0":
            raise RuntimeError(str(rs.error_code)+": "+str(rs.error_msg))
        result["status"] = "returned" if result["rows"] else "empty"
    except Exception as e:
        result.update(status="error", error=str(e)[:500])
    return result


def self_test():
    import tempfile
    import unittest
    class Tests(unittest.TestCase):
        def test_codes(self):
            self.assertEqual(provider_code("000016.SZ"), "sz.000016")
            for c in ("200054.SZ", "688001.SH", "300001.SZ", "920001.BJ"):
                self.assertFalse(mainboard(c))
        def test_evidence(self):
            raw = dict(code="sz.000016", statDate="2025-12-31",
                       pubDate="2026-04-29", roeAvg="0")
            a = assess(raw, "000016.SZ", "20251231", ("roeAvg",), dt.date(2026, 11, 28))
            self.assertTrue(a["roe_candidate"])
            self.assertFalse(a["pit_certified"])
            for v in ("", "NaN", "inf", None):
                self.assertFalse(finite(v))
            for p in ("2025-12-30", "2026-12-01", ""):
                self.assertFalse(assess(dict(raw, pubDate=p), "000016.SZ", "20251231",
                    ("roeAvg",), dt.date(2026,11,28))["roe_candidate"])
        def test_wrong_period(self):
            self.assertIn("wrong_period", assess(dict(code="sz.000016", statDate="2025-09-30",
                pubDate="2026-04-29", roeAvg="0.1"), "000016.SZ", "20251231",
                ("roeAvg",), dt.date(2026,11,28))["issues"])
        def test_selection(self):
            with tempfile.TemporaryDirectory() as d:
                p = Path(d)/"missing.csv"
                p.write_text("ts_code,period,api,reason\n000016.SZ,20251231,fina_indicator,core_null_or_invalid\n"
                             "200054.SZ,20251231,fina_indicator,no_indicator_row\n"
                             "000023.SZ,20240331,fina_indicator,no_indicator_row\n", encoding="utf-8")
                selected, counts = choose(p, 2, dt.date(2026,11,28))
                self.assertEqual(len(selected), 2)
                self.assertEqual(counts["outside_mainboard"], 1)
        def test_empty_error_return(self):
            class RS:
                error_code = "0"
                error_msg = ""
                fields = ["code","statDate","pubDate","roeAvg"]
                def next(self):
                    return False
            class BS:
                def query_profit_data(self, **kwargs):
                    return RS()
            self.assertEqual(probe(BS(),"profit","000016.SZ","20251231",
                "control",dt.date(2026,11,28))["status"],"empty")
            class Bad:
                def query_profit_data(self, **kwargs):
                    raise RuntimeError("network")
            self.assertEqual(probe(Bad(),"profit","000016.SZ","20251231",
                "control",dt.date(2026,11,28))["status"],"error")
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Tests))
    return 0 if result.wasSuccessful() else 1


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--missing", type=Path)
    p.add_argument("--out", type=Path)
    p.add_argument("--asof", default="2026-11-28")
    p.add_argument("--per-reason", type=int, default=10)
    args = p.parse_args()
    if args.self_test:
        return self_test()
    asof = parse_day(args.asof)
    if not asof or not args.missing or not args.out or not 1 <= args.per_reason <= 20:
        p.error("--missing, new --out, valid --asof and --per-reason 1..20 required")
    selected, population = choose(args.missing, args.per_reason, asof)
    controls = [("600000.SH", "20231231", "control"), ("000651.SZ", "20231231", "control")]
    tasks = [(api, *item) for item in controls for api in APIS]
    tasks += [("profit", *item) for item in selected]
    # Six endpoint checks on controls; only profit on missing ROE target sample.
    args.out.mkdir(mode=0o700, parents=False, exist_ok=False)
    plan = {"population": population, "selected": selected, "controls": controls,
            "planned_requests": len(tasks), "asof": args.asof,
            "source": "baostock", "pit_certified": False,
            "production_published": False}
    (args.out/"plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    import baostock as bs
    counts = Counter()
    candidate_keys = set()
    executed = 0
    consecutive_errors = 0
    logged_in = False
    failure = None
    try:
        login = bs.login()
        if str(login.error_code) != "0":
            raise RuntimeError("login: "+str(login.error_code)+": "+str(login.error_msg))
        logged_in = True
        with (args.out/"responses.jsonl").open("x", encoding="utf-8") as f:
            for api, code, period, reason in tasks:
                result = probe(bs, api, code, period, reason, asof)
                f.write(json.dumps(result, ensure_ascii=False, allow_nan=False)+"\n")
                f.flush()
                executed += 1
                counts[(api, reason, result["status"])] += 1
                if reason != "control" and result["status"] == "returned" and any(
                    r["assessment"]["roe_candidate"] for r in result["rows"]):
                    candidate_keys.add((code, period, reason))
                print(api, code, period, reason, result["status"], len(result["rows"]), flush=True)
                consecutive_errors = consecutive_errors+1 if result["status"] == "error" else 0
                if consecutive_errors >= 3:
                    failure = "three consecutive provider errors"
                    break
                time.sleep(1)
    except Exception as e:
        failure = str(e)[:500]
    finally:
        if logged_in:
            try:
                bs.logout()
            except Exception:
                pass
        summary = {**plan, "package_version": importlib.metadata.version("baostock"),
                   "executed_requests": executed, "failure": failure,
                   "counts": [{"api": k[0], "reason": k[1], "status": k[2], "count": v}
                              for k,v in sorted(counts.items())],
                   "roe_candidate_keys": sorted(candidate_keys),
                   "roe_candidate_count": len(candidate_keys),
                   "state": "probe_finished" if executed == len(tasks) and not failure else "probe_incomplete"}
        (args.out/"summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(json.dumps(summary, indent=2), flush=True)
    return 0 if executed == len(tasks) and not failure else 1


if __name__ == "__main__":
    raise SystemExit(main())
