"""
Full-tables PDF.

The document is built as HTML and rendered by headless Chromium, deliberately:
it calls the *same* table renderers the email uses (reports/render_email.py)
with ``rows_all`` and ``cap=None``, so the PDF cannot drift away from the
email's column layouts. A Platypus/reportlab implementation would mean
maintaining six table layouts twice.

Chromium rather than WeasyPrint, matching the deals pipeline's reasoning in
reports/pdf_render.py: the layout is email HTML — nested
``<table role="presentation">`` scaffolding, inline styles, border-collapse
tables — which WeasyPrint mangles, and WeasyPrint additionally needs GTK, so
previews would not render on the desk's own Windows machine. WeasyPrint stays
available as a fallback via ``NSE_PDF_ENGINE=weasyprint``.

Styling comes from reports/design.py, so the attachment is the same document
family as the email and the deals report. Print CSS targets the ``bac-page`` /
``bac-card`` / ``bac-data`` class hooks design.py emits for exactly this
purpose.

No personal name, author or credential is written into the PDF — the document
title is the report name and nothing else is set.
"""

from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path

from reports import design as d

logger = logging.getLogger(__name__)

_ROMAN = ["i", "ii", "iii", "iv", "v", "vi"]

_SECTION_STANDFIRST = {
    "board_meetings": "every filing in the next 14 days",
    "event_calendar": "the complete forward schedule",
    "corporate_actions": "every ex-date in the next 7 days",
    "key_announcements": "every filing routed to this section",
    "other_announcements": "every remaining equity filing",
    "debt_market": "every NCD &amp; bond filing, grouped by issuer",
}

# Print-only overrides. The screen layout centres a fixed-width card on a grey
# page; on paper that grey is wasted ink and the card is a narrow column down
# the middle of an A4 sheet, so print drops the frame and uses the full measure.
#
# Break rules are deliberately narrow. Applying `page-break-inside: avoid` to
# every cell would mean every *section wrapper*, so a section that did not fit
# in the remaining space would move to the next page whole and leave half a page
# blank. Only data rows are protected.
_PRINT_CSS = """
@page { size: A4; margin: 13mm 11mm 15mm 11mm; }
html, body { background:#ffffff !important; padding:0 !important; margin:0 !important; }
.bac-page { background:#ffffff !important; }
.bac-page > tbody > tr > td { padding:0 !important; }
.bac-card { width:100% !important; max-width:100% !important; border:none !important; }
td, th { page-break-inside:auto; break-inside:auto; }
table.bac-data tr { page-break-inside:avoid; break-inside:avoid; }
table.bac-data thead { display:table-header-group; }
table.bac-data tbody { display:table-row-group; }
.pdf-section { page-break-before:always; }
a { text-decoration:none !important; color:inherit !important; }
"""

_PDF_OPTS = {
    "format": "A4",
    "print_background": True,
    "margin": {"top": "13mm", "bottom": "15mm", "left": "11mm", "right": "11mm"},
    "prefer_css_page_size": True,
}


def _tables():
    """Import the email's renderers lazily to avoid a circular import."""
    from reports import render_email as R
    from reports.transforms import group_debt_rows
    return {
        "board_meetings": lambda rows, cap: R.board_meetings_table(rows, cap=cap),
        "event_calendar": lambda rows, cap: R.event_calendar_table(rows, cap=cap),
        "corporate_actions": lambda rows, cap: R.corporate_actions_table(rows, cap=cap),
        "key_announcements": lambda rows, cap: R.announcements_table(rows, cap=cap),
        "other_announcements": lambda rows, cap: R.announcements_table(rows, cap=cap),
        "debt_market": lambda rows, cap: R.debt_table(group_debt_rows(rows), cap=cap),
    }, R


