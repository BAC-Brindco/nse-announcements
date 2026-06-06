"""
Daily NSE Announcements email report — humanised newspaper format.

Runs once per trading-day morning (Tue–Sat IST) at 10:00 IST.
A separate evening scrape (Mon–Fri 4:30 PM IST) captures the day's
announcements so tomorrow's report is complete.

Sections:
  i.   Board Meeting Filings  — recent intimations (source=board_meetings), next 14 days
  ii.  Event Calendar         — NSE forward schedule (source=event_calendar), all upcoming
  iii. Corporate Actions      — ex-dates this week
  iv.  Key Announcements      — financial results, takeover disclosures, record dates, outcomes
  v.   Other Announcements    — analyst meets, presentations, agreements
  vi.  Debt Market            — NCD/bond segment filings

Env vars:
  SUPABASE_URL, SUPABASE_KEY
  SMTP_USER, SMTP_PASSWORD, REPORT_RECIPIENTS
  REPORT_SENDER_NAME  (optional, default "BAC Announcements")
  SLACK_WEBHOOK_URL   (optional)
"""

from __future__ import annotations

import logging
import os
import re
import smtplib
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from html import escape as _e

import pandas as pd
import pytz

logger = logging.getLogger("nse.announcements.report")

REPORT_TYPE = "daily_announcements_email"
_IST        = pytz.timezone("Asia/Kolkata")

# ─── Colour palette ───────────────────────────────────────────────────────────
_INK       = "#1a1410"
_INK_SOFT  = "#3a322a"
_STONE     = "#837763"
_PARCHMENT = "#faf5e8"
_SAND      = "#e8dec8"
_TAN       = "#c6b896"
_CREAM     = "#fcf8ec"
_WARM_GREY = "#f3ecd6"
_BURGUNDY  = "#6f1d1b"
_NAVY      = "#1c2956"
_OLIVE     = "#5a5e2a"
_AMBER     = "#a6562b"

# ─── Announcement category priority ──────────────────────────────────────────
_HIGH_PRIORITY = {
    "Financial Results",
    "Integrated Filing- Financial",
    "Outcome of Board Meeting",
    "Record Date",
    "Disclosure under SEBI Takeover Regulations",
}
_MEDIUM_PRIORITY = {
    "Shareholders meeting",
    "Analysts/Institutional Investor Meet/Con. Call Updates",
    "Investor Presentation",
    "Press Release",
    "Agreements",
    "Appointment",
    "Cessation",
    "Credit rating",
    "Annual Report",
}

# ─── Purpose highlight colours ────────────────────────────────────────────────
_PURPOSE_COLOR = {
    "Financial Results":       _BURGUNDY,
    "Voluntary Delisting":     _BURGUNDY,
    "Fund Raising":            _NAVY,
    "Dividend":                _OLIVE,
    "Bonus":                   _NAVY,
    "Other business matters":  _STONE,
}


def _purpose_color(purpose: str) -> str:
    up = (purpose or "").upper()
    if "FINANCIAL RESULTS" in up:
        return _BURGUNDY
    if "DELIST" in up:
        return _BURGUNDY
    if "FUND RAIS" in up:
        return _NAVY
    if "DIVIDEND" in up:
        return _OLIVE
    if "BONUS" in up:
        return _NAVY
    return _INK


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"Missing env var: {name}")
    return val


def _fmt_date(d) -> str:
    """Platform-safe day-month label: '7 Jun'."""
    if isinstance(d, str):
        try:
            d = date.fromisoformat(d)
        except ValueError:
            return d
    return f"{d.day} {d.strftime('%b')}"


def _ordinal(n: int) -> str:
    s = "th" if 11 <= (n % 100) <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}<sup style='font-size:9px;'>{s}</sup>"


