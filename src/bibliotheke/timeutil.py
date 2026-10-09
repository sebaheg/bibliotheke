"""Time values on disk and how they compare.

A time value is stored as a compact string: a year (``2013``), a month
(``2019-06``), a day (``2026-10-01``) or a UTC timestamp
(``2026-10-04T09:12:00Z``). A partial value names a whole period; ``start``
and ``end`` give its half-open bounds so periods compare as instants.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

_YEAR = re.compile(r"^\d{1,4}$")
_MONTH = re.compile(r"^(\d{1,4})-(\d{1,2})$")
_DAY = re.compile(r"^(\d{1,4})-(\d{1,2})-(\d{1,2})$")


def now() -> str:
    return canonical(datetime.now(timezone.utc))


def canonical(value) -> str | None:
    """Normalise user or YAML input (int, date, datetime, str) to the stored string."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"not a time value: {value!r}")
    if isinstance(value, int):
        return f"{value:04d}"
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if _YEAR.match(text):
        return f"{int(text):04d}"
    if m := _MONTH.match(text):
        date(int(m[1]), int(m[2]), 1)
        return f"{int(m[1]):04d}-{int(m[2]):02d}"
    if m := _DAY.match(text):
        return date(int(m[1]), int(m[2]), int(m[3])).isoformat()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(
            f"not a time value: {text!r} (use YYYY, YYYY-MM, YYYY-MM-DD or an ISO timestamp)"
        ) from None
    return canonical(parsed)


def start(value: str) -> datetime:
    """The first instant of the period ``value`` names."""
    if _YEAR.match(value):
        return datetime(int(value), 1, 1, tzinfo=timezone.utc)
    if m := _MONTH.match(value):
        return datetime(int(m[1]), int(m[2]), 1, tzinfo=timezone.utc)
    if m := _DAY.match(value):
        return datetime(int(m[1]), int(m[2]), int(m[3]), tzinfo=timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def end(value: str) -> datetime:
    """The first instant after the period ``value`` names."""
    first = start(value)
    if _YEAR.match(value):
        return first.replace(year=first.year + 1)
    if _MONTH.match(value):
        return (first + timedelta(days=32)).replace(day=1)
    if _DAY.match(value):
        return first + timedelta(days=1)
    return first + timedelta(seconds=1)