def build_full_tables_html(assembly, generated_at: datetime | None = None) -> str:
    """The complete, unfiltered document as HTML, in the house style."""
    from reports.assembly import SECTION_ORDER

    render, R = _tables()
    cap = assembly.config.attachments.max_pdf_rows_per_section or None
    rd = assembly.report_date
    gen = generated_at or datetime.now()
    src = f"NSE corporate filings &middot; as of {rd.strftime('%a %d-%b-%Y')}"

    body = d.masthead(
        kicker="Brindco Alpha Capital &middot; Quant Desk",
        title="Daily Announcements",
        dateline=f"{rd.strftime('%a %d-%b-%Y')} &middot; Complete tables",
        subline=(
            f"National Stock Exchange of India &middot; "
            f"generated {gen.strftime('%d-%b-%Y %H:%M')} IST"
        ),
        scope=(
            "Every row, unfiltered. The email body renders a curated subset of "
            "this same data; nothing shown there is missing here."
        ),
    )

    # ── Topline counts ───────────────────────────────────────────────────────
    cards = [
        {"label": "Equity Filings", "value": f"{assembly.counts.get('equity_filings', 0):,}",
         "sub": "announcements on the day"},
        {"label": "Board Meetings", "value": f"{assembly.counts.get('board_meetings', 0):,}",
         "sub": "next 14 days"},
        {"label": "Event Calendar", "value": f"{assembly.counts.get('event_calendar', 0):,}",
         "sub": "forward schedule"},
        {"label": "Corporate Actions", "value": f"{assembly.counts.get('corporate_actions', 0):,}",
         "sub": "next 7 days"},
    ]
    body += d.row(d.kpi_grid(cards, per_row=4), pad=d.BLOCK_PAD)

    # ── Contents ─────────────────────────────────────────────────────────────
    toc = ""
    for i, key in enumerate(SECTION_ORDER):
        sec = assembly.section(key)
        toc += (
            f'<tr>'
            f'<td width="24" style="width:24px;{d.font(11, color=d.GOLD, weight="bold")}'
            f'padding:4px 8px 4px 0;border-top:1px solid {d.RULE};">{_ROMAN[i]}</td>'
            f'<td style="{d.font(12)}padding:4px 0;border-top:1px solid {d.RULE};">'
            f'<a href="#sec-{key}" style="color:{d.INK};text-decoration:none;">'
            f'{sec.title}</a></td>'
            f'<td align="right" style="{d.font(11, color=d.INK_FAINT)}'
            f'padding:4px 0;border-top:1px solid {d.RULE};">'
            f'{sec.n_all:,} rows &middot; {sec.n_body} in the email</td>'
            f'</tr>'
        )
    body += d.row(
        d.caption_title("Contents")
        + f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
          f'border="0">{toc}</table>',
        pad=d.SECTION_PAD,
    )

    # ── Sections ─────────────────────────────────────────────────────────────
    for i, key in enumerate(SECTION_ORDER):
        sec = assembly.section(key)
        caption = (
            f"{sec.n_all:,} rows in total; {sec.n_body} of them appeared in the "
            f"email body."
        )
        inner = (
            R.section_caption(_ROMAN[i], sec.title, _SECTION_STANDFIRST.get(key, ""))
            + render[key](sec.rows_all, cap)
            + f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
              f'border="0"><tr><td style="{d.font(10, color=d.INK_SOFT, italic=True)}'
              f'padding:10px 0 0 0;">{caption}</td></tr></table>'
        )
        body += (
            f'  <tr><td class="pdf-section" id="sec-{key}" '
            f'style="padding:{d.SECTION_PAD};">\n{inner}\n  </td></tr>\n'
        )
        # The source line is per-table in the email; here it sits once per
        # section so a printed page always carries its provenance.
        body += d.row(
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            f'border="0"><tr><td style="{d.font(10, color=d.INK_FAINT)}">'
            f'{src}</td></tr></table>',
            pad=f"0 {d.PAD_X}px 0 {d.PAD_X}px",
        )

    title = f"BAC Announcements — NSE — {rd.strftime('%d %b %Y')} — Full Tables"

    # Times New Roman does not exist on Linux, so a CI-rendered PDF would fall
    # back to whatever serif fontconfig picks and reflow against the local
    # preview. Liberation Serif is metrically identical and ships in
    # fonts-liberation (installed by daily_report.yml). This override lives here
    # rather than in design.py so the shared house-style module stays
    # byte-identical with the copy in the deals repo.
    font_fallback = (
        '<style>\n'
        '  body, td, th, div, span, a, p {\n'
        "    font-family: 'Times New Roman', 'Liberation Serif', 'Tinos',"
        " 'Nimbus Roman', Times, serif !important;\n"
        '  }\n'
        '</style>\n'
    )
    return d.doc_open(title) + font_fallback + body + d.DOC_CLOSE


# ─── engines ──────────────────────────────────────────────────────────────────

def _render_weasyprint(html: str) -> bytes:
    from weasyprint import HTML  # type: ignore
    return HTML(string=html).write_pdf()


def _render_chromium(html: str) -> bytes:
    from playwright.sync_api import sync_playwright  # type: ignore

    tmp: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False,
                                         encoding="utf-8") as fh:
            fh.write(html)
            tmp = Path(fh.name)
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox"])
            try:
                page = browser.new_page()
                # A real file:// document rather than set_content, so the
                # in-page anchors in the contents list resolve as they would in
                # a browser.
                page.goto(tmp.as_uri(), wait_until="load", timeout=60_000)
                page.add_style_tag(content=_PRINT_CSS)
                page.wait_for_timeout(400)
                # display_header_footer stays off: Chromium's default furniture
                # stamps the source path and title into every page.
                return page.pdf(display_header_footer=False, **_PDF_OPTS)
            finally:
                browser.close()
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


_ENGINE_FNS = {"chromium": _render_chromium, "weasyprint": _render_weasyprint}


def _engines():
    """Chromium first — the engine that works unmodified on Windows and ubuntu."""
    preferred = (os.environ.get("NSE_PDF_ENGINE") or "").strip().lower()
    order = ["chromium", "weasyprint"]
    if preferred in _ENGINE_FNS:
        order.remove(preferred)
        order.insert(0, preferred)
    return [(n, _ENGINE_FNS[n]) for n in order]


def build_pdf(assembly, generated_at: datetime | None = None) -> bytes:
    """Render the full-tables document, trying each engine in order."""
    html = build_full_tables_html(assembly, generated_at=generated_at)
    errors = []
    for name, fn in _engines():
        try:
            pdf = fn(html)
            logger.info("PDF rendered via %s (%.1f KB)", name, len(pdf) / 1024)
            return pdf
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
            logger.debug("PDF engine %s unavailable — %s", name, exc)
    raise RuntimeError("No PDF engine available — " + " | ".join(errors))
