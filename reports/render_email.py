"""
Presentation for the daily announcements email, in the BAC house style.

Everything here renders through ``reports/design.py`` — the same module the
daily deals report uses — so the two emails are the same document family rather
than two designs that happen to share a masthead. A colour or size literal in
this file is a bug; if a style is missing, it belongs in design.py.

The split with the rest of the package:

  reports/assembly.py    decides WHICH rows appear      (data)
  reports/render_email.py decides HOW they look          (presentation)
  daily_announcements_report.py  fetches, orchestrates, dispatches

Section tables take assembled rows and an optional cap. The email passes
``rows_body``; the full-tables PDF passes ``rows_all`` with ``cap=None``, so the
attachment cannot drift away from the email's column layouts.
"""

from __future__ import annotations

from datetime import date, datetime
from html import escape as _e

from reports import design as d
from reports import universes as U
from reports.transforms import (
    classify_corp_action, clean_cell, normalize_currency, truncate,
)

# ─── Small shared pieces ──────────────────────────────────────────────────────

_ROMAN = ["", "i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"]

# Corporate-action kinds. Colour is structural here, not semantic: a dividend is
# not "good" and a buyback is not "bad", so these stay in the neutral family and
# the legend does the work. Only GOOD/BAD/WARN carry meaning (design.py).
_CA_LABEL = {
    "dividend": "Dividend", "bonus": "Bonus", "split": "Split",
    "rights": "Rights", "buyback": "Buy Back", "other": "Other",
}


def index_badge(symbol: str) -> str:
    """NIFTY50 / NIFTY100 chip, or nothing."""
    label = U.index_label(symbol)
    return d.badge(label, d.NAVY_SOFT) if label else ""


def seg_badge(segment: str) -> str:
    """SME / DEBT chip. Equities is the default and goes unmarked."""
    seg = clean_cell(segment).lower()
    return d.badge(seg.upper(), d.INK_FAINT) if seg in ("sme", "debt") else ""


def coverage_mark(symbol: str) -> str:
    """The ✦ that marks a BAC coverage name."""
    if not U.is_coverage(symbol):
        return ""
    return f'<span style="color:{d.GOLD};font-weight:bold;">&#10022;</span>&nbsp;'


def _sym_cell(symbol: str, segment: str = "", *, mark: bool = True,
              extra: str = "") -> str:
    sym = clean_cell(symbol)
    if not sym:
        return f'<span style="color:{d.INK_FAINT};">{d.EM_DASH}</span>'
    return (
        f'{coverage_mark(sym) if mark else ""}'
        f'<span style="font-weight:bold;">{_e(sym)}</span>'
        f'{seg_badge(segment)}{index_badge(sym)}{extra}'
    )


def _soft(text: str) -> str:
    """Secondary text, or the em-dash when there is nothing to say."""
    t = clean_cell(text)
    if not t:
        return f'<span style="color:{d.INK_FAINT};">{d.EM_DASH}</span>'
    return f'<span style="color:{d.INK_SOFT};">{_e(t)}</span>'


def _fmt_day(iso: str) -> str:
    s = clean_cell(iso)
    if not s:
        return d.EM_DASH
    try:
        return date.fromisoformat(s[:10]).strftime("%d-%b")
    except ValueError:
        return _e(s)


def section_caption(num: str, title: str, standfirst: str = "") -> str:
    """Gold uppercase section caption with an optional grey standfirst.

    House convention (from the deals report): the caption goes *before* the
    table and the explanation goes *after* it as an italic caption, so the data
    is the first thing the eye reaches.
    """
    head = f"{num} &middot; {title}" if num else title
    out = (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
        f'<tr><td style="{d.font(10.5, color=d.GOLD, weight="bold", ls=1.6, upper=True)}'
        f'padding:0 0 {"6px" if standfirst else "14px"} 0;">{head}</td></tr>'
    )
    if standfirst:
        out += (
            f'<tr><td style="{d.font(10.5, color=d.INK_FAINT)}padding:0 0 14px 0;">'
            f'{standfirst}</td></tr>'
        )
    return out + "</table>"


