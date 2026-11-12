"""Deterministic extraction of an operation timestamp from an SMS message."""

import re
from datetime import datetime
from zoneinfo import ZoneInfo


MOSCOW_TIMEZONE = ZoneInfo("Europe/Moscow")

# Sber messages commonly use DD.MM.YY or DD.MM.YYYY, optionally followed by
# HH:MM. Requiring a year prevents decimal amounts from being treated as dates.
_OPERATION_DATETIME_RE = re.compile(
    r"(?<!\d)"
    r"(?P<day>0?[1-9]|[12]\d|3[01])"
    r"[.\-/]"
    r"(?P<month>0?[1-9]|1[0-2])"
    r"[.\-/]"
    r"(?P<year>\d{2}|\d{4})"
    r"(?:\s+(?P<hour>[01]?\d|2[0-3]):(?P<minute>[0-5]\d))?"
    r"(?!\d)"
)


def _as_moscow_time(received_at: datetime) -> datetime:
    """Return the receipt timestamp in Moscow time.

    PostgreSQL returns timezone-aware values for this field. A naive value is
    treated as Moscow local time to keep development and SQLite behavior
    predictable.
    """
    if received_at.tzinfo is None:
        return received_at.replace(tzinfo=MOSCOW_TIMEZONE)
    return received_at.astimezone(MOSCOW_TIMEZONE)


def parse_transaction_datetime(sms_text: str, received_at: datetime) -> datetime:
    """Extract operation time from SMS, falling back to message receipt time.

    If an SMS contains only the operation date, its time is inherited from the
    receipt timestamp. Invalid calendar dates are ignored safely.
    """
    fallback = _as_moscow_time(received_at)
    match = _OPERATION_DATETIME_RE.search(sms_text)
    if match is None:
        return fallback

    year = int(match.group("year"))
    if year < 100:
        year += 2000

    hour = int(match.group("hour")) if match.group("hour") is not None else fallback.hour
    minute = int(match.group("minute")) if match.group("minute") is not None else fallback.minute

    try:
        return datetime(
            year,
            int(match.group("month")),
            int(match.group("day")),
            hour,
            minute,
            tzinfo=MOSCOW_TIMEZONE,
        )
    except ValueError:
        return fallback
