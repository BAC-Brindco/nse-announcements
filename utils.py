import logging
from datetime import date, datetime, timedelta

import pytz

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")


def today_ist() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


def clean_str(val) -> str | None:
    if val is None:
        return None
    s = str(val).strip()
    return s if s not in ("", "-", "NA", "N/A", "None", "null") else None


def parse_nse_dt(val: str | None) -> str | None:
    """Parse NSE datetime strings → ISO 8601 UTC string or None.
    Handles: '04-Jun-2026 15:16:18', '04-JUN-2026 14:41:37'
    """
    if not val:
        return None
    s = str(val).strip()
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%B-%Y %H:%M:%S"):
        try:
            dt = datetime.strptime(s, fmt)
            # NSE timestamps are IST — attach timezone then convert to UTC
            dt_ist = IST.localize(dt)
            return dt_ist.isoformat()
        except ValueError:
            continue
    logger.debug("Could not parse NSE datetime: %r", val)
    return None


def parse_nse_date(val: str | None) -> str | None:
    """Parse NSE date strings → ISO YYYY-MM-DD or None.
    Handles: '04-Jun-2026', '05-Jun-2026'
    """
    if not val:
        return None
    s = str(val).strip()
    if s in ("", "-", "NA"):
        return None
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    logger.debug("Could not parse NSE date: %r", val)
    return None


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5


def previous_trading_day(today: date) -> date:
    d = today - timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d
