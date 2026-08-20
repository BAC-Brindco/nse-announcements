"""
Deterministic offline render of a report edition from cached fixtures.

Reads tests/fixtures/<name>/*.json and calls the report's _build_html with a
pinned today/generated_at so output is byte-stable across runs (no DB, no clock).

Usage:
  python tests/render_fixture.py                          # issue113 -> repo root
  python tests/render_fixture.py --fixture aug07
  python tests/render_fixture.py --fixture aug18 --out previews/aug18.html
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

_FIXROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Pinned generation timestamp → stable colophon across runs.
_GENERATED_AT = datetime(2026, 6, 9, 11, 43, 0, tzinfo=timezone.utc)


def _load(fixdir: str, name: str) -> pd.DataFrame:
    with open(os.path.join(fixdir, f"{name}.json"), encoding="utf-8") as fh:
        return pd.DataFrame(json.load(fh))


def load_fixture(name: str = "issue113"):
    """Return (report_date, today, ann, bm, ec, ca, prices_dict)."""
    fixdir = os.path.join(_FIXROOT, name)
    with open(os.path.join(fixdir, "meta.json"), encoding="utf-8") as fh:
        meta = json.load(fh)
    report_date = date.fromisoformat(meta["report_date"])
    today = date.fromisoformat(meta["today"])

    ann = _load(fixdir, "announcements")
    bm = _load(fixdir, "board_meetings")
    ec = _load(fixdir, "event_calendar")
    ca = _load(fixdir, "corporate_actions")

    with open(os.path.join(fixdir, "prices.json"), encoding="utf-8") as fh:
        price_rows = json.load(fh)
    # Mirror _fetch_prices: keep most-recent row per symbol (rows already desc by date)
    prices: dict[str, dict] = {}
    for r in price_rows:
        if r["symbol"] not in prices:
            prices[r["symbol"]] = r
    return report_date, today, ann, bm, ec, ca, prices


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def visible_text(html: str) -> str:
    """Approximate the reader-visible text of an HTML email: drop <style>/<head>
    content and tags, collapse whitespace. Used to size the body objectively."""
    body = re.sub(r"(?is)<head\b.*?</head>", " ", html)
    body = re.sub(r"(?is)<(style|script)\b.*?</\1>", " ", body)
    body = _TAG_RE.sub(" ", body)
    body = body.replace("&nbsp;", " ").replace("&#160;", " ")
    return _WS_RE.sub(" ", body).strip()


def render(name: str = "issue113", out: str | None = None,
           attachments: bool = False) -> tuple[str, dict]:
    report_date, today, ann, bm, ec, ca, prices = load_fixture(name)

    from reports.assembly import build_assembly
    from reports.daily_announcements_report import _build_html

    asm = build_assembly(report_date, ann, bm, ec, ca, today=today)

    atts: list[tuple[str, bytes, str]] = []
    note = ""
    if attachments:
        from reports.attachments import ATTACHMENT_NOTICE, build_attachments
        atts = build_attachments(asm, generated_at=_GENERATED_AT)
        if atts:
            note = ATTACHMENT_NOTICE

    html = _build_html(report_date, ann, bm, ec, ca, _GENERATED_AT, today=today,
                       prices=prices, assembly=asm, attachment_note=note)

    stats = {
        "fixture": name,
        "report_date": report_date.isoformat(),
        "html_chars": len(html),
        "text_chars": len(visible_text(html)),
        "rows": {"ann": len(ann), "bm": len(bm), "ec": len(ec), "ca": len(ca)},
        "sections": {k: (s.n_all, s.n_body) for k, s in asm.sections.items()},
        "attachments": [(n, len(p)) for n, p, _ in atts],
    }
    if out:
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(html)
        stats["out"] = out
        for filename, payload, _mime in atts:
            side = os.path.join(os.path.dirname(os.path.abspath(out)), filename)
            with open(side, "wb") as fh:
                fh.write(payload)
    return html, stats


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--fixture", default="issue113")
    p.add_argument("--out")
    p.add_argument("--attachments", action="store_true",
                   help="Also build the PDF and CSV bundle alongside the HTML")
    a = p.parse_args()
    out = a.out or os.path.join(_REPO, f"report_preview_{a.fixture}_rerender.html")
    _, stats = render(a.fixture, out, attachments=a.attachments)
    print(f"{stats['fixture']} ({stats['report_date']}): "
          f"html={stats['html_chars']:,} chars  text={stats['text_chars']:,} chars")
    print(f"  rows: {stats['rows']}")
    print("  sections (all/body): "
          + ", ".join(f"{k}={v[0]}/{v[1]}" for k, v in stats["sections"].items()))
    for n, sz in stats["attachments"]:
        print(f"  attachment: {n} ({sz:,} bytes)")
    print(f"  -> {stats['out']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
