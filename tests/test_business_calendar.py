"""
Business calendar, WD0 and the close workday indicator (spec section 20, REQ-MR-15..17). Pure functions, no database.
Month/year boundaries, weekends, configured and observed holidays, and the America/Chicago date rollover.
"""
import json
from datetime import date, datetime, timezone

import pytest

from app.services import business_calendar as bc


@pytest.fixture(autouse=True)
def _default_calendar():
    prev = bc.set_calendar(bc.load_calendar())
    yield
    bc.set_calendar(prev)


# ---- WD0 -------------------------------------------------------------------

def test_wd0_is_the_last_business_day_of_the_month():
    assert bc.wd0_for_month(date(2026, 11, 1)) == date(2026, 11, 30)      # Monday
    assert bc.wd0_for_month(date(2026, 10, 1)) == date(2026, 10, 30)      # Oct 31 is a Saturday
    assert bc.wd0_for_month(date(2027, 1, 1)) == date(2027, 1, 29)        # Jan 31 is a Sunday
    assert bc.wd0_for_month(date(2026, 12, 1)) == date(2026, 12, 31)      # Thursday


def test_wd0_skips_an_observed_holiday_across_a_year_boundary():
    # Jan 1, 2028 is a Saturday: New Year's Day is observed on Friday Dec 31, 2027
    assert bc.holiday_name(date(2027, 12, 31)) == "New Year's Day (observed)"
    assert bc.wd0_for_month(date(2027, 12, 1)) == date(2027, 12, 30)


def test_spec_bd_n_equals_wd_plus_n():
    # the spec's "BD n" counts business days after month end; WD+n counts them after WD0. Same day.
    for month in (date(2026, 10, 1), date(2026, 11, 1), date(2027, 1, 1), date(2026, 12, 1)):
        wd0 = bc.wd0_for_month(month)
        end = date(month.year, month.month, 1).replace(day=28)
        import calendar
        end = date(month.year, month.month, calendar.monthrange(month.year, month.month)[1])
        for n in (1, 5, 7, 10):
            assert bc.add_workdays(end, n) == bc.workday_on(wd0, n)


# ---- offsets and labels ----------------------------------------------------

def test_workday_offsets_across_a_holiday_and_a_weekend():
    wd0 = date(2026, 11, 30)
    assert bc.wd_label(bc.wd_offset(date(2026, 11, 30), wd0)) == "WD0"
    assert bc.wd_label(bc.wd_offset(date(2026, 11, 27), wd0)) == "WD−1"          # Friday
    assert bc.wd_label(bc.wd_offset(date(2026, 11, 25), wd0)) == "WD−2"          # Thanksgiving (Nov 26) does not count
    assert bc.wd_offset(date(2026, 11, 26), wd0) is None                              # the holiday itself
    assert bc.wd_label(bc.wd_offset(date(2026, 12, 2), wd0)) == "WD+2"


def test_wd_plus_n_crosses_the_month_and_year_boundaries():
    wd0 = bc.wd0_for_month(date(2026, 11, 1))
    assert bc.workday_on(wd0, 5) == date(2026, 12, 7)
    dec = bc.wd0_for_month(date(2026, 12, 1))
    # Dec 31 (Thu) WD0; Jan 1, 2027 is a Friday holiday; Jan 2-3 weekend; Jan 4 is WD+1
    assert bc.workday_on(dec, 1) == date(2027, 1, 4)
    assert bc.workday_on(dec, -1) == date(2026, 12, 30)


# ---- the indicator ----------------------------------------------------------

def test_indicator_before_on_and_after_wd0():
    m = date(2026, 11, 1)
    i = bc.indicator(m, date(2026, 11, 25))
    assert (i["label"], i["remaining"], i["phase"]) == ("WD−2", 2, "before")
    i = bc.indicator(m, date(2026, 11, 30))
    assert (i["label"], i["remaining"], i["phase"]) == ("WD0", 0, "today")
    i = bc.indicator(m, date(2026, 12, 2))
    assert (i["label"], i["phase"]) == ("WD+2", "after")


def test_weekend_is_a_non_workday_and_does_not_advance_the_counter():
    m = date(2026, 11, 1)
    sat = bc.indicator(m, date(2026, 11, 28))
    assert sat["label"] == "Non-workday" and sat["non_workday_reason"] == "Saturday"
    assert sat["next_workday"] == date(2026, 11, 30) and sat["next_label"] == "WD0" and sat["remaining"] == 1
    sun = bc.indicator(m, date(2026, 11, 29))
    assert sun["label"] == "Non-workday" and sun["next_label"] == "WD0"
    # the counter on the Friday before and the Monday after differ by exactly one workday
    assert bc.indicator(m, date(2026, 11, 27))["offset"] == -1 and bc.indicator(m, date(2026, 11, 30))["offset"] == 0


def test_holiday_is_a_non_workday_with_its_name():
    i = bc.indicator(date(2026, 11, 1), date(2026, 11, 26))
    assert i["label"] == "Non-workday" and i["non_workday_reason"] == "Thanksgiving Day"
    assert i["next_workday"] == date(2026, 11, 27) and i["next_label"] == "WD−1"


