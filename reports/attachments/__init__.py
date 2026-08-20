"""Email attachments built from the unfiltered assembly.

``build_attachments(assembly)`` returns a list of ``(filename, payload, mimetype)``
tuples ready to hand to ``_send_email``. Both builders read ``SectionData.rows_all``,
so no body filter can affect what they contain.
"""

from __future__ import annotations

import logging

from reports.attachments.csv_bundle import build_csv_bundle, CSV_COLUMNS, SECTION_FILENAMES
from reports.attachments.pdf import build_pdf, build_full_tables_html

logger = logging.getLogger(__name__)

__all__ = [
    "build_attachments", "build_csv_bundle", "build_pdf",
    "build_full_tables_html", "CSV_COLUMNS", "SECTION_FILENAMES",
    "ATTACHMENT_NOTICE",
]

ATTACHMENT_NOTICE = "Full tables attached — PDF for reading, CSV bundle for analysis."


def build_attachments(assembly, generated_at=None) -> list[tuple[str, bytes, str]]:
    """Build every enabled attachment. A failure in one never blocks the email."""
    cfg = assembly.config.attachments
    out: list[tuple[str, bytes, str]] = []
    d = assembly.report_date

    if cfg.pdf_enabled:
        try:
            name = f"BAC Announcements — NSE — {d.strftime('%d %b %Y')} — Full Tables.pdf"
            out.append((name, build_pdf(assembly, generated_at=generated_at), "application/pdf"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("PDF attachment skipped (%s: %s)", type(exc).__name__, exc)

    if cfg.csv_enabled:
        try:
            name = f"BAC_Announcements_NSE_{d.strftime('%Y%m%d')}_data.zip"
            out.append((name, build_csv_bundle(assembly), "application/zip"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("CSV attachment skipped (%s: %s)", type(exc).__name__, exc)

    return out
