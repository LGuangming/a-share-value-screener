"""Minimal official Tushare Pro HTTP adapter with errors and bounded retries.
Docs: https://tushare.pro/document/2 . Credentials must never be logged.
"""
from __future__ import annotations
import json
import os
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

API_URL = "https://api.tushare.pro"
RELAY_API_URL = "http://tus.sxdata.top/tu_share/"


class TushareError(RuntimeError):
    pass


class TushareClient:
    def __init__(self, token: str, timeout: int = 40, *,
                 api_source: str = "official", relay_url: str | None = None,
                 allow_insecure_http: bool = False):
        if not token:
            raise ValueError("Tushare API token is required")
        if api_source not in ("official", "relay"):
            raise ValueError("TUSHARE_API_SOURCE must be official or relay")
        self.source = api_source
        self._token = token
        self.timeout = timeout
        if api_source == "official":
            self.api_url = API_URL  # Unchanged official HTTPS endpoint.
        else:
            url = (relay_url or RELAY_API_URL).strip()
            parsed = urlsplit(url)
            if (parsed.scheme not in ("https", "http") or not parsed.netloc
                    or not parsed.hostname or parsed.username or parsed.password
                    or parsed.query or parsed.fragment):
                raise ValueError("Relay URL must be a bare HTTP(S) endpoint without credentials, query or fragment")
            if parsed.scheme == "http" and not allow_insecure_http:
                raise ValueError("HTTP relay sends the API token unencrypted; explicitly set TUSHARE_ALLOW_HTTP=1 to opt in")
            self.api_url = url

    @classmethod
    def from_environment(cls, timeout: int = 40) -> "TushareClient":
        """Select token and URL explicitly; never forward the official secret to HTTP by default."""
        source = os.environ.get("TUSHARE_API_SOURCE", "official").strip().lower()
        if source not in ("official", "relay"):
            raise ValueError("TUSHARE_API_SOURCE must be official or relay")
        key = "TUSHARE_TOKEN" if source == "official" else "TUSHARE_RELAY_TOKEN"
        token = os.environ.get(key, "")
        if not token:
            raise ValueError(f"{key} is required for the {source} API source")
        return cls(token, timeout=timeout, api_source=source,
                   relay_url=os.environ.get("TUSHARE_RELAY_URL") or None,
                   allow_insecure_http=os.environ.get("TUSHARE_ALLOW_HTTP") == "1")

    def query(self, api_name: str, params: dict | None = None, fields: str = "", retries: int = 3) -> list[dict]:
        body = json.dumps({"api_name": api_name, "token": self._token,
                           "params": params or {}, "fields": fields}).encode("utf-8")
        request = Request(self.api_url, data=body,
                          headers={"Content-Type": "application/json", "User-Agent": "a-share-value-screener/0.1"},
                          method="POST")
        for attempt in range(retries):
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    payload = json.load(response)
                if payload.get("code") != 0:
                    # Provider failures usually indicate permission / quota / query errors.
                    raise TushareError(f"{api_name}: provider error {payload.get('code')}: {payload.get('msg')}")
                fields_out = (payload.get("data") or {}).get("fields") or []
                items = (payload.get("data") or {}).get("items") or []
                return [dict(zip(fields_out, item)) for item in items]
            except TushareError:
                raise  # Permission errors should fail visibly, not burn the quota.
            except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
                if attempt == retries - 1:
                    raise TushareError(f"{api_name}: network/response failure ({type(exc).__name__})") from exc
                time.sleep(2 ** attempt)
        raise AssertionError("unreachable")

    def trade_open(self, day: str) -> bool:
        rows = self.query("trade_cal", {"exchange": "SSE", "start_date": day, "end_date": day}, "cal_date,is_open")
        if not rows:
            raise TushareError("trade_cal returned no rows; cannot certify session")
        return str(rows[0].get("is_open")) == "1"

    def stocks(self) -> list[dict]:
        rows = self.query("stock_basic", {"exchange": "", "list_status": "L"},
                          "ts_code,name,industry,market,list_date")
        if not rows:
            raise TushareError("stock_basic returned no listed companies")
        return rows

    def daily(self, day: str) -> list[dict]:
        return self.query("daily_basic", {"trade_date": day},
                          "ts_code,trade_date,close,turnover_rate,pb,total_mv")

    @staticmethod
    def report_fields(kind: str) -> str:
        common = "ts_code,ann_date,f_ann_date,end_date,report_type,comp_type,"
        mapping = {
            "balancesheet": ("total_cur_assets,total_liab,total_assets,money_cap,"
                             "accounts_receiv,inventories,prepayment,minority_int,oth_cur_assets"),
            "income": "revenue,n_income,n_income_attr_p",
            "cashflow": "n_cashflow_act",
        }
        if kind not in mapping:
            raise ValueError("unsupported report API")
        return common + mapping[kind]

    def report_rows(self, api_name: str, code: str, start_date: str, end_date: str) -> list[dict]:
        # End-date params filter publication/report range depending on provider;
        # use a sufficiently long window and screen disclosures point-in-time.
        return self.query(api_name,
                          {"ts_code": code, "start_date": start_date, "end_date": end_date},
                          self.report_fields(api_name))

    def bulk_report_rows(self, kind: str, period: str) -> list[dict]:
        """Tushare Pro VIP: all companies for a given reporting quarter.

        Requires separate permissions; call only during deliberate bootstrap.
        """
        return self.query(f"{kind}_vip", {"period": period}, self.report_fields(kind))
