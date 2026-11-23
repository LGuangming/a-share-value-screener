"""Permission and availability probe for the A-share research backfill.

Only 11 bounded historical requests. Does not write market data or modify Neon.
Raw quotes, access tokens and customer credentials are never printed.
"""
from __future__ import annotations

import json
from .tushare_client import TushareClient, TushareError


# Use an actual historical trading day, not the present-day stock list alone.
SAMPLE_DAY = "20250813"
SAMPLE_QUARTER = "20241231"
PROBES = (
    ("trade_cal", {"exchange": "SSE", "start_date": SAMPLE_DAY,
                   "end_date": SAMPLE_DAY}, "cal_date,is_open", True),
    ("adj_factor", {"trade_date": SAMPLE_DAY},
     "ts_code,trade_date,adj_factor", True),
    ("stk_limit", {"trade_date": SAMPLE_DAY},
     "ts_code,trade_date,up_limit,down_limit", True),
    ("daily_basic", {"trade_date": SAMPLE_DAY},
     "ts_code,trade_date,total_mv,total_share,pb,turnover_rate", True),
    ("suspend_d", {"trade_date": SAMPLE_DAY},
     "ts_code,trade_date,suspend_type,suspend_timing", True),
    ("stock_st", {"trade_date": SAMPLE_DAY},
     "ts_code,name,trade_date,type,type_name", False),
    ("stock_basic_L", {"exchange": "", "list_status": "L"},
     "ts_code,name,market,list_status,list_date,delist_date", True),
    ("stock_basic_D", {"exchange": "", "list_status": "D"},
     "ts_code,name,market,list_status,list_date,delist_date", True),
    ("balancesheet_vip", {"period": SAMPLE_QUARTER},
     "ts_code,ann_date,f_ann_date,end_date,report_type,comp_type,total_cur_assets,total_liab", False),
    ("income_vip", {"period": SAMPLE_QUARTER},
     "ts_code,ann_date,f_ann_date,end_date,report_type,comp_type,revenue,n_income_attr_p", False),
    ("cashflow_vip", {"period": SAMPLE_QUARTER},
     "ts_code,ann_date,f_ann_date,end_date,report_type,comp_type,n_cashflow_act", False),
)


def probe(client: TushareClient) -> dict:
    results = {}
    for name, params, fields, critical in PROBES:
        api_name = "stock_basic" if name.startswith("stock_basic_") else name
        try:
            rows = client.query(api_name, params, fields, retries=1)
            if critical and not rows and name != "suspend_d":
                raise RuntimeError("empty response for a required full-market API")
            # The 2025 daily queries should not be truncated by provider row cap.
            cap = {"stk_limit": 5800, "suspend_d": 5000, "stock_st": 1000}
            if name in cap and len(rows) >= cap[name]:
                raise RuntimeError("response reached documented API row cap")
            status = "available"
            if not rows:
                status = "available_no_rows" if name == "suspend_d" else "empty_review"
            results[name] = {"status": status, "rows_in_probe": len(rows), "required": critical}
        except (TushareError, RuntimeError) as exc:
            error = str(exc)
            if "provider error" in error:
                # Provider message is untrusted and could contain private data.
                kind = "provider_rejected"
            elif "row cap" in error:
                kind = "truncated"
            elif "empty response" in error:
                kind = "empty"
            else:
                kind = "request_failed"
            results[name] = {"status": kind, "required": critical}
        print("PHASE2_API_PROBE " + json.dumps({"api": name, **results[name]},
                                                ensure_ascii=False), flush=True)
    critical_ok = all(
        results[name]["status"] in ("available", "available_no_rows")
        for name, _, _, required in PROBES if required
    )
    financial_ok = all(
        results[name]["status"] == "available"
        for name in ("balancesheet_vip", "income_vip", "cashflow_vip")
    )
    result = {
        "market_critical_ready": critical_ok,
        "financial_vip_ready": financial_ok,
        "historical_st_ready": results["stock_st"]["status"] == "available",
        "results": results,
    }
    print("PHASE2_PROBE_SUMMARY " + json.dumps(result, ensure_ascii=False,
                                               sort_keys=True), flush=True)
    return result


if __name__ == "__main__":
    probe(TushareClient.from_environment())
