"""
Board Meetings scraper.

Fetches from TWO endpoints, both stored in the same table:

  /api/corporate-board-meetings?index=equities  (source='board_meetings')
  /api/corporate-board-meetings?index=sme       (source='board_meetings')
  /api/event-calendar?index=equities            (source='event_calendar')  [no index param needed]
  /api/event-calendar                           (source='event_calendar')

board-meetings fields:
  bm_symbol, bm_date, bm_purpose, bm_desc, sm_name, sm_isin, bm_timestamp, attachment

event-calendar fields:
  symbol, company, purpose, bm_desc, date

Both get stored in the `board_meetings` table.
Deduplication key: (symbol, meeting_date, purpose, source)

Note: board-meetings generates two records per meeting (XBRL + PDF).
We skip records whose purpose == 'Board Meeting Intimation' since those
are structural XBRL wrappers with no added information beyond the PDF record.
"""

import logging

from scrapers.nse_session import NSESession
from database.client import bulk_upsert
from utils import clean_str, parse_nse_date, parse_nse_dt, today_ist

logger = logging.getLogger(__name__)

_BM_URLS = {
    "equities": "https://www.nseindia.com/api/corporate-board-meetings?index=equities",
    "sme":      "https://www.nseindia.com/api/corporate-board-meetings?index=sme",
}

_EC_URLS = {
    "equities": "https://www.nseindia.com/api/event-calendar",
    "sme":      "https://www.nseindia.com/api/event-calendar?index=sme",
}

_SKIP_PURPOSES = {"Board Meeting Intimation"}


def _parse_bm(row: dict, segment: str, scrape_date: str) -> dict | None:
    symbol  = clean_str(row.get("bm_symbol"))
    purpose = clean_str(row.get("bm_purpose"))
    if not symbol or purpose in _SKIP_PURPOSES:
        return None
    meeting_date = parse_nse_date(row.get("bm_date"))
    if not meeting_date:
        return None
    return {
        "symbol":         symbol,
        "segment":        segment,
        "source":         "board_meetings",
        "meeting_date":   meeting_date,
        "purpose":        purpose,
        "description":    clean_str(row.get("bm_desc")),
        "company_name":   clean_str(row.get("sm_name")),
        "isin":           clean_str(row.get("sm_isin")),
        "attachment_url": clean_str(row.get("attachment")),
        "filed_at":       parse_nse_dt(row.get("bm_timestamp")),
        "scrape_date":    scrape_date,
        "raw_payload":    row,
    }


def _parse_ec(row: dict, segment: str, scrape_date: str) -> dict | None:
    symbol  = clean_str(row.get("symbol"))
    purpose = clean_str(row.get("purpose"))
    if not symbol:
        return None
    meeting_date = parse_nse_date(row.get("date"))
    if not meeting_date:
        return None
    return {
        "symbol":         symbol,
        "segment":        segment,
        "source":         "event_calendar",
        "meeting_date":   meeting_date,
        "purpose":        purpose,
        "description":    clean_str(row.get("bm_desc")),
        "company_name":   clean_str(row.get("company")),
        "isin":           None,
        "attachment_url": None,
        "filed_at":       None,
        "scrape_date":    scrape_date,
        "raw_payload":    row,
    }


def scrape_board_meetings(session: NSESession | None = None) -> dict:
    session = session or NSESession()
    scrape_date = today_ist()
    total_fetched = total_upserted = 0

    # ── Board meetings endpoint ───────────────────────────────────────────────
    for segment, url in _BM_URLS.items():
        try:
            payload = session.get_json(url)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Board meetings %s failed: %s", segment, exc)
            continue
        rows = payload if isinstance(payload, list) else []
        logger.info("Board meetings %s: %d raw rows", segment, len(rows))
        records = [r for r in (_parse_bm(row, segment, scrape_date) for row in rows) if r]
        total_fetched += len(records)
        n = bulk_upsert("board_meetings", records,
                        conflict_columns=["symbol", "meeting_date", "purpose", "source"])
        total_upserted += n
        logger.info("Board meetings %s: %d upserted", segment, n)

    # ── Event calendar endpoint ───────────────────────────────────────────────
    for segment, url in _EC_URLS.items():
        try:
            payload = session.get_json(url)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Event calendar %s failed: %s", segment, exc)
            continue
        rows = payload if isinstance(payload, list) else []
        logger.info("Event calendar %s: %d raw rows", segment, len(rows))
        records = [r for r in (_parse_ec(row, segment, scrape_date) for row in rows) if r]
        total_fetched += len(records)
        n = bulk_upsert("board_meetings", records,
                        conflict_columns=["symbol", "meeting_date", "purpose", "source"])
        total_upserted += n
        logger.info("Event calendar %s: %d upserted", segment, n)

    return {"fetched": total_fetched, "upserted": total_upserted}
