"""Time zones that also work on Windows.

Windows has no built-in time zone database, so ZoneInfo("America/New_York") fails
unless the `tzdata` package is installed. In that case we fall back to the
computer's local time zone and print a one-time hint.
"""
from __future__ import annotations

from datetime import datetime, tzinfo
from functools import lru_cache


@lru_cache(maxsize=8)
def get_tz(name: str) -> tzinfo:
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:
        print(f"  Note: time zone '{name}' isn't installed on this computer, so TrialMate uses your local time. "
              "On Windows, 'py -m pip install tzdata' fixes this.")
        return datetime.now().astimezone().tzinfo  # type: ignore[return-value]