def _long_date(d: date) -> str:
    ones = ["", "one", "two", "three", "four", "five", "six", "seven",
            "eight", "nine", "ten", "eleven", "twelve", "thirteen",
            "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
    tens = ["", "", "twenty", "thirty", "forty", "fifty",
            "sixty", "seventy", "eighty", "ninety"]
    rem = d.year - 2000
    if rem == 0:
        yr = "two thousand"
    elif rem < 20:
        yr = f"two thousand and {ones[rem]}"
    else:
        yr = f"two thousand and {tens[rem // 10]}" + (f"-{ones[rem % 10]}" if rem % 10 else "")
    months = ["", "January", "February", "March", "April", "May", "June",
              "July", "August", "September", "October", "November", "December"]
    return (
        f"<span style='font-style:italic;'>{d.strftime('%A')}</span>, "
        f"the {_ordinal(d.day)} of {months[d.month]}, {yr}"
    )


def _th(label: str, align: str = "left") -> str:
    return (
        f'<th align="{align}" style="padding:8px 10px 8px 12px; font-weight:600; '
        f'font-size:9.5px; letter-spacing:0.2em; text-transform:uppercase; '
        f'color:{_STONE}; border-bottom:1.5px solid {_INK};">{label}</th>'
    )


def _section_hdr(num: str, title: str, subtitle: str = "", desc: str = "") -> str:
    sub = (
        f'<td align="right" valign="baseline" style="font-family:\'Times New Roman\',Times,serif; '
        f'font-size:12.5px; color:{_STONE}; font-style:italic;">{subtitle}</td>'
    ) if subtitle else ""
    desc_html = (
        f'<p style="font-family:\'Times New Roman\',Times,serif; font-size:13.5px; '
        f'color:{_INK_SOFT}; margin:10px 0 0 0; line-height:1.55; max-width:540px;">{desc}</p>'
    ) if desc else ""
    return f"""
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
  <tr><td style="padding:36px 36px 0 36px;">
    <div style="border-top:1px solid {_TAN}; padding-top:24px;"></div>
    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"><tr>
      <td valign="baseline">
        <span style="font-family:'Times New Roman',Times,serif; font-size:46px; font-weight:400;
          color:{_BURGUNDY}; line-height:0.9; letter-spacing:-0.02em; font-style:italic;">{num}.</span>
        <span style="font-family:'Times New Roman',Times,serif; font-size:30px; font-weight:500;
          color:{_INK}; letter-spacing:-0.012em; margin-left:14px;">{title}</span>
      </td>
      {sub}
    </tr></table>
    {desc_html}
  </td></tr>
</table>"""


# ─── Idempotency ──────────────────────────────────────────────────────────────

def _claim_slot(report_date: date, recipients: list[str]) -> bool:
    from database.client import get_client
    try:
        get_client().table("report_log").insert({
            "report_type": REPORT_TYPE,
            "report_date": report_date.isoformat(),
            "status":      "pending",
            "recipients":  ",".join(recipients),
        }).execute()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.info("Slot for %s already claimed (%s) — exiting.", report_date, type(exc).__name__)
        return False


def _mark_sent(report_date: date) -> None:
    from database.client import get_client
    get_client().table("report_log").update({
        "status":  "sent",
        "sent_at": datetime.now(timezone.utc).isoformat(),
    }).eq("report_type", REPORT_TYPE).eq("report_date", report_date.isoformat()).execute()


def _mark_failed(report_date: date, err: str) -> None:
    from database.client import get_client
    try:
        get_client().table("report_log").update({
            "status":        "failed",
            "error_message": err[:2000],
            "sent_at":       datetime.now(timezone.utc).isoformat(),
        }).eq("report_type", REPORT_TYPE).eq("report_date", report_date.isoformat()).execute()
    except Exception:  # noqa: BLE001
        pass


# ─── Data fetch ───────────────────────────────────────────────────────────────

def _paginate(query_fn) -> list[dict]:
    page, page_size, out = 0, 1000, []
    while True:
        resp  = query_fn(page * page_size, (page + 1) * page_size - 1)
        chunk = resp.data or []
        out.extend(chunk)
        if len(chunk) < page_size:
            break
        page += 1
    return out


def _fetch_announcements(report_date: date) -> pd.DataFrame:
    from database.client import get_client
    dt_start = _IST.localize(datetime.combine(report_date, datetime.min.time())).isoformat()
    dt_end   = _IST.localize(datetime.combine(report_date + timedelta(days=1), datetime.min.time())).isoformat()
    client   = get_client()
    rows = _paginate(lambda s, e:
        client.table("corporate_announcements").select("*")
        .gte("announced_at", dt_start)
        .lt("announced_at", dt_end)
        .order("announced_at", desc=True)
        .range(s, e)
        .execute()
    )
    return pd.DataFrame(rows)


def _fetch_bm_filings(from_date: date, to_date: date) -> pd.DataFrame:
    """Board meeting intimation filings — next 14 days."""
    from database.client import get_client
    client = get_client()
    rows = _paginate(lambda s, e:
        client.table("board_meetings").select("*")
        .eq("source", "board_meetings")
        .gte("meeting_date", from_date.isoformat())
        .lte("meeting_date", to_date.isoformat())
        .order("meeting_date")
        .range(s, e)
        .execute()
    )
    return pd.DataFrame(rows)


def _fetch_event_calendar(from_date: date) -> pd.DataFrame:
    """Event calendar — all upcoming scheduled meetings."""
    from database.client import get_client
    client = get_client()
    rows = _paginate(lambda s, e:
        client.table("board_meetings").select("*")
        .eq("source", "event_calendar")
        .gte("meeting_date", from_date.isoformat())
        .order("meeting_date")
        .range(s, e)
        .execute()
    )
    return pd.DataFrame(rows)


def _fetch_corporate_actions(from_date: date, to_date: date) -> pd.DataFrame:
    from database.client import get_client
    client = get_client()
    rows = _paginate(lambda s, e:
        client.table("corporate_actions").select("*")
        .gte("ex_date", from_date.isoformat())
        .lte("ex_date", to_date.isoformat())
        .order("ex_date")
        .range(s, e)
        .execute()
    )
    return pd.DataFrame(rows)


# ─── HTML renderers ───────────────────────────────────────────────────────────

def _meetings_table(df: pd.DataFrame, show_description: bool = False) -> str:
    """Shared renderer for both board meetings and event calendar tables."""
    if df.empty:
        return (
            f'<p style="color:{_STONE}; font-style:italic; '
            f'font-family:\'Times New Roman\',Times,serif; font-size:13px; padding:0 36px;">'
            f'No entries.</p>'
        )

    rows: list[str] = []
    prev_date = None
    for _, row in df.iterrows():
        d         = str(row.get("meeting_date") or "")
        purpose   = str(row.get("purpose") or "")
        symbol    = str(row.get("symbol") or "")
        company   = str(row.get("company_name") or "")
        segment   = str(row.get("segment") or "")
        desc      = str(row.get("description") or "")
        p_color   = _purpose_color(purpose)

        is_new = d != prev_date
        if is_new:
            date_label = _fmt_date(d)
            date_cell  = (
                f'<td style="padding:7px 10px 7px 12px; border-bottom:1px solid {_SAND}; '
                f'font-family:\'Times New Roman\',Times,serif; font-size:12px; font-weight:600; '
                f'color:{_BURGUNDY}; white-space:nowrap; vertical-align:top;">{_e(date_label)}</td>'
            )
            prev_date = d
        else:
            date_cell = (
                f'<td style="padding:7px 10px 7px 12px; border-bottom:1px solid {_SAND}; '
                f'vertical-align:top;"></td>'
            )

        seg_badge = (
            f'<span style="font-size:9px; color:{_STONE}; border:1px solid {_TAN}; '
            f'padding:1px 4px; margin-left:6px;">{_e(segment.upper())}</span>'
        ) if segment == "sme" else ""

        purpose_cell = (
            f'<td style="padding:7px 12px 7px 6px; border-bottom:1px solid {_SAND}; '
            f'font-family:\'Times New Roman\',Times,serif; font-size:12px; '
            f'color:{p_color}; font-weight:500; vertical-align:top;">{_e(purpose)}</td>'
        )

        if show_description and desc:
            # Truncate long descriptions
            short_desc = desc[:220] + "…" if len(desc) > 220 else desc
            name_desc_cell = (
                f'<td style="padding:7px 10px; border-bottom:1px solid {_SAND}; vertical-align:top;">'
                f'<div style="font-weight:600; color:{_INK}; font-family:\'Times New Roman\',Times,serif; '
                f'font-size:13px;">{_e(symbol)}{seg_badge}</div>'
                f'<div style="font-style:italic; color:{_INK_SOFT}; font-family:\'Times New Roman\',Times,serif; '
                f'font-size:11.5px; margin-top:2px;">{_e(company)}</div>'
                f'<div style="color:{_STONE}; font-family:\'Times New Roman\',Times,serif; '
                f'font-size:11px; margin-top:4px; line-height:1.45;">{_e(short_desc)}</div>'
                f'</td>'
            )
            rows.append(f'<tr>{date_cell}{name_desc_cell}{purpose_cell}</tr>')
        else:
            rows.append(
                f'<tr>'
                f'{date_cell}'
                f'<td style="padding:7px 10px; border-bottom:1px solid {_SAND}; '
                f'font-weight:600; color:{_INK}; font-family:\'Times New Roman\',Times,serif; '
                f'font-size:13px; vertical-align:top;">{_e(symbol)}{seg_badge}</td>'
                f'<td style="padding:7px 10px; border-bottom:1px solid {_SAND}; '
                f'font-family:\'Times New Roman\',Times,serif; font-style:italic; '
                f'color:{_INK_SOFT}; font-size:12.5px; vertical-align:top;">{_e(company)}</td>'
                f'{purpose_cell}'
                f'</tr>'
            )

    if show_description:
        thead = f'<tr>{_th("Date")}{_th("Symbol / Company / Agenda")}{_th("Purpose")}</tr>'
    else:
        thead = f'<tr>{_th("Date")}{_th("Symbol")}{_th("Company")}{_th("Purpose")}</tr>'

    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
        f'style="border-collapse:collapse; font-family:\'Times New Roman\',Times,serif; font-size:12.5px;">'
        f'<thead>{thead}</thead><tbody>{"".join(rows)}</tbody></table>'
    )


