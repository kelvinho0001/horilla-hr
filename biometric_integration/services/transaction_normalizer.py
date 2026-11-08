"""Turns connector output into aware datetimes and validates them."""

from datetime import timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone

from ..connectors.base import RowError

MAX_FUTURE = timedelta(days=1)


def to_aware(stamp, tz_name):
    if timezone.is_aware(stamp):
        return stamp
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown timezone '{tz_name}'") from exc
    return stamp.replace(tzinfo=tz)


def normalize(transactions, tz_name):
    """Return (list of (txn, aware_timestamp), list[RowError])."""
    good, errors = [], []
    now = timezone.now()
    for i, txn in enumerate(transactions, start=1):
        try:
            aware = to_aware(txn.timestamp, tz_name)
        except ValueError as exc:
            errors.append(RowError(i, str(exc)))
            continue
        if aware > now + MAX_FUTURE:
            errors.append(RowError(i, f"Timestamp {aware:%Y-%m-%d %H:%M} is in the future"))
            continue
        good.append((txn, aware))
    return good, errors
