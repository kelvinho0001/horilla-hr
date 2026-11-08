"""Pure functions (no database): easy to test and reused by payroll later."""

from datetime import timedelta

from .models import ABSENT, HOLIDAY, LEAVE, MISSING, PRESENT, REST, REVIEW  # noqa: F401


def classify(punch_times, *, window, working_weekday, is_holiday, on_leave):
    """
    Decide one day's status from the local times of that day's punches.

    ``window`` is (start, end) or None. Several punches never mean several
    days: any number of in-window punches is still one PRESENT.
    Returns (status, flags).
    """
    flags = []
    if punch_times:
        if len(punch_times) > 1:
            flags.append("multiple_punches")
        in_window = [
            t for t in punch_times if window is None or window[0] <= t <= window[1]
        ]
        if in_window:
            if is_holiday or not working_weekday:
                flags.append("worked_on_non_working_day")
            return PRESENT, flags
        flags.append("out_of_window")
        return REVIEW, flags
    if on_leave:
        return LEAVE, flags
    if is_holiday:
        return HOLIDAY, flags
    if not working_weekday:
        return REST, flags
    return MISSING, flags


def week_buckets(start, end):
    """Consecutive 7-day blocks from ``start``; the last may be shorter."""
    out, cursor = [], start
    while cursor <= end:
        last = min(cursor + timedelta(days=6), end)
        out.append((cursor, last))
        cursor = last + timedelta(days=1)
    return out


def daterange(start, end):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)