def _corporate_actions_html(df: pd.DataFrame) -> str:
    if df.empty:
        return (
            f'<p style="color:{_STONE}; font-style:italic; '
            f'font-family:\'Times New Roman\',Times,serif; font-size:13px; padding:0 36px;">'
            f'No corporate actions this week.</p>'
        )

    rows: list[str] = []
    for _, row in df.iterrows():
        subject = str(row.get("subject") or "")
        s_up    = subject.upper()
        if "DIVIDEND" in s_up or "INTERIM" in s_up:
            action_color = _OLIVE
        elif "BONUS" in s_up:
            action_color = _NAVY
        elif "SPLIT" in s_up or "SUB-DIVISION" in s_up:
            action_color = _AMBER
        elif "RIGHTS" in s_up:
            action_color = _BURGUNDY
        elif "BUY BACK" in s_up or "BUYBACK" in s_up:
            action_color = _STONE
        else:
            action_color = _INK

        rows.append(
            f'<tr>'
            f'<td style="padding:7px 10px 7px 12px; border-bottom:1px solid {_SAND}; '
            f'font-family:\'Times New Roman\',Times,serif; font-size:12px; '
            f'font-weight:600; color:{_BURGUNDY}; white-space:nowrap;">'
            f'{_e(_fmt_date(str(row.get("ex_date") or "")))}</td>'
            f'<td style="padding:7px 10px; border-bottom:1px solid {_SAND}; '
            f'font-weight:600; color:{_INK}; font-family:\'Times New Roman\',Times,serif; font-size:13px;">'
            f'{_e(str(row.get("symbol") or ""))}</td>'
            f'<td style="padding:7px 10px; border-bottom:1px solid {_SAND}; '
            f'font-family:\'Times New Roman\',Times,serif; font-style:italic; color:{_INK_SOFT}; font-size:12px;">'
            f'{_e(str(row.get("company") or ""))}</td>'
            f'<td style="padding:7px 12px 7px 10px; border-bottom:1px solid {_SAND}; '
            f'font-family:\'Times New Roman\',Times,serif; font-size:12px; '
            f'color:{action_color}; font-weight:500;">{_e(subject)}</td>'
            f'</tr>'
        )

    thead = f'<tr>{_th("Ex-Date")}{_th("Symbol")}{_th("Company")}{_th("Action")}</tr>'
    legend = (
        f'<div style="font-family:\'Times New Roman\',Times,serif; font-size:11.5px; color:{_STONE}; '
        f'padding:10px 2px 0 2px; font-style:italic;">'
        f'<span style="color:{_OLIVE}; font-weight:500;">&#x25A0;</span>&nbsp;Dividend&nbsp;&thinsp;·&thinsp;'
        f'<span style="color:{_NAVY}; font-weight:500;">&#x25A0;</span>&nbsp;Bonus&nbsp;&thinsp;·&thinsp;'
        f'<span style="color:{_AMBER}; font-weight:500;">&#x25A0;</span>&nbsp;Split&nbsp;&thinsp;·&thinsp;'
        f'<span style="color:{_BURGUNDY}; font-weight:500;">&#x25A0;</span>&nbsp;Rights&nbsp;&thinsp;·&thinsp;'
        f'<span style="color:{_STONE}; font-weight:500;">&#x25A0;</span>&nbsp;Buy Back'
        f'</div>'
    )
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
        f'style="border-collapse:collapse; font-family:\'Times New Roman\',Times,serif; font-size:12.5px;">'
        f'<thead>{thead}</thead><tbody>{"".join(rows)}</tbody></table>'
        + legend
    )


