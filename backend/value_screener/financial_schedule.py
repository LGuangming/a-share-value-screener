"""Quarter calendar and verified actual-disclosure events for 2000-point plans."""
from __future__ import annotations
from calendar import monthrange
from datetime import date
import re


def recent_closed_quarters(asof: date, count: int = 2) -> list[date]:
    """Two most recent finished quarters, handling year transitions."""
    year, quarter = asof.year, (asof.month - 1) // 3
    if quarter == 0:
        year, quarter = year - 1, 4
    result = []
    for _ in range(count):
        month = quarter * 3
        result.append(date(year, month, monthrange(year, month)[1]))
        quarter -= 1
        if quarter == 0:
            quarter, year = 4, year - 1
    return result


def confirmed_disclosures(rows: list[dict], period: date, asof: date) -> list[dict]:
    """An announced *plan* never counts as a disclosed financial report."""
    confirmed: dict[str, dict] = {}
    for row in rows:
        code = str(row.get("ts_code") or "")
        if not re.fullmatch(r"\d{6}\.(SH|SZ)", code):
            continue
        if str(row.get("end_date") or "") != period.strftime("%Y%m%d"):
            continue
        raw = str(row.get("actual_date") or "")
        if not re.fullmatch(r"\d{8}", raw):
            continue
        try:
            actual = date.fromisoformat(f"{raw[:4]}-{raw[4:6]}-{raw[6:]}")
        except ValueError:
            continue
        if not period < actual <= asof:
            continue
        previous = confirmed.get(code)
        if not previous or actual > previous["actual_date"]:
            confirmed[code] = {"ts_code": code, "period": period, "actual_date": actual}
    return list(confirmed.values())
