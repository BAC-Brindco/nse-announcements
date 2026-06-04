"""
Corporate Actions scraper.

Endpoints:
  /api/corporates-corporateActions?index=equities
  /api/corporates-corporateActions?index=sme

Fields: symbol, comp, isin, series, exDate, recDate, subject,
        faceVal, caBroadcastDate
"""

import logging

from scrapers.nse_session import NSESession
from database.client import bulk_upsert
from utils import clean_str, parse_nse_date, parse_nse_dt, today_ist

logger = logging.getLogger(__name__)

_URLS = {
    "equities": "https://www.nseindia.com/api/corporates-corporateActions?index=equities",
    "sme":      "https://www.nseindia.com/api/corporates-corporateActions?index=sme",
}


def _parse(row: dict, segment: str, scrape_date: str) -> dict | None:
    symbol = clean_str(row.get("symbol"))
    ex_date = parse_nse_date(row.get("exDate"))
    subject = clean_str(row.get("subject"))
    if not symbol or not ex_date:
        return None
    return {
        "symbol":       symbol,
        "company":      clean_str(row.get("comp")),
        "isin":         clean_str(row.get("isin")),
        "series":       clean_str(row.get("series")),
        "segment":      segment,
        "ex_date":      ex_date,
        "record_date":  parse_nse_date(row.get("recDate")),
        "subject":      subject,
        "face_val":     clean_str(row.get("faceVal")),
        "broadcast_at": parse_nse_dt(row.get("caBroadcastDate")),
        "scrape_date":  scrape_date,
        "raw_payload":  row,
    }


def scrape_corporate_actions(session: NSESession | None = None) -> dict:
    session = session or NSESession()
    scrape_date = today_ist()
    total_fetched = total_upserted = 0

    for segment, url in _URLS.items():
        try:
            payload = session.get_json(url)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Corporate actions %s failed: %s", segment, exc)
            continue
        rows = payload if isinstance(payload, list) else []
        logger.info("Corporate actions %s: %d rows", segment, len(rows))
        records = [r for r in (_parse(row, segment, scrape_date) for row in rows) if r]
        total_fetched += len(records)
        n = bulk_upsert("corporate_actions", records,
                        conflict_columns=["symbol", "ex_date", "subject"])
        total_upserted += n
        logger.info("Corporate actions %s: %d upserted", segment, n)

    return {"fetched": total_fetched, "upserted": total_upserted}
