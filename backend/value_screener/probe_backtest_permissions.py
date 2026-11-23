"""Read-only Tushare backtest entitlement probe. Never logs credentials or writes data."""
from __future__ import annotations
import json
from value_screener.tushare_client import TushareClient, TushareError

def main():
    api = TushareClient.from_environment()
    checks = [
        # Use historic sample days as well as a current session to validate
        # actual availability in the requested 2020-to-present study window.
        ("daily_2020", "daily", {"ts_code":"000001.SZ","start_date":"20200102","end_date":"20200110"},
         "ts_code,trade_date,open,high,low,close,vol,amount"),
        ("daily_2026", "daily", {"ts_code":"000001.SZ","start_date":"20261116","end_date":"20261120"},
         "ts_code,trade_date,open,high,low,close,vol,amount"),
        ("adj_factor_2020", "adj_factor", {"ts_code":"000001.SZ","start_date":"20200102","end_date":"20200110"},
         "ts_code,trade_date,adj_factor"),
        ("stk_limit_2020", "stk_limit", {"ts_code":"000001.SZ","start_date":"20200102","end_date":"20200110"},
         "ts_code,trade_date,up_limit,down_limit"),
        ("suspend_d_2020", "suspend_d", {"trade_date":"20201120","suspend_type":"S"},
         "ts_code,trade_date,suspend_type"),
        ("stock_basic_delisted", "stock_basic", {"exchange":"","list_status":"D"},
         "ts_code,name,list_status,list_date,delist_date,market"),
        ("stock_st_2020", "stock_st", {"trade_date":"20201120"},
         "ts_code,trade_date,name,type"),
        ("index_daily_2020", "index_daily", {"ts_code":"000300.SH","start_date":"20200102","end_date":"20200110"},
         "ts_code,trade_date,close"),
        ("dividend_history", "dividend", {"ts_code":"000001.SZ"},
         "ts_code,ann_date,ex_date,cash_div_tax,stk_div"),
        ("balancesheet_vip", "balancesheet_vip", {"period":"20260930"},
         "ts_code,end_date,total_cur_assets,total_liab"),
        ("income_vip", "income_vip", {"period":"20260930"},
         "ts_code,end_date,n_income"),
        ("cashflow_vip", "cashflow_vip", {"period":"20260930"},
         "ts_code,end_date,n_cashflow_act"),
    ]
    summary = {}
    for label, endpoint, params, fields in checks:
        try:
            rows = api.query(endpoint, params, fields, retries=1)
            status = "OK" if rows else "EMPTY"
            sample = rows[0] if rows else {}
            field_hits = [k for k in fields.split(",") if k in sample]
            summary[label] = {"status":status,"rows":len(rows),"fields_found":field_hits}
        except (TushareError, ValueError) as exc:
            msg = str(exc)
            if any(k in msg.lower() for k in ("权限","permission","access denied","40203","积分不足","not have access")):
                status="DENIED"
            else:
                status="ERROR"
            # Avoid echoing provider-supplied text which might contain private data.
            summary[label] = {"status":status,"detail":msg[:130].replace("\n", " ")}
        print("PERMISSION_CHECK " + json.dumps({"name":label,**summary[label]},ensure_ascii=False),flush=True)
    print("PERMISSION_SUMMARY "+json.dumps(summary,ensure_ascii=False),flush=True)

if __name__ == "__main__":
    main()