def _source(report_date: date) -> str:
    return f"NSE corporate filings &middot; as of {report_date.strftime('%a %d-%b-%Y')}"


# ─── Section tables ───────────────────────────────────────────────────────────
#
# Each takes assembled rows plus an optional cap and returns a house datatable.
# `caption` carries the rollup line naming whatever the body filter removed.


def board_meetings_table(rows, *, cap=None, source="", caption="") -> str:
    rows = list(rows)[:cap] if cap else list(rows)
    body = []
    prev_day = None
    for r in rows:
        day = clean_cell(r.get("meeting_date"))
        # Repeat the date only when it changes — a column of identical dates is
        # noise, and the eye needs the change points.
        day_cell = _fmt_day(day) if day != prev_day else ""
        prev_day = day
        body.append([
            f'<span style="color:{d.NAVY};font-weight:bold;">{day_cell}</span>' if day_cell else "",
            _sym_cell(r.get("symbol"), r.get("segment")),
            _soft(truncate(clean_cell(r.get("company_name")), 40)),
            _e(clean_cell(r.get("purpose"))) or d.EM_DASH,
        ])
    return d.datatable(
        ["Date", "Symbol", "Company", "Purpose"], body,
        align=["l", "l", "l", "l"], widths=[58, 132, 170, 0],
        source=source, caption=caption, empty="No board meetings in scope.",
    )


def event_calendar_table(rows, *, cap=None, source="", caption="") -> str:
    rows = list(rows)[:cap] if cap else list(rows)
    body = []
    prev_day = None
    for r in rows:
        day = clean_cell(r.get("meeting_date"))
        day_cell = _fmt_day(day) if day != prev_day else ""
        prev_day = day
        desc = truncate(clean_cell(r.get("description")), 70)
        company = truncate(clean_cell(r.get("company_name")), 38)
        # Company and agenda share a cell so the table stays inside the house
        # column budget while still carrying the detail section ii exists for.
        detail = _soft(company)
        if desc:
            detail += (
                f'<div style="{d.font(10.5, color=d.INK_FAINT, leading=14)}'
                f'padding-top:2px;">{_e(desc)}</div>'
            )
        body.append([
            f'<span style="color:{d.NAVY};font-weight:bold;">{day_cell}</span>' if day_cell else "",
            _sym_cell(r.get("symbol"), r.get("segment")),
            detail,
            _e(clean_cell(r.get("purpose"))) or d.EM_DASH,
        ])
    return d.datatable(
        ["Date", "Symbol", "Company / Agenda", "Purpose"], body,
        align=["l", "l", "l", "l"], widths=[58, 132, 0, 128],
        source=source, caption=caption, empty="No scheduled events in scope.",
    )


def corporate_actions_table(rows, *, cap=None, source="", caption="") -> str:
    rows = list(rows)[:cap] if cap else list(rows)
    body = []
    for r in rows:
        subject = normalize_currency(r.get("subject"))
        kind = classify_corp_action(r.get("subject"))
        body.append([
            f'<span style="color:{d.NAVY};font-weight:bold;">'
            f'{_fmt_day(r.get("ex_date"))}</span>',
            _sym_cell(r.get("symbol"), r.get("segment")),
            _soft(truncate(clean_cell(r.get("company")), 36)),
            d.badge(_CA_LABEL.get(kind, "Other"), d.NAVY_SOFT).lstrip("&nbsp;"),
            _e(truncate(subject, 90)) or d.EM_DASH,
        ])
    return d.datatable(
        ["Ex-Date", "Symbol", "Company", "Type", "Action"], body,
        align=["l", "l", "l", "c", "l"], widths=[58, 122, 138, 62, 0],
        source=source, caption=caption, empty="No corporate actions this week.",
    )


