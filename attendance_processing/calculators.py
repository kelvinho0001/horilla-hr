"""Pure functions (no database): easy to test and reused by payroll."""

from datetime import time, timedelta

OK, ATTENTION = "ok", "attention"


def _windows(window):
    """Accept None, one (start, end) pair, or a list of pairs."""
    if not window:
        return []
    if isinstance(window[0], time):
        return [tuple(window)]
    return [tuple(w) for w in window]


def classify(punch_times, window=None):
    """
    Decide whether one day's punches make a day of attendance.

    ``window`` is None (any time counts), a (start, end) pair, or a list of pairs (e.g. night and afternoon
    shift). Any number of valid punches is still ONE day. Punches in two different windows (two shifts in
    one day) are not counted silently; neither are punches that fall outside every window.
    Returns (OK | ATTENTION, flags).
    """
    flags = []
    windows = _windows(window)
    if not punch_times:
        return None, flags
    if len(punch_times) > 1:
        flags.append("multiple_punches")
    in_window = [t for t in punch_times if not windows or any(a <= t <= b for a, b in windows)]
    used = {i for t in in_window for i, (a, b) in enumerate(windows) if a <= t <= b}
    if len(used) > 1:
        flags.append("two_shifts_same_day")
        return ATTENTION, flags
    if in_window:
        return OK, flags
    flags.append("out_of_window")
    return ATTENTION, flags


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
