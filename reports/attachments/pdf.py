"""
Full-tables PDF.

The document is built as HTML and rendered by a browser engine, deliberately:
it calls the *same* section renderers the email uses, with ``cap=None`` and
``rows_all``, so the PDF cannot drift away from the email's column layouts. A
Platypus/reportlab implementation would mean maintaining six table layouts twice.

Engine selection
----------------
WeasyPrint is tried first — it is far lighter in CI (two apt packages). It needs
GTK/Pango, which is not present on a stock Windows box, so Chromium via
Playwright is the fallback and is what renders locally. Both consume the same
HTML, so output is equivalent either way.

Fonts
-----
Times New Roman does not exist on Linux. The stack is
``'Times New Roman', 'Liberation Serif', 'Nimbus Roman', Times, serif``:
Liberation Serif is metrically identical to Times New Roman and ships with
``fonts-liberation`` on ubuntu, so CI output matches local output closely.

Metadata
--------
No personal name, author or credential is written into the PDF — the document
title is the report name and nothing else is set.
"""

from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger(__name__)

_INK = "#1a1410"
_INK_SOFT = "#3a322a"
_STONE = "#837763"
_PARCHMENT = "#faf5e8"
_SAND = "#e8dec8"
_TAN = "#c6b896"
_CREAM = "#fcf8ec"
_WARM_GREY = "#f3ecd6"
_BURGUNDY = "#6f1d1b"

_SERIF = "'Times New Roman', 'Liberation Serif', 'Nimbus Roman', Times, serif"

_ROMAN = ["i", "ii", "iii", "iv", "v", "vi"]

_SECTION_SUBTITLES = {
    "board_meetings": "next 14 days · all filings",
    "event_calendar": "full forward schedule · all filings",
    "corporate_actions": "ex-dates this week · all actions",
    "key_announcements": "all filings in scope",
    "other_announcements": "all remaining equity filings",
    "debt_market": "all NCD &amp; bond filings, grouped by issuer",
}


def _renderers():
    """Import the email's renderers lazily to avoid a circular import."""
    from reports import daily_announcements_report as R
    return {
        "board_meetings": R._bm_table_enhanced,
        "event_calendar": R._ec_table_enhanced,
        "corporate_actions": R._corporate_actions_html,
        "key_announcements": lambda rows, cap=None: R._announcements_html(rows, cap=cap),
        "other_announcements": lambda rows, cap=None: R._announcements_html(rows, cap=cap),
        "debt_market": R._debt_market_html,
    }


