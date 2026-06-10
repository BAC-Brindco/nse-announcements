"""
One-shot fixture capture for the Issue №113 (2026-06-09) report.

Reproduces the exact Supabase queries the report ran on 2026-06-09 with
today=2026-06-09, and snapshots the raw rows to JSON under
tests/fixtures/issue113/ so the report can be re-rendered deterministically
offline (no DB, stable across runs).

Run once:  python tests/capture_fixture.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytz
from dotenv import load_dotenv

load_dotenv()

_IST = pytz.timezone("Asia/Kolkata")
FIXDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "issue113")

# Issue №113 parameters
REPORT_DATE = date(2026, 6, 9)
TODAY = date(2026, 6, 9)


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


def main() -> int:
    from database.client import get_client
    client = get_client()

    dt_start = _IST.localize(datetime.combine(REPORT_DATE, datetime.min.time())).isoformat()
    dt_end = _IST.localize(datetime.combine(REPORT_DATE + timedelta(days=1), datetime.min.time())).isoformat()

    ann = _paginate(lambda s, e:
        client.table("corporate_announcements").select("*")
        .gte("announced_at", dt_start).lt("announced_at", dt_end)
        .order("announced_at", desc=True).range(s, e).execute())

    bm = _paginate(lambda s, e:
        client.table("board_meetings").select("*")
        .eq("source", "board_meetings")
        .gte("meeting_date", TODAY.isoformat())
        .lte("meeting_date", (TODAY + timedelta(days=14)).isoformat())
        .order("meeting_date").range(s, e).execute())

    ec = _paginate(lambda s, e:
        client.table("board_meetings").select("*")
        .eq("source", "event_calendar")
        .gte("meeting_date", TODAY.isoformat())
        .order("meeting_date").range(s, e).execute())

    ca = _paginate(lambda s, e:
        client.table("corporate_actions").select("*")
        .gte("ex_date", TODAY.isoformat())
        .lte("ex_date", (TODAY + timedelta(days=7)).isoformat())
        .order("ex_date").range(s, e).execute())

    # Prices: HIGH | MEDIUM priority announcement symbols (mirror main())
    high_med = {
        "Financial Results", "Integrated Filing- Financial", "Outcome of Board Meeting",
        "Record Date", "Disclosure under SEBI Takeover Regulations",
        "Shareholders meeting", "Analysts/Institutional Investor Meet/Con. Call Updates",
        "Investor Presentation", "Press Release", "Agreements", "Appointment",
        "Cessation", "Credit rating", "Annual Report",
    }
    syms = sorted({
        r["symbol"] for r in ann
        if r.get("symbol") and r.get("category") in high_med
    })
    prices_rows = []
    if syms:
        from_date = (TODAY - timedelta(days=7)).isoformat()
        resp = (client.table("daily_prices")
            .select("symbol,close,prev_close,volume,value_cr,trade_date")
            .gte("trade_date", from_date).lte("trade_date", TODAY.isoformat())
            .eq("series", "EQ").in_("symbol", syms)
            .order("trade_date", desc=True).execute())
        prices_rows = resp.data or []

    os.makedirs(FIXDIR, exist_ok=True)
    for name, rows in [
        ("announcements", ann), ("board_meetings", bm),
        ("event_calendar", ec), ("corporate_actions", ca), ("prices", prices_rows),
    ]:
        path = os.path.join(FIXDIR, f"{name}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, indent=2, default=str, ensure_ascii=False)
        print(f"{name}: {len(rows)} rows -> {path}")

    meta = {"report_date": REPORT_DATE.isoformat(), "today": TODAY.isoformat()}
    with open(os.path.join(FIXDIR, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2)
    print("meta:", meta)
    return 0


if __name__ == "__main__":
    sys.exit(main())