def announcements_table(rows, *, cap=None, source="", caption="",
                        empty="None today.") -> str:
    rows = list(rows)[:cap] if cap else list(rows)
    body = []
    for r in rows:
        sym = clean_cell(r.get("symbol"))
        company = clean_cell(r.get("company_name"))
        # Debt issuers and some SME filings carry no ticker; fall back to the
        # issuer name rather than printing an empty cell.
        display = sym or truncate(company, 22)
        n_filings = int(r.get("filing_count") or 1)
        chip = d.badge(f"{n_filings} filings", d.INK_FAINT) if n_filings > 1 else ""
        url = clean_cell(r.get("attachment_url"))
        label = _sym_cell(display, r.get("segment"), extra=chip) if sym else (
            f'<span style="font-weight:bold;">{_e(display)}</span>'
            f'{seg_badge(r.get("segment"))}{chip}'
        )
        if url:
            label = (
                f'<a href="{_e(url)}" style="color:{d.NAVY};text-decoration:none;">'
                f'{label}</a>'
            )
        body.append([
            label,
            _soft(truncate(clean_cell(r.get("category")), 34)),
            _e(truncate(clean_cell(r.get("summary")), 300)) or d.EM_DASH,
        ])
    return d.datatable(
        ["Symbol", "Category", "Summary"], body,
        align=["l", "l", "l"], widths=[132, 130, 0],
        source=source, caption=caption, empty=empty,
    )


def debt_table(groups, *, cap=None, source="", caption="") -> str:
    """Debt filings pre-grouped by (issuer, payment nature) — see group_debt_rows."""
    groups = list(groups)[:cap] if cap else list(groups)
    body = []
    for g in groups:
        n = g["count"]
        detail = f'{n} {g["nature"]}{"s" if n != 1 else ""}'
        isins = g.get("isins") or []
        if isins:
            detail += f' &middot; {len(isins)} ISIN{"s" if len(isins) != 1 else ""}'
        dates = g.get("dates") or []
        if dates:
            shown = " / ".join(dates[:3])
            more = f" +{len(dates) - 3}" if len(dates) > 3 else ""
            detail += f' &middot; {_e(shown)}{more}'
        body.append([
            f'<span style="font-weight:bold;">'
            f'{_e(truncate(g["issuer"], 42)) or d.EM_DASH}</span>',
            _soft(str(n)),
            detail,
        ])
    return d.datatable(
        ["Issuer", "Filings", "Grouped Detail"], body,
        align=["l", "c", "l"], widths=[210, 54, 0],
        source=source, caption=caption, empty="No debt-market filings today.",
    )


def sast_table(rows, *, cap=None) -> str:
    rows = list(rows)[:cap] if cap else list(rows)
    body = [
        [_sym_cell(r.get("symbol"), r.get("segment")),
         _e(truncate(clean_cell(r.get("summary")), 220)) or d.EM_DASH]
        for r in rows
    ]
    return d.datatable(
        ["Symbol", "Disclosure"], body, align=["l", "l"], widths=[132, 0],
        title="SAST Reg 31(4) &middot; coverage touchpoints",
        empty="None touching the BAC / NIFTY universe today.",
    )


def next_sessions_table(events, *, source="", caption="") -> str:
    body = []
    for e in events:
        note = ""
        if e.get("section_note"):
            note = (
                f'<span style="{d.font(10, color=d.INK_FAINT, italic=True)}">'
                f'&nbsp;({_e(e["section_note"])})</span>'
            )
        body.append([
            f'<span style="color:{d.NAVY};font-weight:bold;">'
            f'{_fmt_day(e["date"])}</span>',
            _soft(e["type"]),
            _sym_cell(e["symbol"], e.get("segment")),
            _e(clean_cell(e["purpose"])) + note,
        ])
    return d.datatable(
        ["Date", "Type", "Symbol", "Event"], body,
        align=["l", "l", "l", "l"], widths=[58, 76, 138, 0],
        source=source, caption=caption,
        empty="No coverage or index events across the next sessions.",
    )


