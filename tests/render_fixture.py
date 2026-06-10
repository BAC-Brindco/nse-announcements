"""
Deterministic offline render of the Issue №113 report from cached fixtures.

Reads tests/fixtures/issue113/*.json and calls the report's _build_html with a
pinned today/generated_at so output is byte-stable across runs (no DB, no clock).

Usage:
  python tests/render_fixture.py [OUTPUT_HTML]
Default output: report_preview_issue113_rerender.html in repo root.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

FIXDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "issue113")


def _load(name: str) -> pd.DataFrame:
    with open(os.path.join(FIXDIR, f"{name}.json"), encoding="utf-8") as fh:
        return pd.DataFrame(json.load(fh))


def load_fixture():
    """Return (report_date, today, ann, bm, ec, ca, prices_dict)."""
    with open(os.path.join(FIXDIR, "meta.json"), encoding="utf-8") as fh:
        meta = json.load(fh)
    report_date = date.fromisoformat(meta["report_date"])
    today = date.fromisoformat(meta["today"])

    ann = _load("announcements")
    bm = _load("board_meetings")
    ec = _load("event_calendar")
    ca = _load("corporate_actions")

    price_rows = json.load(open(os.path.join(FIXDIR, "prices.json"), encoding="utf-8"))
    # Mirror _fetch_prices: keep most-recent row per symbol (rows already desc by date)
    prices: dict[str, dict] = {}
    for r in price_rows:
        if r["symbol"] not in prices:
            prices[r["symbol"]] = r
    return report_date, today, ann, bm, ec, ca, prices


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "report_preview_issue113_rerender.html",
    )
    report_date, today, ann, bm, ec, ca, prices = load_fixture()

    from reports.daily_announcements_report import _build_html
    # Pinned generation timestamp → stable colophon
    generated_at = datetime(2026, 6, 9, 11, 43, 0, tzinfo=timezone.utc)

    html = _build_html(report_date, ann, bm, ec, ca, generated_at, today=today, prices=prices)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"Rendered {len(html):,} bytes -> {out}")
    print(f"  announcements={len(ann)} bm={len(bm)} ec={len(ec)} ca={len(ca)} prices={len(prices)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