def _announcements_html(df: pd.DataFrame, categories: set[str]) -> str:
    if df.empty:
        return (
            f'<p style="color:{_STONE}; font-style:italic; '
            f'font-family:\'Times New Roman\',Times,serif; font-size:13px; padding:0 36px;">'
            f'None today.</p>'
        )
    sub = df[df["category"].isin(categories)].copy() if categories else df.copy()
    if sub.empty:
        return (
            f'<p style="color:{_STONE}; font-style:italic; '
            f'font-family:\'Times New Roman\',Times,serif; font-size:13px; padding:0 36px;">'
            f'None today.</p>'
        )

    rows: list[str] = []
    for _, row in sub.head(60).iterrows():
        symbol  = str(row.get("symbol") or "")
        company = str(row.get("company_name") or "")
        cat     = str(row.get("category") or "")
        summary = str(row.get("summary") or "")
        seg     = str(row.get("segment") or "")
        url     = str(row.get("attachment_url") or "")

        seg_badge = (
            f'<span style="font-size:9px; color:{_STONE}; border:1px solid {_TAN}; '
            f'padding:1px 4px; margin-left:6px;">{_e(seg.upper())}</span>'
        ) if seg in ("sme", "debt") else ""

        name      = symbol or company
        link_open  = f'<a href="{_e(url)}" style="color:{_BURGUNDY}; text-decoration:none;" target="_blank">' if url else ""
        link_close = "</a>" if url else ""

        rows.append(
            f'<tr>'
            f'<td style="padding:8px 10px 8px 12px; border-bottom:1px solid {_SAND}; '
            f'font-family:\'Times New Roman\',Times,serif; font-size:13px; '
            f'font-weight:600; vertical-align:top; white-space:nowrap;">'
            f'{link_open}{_e(name)}{link_close}{seg_badge}</td>'
            f'<td style="padding:8px 12px 8px 10px; border-bottom:1px solid {_SAND}; '
            f'font-family:\'Times New Roman\',Times,serif; font-size:12.5px; color:{_INK_SOFT}; '
            f'line-height:1.5; vertical-align:top;">{_e(summary[:300])}</td>'
            f'</tr>'
        )

    extra = ""
    if len(sub) > 60:
        extra = (
            f'<tr><td colspan="2" style="padding:8px 12px; font-family:\'Times New Roman\',Times,serif; '
            f'font-size:12px; color:{_STONE}; font-style:italic;">…and {len(sub) - 60} more</td></tr>'
        )

    thead = f'<tr>{_th("Symbol / Company")}{_th("Summary")}</tr>'
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
        f'style="border-collapse:collapse; font-family:\'Times New Roman\',Times,serif; font-size:12.5px;">'
        f'<thead>{thead}</thead><tbody>{"".join(rows)}{extra}</tbody></table>'
    )


