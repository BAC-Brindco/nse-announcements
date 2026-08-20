"""
CSV bundle — one CSV per section, zipped.

Built from ``SectionData.rows_all``, so row counts equal the unfiltered assembly
counts exactly. That equality is asserted in tests/test_attachments.py and is the
guarantee behind the email's "full tables attached" claim.

Encoding is UTF-8 **with BOM** (``utf-8-sig``): Excel on Windows otherwise reads a
plain UTF-8 CSV as cp1252 and mangles the ₹ sign and the '·' join separator.
"""

from __future__ import annotations

import csv
import io
import zipfile

from reports import universes as U
from reports.transforms import clean_cell

SECTION_FILENAMES = {
    "board_meetings": "board_meetings.csv",
    "event_calendar": "event_calendar.csv",
    "corporate_actions": "corporate_actions.csv",
    "key_announcements": "key_announcements.csv",
    "other_announcements": "other_announcements.csv",
    "debt_market": "debt_market.csv",
}

# Per-section output columns. Each maps a CSV header to a source-row key; the
# derived columns (index membership, coverage/SME flags) are computed below.
_COMMON_TAIL = ["index_membership", "is_coverage", "is_sme", "attachment_url"]

CSV_COLUMNS = {
    "board_meetings": [
        "symbol", "company_name", "segment", "source", "meeting_date",
        "purpose", "description", "isin", "filed_at", "scrape_date", *_COMMON_TAIL,
    ],
    "event_calendar": [
        "symbol", "company_name", "segment", "source", "meeting_date",
        "purpose", "description", "isin", "scrape_date", *_COMMON_TAIL,
    ],
    "corporate_actions": [
        "symbol", "company", "segment", "series", "ex_date", "record_date",
        "subject", "face_val", "isin", "broadcast_at", "scrape_date", *_COMMON_TAIL,
    ],
    "key_announcements": [
        "seq_id", "symbol", "company_name", "segment", "category", "summary",
        "announced_at", "isin", "industry", "attachment_size", "scrape_date",
        *_COMMON_TAIL,
    ],
    "other_announcements": [
        "seq_id", "symbol", "company_name", "segment", "category", "summary",
        "announced_at", "isin", "industry", "attachment_size", "scrape_date",
        *_COMMON_TAIL,
    ],
    "debt_market": [
        "seq_id", "symbol", "company_name", "segment", "category", "summary",
        "announced_at", "isin", "attachment_size", "scrape_date", *_COMMON_TAIL,
    ],
}


def _derived(row: dict) -> dict:
    sym = clean_cell(row.get("symbol"))
    return {
        "index_membership": U.index_label(sym),
        "is_coverage": "Y" if U.is_coverage(sym) else "",
        "is_sme": "Y" if clean_cell(row.get("segment")).lower() == "sme" else "",
    }


def _cell(row: dict, derived: dict, col: str) -> str:
    if col in derived:
        return derived[col]
    val = row.get(col)
    if val is None:
        return ""
    # raw_payload is a nested dict; never expand it into the CSV.
    if isinstance(val, (dict, list)):
        return ""
    return clean_cell(val)


def section_csv(section, columns: list[str]) -> str:
    """Render one section's rows_all as CSV text."""
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(columns)
    for row in section.rows_all:
        d = _derived(row)
        w.writerow([_cell(row, d, c) for c in columns])
    return buf.getvalue()


def build_csv_bundle(assembly) -> bytes:
    """Zip one CSV per section. Returns the archive bytes."""
    buf = io.BytesIO()
    # Fixed timestamp on every entry: a deterministic archive makes the
    # regression fixtures byte-comparable across runs.
    zinfo_date = (2026, 1, 1, 0, 0, 0)
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for key, fname in SECTION_FILENAMES.items():
            sec = assembly.section(key)
            text = section_csv(sec, CSV_COLUMNS[key])
            info = zipfile.ZipInfo(fname, date_time=zinfo_date)
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, text.encode("utf-8-sig"))

        readme = _readme(assembly)
        info = zipfile.ZipInfo("README.txt", date_time=zinfo_date)
        info.compress_type = zipfile.ZIP_DEFLATED
        zf.writestr(info, readme.encode("utf-8-sig"))
    return buf.getvalue()


def _readme(assembly) -> str:
    d = assembly.report_date
    lines = [
        f"BAC Announcements — NSE — {d.strftime('%d %b %Y')}",
        "",
        "Complete, unfiltered section data. The email body renders a curated",
        "subset; every row filtered out of the body is present here.",
        "",
        "Encoding: UTF-8 with BOM (opens cleanly in Excel).",
        "",
        "Row counts:",
    ]
    for key, fname in SECTION_FILENAMES.items():
        sec = assembly.section(key)
        lines.append(f"  {fname:26} {sec.n_all:6d} rows   ({sec.n_body} shown in the email)")
    lines += [
        "",
        "Derived columns:",
        "  index_membership  NIFTY50 / NIFTY100 / blank",
        "  is_coverage       Y when the symbol is in the BAC active coverage book",
        "  is_sme            Y for the SME segment",
        "",
        "Source: NSE corporate filings (announcements, event calendar,",
        "board meetings, corporate actions).",
    ]
    return "\r\n".join(lines)
