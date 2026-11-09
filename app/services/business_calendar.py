"""
Business calendar and close-workday convention for the Monthly Review (spec section 20, REQ-MR-15..17).

THE ONE PLACE THAT DEFINES: (1) which days are workdays, (2) which holidays are observed, (3) what WD0 is.

(1) Workdays: Monday to Friday, excluding the holidays of the active calendar. Dates are America/Chicago.
(2) Holidays: the spec defines none. DEFAULT_CALENDAR below is a PROVISIONAL assumption (six common US holidays, observed
    on the nearest weekday when they fall on a weekend). It is shown on screen as provisional until Mudbugs confirms it.
    To change it without code, point BUSINESS_CALENDAR_FILE at a JSON file:
        {"name": "...", "provisional": false, "observe_weekend_shift": true,
         "rules": [{"name": "Christmas Day", "month": 12, "day": 25},
                   {"name": "Thanksgiving Day", "month": 11, "weekday": 3, "nth": 4}],      # weekday 0=Mon .. 6=Sun, nth -1 = last
         "dates": [{"date": "2026-11-27", "name": "Day after Thanksgiving"}]}              # extra one-off closures
    TWO ANCHORS, KEPT SEPARATE: the calendar month end (calendar_month_end) and WD0 (wd0_for_month, the last business day).
    They differ when the month ends on a weekend or holiday. The spec's "BD n" is counted from the calendar month end
    (bd_after_month_end); WD+n is counted from WD0. Verified: they name the same date for every month and any holiday set.
(3) WD0: the spec defines "BD n" = the n-th business day after month end (section 6.2 deadlines) but no WD0. PROVISIONAL
    CONVENTION: WD0 is the last business day of the reporting month; WD-1 is the business day before it; WD+1 the one after.
    Because no business day lies between WD0 and month end, the spec's "BD n" is the same day as WD+n.
"""
import calendar
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from app import config

TIMEZONE = ZoneInfo("America/Chicago")

WD0_DEFINITION = "WD0 = the last business day of the reporting month (provisional convention; the spec defines only BD n)."

DEFAULT_CALENDAR = {
    "name": "Provisional business calendar",
    "provisional": True,
    "note": "Assumed, not confirmed by Mudbugs: weekends plus six common US holidays, each observed on the nearest weekday.",
    "observe_weekend_shift": True,
    "rules": [
        {"name": "New Year's Day", "month": 1, "day": 1},
        {"name": "Memorial Day", "month": 5, "weekday": 0, "nth": -1},
        {"name": "Independence Day", "month": 7, "day": 4},
        {"name": "Labor Day", "month": 9, "weekday": 0, "nth": 1},
        {"name": "Thanksgiving Day", "month": 11, "weekday": 3, "nth": 4},
        {"name": "Christmas Day", "month": 12, "day": 25},
    ],
    "dates": [],
}


@dataclass
class Calendar:
    name: str
    provisional: bool
    note: str
    observe_weekend_shift: bool
    rules: list
    dates: list
    _cache: dict = field(default_factory=dict, repr=False)

    def holidays_for(self, year: int) -> dict:
        """date -> label, for holidays whose OBSERVED date falls in `year` (an observed date can cross a year boundary)."""
        if year not in self._cache:
            found = {}
            for y in (year - 1, year, year + 1):
                for r in self.rules:
                    d = _rule_date(r, y)
                    if d is None:
                        continue
                    obs = d
                    if self.observe_weekend_shift and "day" in r:
                        if d.weekday() == 5:
                            obs = d - timedelta(days=1)
                        elif d.weekday() == 6:
                            obs = d + timedelta(days=1)
                    label = r["name"] + (" (observed)" if obs != d else "")
                    if obs.year == year:
                        found[obs] = label
                for x in self.dates:
                    d = date.fromisoformat(x["date"])
                    if d.year == year:
                        found[d] = x["name"]
            self._cache[year] = found
        return self._cache[year]


def _rule_date(r: dict, year: int) -> Optional[date]:
    if "day" in r:
        try:
            return date(year, r["month"], r["day"])
        except ValueError:
            return None
    wd, nth, m = r["weekday"], r["nth"], r["month"]
    days = [date(year, m, d) for d in range(1, calendar.monthrange(year, m)[1] + 1) if date(year, m, d).weekday() == wd]
    try:
        return days[nth] if nth < 0 else days[nth - 1]
    except IndexError:
        return None


def load_calendar(path: str = "") -> Calendar:
    spec = dict(DEFAULT_CALENDAR)
    if path:
        with open(path, encoding="utf-8") as fh:
            spec.update(json.load(fh))
    return Calendar(spec["name"], bool(spec.get("provisional", True)), spec.get("note", ""), bool(spec.get("observe_weekend_shift", True)),
                    list(spec.get("rules", [])), list(spec.get("dates", [])))


