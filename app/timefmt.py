from datetime import datetime
from zoneinfo import ZoneInfo

KYIV = ZoneInfo("Europe/Kyiv")


def iso_kyiv(dt: datetime) -> str:
    return dt.astimezone(KYIV).isoformat()
