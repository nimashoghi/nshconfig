"""Classification of scalar defaults that drafts may expose safely."""

import re
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from enum import Enum
from fractions import Fraction
from pathlib import PurePath
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

__all__ = ["is_known_immutable_atom"]


def is_known_immutable_atom(value: Any) -> bool:
    """Return whether a value has no mutable public children."""

    value_type = type(value)
    if value is None or value_type in {
        bool,
        bytes,
        complex,
        date,
        datetime,
        Decimal,
        float,
        Fraction,
        int,
        range,
        slice,
        str,
        time,
        timedelta,
        timezone,
        UUID,
    }:
        return True
    if isinstance(value, Enum):
        try:
            namespace = object.__getattribute__(value, "__dict__")
            extras = set(namespace) - {
                "_value_",
                "_name_",
                "__objclass__",
                "_sort_order_",
            }
            enum_value = object.__getattribute__(value, "_value_")
        except Exception:
            return False
        return not extras and is_known_immutable_atom(enum_value)
    if isinstance(value, (PurePath, ZoneInfo, re.Pattern, type)):
        return True
    return (
        value_type.__module__ == "pydantic_core._pydantic_core"
        and value_type.__name__ in {"Url", "MultiHostUrl"}
    )
