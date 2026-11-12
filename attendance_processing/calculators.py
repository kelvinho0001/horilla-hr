"""Pure functions (no database): easy to test and reused by payroll later."""

from datetime import time, timedelta

from .models import ABSENT, HOLIDAY, LEAVE, MISSING, PRESENT, REST, REVIEW  # noqa: F401


def _windows(window):
    """Accept None, one (start, end) pair, or a list of pairs."""
    if not window:
        return []
    if isinstance(window[0], time):
        return [tuple(window)]
    return [tuple(w) for w in window]


def classify(punch_times, *, window, working_weekday, is_holiday, on_leave):
    """
    Decide one day's status from the local times of that day's punches.

    ``window`` is None, a (start, end) pair or a list of pairs (e.g. night and afternoon shift).
    Several punches never mean several days: any number of in-window punches is still one
    PRESENT. But in-window punches in two *different* windows (two shifts in one day) go to
    review instead of being counted silently. Returns (status, flags).
    """
    flags = []
    windows = _windows(window)
    if punch_times:
        if len(punch_times) > 1:
            flags.append("multiple_punches")
        in_window = [t for t in punch_times if not windows or any(a <= t <= b for a, b in windows)]
        used = {i for t in in_window for i, (a, b) in enumerate(windows) if a <= t <= b}
        if len(used) > 1:
            flags.append("two_shifts_same_day")
            return REVIEW, flags
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