def build_full_tables_html(assembly, generated_at: datetime | None = None) -> str:
    """The complete, unfiltered document as HTML."""
    from reports.assembly import SECTION_ORDER

    render = _renderers()
    cap = assembly.config.attachments.max_pdf_rows_per_section or None
    d = assembly.report_date

    # ── cover counts ─────────────────────────────────────────────────────────
    count_cells = ""
    for label, key in (
        ("Equity Filings", "equity_filings"), ("Board Meetings", "board_meetings"),
        ("Event Calendar", "event_calendar"), ("Corporate Actions", "corporate_actions"),
    ):
        count_cells += (
            f'<td style="padding:12px 14px; border-right:1px solid {_TAN};">'
            f'<div style="font-size:9.5px; letter-spacing:0.24em; text-transform:uppercase;'
            f' color:{_STONE};">{label}</div>'
            f'<div style="font-size:26px; margin-top:5px;">{assembly.counts.get(key, 0)}</div>'
            f'</td>'
        )

    # ── table of contents ────────────────────────────────────────────────────
    toc_rows = ""
    for i, key in enumerate(SECTION_ORDER):
        sec = assembly.section(key)
        toc_rows += (
            f'<tr>'
            f'<td style="padding:4px 10px 4px 0; color:{_BURGUNDY}; width:32px;">'
            f'{_ROMAN[i]}.</td>'
            f'<td style="padding:4px 10px 4px 0;">'
            f'<a href="#sec-{key}" style="color:{_INK}; text-decoration:none;">{sec.title}</a></td>'
            f'<td style="padding:4px 0; text-align:right; color:{_STONE};">'
            f'{sec.n_all} rows</td>'
            f'</tr>'
        )

    # ── sections ─────────────────────────────────────────────────────────────
    body = ""
    for i, key in enumerate(SECTION_ORDER):
        sec = assembly.section(key)
        table = render[key](sec.rows_all, cap=cap)
        shown = f"{sec.n_body} of these appeared in the email body"
        body += f"""
<div class="section" id="sec-{key}">
  <div class="sec-hdr">
    <span class="sec-num">{_ROMAN[i]}.</span>
    <span class="sec-title">{sec.title}</span>
    <span class="sec-sub">{_SECTION_SUBTITLES.get(key, '')}</span>
  </div>
  <div class="sec-rule"></div>
  <div class="sec-note">{sec.n_all} rows &#183; {shown}</div>
  {table}
</div>"""

    gen = (generated_at or datetime.now()).strftime("%H:%M on %d %b %Y")
    title = f"BAC Announcements — NSE — {d.strftime('%d %b %Y')} — Full Tables"

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  @page {{
    size: A4 portrait;
    margin: 14mm 12mm 16mm 12mm;
  }}
  /* Wide tables get their own landscape run so columns are not squeezed. */
  @page landscape {{ size: A4 landscape; }}
  html, body {{
    margin:0; padding:0; background:#ffffff; color:{_INK};
    font-family:{_SERIF}; font-size:9.5pt; line-height:1.4;
  }}
  table {{ border-collapse:collapse; width:100%; }}
  td, th {{ vertical-align:top; word-wrap:break-word; overflow-wrap:anywhere; }}
  .cover {{ padding-bottom:10mm; }}
  .kicker {{
    font-size:8pt; letter-spacing:0.30em; text-transform:uppercase;
    color:{_BURGUNDY};
  }}
  h1 {{ font-size:26pt; font-weight:500; margin:5mm 0 1mm 0; letter-spacing:-0.015em; }}
  .subtitle {{ font-size:12pt; font-style:italic; margin-bottom:4mm; }}
  .rule-heavy {{ border-top:2.5pt solid {_INK}; }}
  .rule-thin  {{ border-top:0.5pt solid {_INK}; margin-top:1pt; }}
  .counts {{
    margin-top:5mm; background:{_WARM_GREY};
    border-top:0.5pt solid {_TAN}; border-bottom:0.5pt solid {_TAN};
  }}
  .toc {{ margin-top:7mm; }}
  .toc-hdr {{
    font-size:8pt; letter-spacing:0.24em; text-transform:uppercase;
    color:{_BURGUNDY}; margin-bottom:3mm;
  }}
  .section {{ page-break-before:always; }}
  .sec-hdr {{ margin-bottom:2mm; }}
  .sec-num {{ color:{_BURGUNDY}; font-size:11pt; margin-right:5px; }}
  .sec-title {{ font-size:14pt; }}
  .sec-sub {{ font-size:9pt; font-style:italic; color:{_STONE}; margin-left:8px; }}
  .sec-rule {{ border-top:1pt solid {_INK}; margin-bottom:2mm; }}
  .sec-note {{ font-size:8pt; font-style:italic; color:{_STONE}; margin-bottom:3mm; }}
  /* Repeat header rows across page breaks and avoid splitting a row. */
  thead {{ display:table-header-group; }}
  tr {{ page-break-inside:avoid; }}
  .foot {{
    margin-top:8mm; padding-top:3mm; border-top:0.5pt solid {_TAN};
    font-size:8pt; color:{_STONE}; font-style:italic;
  }}
  a {{ color:{_BURGUNDY}; text-decoration:none; }}
</style>
</head>
<body>

<div class="cover">
  <div class="kicker">Brindco Alpha Capital</div>
  <h1>Daily Announcements</h1>
  <div class="subtitle">National Stock Exchange of India &#183; complete tables</div>
  <div class="rule-heavy"></div><div class="rule-thin"></div>
  <div style="margin-top:3mm; font-size:11pt;">{d.strftime('%d %B %Y')}</div>

  <table class="counts"><tr>{count_cells}</tr></table>

  <div class="toc">
    <div class="toc-hdr">Contents</div>
    <table>{toc_rows}</table>
  </div>

  <div class="foot">
    Every row is included here without filtering. The email body renders a
    curated subset of the same data. Compiled by the NSE Announcements pipeline
    at {gen}.
  </div>
</div>

{body}

</body>
</html>"""


# ─── engines ──────────────────────────────────────────────────────────────────

def _render_weasyprint(html: str) -> bytes:
    from weasyprint import HTML  # type: ignore
    return HTML(string=html).write_pdf()


def _render_chromium(html: str) -> bytes:
    from playwright.sync_api import sync_playwright  # type: ignore
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        try:
            page = browser.new_page()
            page.set_content(html, wait_until="load")
            return page.pdf(
                format="A4",
                margin={"top": "14mm", "bottom": "16mm", "left": "12mm", "right": "12mm"},
                print_background=True,
                # No header/footer templates: Chromium's defaults would stamp the
                # source path and title into the page furniture.
                display_header_footer=False,
            )
        finally:
            browser.close()


# Chromium first: it is the engine that works unmodified on both Windows and
# ubuntu, so the normal path never touches WeasyPrint's noisy GTK import. Set
# NSE_PDF_ENGINE=weasyprint to prefer the lighter CI dependency.
_ENGINE_FNS = {"chromium": _render_chromium, "weasyprint": _render_weasyprint}


def _engines():
    import os
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
            logger.info("PDF rendered via %s (%d bytes)", name, len(pdf))
            return pdf
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
            logger.debug("PDF engine %s unavailable — %s", name, exc)
    raise RuntimeError("No PDF engine available — " + " | ".join(errors))
