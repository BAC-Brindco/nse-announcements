"""
Corporate Announcements scraper.

Fetches from three segments:
  equities: /api/corporate-announcements?index=equities
  sme:      /api/corporate-announcements?index=sme
  debt:     /api/corporate-announcements?index=debt

All three share the same field schema. Key fields used:
  seq_id, symbol, sm_name, sm_isin, smIndustry, desc,
  attchmntText, attchmntFile, attFileSize, an_dt, sort_date
"""

import logging

from scrapers.nse_session import NSESession
from database.client import bulk_upsert
from utils import clean_str, parse_nse_dt, today_ist

logger = logging.getLogger(__name__)

_BASE = "https://www.nseindia.com/api/corporate-announcements"

_SEGMENTS = {
    "equities": f"{_BASE}?index=equities",
    "sme":      f"{_BASE}?index=sme",
    "debt":     f"{_BASE}?index=debt",
}


def _parse(row: dict, segment: str, scrape_date: str) -> dict | None:
    seq_id = clean_str(str(row.get("seq_id", "")))
    if not seq_id:
        return None
    # an_dt is reliable across all three segments
    announced_at = parse_nse_dt(row.get("an_dt"))
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


def scrape_announcements(session: NSESession | None = None) -> dict:
    session = session or NSESession()
    scrape_date = today_ist()
    total_fetched = total_upserted = 0

    for segment, url in _SEGMENTS.items():
        try:
            payload = session.get_json(url)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Announcements %s failed: %s", segment, exc)
            continue

        rows = payload if isinstance(payload, list) else []
        logger.info("Announcements %s: %d rows", segment, len(rows))

        records = [r for r in (_parse(row, segment, scrape_date) for row in rows) if r]
        total_fetched += len(records)

        n = bulk_upsert("corporate_announcements", records, conflict_columns=["seq_id"])
        total_upserted += n
        logger.info("Announcements %s: %d upserted", segment, n)

    return {"fetched": total_fetched, "upserted": total_upserted}