# ─── Full HTML assembly ───────────────────────────────────────────────────────

def _build_html(
    report_date: date,
    ann: pd.DataFrame,
    bm_filings: pd.DataFrame,
    ec: pd.DataFrame,
    ca: pd.DataFrame,
    generated_at: datetime,
) -> str:
    n_ann = len(ann)
    n_bm  = len(bm_filings)
    n_ec  = len(ec)
    n_ca  = len(ca)

    _gen_ist  = generated_at.astimezone(_IST)
    gen_time  = _gen_ist.strftime("%H:%M IST on the ")
    gen_day   = _ordinal(_gen_ist.day)
    gen_month = _gen_ist.strftime("%B, %Y")

    delta     = (report_date - date(2026, 1, 1)).days
    issue_num = max(1, int(delta * 5 / 7))

    bm_html      = _meetings_table(bm_filings, show_description=False)
    ec_html      = _meetings_table(ec,          show_description=True)
    ca_html      = _corporate_actions_html(ca)
    key_ann_html = _announcements_html(ann, _HIGH_PRIORITY)
    other_html   = _announcements_html(ann, _MEDIUM_PRIORITY)
    debt_html    = _announcements_html(
        ann[ann["segment"] == "debt"] if not ann.empty else ann, set()
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BAC Announcements — NSE — {report_date.day} {report_date.strftime('%b %Y')}</title>
<style>a {{ color: inherit; }}</style>
</head>
<body style="margin:0; padding:28px 12px; background:{_SAND};
  font-family:'Times New Roman',Times,serif; color:{_INK}; -webkit-font-smoothing:antialiased;">

<table role="presentation" cellpadding="0" cellspacing="0" border="0" align="center"
  width="760" style="width:760px; max-width:760px; margin:0 auto;
  background:{_PARCHMENT}; border:1px solid {_TAN};">
<tr><td style="padding:0;">

  <!-- MASTHEAD -->
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
    style="background:{_PARCHMENT};">
    <tr><td style="padding:30px 36px 6px 36px;">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"><tr>
        <td>
          <table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
            <td style="font-family:'Times New Roman',Times,serif; font-size:10.5px;
              letter-spacing:0.32em; text-transform:uppercase; color:{_BURGUNDY}; font-weight:500;">
              Brindco Alpha Capital</td>
            <td style="padding:0 10px; color:{_TAN}; font-size:12px;">◆</td>
            <td style="font-family:'Times New Roman',Times,serif; font-size:11px;
              font-style:italic; color:{_STONE};">a daily note from the quant desk</td>
          </tr></table>
        </td>
        <td align="right" style="font-family:'Times New Roman',Times,serif; font-size:11px;
          color:{_STONE}; font-style:italic;">№ {issue_num}</td>
      </tr></table>

      <div style="font-family:'Times New Roman',Times,serif; font-size:54px; font-weight:500;
        color:{_INK}; letter-spacing:-0.018em; margin:14px 0 0 0; line-height:1;">
        Daily&nbsp;Announcements</div>
      <div style="font-family:'Times New Roman',Times,serif; font-size:20px; color:{_INK};
        font-style:italic; margin:2px 0 18px 2px;">National Stock Exchange of India</div>

      <div style="border-top:3px solid {_INK}; padding-top:1px;"></div>
      <div style="border-top:1px solid {_INK}; margin-top:2px;"></div>

      <table role="presentation" cellpadding="0" cellspacing="0" border="0"
        width="100%" style="margin-top:12px;"><tr>
        <td style="font-family:'Times New Roman',Times,serif; font-size:13.5px; color:{_INK};">
          {_long_date(report_date)}</td>
        <td align="right" style="font-family:'Times New Roman',Times,serif; font-size:10.5px;
          letter-spacing:0.22em; text-transform:uppercase; color:{_STONE};">Mumbai · IST</td>
      </tr></table>
    </td></tr>
  </table>

  <!-- TOPLINE METRICS -->
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
    <tr><td style="padding:18px 36px 6px 36px;">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
        style="background:{_WARM_GREY}; border-top:1px solid {_TAN}; border-bottom:1px solid {_TAN};">
        <tr>
          <td width="25%" valign="top"
            style="padding:14px 12px 14px 18px; border-right:1px solid {_TAN};">
            <div style="font-family:'Times New Roman',Times,serif; font-size:9.5px;
              letter-spacing:0.24em; text-transform:uppercase; color:{_STONE}; font-weight:500;">
              Announcements</div>
            <div style="font-family:'Times New Roman',Times,serif; font-size:28px;
              font-weight:500; color:{_INK}; margin-top:6px; line-height:1;">{n_ann}</div>
            <div style="font-family:'Times New Roman',Times,serif; font-size:12px;
              color:{_STONE}; margin-top:6px; font-style:italic;">equities, SME &amp; debt</div>
          </td>
          <td width="25%" valign="top"
            style="padding:14px 12px 14px 14px; border-right:1px solid {_TAN};">
            <div style="font-family:'Times New Roman',Times,serif; font-size:9.5px;
              letter-spacing:0.24em; text-transform:uppercase; color:{_STONE}; font-weight:500;">
              Board Meetings</div>
            <div style="font-family:'Times New Roman',Times,serif; font-size:28px;
              font-weight:500; color:{_INK}; margin-top:6px; line-height:1;">{n_bm}</div>
            <div style="font-family:'Times New Roman',Times,serif; font-size:12px;
              color:{_STONE}; margin-top:6px; font-style:italic;">next 14 days</div>
          </td>
          <td width="25%" valign="top"
            style="padding:14px 12px 14px 14px; border-right:1px solid {_TAN};">
            <div style="font-family:'Times New Roman',Times,serif; font-size:9.5px;
              letter-spacing:0.24em; text-transform:uppercase; color:{_STONE}; font-weight:500;">
              Event Calendar</div>
            <div style="font-family:'Times New Roman',Times,serif; font-size:28px;
              font-weight:500; color:{_INK}; margin-top:6px; line-height:1;">{n_ec}</div>
            <div style="font-family:'Times New Roman',Times,serif; font-size:12px;
              color:{_STONE}; margin-top:6px; font-style:italic;">scheduled ahead</div>
          </td>
          <td width="25%" valign="top" style="padding:14px 18px 14px 14px;">
            <div style="font-family:'Times New Roman',Times,serif; font-size:9.5px;
              letter-spacing:0.24em; text-transform:uppercase; color:{_STONE}; font-weight:500;">
              Corporate Actions</div>
            <div style="font-family:'Times New Roman',Times,serif; font-size:28px;
              font-weight:500; color:{_INK}; margin-top:6px; line-height:1;">{n_ca}</div>
            <div style="font-family:'Times New Roman',Times,serif; font-size:12px;
              color:{_STONE}; margin-top:6px; font-style:italic;">ex-dates this week</div>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>

  <!-- i. BOARD MEETING FILINGS -->
  {_section_hdr("i", "Board Meeting Filings", "next 14 days",
    "Recent board meeting intimations filed with NSE — upcoming meetings and their agenda.")}
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
    <tr><td style="padding:18px 36px 6px 36px;">{bm_html}</td></tr>
  </table>

  <!-- ii. EVENT CALENDAR -->
  {_section_hdr("ii", "Event Calendar", "full forward schedule",
    "NSE&#8217;s published event calendar — results dates, fund raises, and key board agendas scheduled weeks ahead.")}
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
    <tr><td style="padding:18px 36px 6px 36px;">{ec_html}</td></tr>
  </table>

  <!-- iii. CORPORATE ACTIONS -->
  {_section_hdr("iii", "Corporate Actions", "ex-dates this week",
    "Dividends, bonuses, splits, and rights issues going ex in the next seven days.")}
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
    <tr><td style="padding:18px 36px 6px 36px;">{ca_html}</td></tr>
  </table>

  <!-- iv. KEY ANNOUNCEMENTS -->
  {_section_hdr("iv", "Key Announcements", f"{report_date.strftime('%d %b')} · equities &amp; SME",
    "Financial results, record dates, board outcomes, and takeover disclosures filed yesterday.")}
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
    <tr><td style="padding:18px 36px 6px 36px;">{key_ann_html}</td></tr>
  </table>

  <!-- v. OTHER ANNOUNCEMENTS -->
  {_section_hdr("v", "Other Announcements", "analyst meets &amp; updates")}
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
    <tr><td style="padding:18px 36px 6px 36px;">{other_html}</td></tr>
  </table>

  <!-- vi. DEBT MARKET -->
  {_section_hdr("vi", "Debt Market", "NCD &amp; bond announcements")}
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
    <tr><td style="padding:18px 36px 6px 36px;">{debt_html}</td></tr>
  </table>

  <!-- COLOPHON -->
  <table role="presentation" cellpadding="0" cellspacing="0" border="0"
    width="100%" style="margin-top:32px;">
    <tr><td style="padding:0 36px 32px 36px;">
      <div style="border-top:3px solid {_INK}; padding-top:1px;"></div>
      <div style="border-top:1px solid {_INK}; margin-top:2px;"></div>
      <table role="presentation" cellpadding="0" cellspacing="0" border="0"
        width="100%" style="margin-top:18px;"><tr>
        <td valign="top" width="55%" style="padding-right:24px;">
          <div style="font-family:'Times New Roman',Times,serif; font-size:9.5px;
            letter-spacing:0.24em; text-transform:uppercase; color:{_BURGUNDY};
            font-weight:600; margin-bottom:6px;">Colophon</div>
          <p style="font-family:'Times New Roman',Times,serif; font-size:12.5px;
            color:{_INK}; line-height:1.65; margin:0;">
            Set in <span style="font-style:italic;">Times New Roman</span>.
            Compiled by the NSE Announcements pipeline at {gen_time}{gen_day} of {gen_month}.
          </p>
        </td>
        <td valign="top" width="45%">
          <div style="font-family:'Times New Roman',Times,serif; font-size:9.5px;
            letter-spacing:0.24em; text-transform:uppercase; color:{_BURGUNDY};
            font-weight:600; margin-bottom:6px;">Sources</div>
          <div style="font-family:'Times New Roman',Times,serif; font-size:12.5px;
            color:{_INK}; line-height:1.7;">
            NSE corporate filings — announcements, event calendar, board meetings, and corporate actions.<br>
            Write to <a href="mailto:bac@brindco.com"
              style="color:{_BURGUNDY}; text-decoration:none;">bac@brindco.com</a> with corrections.
          </div>
        </td>
      </tr></table>
      <div style="margin-top:18px; text-align:center; font-family:'Times New Roman',Times,serif;
        font-size:11px; font-style:italic; color:{_STONE};">◆&nbsp;&nbsp;&nbsp;◆&nbsp;&nbsp;&nbsp;◆</div>
    </td></tr>
  </table>

</td></tr>
</table>
</body>
</html>"""


# ─── Email ────────────────────────────────────────────────────────────────────

def _send_email(*, sender, password, sender_name, recipients, subject, html):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"]    = f"{sender_name} <{sender}>"
    msg["To"]      = ", ".join(recipients)
    msg.set_content("This report requires an HTML-capable email client.")
    msg.add_alternative(html, subtype="html")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(sender, password)
        smtp.send_message(msg)


# ─── Slack ────────────────────────────────────────────────────────────────────

def _slack_s(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text[:3000]}}


def _build_slack_blocks(
    report_date: date,
    ann: pd.DataFrame,
    bm_filings: pd.DataFrame,
    ec: pd.DataFrame,
    ca: pd.DataFrame,
) -> list[dict]:
    blocks: list[dict] = []
    blocks.append({"type": "header", "text": {"type": "plain_text",
        "text": f"Daily Announcements — NSE — {report_date.strftime('%d %b %Y')}"}})
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn",
        "text": "Brindco Alpha Capital  ◆  _a daily note from the quant desk_"}]})
    blocks.append({"type": "divider"})

    blocks.append({"type": "section", "fields": [
        {"type": "mrkdwn", "text": f"*Announcements*\n{len(ann)} filed yesterday"},
        {"type": "mrkdwn", "text": f"*Board Meeting Filings*\n{len(bm_filings)} next 14 days"},
        {"type": "mrkdwn", "text": f"*Event Calendar*\n{len(ec)} upcoming"},
        {"type": "mrkdwn", "text": f"*Corporate Actions*\n{len(ca)} ex-dates this week"},
    ]})
    blocks.append({"type": "divider"})

    # i. Board meeting filings
    if not bm_filings.empty:
        lines = []
        for _, r in bm_filings.head(12).iterrows():
            lines.append(
                f"`{str(r['symbol']):<12}` {r['meeting_date']}  "
                f"{str(r.get('purpose') or '')[:35]}"
            )
        blocks.append(_slack_s("*i.  Board Meeting Filings (next 14 days)*\n" + "\n".join(lines)))
        blocks.append({"type": "divider"})

    # ii. Event calendar
    if not ec.empty:
        lines = []
        for _, r in ec.head(15).iterrows():
            lines.append(
                f"`{str(r['symbol']):<12}` {r['meeting_date']}  "
                f"{str(r.get('purpose') or '')[:35]}"
            )
        blocks.append(_slack_s("*ii.  Event Calendar*\n" + "\n".join(lines)))
        blocks.append({"type": "divider"})

    # iii. Corporate actions
    if not ca.empty:
        lines = []
        for _, r in ca.head(15).iterrows():
            lines.append(
                f"`{str(r['symbol']):<12}` {r['ex_date']}  "
                f"{str(r.get('subject') or '')[:40]}"
            )
        blocks.append(_slack_s("*iii.  Corporate Actions (ex-dates this week)*\n" + "\n".join(lines)))
        blocks.append({"type": "divider"})

    # iv. Key announcements
    if not ann.empty:
        hi = ann[ann["category"].isin(_HIGH_PRIORITY)].head(10)
        if not hi.empty:
            lines = [
                f"*{str(r.get('symbol') or r.get('company_name') or '')}*"
                f" — {str(r.get('category') or '')}"
                for _, r in hi.iterrows()
            ]
            blocks.append(_slack_s("*iv.  Key Announcements*\n" + "\n".join(lines)))
            blocks.append({"type": "divider"})

    blocks.append({"type": "context", "elements": [{"type": "mrkdwn",
        "text": f"◆  ◆  ◆  NSE corporate filings.  {report_date.strftime('%d %b %Y')}"}]})
    return blocks[:50]


def _send_slack(webhook_url: str, blocks: list[dict], report_date: date) -> None:
    import json
    payload = json.dumps({
        "text":   f"BAC Daily Announcements — NSE — {report_date.strftime('%d %b %Y')}",
        "blocks": blocks,
    }).encode("utf-8")
    req = urllib.request.Request(
        webhook_url, data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        if resp.status != 200:
            raise RuntimeError(f"Slack {resp.status}: {resp.read()}")


# ─── Entry point ──────────────────────────────────────────────────────────────

def main(report_date_override: date | None = None, preview_path: str | None = None) -> int:
    from dotenv import load_dotenv
    from utils import today_ist, previous_trading_day

    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    today       = date.fromisoformat(today_ist())
    report_date = report_date_override or previous_trading_day(today)
    logger.info("Building report for %s (today IST = %s)", report_date, today)

    preview_mode = preview_path is not None
    if not preview_mode:
        smtp_user     = _env("SMTP_USER")
        smtp_password = _env("SMTP_PASSWORD")
        recipients    = [r.strip() for r in _env("REPORT_RECIPIENTS").split(",") if r.strip()]
        sender_name   = os.environ.get("REPORT_SENDER_NAME", "BAC Announcements")
        if not _claim_slot(report_date, recipients):
            return 0

    try:
        generated_at = datetime.now(timezone.utc)

        ann        = _fetch_announcements(report_date)
        bm_filings = _fetch_bm_filings(today, today + timedelta(days=14))
        ec         = _fetch_event_calendar(today)
        ca         = _fetch_corporate_actions(today, today + timedelta(days=7))

        logger.info(
            "Fetched: %d announcements, %d bm filings, %d event calendar, %d corporate actions",
            len(ann), len(bm_filings), len(ec), len(ca),
        )

        html = _build_html(report_date, ann, bm_filings, ec, ca, generated_at)

        if preview_mode:
            with open(preview_path, "w", encoding="utf-8") as fh:
                fh.write(html)
            logger.info("Preview saved to %s", preview_path)
            return 0

        pretty_date = report_date.strftime("%d %b %Y")
        _send_email(
            sender=smtp_user, password=smtp_password, sender_name=sender_name,
            recipients=recipients,
            subject=f"BAC Announcements — NSE — {pretty_date}",
            html=html,
        )
        _mark_sent(report_date)
        logger.info("Sent to %s", recipients)

        slack_webhook = os.environ.get("SLACK_WEBHOOK_URL", "")
        if slack_webhook:
            try:
                _send_slack(
                    slack_webhook,
                    _build_slack_blocks(report_date, ann, bm_filings, ec, ca),
                    report_date,
                )
                logger.info("Slack sent")
            except Exception as exc:  # noqa: BLE001
                logger.warning("Slack failed (non-fatal): %s", exc)

        return 0

    except Exception as exc:  # noqa: BLE001
        logger.exception("Report failed")
        if not preview_mode:
            _mark_failed(report_date, f"{type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--date",    help="Override report date (YYYY-MM-DD)")
    parser.add_argument("--preview", metavar="PATH", help="Save HTML to file, skip email")
    args     = parser.parse_args()
    override = date.fromisoformat(args.date) if args.date else None
    sys.exit(main(override, preview_path=args.preview))
