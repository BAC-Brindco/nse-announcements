"""
Fixture capture for a given report edition.

Reproduces the exact Supabase queries the report runs for a chosen
(report_date, today) pair and snapshots the raw rows to JSON under
tests/fixtures/<name>/ so the report can be re-rendered deterministically
offline (no DB, stable across runs).

Usage:
  python tests/capture_fixture.py                                  # issue113 (2026-06-09)
  python tests/capture_fixture.py --date 2026-08-07 --name aug07
  python tests/capture_fixture.py --date 2026-08-18 --name aug18

``--today`` defaults to ``--date``: the forward-looking sections (board
meetings, event calendar, corporate actions) are anchored on "today" in the
live report, so pinning today=report_date reconstructs the edition as it
would have been assembled that morning.

CAVEAT: board meetings and the event calendar are *snapshot* feeds — the
scrapers fetch whatever the endpoint currently exposes, with no date window.
Rows added after the original edition shipped are therefore included in a
replay (the event-calendar query has no upper date bound at all). A replay is
a faithful re-run of the queries, not a byte-copy of the historical email.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytz
from dotenv import load_dotenv

load_dotenv()

_IST = pytz.timezone("Asia/Kolkata")
_FIXROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

# Mirrors _HIGH_PRIORITY | _MEDIUM_PRIORITY in the report module.
_PRICE_CATEGORIES = {
    "Financial Results", "Integrated Filing- Financial", "Outcome of Board Meeting",
    "Record Date", "Disclosure under SEBI Takeover Regulations",
    "Shareholders meeting", "Analysts/Institutional Investor Meet/Con. Call Updates",
    "Investor Presentation", "Press Release", "Agreements", "Appointment",
    "Cessation", "Credit rating", "Annual Report",
}


def _paginate(query_fn):
    page, page_size, out = 0, 1000, []
    while True:
        resp = query_fn(page * page_size, (page + 1) * page_size - 1)
        chunk = resp.data or []
        out.extend(chunk)
        if len(chunk) < page_size:
            break
        page += 1
    return out


def capture(report_date: date, today: date, name: str) -> int:
    from database.client import get_client
    client = get_client()

    fixdir = os.path.join(_FIXROOT, name)

    dt_start = _IST.localize(datetime.combine(report_date, datetime.min.time())).isoformat()
    dt_end = _IST.localize(
        datetime.combine(report_date + timedelta(days=1), datetime.min.time())).isoformat()

    ann = _paginate(lambda s, e:
        client.table("corporate_announcements").select("*")
        .gte("announced_at", dt_start).lt("announced_at", dt_end)
        .order("announced_at", desc=True).range(s, e).execute())

    bm = _paginate(lambda s, e:
        client.table("board_meetings").select("*")
        .eq("source", "board_meetings")
        .gte("meeting_date", today.isoformat())
        .lte("meeting_date", (today + timedelta(days=14)).isoformat())
        .order("meeting_date").range(s, e).execute())

    ec = _paginate(lambda s, e:
        client.table("board_meetings").select("*")
        .eq("source", "event_calendar")
        .gte("meeting_date", today.isoformat())
        .order("meeting_date").range(s, e).execute())

    ca = _paginate(lambda s, e:
        client.table("corporate_actions").select("*")
        .gte("ex_date", today.isoformat())
        .lte("ex_date", (today + timedelta(days=7)).isoformat())
        .order("ex_date").range(s, e).execute())

    syms = sorted({
        r["symbol"] for r in ann
        if r.get("symbol") and r.get("category") in _PRICE_CATEGORIES
    })
    prices_rows = []
    if syms:
        from_date = (today - timedelta(days=7)).isoformat()
        # in_() has a URL-length ceiling; chunk the symbol list.
        for i in range(0, len(syms), 200):
            resp = (client.table("daily_prices")
                .select("symbol,close,prev_close,volume,value_cr,trade_date")
                .gte("trade_date", from_date).lte("trade_date", today.isoformat())
                .eq("series", "EQ").in_("symbol", syms[i:i + 200])
                .order("trade_date", desc=True).execute())
            prices_rows.extend(resp.data or [])

    os.makedirs(fixdir, exist_ok=True)
    for fname, rows in [
        ("announcements", ann), ("board_meetings", bm),
        ("event_calendar", ec), ("corporate_actions", ca), ("prices", prices_rows),
    ]:
        path = os.path.join(fixdir, f"{fname}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, indent=2, default=str, ensure_ascii=False)
        print(f"  {fname}: {len(rows)} rows -> {path}")

    meta = {"report_date": report_date.isoformat(), "today": today.isoformat()}
    with open(os.path.join(fixdir, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2)
    print("  meta:", meta)
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--date", default="2026-06-09", help="Report date (YYYY-MM-DD)")
    p.add_argument("--today", help="Anchor for forward sections (default: --date)")
    p.add_argument("--name", default="issue113", help="Fixture directory name")
    a = p.parse_args()

    report_date = date.fromisoformat(a.date)
    today = date.fromisoformat(a.today) if a.today else report_date
    print(f"Capturing {a.name}: report_date={report_date} today={today}")
    return capture(report_date, today, a.name)


if __name__ == "__main__":
    sys.exit(main())
