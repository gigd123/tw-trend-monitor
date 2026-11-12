"""台股多空趨勢與資金風險監控系統。"""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")


def today_taipei() -> date:
    return datetime.now(TAIPEI).date()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
