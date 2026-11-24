"""Select eligible completed market sessions without look-ahead."""
from __future__ import annotations
from datetime import date, datetime, timedelta
from math import ceil, isfinite
import re
import logging
from zoneinfo import ZoneInfo
from .tushare_client import TushareError

CN_TZ = ZoneInfo("Asia/Shanghai")
LOGGER = logging.getLogger(__name__)
MARKET_CLOSE = (15, 0)
SCHEDULED_REFRESH = (19, 30)


def early_probe(day: date, requested: str | None, now: datetime) -> bool:
    local = now.astimezone(CN_TZ)
    return (not requested and day == local.date()
            and MARKET_CLOSE <= (local.hour, local.minute) < SCHEDULED_REFRESH)


def minimum_quotes(listed_count: int, requested: str | None, day: date, now: datetime) -> int:
    # Current automatic updates require substantially complete market coverage.
    # Explicit historical recovery retains the established 70% floor.
    current = not requested or day == now.astimezone(CN_TZ).date()
    return max(1000, ceil(listed_count * 0.95) if current else round(listed_count * 0.70))


def validate_quotes(stocks: list[dict], rows: list[dict], day: date) -> list[dict]:
    """Validate before any quote writes; count unique listed securities only."""
    listed = {s["ts_code"] for s in stocks}
    expected = day.strftime("%Y%m%d")
    seen = set()
    valid = []
    for row in rows:
        code = row.get("ts_code")
        if str(row.get("trade_date")) != expected:
            raise TushareError("daily_basic contains unexpected trade date")
        if not isinstance(code, str) or not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", code):
            raise TushareError("daily_basic contains invalid security code")
        if code in seen:
            raise TushareError("daily_basic contains duplicate security code")
        seen.add(code)
        if code not in listed:
            continue
        try:
            close, cap = float(row["close"]), float(row["total_mv"])
            if not (isfinite(close) and isfinite(cap) and close > 0 and cap > 0):
                continue
            for name in ("pb", "turnover_rate"):
                if row.get(name) is not None and not isfinite(float(row[name])):
                    raise ValueError("invalid optional numeric value")
        except (KeyError, ValueError, TypeError, OverflowError):
            continue
        valid.append(row)
    return valid

def market_session(client, requested: str | None, now: datetime) -> date:
    """Select the last eligible completed session, never the pre-open date.

    After 15:00 CST, select today if it is an open session. Readiness is
    checked separately against valid quote coverage. 19:30 remains the
    scheduled refresh time. Explicit dates are strict and never substituted.
    """
    local = now.astimezone(CN_TZ)
    if requested:
        day = date.fromisoformat(requested)
        if day > local.date():
            raise ValueError("Requested market date is in the future")
        return day
    bound = local.date() - timedelta(days=int((local.hour, local.minute) < MARKET_CLOSE))
    start = bound - timedelta(days=30)
    rows = client.query("trade_cal", {"exchange": "SSE",
        "start_date": start.strftime("%Y%m%d"), "end_date": bound.strftime("%Y%m%d")},
        "cal_date,is_open")
    sessions = set()
    for row in rows:
        raw = str(row.get("cal_date") or "")
        if len(raw) != 8 or not raw.isdigit():
            raise TushareError("Invalid market calendar date")
        day = datetime.strptime(raw, "%Y%m%d").date()
        if start <= day <= bound and str(row.get("is_open")) == "1":
            sessions.add(day)
    if not sessions:
        raise TushareError(f"No verified open session through {bound}")
    selected = max(sessions)
    LOGGER.info("Market session selection: now=%s close=15:00 CST scheduled=19:30 CST bound=%s selected=%s",
                local.isoformat(), bound, selected)
    return selected