def test_observed_holidays_fall_on_the_nearest_weekday():
    assert bc.holiday_name(date(2026, 7, 3)) == "Independence Day (observed)"      # July 4, 2026 is a Saturday
    assert bc.holiday_name(date(2026, 7, 4)) is None                               # the Saturday itself is a weekend anyway
    assert bc.holiday_name(date(2027, 7, 5)) == "Independence Day (observed)"      # July 4, 2027 is a Sunday
    assert not bc.is_workday(date(2026, 7, 3)) and bc.is_workday(date(2026, 7, 2))
    assert bc.holiday_name(date(2026, 5, 25)) == "Memorial Day" and bc.holiday_name(date(2026, 9, 7)) == "Labor Day"


def test_configured_calendar_replaces_the_default(tmp_path):
    f = tmp_path / "cal.json"
    f.write_text(json.dumps({"name": "Mudbugs confirmed", "provisional": False, "observe_weekend_shift": False,
                             "rules": [], "dates": [{"date": "2026-11-30", "name": "Company closed"}]}))
    bc.set_calendar(bc.load_calendar(str(f)))
    assert bc.ACTIVE.name == "Mudbugs confirmed" and not bc.ACTIVE.provisional
    assert bc.holiday_name(date(2026, 11, 26)) is None                 # Thanksgiving is no longer assumed
    assert bc.wd0_for_month(date(2026, 11, 1)) == date(2026, 11, 27)   # Nov 30 is closed, so WD0 moves to Friday
    assert bc.indicator(date(2026, 11, 1), date(2026, 11, 30))["label"] == "Non-workday"


def test_the_default_calendar_is_provisional_and_says_so():
    assert bc.ACTIVE.provisional and "not confirmed" in bc.ACTIVE.note.lower()
    assert "provisional" in bc.WD0_DEFINITION.lower()


# ---- America/Chicago --------------------------------------------------------

def test_chicago_date_rolls_over_at_chicago_midnight_not_utc_midnight():
    # 05:30 UTC on Dec 1 is 23:30 CST on Nov 30: still WD0 in Chicago
    assert bc.chicago_today(datetime(2026, 12, 1, 5, 30, tzinfo=timezone.utc)) == date(2026, 11, 30)
    # 06:30 UTC is 00:30 CST on Dec 1
    assert bc.chicago_today(datetime(2026, 12, 1, 6, 30, tzinfo=timezone.utc)) == date(2026, 12, 1)
    # daylight time: CDT is UTC-5, so 04:30 UTC on Jul 1 is still Jun 30 in Chicago
    assert bc.chicago_today(datetime(2026, 7, 1, 4, 30, tzinfo=timezone.utc)) == date(2026, 6, 30)
    assert bc.chicago_today(datetime(2026, 7, 1, 5, 30, tzinfo=timezone.utc)) == date(2026, 7, 1)


def test_chicago_date_around_the_year_and_dst_boundaries():
    assert bc.chicago_today(datetime(2027, 1, 1, 5, 59, tzinfo=timezone.utc)) == date(2026, 12, 31)
    assert bc.chicago_today(datetime(2027, 1, 1, 6, 0, tzinfo=timezone.utc)) == date(2027, 1, 1)
    assert bc.chicago_today(datetime(2026, 3, 8, 5, 30, tzinfo=timezone.utc)) == date(2026, 3, 7)    # day DST begins


def test_a_naive_datetime_is_refused():
    with pytest.raises(ValueError):
        bc.chicago_today(datetime(2026, 11, 30, 12, 0))


# ---- verification against the spec's exact definitions ----------------------
# Spec 6.1: "BD = business day after month end" (the n-th business day after the CALENDAR month end).
# Provisional anchor: WD0 = the LAST BUSINESS DAY of the month. They are different anchors; the claim under test is only that
# BD n and WD+n name the same date, which holds if no business day can lie between WD0 and the calendar month end.
import calendar as _cal
import random


def _month_end(m):
    return date(m.year, m.month, _cal.monthrange(m.year, m.month)[1])


def _bd_after_month_end(m, n):
    """Independent, brute-force reading of the spec: scan forward from the calendar month end counting business days."""
    d, left = _month_end(m), n
    while left:
        d += __import__("datetime").timedelta(days=1)
        if bc.is_workday(d):
            left -= 1
    return d


def _all_months():
    for y in range(2024, 2033):
        for mo in range(1, 13):
            yield date(y, mo, 1)


def test_weekend_month_ends_keep_the_two_anchors_apart_but_the_same_deadlines():
    # Oct 31, 2026 is a Saturday: calendar month end and WD0 differ
    m = date(2026, 10, 1)
    assert _month_end(m) == date(2026, 10, 31) and _month_end(m).weekday() == 5
    assert bc.wd0_for_month(m) == date(2026, 10, 30)
    assert bc.workday_on(bc.wd0_for_month(m), 1) == date(2026, 11, 2) == _bd_after_month_end(m, 1)
    assert bc.workday_on(bc.wd0_for_month(m), 5) == _bd_after_month_end(m, 5) == date(2026, 11, 6)
    # Jan 31, 2027 is a Sunday
    m = date(2027, 1, 1)
    assert _month_end(m).weekday() == 6 and bc.wd0_for_month(m) == date(2027, 1, 29)
    assert bc.workday_on(bc.wd0_for_month(m), 1) == _bd_after_month_end(m, 1) == date(2027, 2, 1)


