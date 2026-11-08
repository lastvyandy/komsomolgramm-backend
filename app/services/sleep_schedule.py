"""An optional published schedule; never a claim that the person is actually asleep."""
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

def in_sleep_window(enabled: bool, start: int, end: int, timezone: str, now: datetime) -> bool:
    if not enabled or start == end:
        return False
    try:
        local = now.astimezone(ZoneInfo(timezone))
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return False
    minute = local.hour * 60 + local.minute
    return start <= minute < end if start < end else minute >= start or minute < end