def movers_table(movers, *, col2_header="Filing", source="", caption="") -> str:
    body = []
    for m in movers:
        # Δ% is the one genuinely semantic value in this report: it is a signed
        # market move, so it takes GOOD/BAD. Everything else stays structural.
        flag = "good" if m["chg"] >= 0 else "bad"
        body.append([
            _sym_cell(m["sym"], m["seg"]),
            _soft(truncate(m["col2"], 46)),
            f'&#8377;{m["close"]:,.1f}',
            d.value(f'{m["chg"]:+.1f}%', flag),
            _soft(m["vol_str"]),
        ])
    return d.datatable(
        ["Symbol", col2_header, "Close", "&#916;%", "Volume"], body,
        align=["l", "l", "r", "r", "r"], widths=[126, 0, 62, 56, 66],
        source=source, caption=caption,
        empty="No priced movers yet — the bhavcopy batch runs after market close.",
    )


# ─── Document ─────────────────────────────────────────────────────────────────

def build_email_html(
    *,
    report_date: date,
    today: date,
    assembly,
    generated_at: datetime,
    issue_num: int,
    kpis: list[dict],
    editorial_items: list[tuple[str, str]],
    scope_note: str,
    coverage_active: str,
    coverage_pillar: str,
    session_events: list[dict],
    session_days: list[date],
    session_headlines: str,
    session_also: str,
    movers: list[dict],
    movers_col2: str,
    movers_subtitle: str,
    sast_rows: list[dict],
    attachment_note: str = "",
) -> str:
    cfg = assembly.config
    src = _source(report_date)
    body = ""

    # ── Masthead ─────────────────────────────────────────────────────────────
    body += d.masthead(
        kicker="Brindco Alpha Capital &middot; Quant Desk",
        title="Daily Announcements",
        dateline=f"{report_date.strftime('%a %d-%b-%Y')} &middot; Focus edition",
        subline=(
            f"National Stock Exchange of India &middot; No. {issue_num} &middot; "
            f"generated {generated_at.strftime('%d-%b-%Y %H:%M')} IST"
        ),
        scope=scope_note,
    )

    # ── Topline ──────────────────────────────────────────────────────────────
    body += d.row(d.kpi_grid(kpis, per_row=4), pad=d.BLOCK_PAD)

    # ── Today on the tape ────────────────────────────────────────────────────
    if editorial_items:
        body += d.row(
            d.callout(d.numbered_list(editorial_items), accent="gold",
                      title="Things that matter today"),
            pad=d.BLOCK_PAD,
        )

    # ── Coverage touchpoints — prose only ────────────────────────────────────
    # The table this panel used to carry duplicated Next 3 Sessions almost row
    # for row, so the panel is now two prose lines and the forward table below
    # is the single canonical one.
    coverage_body = f"{coverage_active}<br /><br />{coverage_pillar}"
    body += d.row(
        d.callout(coverage_body, accent="navy", title="BAC coverage touchpoints"),
        pad=d.BLOCK_PAD,
    )

    # ── Next sessions ────────────────────────────────────────────────────────
    day_labels = " &middot; ".join(dd.strftime("%a %d-%b") for dd in session_days)
    caption = " ".join(x for x in (session_headlines, session_also) if x)
    body += d.row(
        section_caption("", f"Next {len(session_days)} sessions", day_labels)
        + next_sessions_table(session_events, source=src, caption=caption),
        pad=d.SECTION_PAD,
    )

    # ── Top movers ───────────────────────────────────────────────────────────
    body += d.row(
        section_caption("", "Top movers &middot; announcement overlay", movers_subtitle)
        + movers_table(movers, col2_header=movers_col2, source=src),
        pad=d.SECTION_PAD,
    )

    # ── Underlying filings ───────────────────────────────────────────────────
    body += d.row(
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
        f'<tr><td style="border-top:2px solid {d.NAVY};'
        f'{d.font(10.5, color=d.GOLD, weight="bold", ls=1.6, upper=True)}'
        f'padding:12px 0 0 0;">Underlying filings</td></tr>'
        f'<tr><td style="{d.font(11, color=d.INK_FAINT, leading=16)}padding:3px 0 0 0;">'
        f'The canonical sections. Each is filtered to what matters; the complete '
        f'table is in the attachment.</td></tr></table>',
        pad=d.BLOCK_PAD,
    )

    sec_bm = assembly.section("board_meetings")
    sec_ec = assembly.section("event_calendar")
    sec_ca = assembly.section("corporate_actions")
    sec_key = assembly.section("key_announcements")
    sec_other = assembly.section("other_announcements")
    sec_debt = assembly.section("debt_market")

    body += d.row(
        section_caption(_ROMAN[1], "Board meeting filings",
                        f"next {cfg.board_meetings.horizon_days} days &middot; "
                        f"coverage &amp; index names, plus fund raises and delistings")
        + board_meetings_table(sec_bm.rows_body, source=src, caption=sec_bm.rollup),
        pad=d.SECTION_PAD,
    )

    body += d.row(
        section_caption(_ROMAN[2], "Event calendar",
                        f"next {cfg.event_calendar.horizon_sessions} sessions &middot; "
                        f"coverage &amp; index names, with agenda detail")
        + event_calendar_table(sec_ec.rows_body, source=src, caption=sec_ec.rollup),
        pad=d.SECTION_PAD,
    )

    body += d.row(
        section_caption(_ROMAN[3], "Corporate actions",
                        f"ex-dates in the next {cfg.corporate_actions.horizon_days} days")
        + corporate_actions_table(sec_ca.rows_body, source=src, caption=sec_ca.rollup),
        pad=d.SECTION_PAD,
    )

    key_body = [r for r in sec_key.rows_body
                if clean_cell(r.get("category")) != "Disclosure under SEBI Takeover Regulations"]
    key_html = announcements_table(key_body, source=src, caption=sec_key.rollup)
    if sast_rows:
        key_html += (
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
            f'<tr><td style="padding:18px 0 0 0;">{sast_table(sast_rows)}</td></tr></table>'
        )
    body += d.row(
        section_caption(_ROMAN[4], "Key announcements",
                        "results, M&amp;A, order wins, allotments, takeover and regulatory disclosures")
        + key_html,
        pad=d.SECTION_PAD,
    )

    body += d.row(
        section_caption(_ROMAN[5], "Other announcements",
                        "coverage names and substantive press releases")
        + announcements_table(sec_other.rows_body, source=src, caption=sec_other.rollup),
        pad=d.SECTION_PAD,
    )

    from reports.transforms import group_debt_rows
    body += d.row(
        section_caption(_ROMAN[6], "Debt market",
                        "credit-rating actions and coverage issuers")
        + debt_table(group_debt_rows(sec_debt.rows_body),
                     source=src, caption=sec_debt.rollup),
        pad=d.SECTION_PAD,
    )

    # ── Colophon ─────────────────────────────────────────────────────────────
    provenance = (
        f"Compiled by the NSE Announcements pipeline at "
        f"{generated_at.strftime('%H:%M')} IST on {generated_at.strftime('%d %B %Y')}. "
        f"Source: NSE corporate filings — announcements, event calendar, board "
        f"meetings and corporate actions."
    )
    if attachment_note:
        provenance += f" {attachment_note}"
    disclaimer = (
        "The body is scoped to the BAC coverage book, the NIFTY50/100 overlay and "
        "the NIFTY500; every filtered row is carried in full by the attachments. "
        "For information only — not investment advice. Write to bac@brindco.com "
        "with corrections."
    )
    body += d.row(d.colophon(provenance, disclaimer), pad="26px 24px 26px 24px")

    title = f"BAC Announcements — NSE — {report_date.strftime('%d %b %Y')}"
    preheader = (
        f"{assembly.counts.get('equity_filings', 0)} filings · "
        f"{sec_bm.n_all} board meetings · {sec_ca.n_all} corporate actions"
    )
    return d.doc_open(title, preheader) + body + d.DOC_CLOSE