def test_a_holiday_on_the_month_end_moves_wd0_not_the_deadlines():
    # observed New Year's Day for 2028 is Fri Dec 31, 2027 (the calendar month end itself)
    m = date(2027, 12, 1)
    assert _month_end(m) == date(2027, 12, 31) and bc.holiday_name(_month_end(m)) is not None
    assert bc.wd0_for_month(m) == date(2027, 12, 30)
    for n in (1, 5, 7, 10):
        assert bc.workday_on(bc.wd0_for_month(m), n) == _bd_after_month_end(m, n)
    # a configured closure on the month end, and one on the first business day after it
    bc.set_calendar(bc.Calendar("test", False, "", False, [], [{"date": "2026-11-30", "name": "Closed"}, {"date": "2026-12-01", "name": "Closed too"}]))
    m = date(2026, 11, 1)
    assert bc.wd0_for_month(m) == date(2026, 11, 27)
    assert bc.workday_on(bc.wd0_for_month(m), 1) == _bd_after_month_end(m, 1) == date(2026, 12, 2)


def test_bd_n_equals_wd_plus_n_for_every_month_2024_2032_with_the_default_calendar():
    bad = [(m, n) for m in _all_months() for n in (1, 2, 5, 7, 10)
           if bc.workday_on(bc.wd0_for_month(m), n) != _bd_after_month_end(m, n)]
    assert bad == []                      # no failing example exists with the default calendar


def test_bd_n_equals_wd_plus_n_with_arbitrary_holiday_sets():
    rnd = random.Random(20261108)
    for _ in range(40):
        extra = [{"date": date(rnd.choice((2026, 2027)), rnd.randint(1, 12), rnd.randint(1, 28)).isoformat(), "name": "x"} for _ in range(rnd.randint(0, 25))]
        # plus closures straddling month ends, the case most likely to break the equivalence
        for m in (date(2026, 10, 31), date(2026, 11, 30), date(2026, 12, 31), date(2027, 1, 29), date(2027, 1, 31)):
            for off in range(rnd.randint(0, 3)):
                extra.append({"date": (m - __import__("datetime").timedelta(days=off)).isoformat(), "name": "y"})
            extra.append({"date": (m + __import__("datetime").timedelta(days=rnd.randint(1, 3))).isoformat(), "name": "z"})
        bc.set_calendar(bc.Calendar("random", False, "", rnd.random() < 0.5, [], extra))
        for m in (date(2026, 10, 1), date(2026, 11, 1), date(2026, 12, 1), date(2027, 1, 1)):
            for n in (1, 3, 5, 7, 10):
                assert bc.workday_on(bc.wd0_for_month(m), n) == _bd_after_month_end(m, n), (extra, m, n)


def test_wd_labels_are_consistent_both_ways():
    for m in (date(2026, 10, 1), date(2026, 11, 1), date(2027, 1, 1)):
        wd0 = bc.wd0_for_month(m)
        for off in range(-6, 12):
            d = bc.workday_on(wd0, off)
            assert bc.is_workday(d) and bc.wd_offset(d, wd0) == off


# ---- the two anchors are explicit ------------------------------------------

def test_calendar_month_end_and_the_last_business_day_are_separate_anchors():
    sat = date(2026, 10, 1)
    assert bc.calendar_month_end(sat) == date(2026, 10, 31) and bc.wd0_for_month(sat) == date(2026, 10, 30)
    assert bc.calendar_month_end(date(2026, 11, 1)) == bc.wd0_for_month(date(2026, 11, 1)) == date(2026, 11, 30)
    assert bc.indicator(sat, date(2026, 10, 29))["month_end"] == date(2026, 10, 31)
    assert bc.indicator(sat, date(2026, 10, 29))["wd0"] == date(2026, 10, 30)
    # a configured closure on the month end moves WD0 but not the calendar month end
    bc.set_calendar(bc.Calendar("test", False, "", False, [], [{"date": "2026-11-30", "name": "Closed"}]))
    assert bc.calendar_month_end(date(2026, 11, 1)) == date(2026, 11, 30) and bc.wd0_for_month(date(2026, 11, 1)) == date(2026, 11, 27)


def test_bd_after_month_end_counts_from_the_calendar_month_end_not_wd0():
    assert bc.bd_after_month_end(date(2026, 10, 1), 1) == date(2026, 11, 2)       # Oct 31 is a Saturday; Monday is the 1st business day after
    assert bc.bd_after_month_end(date(2026, 10, 1), 5) == date(2026, 11, 6)
    assert bc.bd_after_month_end(date(2026, 11, 1), 5) == date(2026, 12, 7)
    assert bc.bd_after_month_end(date(2026, 12, 1), 1) == date(2027, 1, 4)        # Jan 1 holiday, then a weekend
