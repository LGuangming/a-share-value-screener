"""Select eligible completed market sessions without look-ahead."""
from __future__ import annotations
from datetime import date, datetime, timedelta
import logging
from zoneinfo import ZoneInfo
from .tushare_client import TushareError

CN_TZ = ZoneInfo("Asia/Shanghai")
LOGGER = logging.getLogger(__name__)

def market_session(client, requested: str | None, now: datetime) -> date:
    """Select the last eligible completed session, never the pre-open date.

    The 19:30 CST cutoff is the normal refresh schedule, not a guarantee
    of provider availability. Explicit dates are strict and never substituted.
    """
    local = now.astimezone(CN_TZ)
    if requested:
        day = date.fromisoformat(requested)
        if day > local.date():
            raise ValueError("Requested market date is in the future")
        return day
    bound = local.date() - timedelta(days=int((local.hour, local.minute) < (19, 30)))
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
    LOGGER.info("Market session selection: now=%s cutoff=19:30 CST bound=%s selected=%s",
                local.isoformat(), bound, selected)
    return selected

