"""Minimal official Tushare Pro HTTP adapter with errors and bounded retries.
Docs: https://tushare.pro/document/2 . Credentials must never be logged.
"""
from __future__ import annotations
from collections import deque
import json
import logging
import math
import os
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

API_URL = "https://api.tushare.pro"
RELAY_API_URL = "http://tus.sxdata.top/tu_share/"
LOGGER = logging.getLogger(__name__)

# Client-local rolling-window cap. Scheduling and manual jobs should not run in
# parallel against the same token; enforce provider account-wide quotas outside
# the process if multiple workers share it.
MAX_REQUESTS_PER_MINUTE = 100
MIN_REQUEST_INTERVAL_SECONDS = 0.61  # > 60/100; avoids 101 calls at minute boundaries
RETRY_WAIT_SECONDS = 60


class TushareError(RuntimeError):
    pass


class TushareClient:
    def __init__(self, token: str, timeout: int = 40, *,
                 api_source: str = "official", relay_url: str | None = None,
                 allow_insecure_http: bool = False,
                 relay_min_interval_seconds: float = 5.0):
        if not token:
            raise ValueError("Tushare API token is required")
        if api_source not in ("official", "relay"):
            raise ValueError("TUSHARE_API_SOURCE must be official or relay")
        self.source = api_source
        self._token = token
        self.timeout = timeout
        if (not math.isfinite(relay_min_interval_seconds) or
                not 0.5 <= relay_min_interval_seconds <= 60):
            raise ValueError("Relay minimum request interval must be between 0.5 and 60 seconds")
        self._relay_interval = relay_min_interval_seconds
        self._min_request_interval = max(
            MIN_REQUEST_INTERVAL_SECONDS,
            self._relay_interval if api_source == "relay" else 0.0
        )
        self._last_request_start: float | None = None
        self._request_starts: deque[float] = deque()
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
        try:
            interval = float(os.environ.get("TUSHARE_RELAY_MIN_INTERVAL_SECONDS", "5"))
        except ValueError as exc:
            raise ValueError("TUSHARE_RELAY_MIN_INTERVAL_SECONDS must be numeric") from exc
        return cls(token, timeout=timeout, api_source=source,
                   relay_url=os.environ.get("TUSHARE_RELAY_URL") or None,
                   allow_insecure_http=os.environ.get("TUSHARE_ALLOW_HTTP") == "1",
                   relay_min_interval_seconds=interval)

    def _wait_for_request_slot(self) -> None:
        """Pace every outgoing call and enforce <=100 starts in any 60s window.

        Counts retry attempts as well as initial calls. All calls from one CLI
        job share this instance, so scanning and financial-report loops need no
        separate sleeps.
        """
        while True:
            now = time.monotonic()
            while self._request_starts and now - self._request_starts[0] >= 60.0:
                self._request_starts.popleft()
            wait = 0.0
            if self._last_request_start is not None:
                wait = max(wait, self._min_request_interval -
                           (now - self._last_request_start))
            if len(self._request_starts) >= MAX_REQUESTS_PER_MINUTE:
                wait = max(wait, 60.0 - (now - self._request_starts[0]))
            if wait <= 0:
                self._request_starts.append(now)
                self._last_request_start = now
                return
            time.sleep(wait)

    def _retry_transient(self, api_name: str, attempt: int, retries: int,
                         reason: str) -> bool:
        """Delay a retriable error for exactly 60 seconds, max 'retries' calls."""
        if attempt >= retries - 1:
            return False
        LOGGER.warning("%s: temporary %s; retrying after %d seconds (%d/%d)",
                       api_name, reason, RETRY_WAIT_SECONDS, attempt + 1, retries)
        time.sleep(RETRY_WAIT_SECONDS)
        return True

    def query(self, api_name: str, params: dict | None = None, fields: str = "",
              retries: int = 3) -> list[dict]:
        body = json.dumps({"api_name": api_name, "token": self._token,
                           "params": params or {}, "fields": fields}).encode("utf-8")
        request = Request(self.api_url, data=body,
                          headers={"Content-Type": "application/json",
                                   "User-Agent": "a-share-value-screener/0.1"},
                          method="POST")
        if retries < 1:
            raise ValueError("retries must be positive")
        for attempt in range(retries):
            self._wait_for_request_slot()
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    payload = json.load(response)
                if not isinstance(payload, dict):
                    raise ValueError("provider response must be a JSON object")
                code = payload.get("code")
                if code != 0:
                    message = str(payload.get("msg") or "")
                    rate_limited = (str(code) == "429" or
                                    "请求速度过快" in message or
                                    "too many requests" in message.lower())
                    server_error = str(code) in {"500", "502", "503", "504"}
                    if rate_limited or server_error:
                        if self._retry_transient(api_name, attempt, retries,
                                                 "rate limit" if rate_limited else "server error"):
                            continue
                        reason = "rate-limited (429)" if rate_limited else "provider server error"
                        raise TushareError(f"{api_name}: {reason} after {retries} attempts")
                    # Permanent permissions, API names and validation errors are
                    # never retried and never sent to another provider automatically.
                    raise TushareError(f"{api_name}: provider error {code}: {message}")
                fields_out = (payload.get("data") or {}).get("fields") or []
                items = (payload.get("data") or {}).get("items") or []
                return [dict(zip(fields_out, item)) for item in items]
            except HTTPError as exc:
                if exc.code in (429, 500, 502, 503, 504):
                    if self._retry_transient(api_name, attempt, retries,
                                             f"HTTP {exc.code}"):
                        continue
                raise TushareError(f"{api_name}: HTTP {exc.code} from selected API") from exc
            except (URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError) as exc:
                if self._retry_transient(api_name, attempt, retries,
                                         type(exc).__name__):
                    continue
                raise TushareError(
                    f"{api_name}: network/response failure ({type(exc).__name__}) "
                    f"after {retries} attempts") from exc
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
