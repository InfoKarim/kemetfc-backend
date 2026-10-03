"""Exact handling of manual stopwatch values.

Storage: INTEGER centiseconds (hundredths of a second). Exact on SQLite and
PostgreSQL; a binary float or a SQLite NUMERIC column is not. Python: ``Decimal``
quantised to 0.01. API plan: a two-decimal string such as "4.25", never a JSON number.
The value stored is the value the stopwatch displayed; no accuracy is claimed.
"""
import math
import re
from decimal import Decimal, InvalidOperation

from app.v1.definitions import MAX_CENTISECONDS

_TEXT = re.compile(r"^\d+(\.\d{1,2})?$")


class InvalidTimeValue(ValueError):
    """The entered time cannot be stored as typed. It is never corrected silently."""


def parse_seconds_to_centiseconds(value) -> int:
    if isinstance(value, bool) or value is None:
        raise InvalidTimeValue("A time in seconds is required")
    if isinstance(value, str):
        text = value.strip()
        if not _TEXT.match(text):
            raise InvalidTimeValue("Time must be a plain number with at most 2 decimals")
        number = Decimal(text)
    elif isinstance(value, Decimal):
        number = value
    elif isinstance(value, int):
        number = Decimal(value)
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise InvalidTimeValue("Time must be a finite number")
        number = Decimal(repr(value))  # the digits as typed, not the binary expansion
    else:
        raise InvalidTimeValue("Unsupported time type")
    try:
        if not number.is_finite():
            raise InvalidTimeValue("Time must be a finite number")
        scaled = number * 100
    except InvalidOperation as error:
        raise InvalidTimeValue("Time is not a valid number") from error
    if scaled != scaled.to_integral_value():
        raise InvalidTimeValue("Time may have at most 2 decimals; it is never rounded")
    centiseconds = int(scaled)
    if centiseconds <= 0:
        raise InvalidTimeValue("Time must be greater than zero")
    if centiseconds > MAX_CENTISECONDS:
        raise InvalidTimeValue("Time exceeds the largest stopwatch display (99:59.99)")
    return centiseconds


def centiseconds_to_decimal(centiseconds: int) -> Decimal:
    return (Decimal(centiseconds) / 100).quantize(Decimal("0.01"))


def format_seconds(centiseconds: int | None) -> str | None:
    return None if centiseconds is None else f"{centiseconds_to_decimal(centiseconds):.2f}"
