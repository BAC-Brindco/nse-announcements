"""
NSE CM Bhavcopy scraper.

Downloads the daily equity bhavcopy CSV from NSE archives and upserts
close price, prev_close, volume into daily_prices.

URL format (current):
  https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_DDMMYYYY.csv

Columns (note leading spaces — stripped on read):
  SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE,
  CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES

Retry logic: if the requested date returns 404 (holiday or not yet published),
walks back up to 5 trading days to find the most recent available file.
"""

import io
import logging
from datetime import date, timedelta

import pandas as pd

from scrapers.nse_session import NSESession
from database.client import bulk_upsert
from utils import today_ist, is_trading_day

logger = logging.getLogger(__name__)

_BASE = "https://nsearchives.nseindia.com/products/content"


def _url(d: date) -> str:
    return f"{_BASE}/sec_bhavdata_full_{d.strftime('%d%m%Y')}.csv"


def _prev_trading_day(d: date) -> date:
    d = d - timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def scrape_bhavcopy(session: NSESession | None = None, trade_date: date | None = None) -> dict:
    session    = session or NSESession()
    scrape_day = date.fromisoformat(today_ist())

    # Walk back from requested date to find the most recent available bhavcopy
    target = trade_date or _prev_trading_day(scrape_day)
    d      = target
    resp   = None

    for _ in range(5):
        url = _url(d)
        logger.info("Trying bhavcopy %s: %s", d, url)
        try:
            resp = session.get(url)
            logger.info("Bhavcopy found for %s (%d bytes)", d, len(resp.content))
            break
        except Exception as exc:  # noqa: BLE001
            logger.info("Not available for %s (%s) — trying previous day", d, str(exc)[:40])
            d = _prev_trading_day(d)

    if resp is None:
        logger.warning("Bhavcopy not found for any of the last 5 trading days from %s", target)
        return {"fetched": 0, "upserted": 0, "date": target.isoformat()}

    try:
        df = pd.read_csv(io.StringIO(resp.text))
        # Strip leading/trailing spaces from all column names
        df.columns = [c.strip() for c in df.columns]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Bhavcopy parse failed: %s", exc)
        return {"fetched": 0, "upserted": 0, "date": d.isoformat()}

    # Keep EQ series only
    if "SERIES" in df.columns:
        df = df[df["SERIES"].str.strip() == "EQ"].copy()
    logger.info("Bhavcopy %s: %d EQ records", d, len(df))

    def _f(v):
        try:
            return float(str(v).replace(",", "")) if pd.notna(v) else None
        except (ValueError, TypeError):
            return None

    def _i(v):
        try:
            return int(float(str(v).replace(",", ""))) if pd.notna(v) else None
        except (ValueError, TypeError):
            return None

    records = []
    for _, row in df.iterrows():
        sym = str(row.get("SYMBOL", "")).strip()
        if not sym:
            continue
        turnover_lacs = _f(row.get("TURNOVER_LACS"))
        records.append({
            "trade_date": d.isoformat(),
            "symbol":     sym,
            "series":     str(row.get("SERIES", "EQ")).strip(),
            "open":       _f(row.get("OPEN_PRICE")),
            "high":       _f(row.get("HIGH_PRICE")),
            "low":        _f(row.get("LOW_PRICE")),
            "close":      _f(row.get("CLOSE_PRICE")),
            "prev_close": _f(row.get("PREV_CLOSE")),
            "volume":     _i(row.get("TTL_TRD_QNTY")),
            "value_cr":   round(turnover_lacs / 100, 2) if turnover_lacs else None,
            "trades":     _i(row.get("NO_OF_TRADES")),
            "isin":       None,
        })

    n = bulk_upsert("daily_prices", records, conflict_columns=["trade_date", "symbol", "series"])
    logger.info("Bhavcopy %s: %d upserted", d, n)
    return {"fetched": len(records), "upserted": n, "date": d.isoformat()}
