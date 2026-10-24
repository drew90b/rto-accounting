#!/usr/bin/env python3
"""Retention for backups made by rto-backup.sh.

Keeps: the newest backup; every backup from the last 14 days; the oldest backup of
each of the last 8 weeks; the oldest backup of each of the last 6 months.
Only directories named rto_YYYYMMDD_HHMMSS directly under ROOT are ever considered
or deleted. Anything else in ROOT is left alone.

    usage: rto-backup-prune.py ROOT [--dry-run]
"""
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

NAME = re.compile(r"^rto_(\d{8})_(\d{6})$")
DAILY_DAYS, WEEKLY_WEEKS, MONTHLY_DAYS = 14, 8, 183


def choose(stamps, now):
    """stamps: datetimes. Returns the set to keep."""
    keep, weeks, months = set(), {}, {}
    ordered = sorted(stamps)
    if ordered:
        keep.add(ordered[-1])
    for t in ordered:  # oldest first, so setdefault keeps the oldest of each period
        age = now - t
        if age <= timedelta(days=DAILY_DAYS):
            keep.add(t)
        if age <= timedelta(weeks=WEEKLY_WEEKS):
            weeks.setdefault(t.isocalendar()[:2], t)
        if age <= timedelta(days=MONTHLY_DAYS):
            months.setdefault((t.year, t.month), t)
    return keep | set(weeks.values()) | set(months.values())


def main(argv):
    root = Path(argv[1])
    dry = "--dry-run" in argv
    found = {}
    for p in root.iterdir():
        m = NAME.match(p.name)
        if m and p.is_dir() and not p.is_symlink():
            found[datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)] = p
    keep = choose(found, datetime.now(timezone.utc))
    removed = 0
    for t, p in sorted(found.items()):
        if t not in keep:
            print(("would remove " if dry else "removing ") + p.name)
            if not dry:
                shutil.rmtree(p)
            removed += 1
    print("rto-backup-prune: kept %d, removed %d" % (len(found) - removed, removed))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv)