ACTIVE: Calendar = load_calendar(config.BUSINESS_CALENDAR_FILE)


def set_calendar(cal: Calendar) -> Calendar:
    """Swap the active calendar (tests, or a confirmed calendar). Returns the previous one."""
    global ACTIVE
    prev, ACTIVE = ACTIVE, cal
    return prev


# --- dates -----------------------------------------------------------------------------------------------------------------

def chicago_today(now: Optional[datetime] = None) -> date:
    """Today's date in America/Chicago. `now` may be any aware datetime (UTC on a server, for example)."""
    now = now or datetime.now(TIMEZONE)
    if now.tzinfo is None:
        raise ValueError("pass an aware datetime")
    return now.astimezone(TIMEZONE).date()


def holiday_name(d: date) -> Optional[str]:
    return ACTIVE.holidays_for(d.year).get(d)


def is_workday(d: date) -> bool:
    return d.weekday() < 5 and holiday_name(d) is None


def next_workday(d: date, *, inclusive: bool = False) -> date:
    x = d if inclusive else d + timedelta(days=1)
    while not is_workday(x):
        x += timedelta(days=1)
    return x


def prev_workday(d: date, *, inclusive: bool = False) -> date:
    x = d if inclusive else d - timedelta(days=1)
    while not is_workday(x):
        x -= timedelta(days=1)
    return x


def add_workdays(d: date, n: int) -> date:
    """The n-th workday after (n>0) or before (n<0) d, counting only workdays; n=0 gives d itself if it is a workday, else the next one."""
    if n == 0:
        return next_workday(d, inclusive=True)
    x, step, left = d, (1 if n > 0 else -1), abs(n)
    while left:
        x += timedelta(days=step)
        if is_workday(x):
            left -= 1
    return x


def calendar_month_end(month_start: date) -> date:
    """The calendar month end (anchor 1). Not necessarily a business day."""
    return month_start.replace(day=calendar.monthrange(month_start.year, month_start.month)[1])


def bd_after_month_end(month_start: date, n: int) -> date:
    """The spec's "BD n" (section 6.1/6.2): the n-th business day AFTER the calendar month end (anchor 1)."""
    return add_workdays(calendar_month_end(month_start), n)


def wd0_for_month(month_start: date) -> date:
    """WD0 (provisional convention): the last business day of the month."""
    return prev_workday(calendar_month_end(month_start), inclusive=True)


def wd_offset(d: date, wd0: date) -> Optional[int]:
    """Signed workday count from WD0 (0 on WD0); None when d is not a workday."""
    if not is_workday(d):
        return None
    if d == wd0:
        return 0
    lo, hi, sign = (d, wd0, -1) if d < wd0 else (wd0, d, 1)
    n, x = 0, lo
    while x < hi:
        x += timedelta(days=1)
        if is_workday(x):
            n += 1
    return sign * n


def wd_label(n: int) -> str:
    return "WD0" if n == 0 else (f"WD+{n}" if n > 0 else f"WD−{abs(n)}")


def workday_on(wd0: date, offset: int) -> date:
    """The calendar date of WD<offset> for a given WD0."""
    if offset == 0:
        return wd0
    x, step, left = wd0, (1 if offset > 0 else -1), abs(offset)
    while left:
        x += timedelta(days=step)
        if is_workday(x):
            left -= 1
    return x


def indicator(month_start: date, today: date) -> dict:
    """The Close Workday Indicator for a reporting month, as of `today` (America/Chicago)."""
    wd0 = wd0_for_month(month_start)
    off = wd_offset(today, wd0)
    d = {"wd0": wd0, "month_end": calendar_month_end(month_start), "today": today, "calendar": ACTIVE, "definition": WD0_DEFINITION}
    if off is None:
        why = holiday_name(today) or today.strftime("%A")
        nxt = next_workday(today)
        n_off = wd_offset(nxt, wd0)
        d.update(label="Non-workday", offset=None, non_workday_reason=why, next_workday=nxt, next_label=wd_label(n_off),
                 remaining=_workdays_between(today, wd0) if today < wd0 else 0)
    else:
        d.update(label=wd_label(off), offset=off, non_workday_reason=None, next_workday=None, next_label=None,
                 remaining=max(-off, 0))
    d["phase"] = "before" if today < wd0 else ("today" if today == wd0 else "after")
    return d


def _workdays_between(start: date, end: date) -> int:
    """Workdays after `start` up to and including `end`."""
    n, x = 0, start
    while x < end:
        x += timedelta(days=1)
        if is_workday(x):
            n += 1
    return n
