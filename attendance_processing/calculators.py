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


def inside(t, a, b):
    """Is time ``t`` in the window a..b? A window whose end is before its start runs overnight (19:00-09:00)."""
    return a <= t <= b if a <= b else (t >= a or t <= b)


def attendance_day(moment, window=None):
    """
    The day a punch counts for. Normally its own date. In an overnight window (start after end, e.g. 19:00-09:00)
    the after-midnight part belongs to the day the shift STARTED: a 04:00 punch on the 26th is the 25th's night shift.
    """
    t = moment.time()
    for a, b in _windows(window):
        if a > b and t <= b and t < a:
            return moment.date() - timedelta(days=1)
        if inside(t, a, b):
            return moment.date()
    return moment.date()


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
    in_window = [t for t in punch_times if not windows or any(inside(t, a, b) for a, b in windows)]
    used = {i for t in in_window for i, (a, b) in enumerate(windows) if inside(t, a, b)}
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
