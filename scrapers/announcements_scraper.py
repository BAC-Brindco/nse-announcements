"""
Corporate Announcements scraper.

Fetches from three segments (equities, sme, debt) over an explicit date
window. All three share the same field schema. Key fields used:
  seq_id, symbol, sm_name, sm_isin, smIndustry, desc,
  attchmntText, attchmntFile, attFileSize, an_dt, sort_date

Why the date window matters
---------------------------
This scraper originally called /api/corporate-announcements?index=<seg>
with no date parameters. That form returns only a small trailing slice of
the most recent filings (~20 rows), and there is no pagination. Polling it
5x/day therefore captured only what happened to be in the window at each
poll and permanently lost everything filed in between: measured against
NSE's own date-ranged responses, capture was ~27% on 2026-08-03..07 while
every run was reporting success.

Passing from_date/to_date makes the same endpoint return the complete set
for the window, which is both correct and replayable. A trailing lookback
(default 7 days) also makes the pipeline self-healing: because upserts are
idempotent on seq_id, any run recovers whatever earlier runs missed, so an
outage shorter than the lookback repairs itself with no manual backfill.

Dates are DD-MM-YYYY (NSE's format). Ranges up to ~90 days respond fine;
beyond that the response gets large enough to risk a client timeout, so
long backfills should be chunked.
"""

import logging
import os
from datetime import date, datetime, timedelta

from scrapers.nse_session import NSESession
from database.client import bulk_upsert
from utils import IST, clean_str, parse_nse_dt

logger = logging.getLogger(__name__)

_BASE = "https://www.nseindia.com/api/corporate-announcements"

_SEGMENT_NAMES = ("equities", "sme", "debt")

# Trailing window re-fetched on every run. Sized to absorb a multi-day
# outage (weekend + holidays) without manual intervention.
_DEFAULT_LOOKBACK_DAYS = int(os.getenv("ANNOUNCEMENTS_LOOKBACK_DAYS", "7"))


def _nse_date(d: date) -> str:
    return d.strftime("%d-%m-%Y")


def _url(segment: str, from_date: date, to_date: date) -> str:
    return (
        f"{_BASE}?index={segment}"
        f"&from_date={_nse_date(from_date)}"
        f"&to_date={_nse_date(to_date)}"
    )


def _parse(row: dict, segment: str, scrape_date: str | None = None) -> dict | None:
    seq_id = clean_str(str(row.get("seq_id", "")))
    if not seq_id:
        return None
    # an_dt is reliable across all three segments. Note debt returns an
    # uppercase month ("11-AUG-2026") vs title case elsewhere; parse_nse_dt
    # uses %b, which strptime matches case-insensitively.
    announced_at = parse_nse_dt(row.get("an_dt"))
    # Now that a run covers a multi-day window, "today" is the wrong stamp
    # for a row filed 5 days ago. Derive it from the announcement instead so
    # the column means the same thing for live and backfilled rows.
    if scrape_date is None:
        scrape_date = announced_at[:10] if announced_at else None
    return {
        "seq_id":          seq_id,
        "segment":         segment,
        "symbol":          clean_str(row.get("symbol")),
        "company_name":    clean_str(row.get("sm_name")),
        "isin":            clean_str(row.get("sm_isin")),
        "industry":        clean_str(row.get("smIndustry")),
        "category":        clean_str(row.get("desc")),
        "summary":         clean_str(row.get("attchmntText")),
        "attachment_url":  clean_str(row.get("attchmntFile")),
        "attachment_size": clean_str(row.get("attFileSize")),
        "announced_at":    announced_at,
        "scrape_date":     scrape_date,
        "raw_payload":     row,
    }


def scrape_announcements(
    session: NSESession | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    lookback_days: int | None = None,
) -> dict:
    """Scrape announcements for a date window.

    Defaults to a trailing `lookback_days` window ending today (IST). Pass
    explicit from_date/to_date to backfill. Upserts are idempotent on
    seq_id, so overlapping windows are safe and re-running is a no-op.
    """
    session  = session or NSESession()
    today    = datetime.now(IST).date()
    to_date   = to_date   or today
    from_date = from_date or to_date - timedelta(
        days=lookback_days if lookback_days is not None else _DEFAULT_LOOKBACK_DAYS
    )
    if from_date > to_date:
        raise ValueError(f"from_date {from_date} is after to_date {to_date}")

    logger.info("Announcements window: %s -> %s", from_date, to_date)
    total_fetched = total_upserted = 0
    failed: list[str] = []

    for segment in _SEGMENT_NAMES:
        url = _url(segment, from_date, to_date)
        try:
            payload = session.get_json(url)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Announcements %s failed: %s", segment, exc)
            failed.append(segment)
            continue

        rows = payload if isinstance(payload, list) else []
        logger.info("Announcements %s: %d rows", segment, len(rows))

        records = [r for r in (_parse(row, segment) for row in rows) if r]
        total_fetched += len(records)

        n = bulk_upsert("corporate_announcements", records, conflict_columns=["seq_id"])
        total_upserted += n
        logger.info("Announcements %s: %d upserted", segment, n)

    result = {
        "fetched":   total_fetched,
        "upserted":  total_upserted,
        "from_date": from_date.isoformat(),
        "to_date":   to_date.isoformat(),
    }
    if failed:
        result["failed_segments"] = failed
    return result
