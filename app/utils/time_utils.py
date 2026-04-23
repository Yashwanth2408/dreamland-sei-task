"""
Timezone-aware day boundary utilities.

WHY THIS MATTERS:
  A user in Tokyo playing at 23:30 JST is still on TODAY in Japan,
  but UTC has already crossed to tomorrow.
  Daily cap MUST use the user's local day — not UTC midnight.
  All timestamps are stored as UTC in DB, but boundaries are
  computed in the user's IANA timezone.
"""
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def get_user_day_bounds(
    user_timezone: str,
    reference_dt: datetime,
) -> tuple[datetime, datetime]:
    """
    Given a reference datetime and a user's IANA timezone string,
    return (day_start_utc, day_end_utc) for the local calendar day
    that reference_dt falls on.

    Example:
      user_timezone = "Asia/Kolkata"
      reference_dt  = 2024-11-01 20:00:00 UTC
      → local time  = 2024-11-02 01:30:00 IST  (next day in India!)
      → returns day boundaries for Nov 2 in IST, converted to UTC
    """
    try:
        tz = ZoneInfo(user_timezone)
    except ZoneInfoNotFoundError:
        tz = timezone.utc

    local_dt        = reference_dt.astimezone(tz)
    local_day_start = local_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    local_day_end   = local_day_start + timedelta(days=1)

    return (
        local_day_start.astimezone(timezone.utc),
        local_day_end.astimezone(timezone.utc),
    )


def floor_to_hour(dt: datetime) -> datetime:
    """
    Floor a datetime to the start of its UTC hour.

    Example:
      2024-11-01 14:37:22 UTC → 2024-11-01 14:00:00 UTC
    Used by the conversion job to compute which hour bucket to process.
    """
    utc = dt.astimezone(timezone.utc)
    return utc.replace(minute=0, second=0, microsecond=0)