"""AKShare experimental adapter. Never conflates live snapshots with dated Tushare closes.

All accounting values are Yuan; total_mv exposed to the shared rules is 万元.
Provider data is experimental and may be blocked or changed upstream.
"""
from __future__ import annotations

from datetime import date, datetime
import math
import re
from typing import Any


class AKShareError(RuntimeError):
    pass


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        n = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return n if math.isfinite(n) else None


def _day(value: Any) -> date | None:
    if value is None:
        return None
    try:
        if hasattr(value, "to_pydatetime"):
            value = value.to_pydatetime()
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        text = str(value).strip()
        if not text or text.lower() in ("nan", "nat", "none"):
            return None
        if re.match(r"^\d{8}$", text):
            return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
        return date.fromisoformat(text[:10])
    except (ValueError, TypeError):
        return None


def _pick(row: dict, *aliases: str) -> float | None:
    for alias in aliases:
        if alias in row:
            number = _number(row[alias])
            if number is not None:
                return number
    return None


def _market(code: str) -> str | None:
    if re.match(r"^(600|601|603|605|688|689)\d{3}$", code):
        return "SH"
    if re.match(r"^(000|001|002|003|300|301)\d{3}$", code):
        return "SZ"
    if re.match(r"^(4|8|9)\d{5}$", code):
        return "BJ"
    return None


def normalize_spot(rows: list[dict]) -> dict[str, dict]:
    """Normalize Eastmoney A-share spot rows; cap is CNY yuan, not Tushare 万元."""
    result: dict[str, dict] = {}
    for row in rows:
        code = str(row.get("代码", "")).strip()
        market = _market(code)
        if market is None:
            continue
        price = _pick(row, "最新价")
        cap = _pick(row, "总市值")
        if price is None or price <= 0 or cap is None or cap <= 0:
            continue
        ts_code = f"{code}.{market}"
        pb = _pick(row, "市净率")
        result[ts_code] = {
            "ts_code": ts_code,
            "name": str(row.get("名称") or code),
            "market": "主板" if code.startswith(("000","001","002","003","600","601","603","605")) else "其他板",
            "close": price, "total_mv": cap / 10000.0,
            "pb": pb, "turnover_rate": _pick(row, "换手率"),
            "market_cap_yuan": cap,
        }
    return result


STATEMENT_FUNCTIONS = {
    "balancesheet": "stock_balance_sheet_by_report_em",
    "income": "stock_profit_sheet_by_report_em",
    "cashflow": "stock_cash_flow_sheet_by_report_em",
}
FIELD_MAP = {
    "balancesheet": {
        "total_cur_assets": ("TOTAL_CURRENT_ASSETS", "TOTAL_CUR_ASSETS", "TOTAL_CURRENT_ASSET"),
        "total_liab": ("TOTAL_LIABILITIES", "TOTAL_LIAB", "TOTAL_LIABILITY"),
        "total_assets": ("TOTAL_ASSETS",),
        "accounts_receiv": ("ACCOUNTS_RECE", "ACCOUNTS_RECEIV", "ACCOUNTS_RECEIVABLE", "ACCOUNT_RECEIVABLE"),
        "inventories": ("INVENTORY", "INVENTORIES"),
        "prepayment": ("PREPAYMENT", "PREPAYMENTS", "PREPAID_ACCOUNTS"),
        "minority_int": ("MINORITY_EQUITY", "MINORITY_INTEREST", "MINORITY_INT"),
    },
    "income": {"n_income": ("NETPROFIT", "NET_PROFIT", "NET_INCOME")},
    "cashflow": {"n_cashflow_act": ("NETCASH_OPERATE", "NET_CASH_OPERATE")},
}


def normalize_report(rows: list[dict], kind: str, asof: date) -> tuple[dict | None, date | None, date | None, list[str]]:
    """Use actual NOTICE_DATE, never fabricate disclosure date from REPORT_DATE.

    If upstream does not provide an announcement date the report is ineligible
    for as-of research. Parent-only rows are rejected.
    """
    if kind not in FIELD_MAP:
        raise ValueError(f"unsupported report kind: {kind}")
    candidates: list[tuple[date, date, dict]] = []
    for raw in rows:
        row = {str(k).upper(): v for k, v in raw.items()}
        type_name = str(row.get("REPORT_TYPE_NAME") or row.get("STATEMENT_TYPE") or "").upper()
        if "母公司" in type_name or "PARENT" in type_name:
            continue
        report_day = _day(row.get("REPORT_DATE"))
        announcement = _day(row.get("NOTICE_DATE") or row.get("ANNOUNCEMENT_DATE"))
        if not report_day or not announcement or announcement < report_day:
            continue
        # Conservative one-session lag: avoid treating after-close releases as known.
        if report_day > asof or announcement >= asof:
            continue
        candidates.append((report_day, announcement, row))
    if not candidates:
        return None, None, None, ["NO_VERIFIABLE_DISCLOSURE"]
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    report_day, announcement, row = candidates[0]
    values = {name: _pick(row, *aliases) for name, aliases in FIELD_MAP[kind].items()}
    flags: list[str] = []
    if kind == "balancesheet" and (values["total_cur_assets"] is None or values["total_liab"] is None):
        flags.append("MISSING_NCAV_INPUTS")
    if not str(row.get("REPORT_TYPE_NAME") or ""):
        flags.append("CONSOLIDATION_NOT_CONFIRMED")
    return values, report_day, announcement, flags


class AKShareClient:
    def __init__(self, provider: Any = None):
        if provider is None:
            try:
                import akshare as provider
            except ImportError as exc:
                raise AKShareError("Install backend/requirements-akshare.txt to run AKShare tests") from exc
        self.ak = provider

    @staticmethod
    def _rows(data: Any, endpoint: str) -> list[dict]:
        if data is None or not hasattr(data, "to_dict"):
            raise AKShareError(f"{endpoint}: invalid or empty DataFrame")
        try:
            rows = data.to_dict(orient="records")
        except (TypeError, ValueError, AttributeError) as exc:
            raise AKShareError(f"{endpoint}: unexpected DataFrame structure") from exc
        if not isinstance(rows, list) or not rows:
            raise AKShareError(f"{endpoint}: upstream returned no rows")
        return rows

    def spot(self) -> dict[str, dict]:
        try:
            raw = self.ak.stock_zh_a_spot_em()
            rows = self._rows(raw, "stock_zh_a_spot_em")
            result = normalize_spot(rows)
        except AKShareError:
            raise
        except Exception as exc:
            raise AKShareError(f"stock_zh_a_spot_em unavailable ({type(exc).__name__})") from exc
        if len(result) < 1000:
            raise AKShareError(f"stock_zh_a_spot_em too few valid quotes: {len(result)}; refusing incomplete market data")
        return result

    def report(self, code: str, kind: str, asof: date) -> tuple[dict | None, date | None, date | None, list[str]]:
        if kind not in STATEMENT_FUNCTIONS:
            raise ValueError("Unsupported financial statement type")
        method = STATEMENT_FUNCTIONS[kind]
        sym = code.split(".")[1] + code.split(".")[0]
        try:
            raw = getattr(self.ak, method)(symbol=sym)
            rows = self._rows(raw, method)
        except AKShareError:
            raise
        except Exception as exc:
            raise AKShareError(f"{method} {code}: upstream error ({type(exc).__name__})") from exc
        return normalize_report(rows, kind, asof)
