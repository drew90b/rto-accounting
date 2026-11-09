"""
Reporting period and date helpers for the Monthly Review (pure; no database).

Spec section 20 (REQ-MR-01) and section 6.1: a month is its calendar month, except that November 2026 starts at the
Workday 1 snapshot and is the stub period "Nov 2 to 30 (partial)", never presented as the full month.
"""
import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

# first day of the month -> the day its reporting period actually starts (spec 6.1)
STUB_STARTS = {date(2026, 11, 1): date(2026, 11, 2)}


class BadMonth(ValueError):
    pass


@dataclass(frozen=True)
class Period:
    month: date          # first day of the calendar month
    start: date          # first day of the reporting period
    end: date            # last day of the month
    label: str           # "November 2026"
    window: str          # "Nov 2 to 30, 2026 (partial)"
    is_stub: bool

    @property
    def span(self) -> str:
        return self.window.replace(" (partial)", "")

    @property
    def key(self) -> str:
        return self.month.strftime("%Y-%m")


def parse_month(raw: Optional[str], default: date) -> date:
    """'2026-11' -> date(2026, 11, 1). Blank gives the default month. Anything else is refused."""
    if raw is None or not raw.strip():
        return default.replace(day=1)
    m = re.fullmatch(r"(\d{4})-(\d{2})", raw.strip())
    if not m:
        raise BadMonth("Choose a month like 2026-11.")
    y, mo = int(m.group(1)), int(m.group(2))
    if not (2000 <= y <= 2100 and 1 <= mo <= 12):
        raise BadMonth("Choose a month like 2026-11.")
    return date(y, mo, 1)


def month_end(month: date) -> date:
    return month.replace(day=calendar.monthrange(month.year, month.month)[1])


def add_months(month: date, n: int) -> date:
    idx = month.year * 12 + (month.month - 1) + n
    return date(idx // 12, idx % 12 + 1, 1)


def _short(d: date) -> str:
    return d.strftime("%b ") + str(d.day)


def period_for(month: date) -> Period:
    month = month.replace(day=1)
    end = month_end(month)
    start = STUB_STARTS.get(month, month)
    stub = start != month
    window = f"{_short(start)} to {end.day}, {end.year}" + (" (partial)" if stub else "")
    if not stub:
        window = f"{_short(start)} to {_short(end)}, {end.year}"
    return Period(month, start, end, month.strftime("%B %Y"), window, stub)


def is_weekday(d: date) -> bool:
    return d.weekday() < 5


def business_day_after(end: date, n: int) -> date:
    """The n-th business day after `end` (the spec's "BD n"), using the active business calendar. This equals WD+n."""
    from app.services import business_calendar as bc
    return bc.add_workdays(end, n)


def fridays(start: date, end: date) -> list:
    d = start + timedelta(days=(4 - start.weekday()) % 7)
    out = []
    while d <= end:
        out.append(d)
        d += timedelta(days=7)
    return out


def days(start: date, end: date) -> set:
    return {start + timedelta(days=i) for i in range((end - start).days + 1)} if start <= end else set()


def uncovered(period: Period, intervals) -> list:
    """Gaps inside the reporting period not covered by any (start, end) interval -> [(start, end), ...]."""
    covered = set()
    for s, e in intervals:
        if s and e:
            covered |= days(s, e)
    gaps, run = [], None
    d = period.start
    while d <= period.end:
        if d in covered:
            if run:
                gaps.append(run)
                run = None
        else:
            run = (run[0], d) if run else (d, d)
        d += timedelta(days=1)
    if run:
        gaps.append(run)
    return gaps


def fmt_range(s: date, e: date) -> str:
    return _short(s) if s == e else f"{_short(s)} to {_short(e)}"
