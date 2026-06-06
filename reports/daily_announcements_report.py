"""
Daily NSE Announcements email report — humanised newspaper format.

Runs once per trading-day morning (Tue–Sat IST) at 10:00 IST.
Reports on:
  - Yesterday's corporate announcements (equities, SME, debt)
  - Upcoming board meetings (event-calendar + board-meetings, next 14 days)
  - Corporate actions going ex this week

Env vars required:
  SUPABASE_URL, SUPABASE_KEY
  SMTP_USER           — sending address
  SMTP_PASSWORD       — Google app-specific password
  REPORT_RECIPIENTS   — comma-separated list
  REPORT_SENDER_NAME  — optional, defaults to "BAC Announcements"
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

_IST = pytz.timezone("Asia/Kolkata")

# ─── Colour palette (same as surveillance pipeline) ───────────────────────────
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

# ─── Announcement categories ──────────────────────────────────────────────────
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


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"Missing env var: {name}")
    return val


def _ordinal(n: int) -> str:
    s = "th" if 11 <= (n % 100) <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}<sup style='font-size:9px;'>{s}</sup>"


def _long_date(d: date) -> str:
    ones = ["", "one", "two", "three", "four", "five", "six", "seven",
            "eight", "nine", "ten", "eleven", "twelve", "thirteen",
            "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
    tens = ["", "", "twenty", "thirty", "forty", "fifty",
            "sixty", "seventy", "eighty", "ninety"]
    year = d.year
    rem  = year - 2000
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
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not mark failed: %s", exc)


# ─── Data fetch ───────────────────────────────────────────────────────────────

def _fetch_announcements(report_date: date) -> pd.DataFrame:
    """Announcements where announced_at falls on report_date (IST)."""
    from database.client import get_client
    # announced_at is stored as UTC — we query a day-wide IST window
    dt_start = _IST.localize(datetime.combine(report_date, datetime.min.time())).isoformat()
    dt_end   = _IST.localize(datetime.combine(report_date + timedelta(days=1), datetime.min.time())).isoformat()
    client   = get_client()
    page, page_size, out = 0, 1000, []
    while True:
        resp = (
            client.table("corporate_announcements").select("*")
            .gte("announced_at", dt_start)
            .lt("announced_at", dt_end)
            .order("announced_at", desc=True)
            .range(page * page_size, (page + 1) * page_size - 1)
            .execute()
        )
        chunk = resp.data or []
        out.extend(chunk)
        if len(chunk) < page_size:
            break
        page += 1
    return pd.DataFrame(out)


def _fetch_board_meetings(from_date: date, to_date: date) -> pd.DataFrame:
    from database.client import get_client
    client = get_client()
    page, page_size, out = 0, 1000, []
    while True:
        resp = (
            client.table("board_meetings").select("*")
            .gte("meeting_date", from_date.isoformat())
            .lte("meeting_date", to_date.isoformat())
            .order("meeting_date")
            .range(page * page_size, (page + 1) * page_size - 1)
            .execute()
        )
        chunk = resp.data or []
        out.extend(chunk)
        if len(chunk) < page_size:
            break
        page += 1
    return pd.DataFrame(out)


def _fetch_corporate_actions(from_date: date, to_date: date) -> pd.DataFrame:
    from database.client import get_client
    client = get_client()
    page, page_size, out = 0, 1000, []
    while True:
        resp = (
            client.table("corporate_actions").select("*")
            .gte("ex_date", from_date.isoformat())
            .lte("ex_date", to_date.isoformat())
            .order("ex_date")
            .range(page * page_size, (page + 1) * page_size - 1)
            .execute()
        )
        chunk = resp.data or []
        out.extend(chunk)
        if len(chunk) < page_size:
            break
        page += 1
    return pd.DataFrame(out)


# ─── HTML section renderers ───────────────────────────────────────────────────

def _board_meetings_html(df: pd.DataFrame) -> str:
    if df.empty:
        return f'<p style="color:{_STONE}; font-style:italic; font-family:\'Times New Roman\',Times,serif; font-size:13px; padding:0 36px;">No upcoming board meetings found.</p>'

    # Deduplicate: for same (symbol, meeting_date), prefer event_calendar over board_meetings
    # and prefer specific purpose over generic ones
    df = df.copy()
    df["_prio"] = df["source"].map({"event_calendar": 0, "board_meetings": 1}).fillna(1)
    df = (
        df.sort_values("_prio")
        .drop_duplicates(subset=["symbol", "meeting_date"], keep="first")
        .drop(columns="_prio")
        .sort_values("meeting_date")
    )

    rows: list[str] = []
    prev_date = None
    for _, row in df.iterrows():
        d = row["meeting_date"]
        is_new_date = d != prev_date
        date_cell = ""
        if is_new_date:
            try:
                d_obj = date.fromisoformat(d)
                date_label = d_obj.strftime("%-d %b") if hasattr(d_obj, 'strftime') else d
            except Exception:
                date_label = d
            date_cell = (
                f'<td style="padding:7px 10px 7px 12px; border-bottom:1px solid {_SAND}; '
                f'font-family:\'Times New Roman\',Times,serif; font-size:12px; '
                f'font-weight:600; color:{_BURGUNDY}; white-space:nowrap;">{_e(str(date_label))}</td>'
            )
            prev_date = d
        else:
            date_cell = f'<td style="padding:7px 10px 7px 12px; border-bottom:1px solid {_SAND};"></td>'

        purpose = str(row.get("purpose") or "")
        symbol  = str(row.get("symbol") or "")
        company = str(row.get("company_name") or "")
        segment = str(row.get("segment") or "")
        seg_badge = (
            f'<span style="font-size:9px; color:{_STONE}; border:1px solid {_TAN}; '
            f'padding:1px 4px; margin-left:6px; font-family:\'Times New Roman\',Times,serif;">'
            f'{_e(segment.upper())}</span>'
        ) if segment == "sme" else ""

        rows.append(
            f'<tr>'
            f'{date_cell}'
            f'<td style="padding:7px 10px; border-bottom:1px solid {_SAND}; '
            f'font-weight:600; color:{_INK}; font-family:\'Times New Roman\',Times,serif; font-size:13px;">'
            f'{_e(symbol)}{seg_badge}</td>'
            f'<td style="padding:7px 10px; border-bottom:1px solid {_SAND}; '
            f'font-family:\'Times New Roman\',Times,serif; font-style:italic; color:{_INK_SOFT}; font-size:12.5px;">'
            f'{_e(company)}</td>'
            f'<td style="padding:7px 12px 7px 10px; border-bottom:1px solid {_SAND}; '
            f'font-family:\'Times New Roman\',Times,serif; font-size:12px; color:{_INK};">'
            f'{_e(purpose)}</td>'
            f'</tr>'
        )

    thead = f'<tr>{_th("Date")}{_th("Symbol")}{_th("Company")}{_th("Purpose")}</tr>'
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
        f'style="border-collapse:collapse; font-family:\'Times New Roman\',Times,serif; font-size:12.5px;">'
        f'<thead>{thead}</thead><tbody>{"".join(rows)}</tbody></table>'
    )


def _corporate_actions_html(df: pd.DataFrame) -> str:
    if df.empty:
        return f'<p style="color:{_STONE}; font-style:italic; font-family:\'Times New Roman\',Times,serif; font-size:13px; padding:0 36px;">No corporate actions this week.</p>'

    rows: list[str] = []
    for _, row in df.iterrows():
        ex_date = str(row.get("ex_date") or "")
        try:
            d_obj = date.fromisoformat(ex_date)
            date_label = d_obj.strftime("%-d %b")
        except Exception:
            date_label = ex_date

        subject = str(row.get("subject") or "")
        # Colour-code action type
        s_up = subject.upper()
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
            f'font-weight:600; color:{_BURGUNDY}; white-space:nowrap;">{_e(date_label)}</td>'
            f'<td style="padding:7px 10px; border-bottom:1px solid {_SAND}; '
            f'font-weight:600; color:{_INK}; font-family:\'Times New Roman\',Times,serif; font-size:13px;">'
            f'{_e(str(row.get("symbol") or ""))}</td>'
            f'<td style="padding:7px 10px; border-bottom:1px solid {_SAND}; '
            f'font-family:\'Times New Roman\',Times,serif; font-style:italic; color:{_INK_SOFT}; font-size:12px;">'
            f'{_e(str(row.get("company") or ""))}</td>'
            f'<td style="padding:7px 12px 7px 10px; border-bottom:1px solid {_SAND}; '
            f'font-family:\'Times New Roman\',Times,serif; font-size:12px; color:{action_color}; font-weight:500;">'
            f'{_e(subject)}</td>'
            f'</tr>'
        )

    thead = f'<tr>{_th("Ex-Date")}{_th("Symbol")}{_th("Company")}{_th("Action")}</tr>'
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
        f'style="border-collapse:collapse; font-family:\'Times New Roman\',Times,serif; font-size:12.5px;">'
        f'<thead>{thead}</thead><tbody>{"".join(rows)}</tbody></table>'
        f'<div style="font-family:\'Times New Roman\',Times,serif; font-size:11.5px; color:{_STONE}; '
        f'padding:10px 2px 0 2px; font-style:italic;">'
        f'<span style="color:{_OLIVE}; font-weight:500;">&#x25A0;</span>&nbsp;Dividend&nbsp;·&nbsp;'
        f'<span style="color:{_NAVY}; font-weight:500;">&#x25A0;</span>&nbsp;Bonus&nbsp;·&nbsp;'
        f'<span style="color:{_AMBER}; font-weight:500;">&#x25A0;</span>&nbsp;Split&nbsp;·&nbsp;'
        f'<span style="color:{_BURGUNDY}; font-weight:500;">&#x25A0;</span>&nbsp;Rights&nbsp;·&nbsp;'
        f'<span style="color:{_STONE}; font-weight:500;">&#x25A0;</span>&nbsp;Buy Back'
        f'</div>'
    )


def _announcements_section_html(df: pd.DataFrame, categories: set[str], title_label: str) -> str:
    if df.empty:
        return ""
    sub = df[df["category"].isin(categories)].copy() if categories else df.copy()
    if sub.empty:
        return f'<p style="color:{_STONE}; font-style:italic; font-family:\'Times New Roman\',Times,serif; font-size:13px; padding:0 36px;">None today.</p>'

    rows: list[str] = []
    for _, row in sub.head(50).iterrows():
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

        link_open  = f'<a href="{_e(url)}" style="color:{_BURGUNDY}; text-decoration:none;" target="_blank">' if url else ""
        link_close = "</a>" if url else ""
        sym_cell   = f'{link_open}<b>{_e(symbol or company)}</b>{link_close}{seg_badge}' if (symbol or company) else "—"

        rows.append(
            f'<tr>'
            f'<td style="padding:8px 10px 8px 12px; border-bottom:1px solid {_SAND}; '
            f'font-family:\'Times New Roman\',Times,serif; font-size:13px; vertical-align:top;">'
            f'{sym_cell}</td>'
            f'<td style="padding:8px 12px 8px 10px; border-bottom:1px solid {_SAND}; '
            f'font-family:\'Times New Roman\',Times,serif; font-size:12.5px; color:{_INK_SOFT}; '
            f'line-height:1.5; vertical-align:top;">{_e(summary[:280])}</td>'
            f'</tr>'
        )

    extra = ""
    if len(sub) > 50:
        extra = (
            f'<tr><td colspan="2" style="padding:8px 12px; font-family:\'Times New Roman\',Times,serif; '
            f'font-size:12px; color:{_STONE}; font-style:italic;">…and {len(sub) - 50} more</td></tr>'
        )

    thead = f'<tr>{_th("Symbol / Company")}{_th("Summary")}</tr>'
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
        f'style="border-collapse:collapse; font-family:\'Times New Roman\',Times,serif; font-size:12.5px;">'
        f'<thead>{thead}</thead><tbody>{"".join(rows)}{extra}</tbody></table>'
    )


# ─── Full HTML build ──────────────────────────────────────────────────────────

def _build_html(
    report_date: date,
    ann: pd.DataFrame,
    bm: pd.DataFrame,
    ca: pd.DataFrame,
    generated_at: datetime,
) -> str:
    n_ann = len(ann)
    n_bm  = len(bm.drop_duplicates(subset=["symbol", "meeting_date"])) if not bm.empty else 0
    n_ca  = len(ca) if not ca.empty else 0

    _gen_ist    = generated_at.astimezone(_IST)
    gen_time    = _gen_ist.strftime("%H:%M IST on the ")
    gen_day     = _ordinal(_gen_ist.day)
    gen_month   = _gen_ist.strftime("%B, %Y")

    bm_html   = _board_meetings_html(bm)
    ca_html   = _corporate_actions_html(ca)
    hi_ann    = _announcements_section_html(ann, _HIGH_PRIORITY, "High priority")
    mid_ann   = _announcements_section_html(ann, _MEDIUM_PRIORITY, "Analyst meets & updates")
    debt_ann  = _announcements_section_html(
        ann[ann["segment"] == "debt"] if not ann.empty else ann, set(), "Debt"
    )

    # Issue number — trading days since 2026-01-01
    delta = (report_date - date(2026, 1, 1)).days
    issue_num = max(1, int(delta * 5 / 7))

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BAC Announcements — NSE — {report_date.day} {report_date.strftime('%b %Y')}</title>
<style>a {{ color: inherit; }}</style>
</head>
<body style="margin:0; padding:28px 12px; background:{_SAND}; font-family:'Times New Roman',Times,serif; color:{_INK};">

<table role="presentation" cellpadding="0" cellspacing="0" border="0" align="center" width="760"
  style="width:760px; max-width:760px; margin:0 auto; background:{_PARCHMENT}; border:1px solid {_TAN};">
<tr><td style="padding:0;">

  <!-- MASTHEAD -->
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
    <tr><td style="padding:30px 36px 6px 36px;">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"><tr>
        <td>
          <table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
            <td style="font-family:'Times New Roman',Times,serif; font-size:10.5px; letter-spacing:0.32em;
              text-transform:uppercase; color:{_BURGUNDY}; font-weight:500;">Brindco Alpha Capital</td>
            <td style="padding:0 10px; color:{_TAN}; font-size:12px;">◆</td>
            <td style="font-family:'Times New Roman',Times,serif; font-size:11px; font-style:italic; color:{_STONE};">a daily note from the quant desk</td>
          </tr></table>
        </td>
        <td align="right" style="font-family:'Times New Roman',Times,serif; font-size:11px; color:{_STONE}; font-style:italic;">№ {issue_num}</td>
      </tr></table>

      <div style="font-family:'Times New Roman',Times,serif; font-size:54px; font-weight:500; color:{_INK};
        letter-spacing:-0.018em; margin:14px 0 0 0; line-height:1;">Daily&nbsp;Announcements</div>
      <div style="font-family:'Times New Roman',Times,serif; font-size:20px; color:{_INK}; font-style:italic;
        margin:2px 0 18px 2px;">National Stock Exchange of India</div>

      <div style="border-top:3px solid {_INK}; padding-top:1px;"></div>
      <div style="border-top:1px solid {_INK}; margin-top:2px;"></div>

      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="margin-top:12px;">
        <tr>
          <td style="font-family:'Times New Roman',Times,serif; font-size:13.5px; color:{_INK};">
            {_long_date(report_date)}
          </td>
          <td align="right" style="font-family:'Times New Roman',Times,serif; font-size:10.5px;
            letter-spacing:0.22em; text-transform:uppercase; color:{_STONE};">Mumbai · IST</td>
        </tr>
      </table>
    </td></tr>
  </table>

  <!-- TOPLINE METRICS -->
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
    <tr><td style="padding:18px 36px 6px 36px;">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
        style="background:{_WARM_GREY}; border-top:1px solid {_TAN}; border-bottom:1px solid {_TAN};">
        <tr>
          <td width="33%" valign="top" style="padding:14px 14px 14px 18px; border-right:1px solid {_TAN};">
            <div style="font-family:'Times New Roman',Times,serif; font-size:9.5px; letter-spacing:0.24em;
              text-transform:uppercase; color:{_STONE}; font-weight:500;">Announcements</div>
            <div style="font-family:'Times New Roman',Times,serif; font-size:28px; font-weight:500;
              color:{_INK}; margin-top:6px; line-height:1;">{n_ann}</div>
            <div style="font-family:'Times New Roman',Times,serif; font-size:12px; color:{_STONE};
              margin-top:6px; font-style:italic;">equities, SME &amp; debt</div>
          </td>
          <td width="33%" valign="top" style="padding:14px 14px 14px 16px; border-right:1px solid {_TAN};">
            <div style="font-family:'Times New Roman',Times,serif; font-size:9.5px; letter-spacing:0.24em;
              text-transform:uppercase; color:{_STONE}; font-weight:500;">Board Meetings</div>
            <div style="font-family:'Times New Roman',Times,serif; font-size:28px; font-weight:500;
              color:{_INK}; margin-top:6px; line-height:1;">{n_bm}</div>
            <div style="font-family:'Times New Roman',Times,serif; font-size:12px; color:{_STONE};
              margin-top:6px; font-style:italic;">scheduled next 14 days</div>
          </td>
          <td width="34%" valign="top" style="padding:14px 18px 14px 16px;">
            <div style="font-family:'Times New Roman',Times,serif; font-size:9.5px; letter-spacing:0.24em;
              text-transform:uppercase; color:{_STONE}; font-weight:500;">Corporate Actions</div>
            <div style="font-family:'Times New Roman',Times,serif; font-size:28px; font-weight:500;
              color:{_INK}; margin-top:6px; line-height:1;">{n_ca}</div>
            <div style="font-family:'Times New Roman',Times,serif; font-size:12px; color:{_STONE};
              margin-top:6px; font-style:italic;">ex-dates this week</div>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>

  <!-- i. BOARD MEETINGS -->
  {_section_hdr("i", "Upcoming Board Meetings", "next 14 days",
    "Scheduled board meetings — sourced from both the NSE event calendar and board-meeting filings.")}
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
    <tr><td style="padding:18px 36px 6px 36px;">{bm_html}</td></tr>
  </table>

  <!-- ii. CORPORATE ACTIONS -->
  {_section_hdr("ii", "Corporate Actions", "ex-dates this week",
    "Dividends, bonuses, splits, and rights issues going ex in the next seven days.")}
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
    <tr><td style="padding:18px 36px 6px 36px;">{ca_html}</td></tr>
  </table>

  <!-- iii. KEY ANNOUNCEMENTS -->
  {_section_hdr("iii", "Key Announcements", f"{report_date.strftime('%d %b')} · equities &amp; SME",
    "Financial results, record dates, takeover disclosures, and board outcomes filed yesterday.")}
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
    <tr><td style="padding:18px 36px 6px 36px;">{hi_ann}</td></tr>
  </table>

  <!-- iv. OTHER ANNOUNCEMENTS -->
  {_section_hdr("iv", "Other Announcements", "analyst meets &amp; updates")}
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
    <tr><td style="padding:18px 36px 6px 36px;">{mid_ann}</td></tr>
  </table>

  <!-- v. DEBT MARKET -->
  {_section_hdr("v", "Debt Market", "NCD &amp; bond announcements")}
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
    <tr><td style="padding:18px 36px 6px 36px;">{debt_ann}</td></tr>
  </table>

  <!-- COLOPHON -->
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="margin-top:32px;">
    <tr><td style="padding:0 36px 32px 36px;">
      <div style="border-top:3px solid {_INK}; padding-top:1px;"></div>
      <div style="border-top:1px solid {_INK}; margin-top:2px;"></div>
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="margin-top:18px;">
        <tr>
          <td valign="top" width="55%" style="padding-right:24px;">
            <div style="font-family:'Times New Roman',Times,serif; font-size:9.5px; letter-spacing:0.24em;
              text-transform:uppercase; color:{_BURGUNDY}; font-weight:600; margin-bottom:6px;">Colophon</div>
            <p style="font-family:'Times New Roman',Times,serif; font-size:12.5px; color:{_INK}; line-height:1.65; margin:0;">
              Set in <span style="font-style:italic;">Times New Roman</span>. Compiled by the NSE Announcements pipeline at {gen_time}{gen_day} of {gen_month}.
            </p>
          </td>
          <td valign="top" width="45%">
            <div style="font-family:'Times New Roman',Times,serif; font-size:9.5px; letter-spacing:0.24em;
              text-transform:uppercase; color:{_BURGUNDY}; font-weight:600; margin-bottom:6px;">Sources</div>
            <div style="font-family:'Times New Roman',Times,serif; font-size:12.5px; color:{_INK}; line-height:1.7;">
              Data from NSE corporate filings feeds — announcements, event calendar, board meetings, and corporate actions.<br>
              Write to <a href="mailto:bac@brindco.com" style="color:{_BURGUNDY}; text-decoration:none;">bac@brindco.com</a> with corrections.
            </div>
          </td>
        </tr>
      </table>
      <div style="margin-top:18px; text-align:center; font-family:'Times New Roman',Times,serif; font-size:11px; font-style:italic; color:{_STONE};">
        ◆&nbsp;&nbsp;&nbsp;◆&nbsp;&nbsp;&nbsp;◆
      </div>
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


def _build_slack_blocks(report_date: date, ann: pd.DataFrame, bm: pd.DataFrame, ca: pd.DataFrame) -> list[dict]:
    blocks: list[dict] = []
    blocks.append({"type": "header", "text": {"type": "plain_text",
        "text": f"Daily Announcements — NSE — {report_date.strftime('%d %b %Y')}"}})
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn",
        "text": "Brindco Alpha Capital  ◆  _a daily note from the quant desk_"}]})
    blocks.append({"type": "divider"})

    # Topline
    n_ann = len(ann)
    n_bm  = len(bm.drop_duplicates(subset=["symbol", "meeting_date"])) if not bm.empty else 0
    n_ca  = len(ca)
    blocks.append({"type": "section", "fields": [
        {"type": "mrkdwn", "text": f"*Announcements*\n{n_ann} (equities, SME, debt)"},
        {"type": "mrkdwn", "text": f"*Board Meetings*\n{n_bm} scheduled · next 14 days"},
        {"type": "mrkdwn", "text": f"*Corporate Actions*\n{n_ca} ex-dates this week"},
    ]})
    blocks.append({"type": "divider"})

    # Board meetings (next 7 days)
    if not bm.empty:
        bm_d = (
            bm.sort_values("meeting_date")
            .drop_duplicates(subset=["symbol", "meeting_date"])
            .head(15)
        )
        lines = []
        for _, r in bm_d.iterrows():
            d = r["meeting_date"]
            lines.append(f"`{str(r['symbol']):<12}` {d}  {str(r.get('purpose') or '')[:40]}")
        blocks.append(_slack_s("*i.  Upcoming Board Meetings*\n" + "\n".join(lines)))
        blocks.append({"type": "divider"})

    # Corporate actions
    if not ca.empty:
        lines = []
        for _, r in ca.head(15).iterrows():
            lines.append(f"`{str(r['symbol']):<12}` {r['ex_date']}  {str(r.get('subject') or '')[:40]}")
        blocks.append(_slack_s("*ii.  Corporate Actions (ex-dates this week)*\n" + "\n".join(lines)))
        blocks.append({"type": "divider"})

    # Key announcements
    if not ann.empty:
        hi = ann[ann["category"].isin(_HIGH_PRIORITY)].head(10)
        if not hi.empty:
            lines = []
            for _, r in hi.iterrows():
                sym = str(r.get("symbol") or r.get("company_name") or "")
                lines.append(f"*{sym}* — {str(r.get('category') or '')}")
            blocks.append(_slack_s("*iii.  Key Announcements*\n" + "\n".join(lines)))
            blocks.append({"type": "divider"})

    blocks.append({"type": "context", "elements": [{"type": "mrkdwn",
        "text": f"◆  ◆  ◆  Data from NSE corporate filings.  {report_date.strftime('%d %b %Y')}"}]})
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
            raise RuntimeError(f"Slack webhook {resp.status}: {resp.read()}")


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
    logger.info("Building report for %s", report_date)

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

        ann = _fetch_announcements(report_date)
        bm  = _fetch_board_meetings(today, today + timedelta(days=14))
        ca  = _fetch_corporate_actions(today, today + timedelta(days=7))

        logger.info("Fetched: %d announcements, %d board meetings, %d corporate actions",
                    len(ann), len(bm), len(ca))

        html = _build_html(report_date, ann, bm, ca, generated_at)

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
                _send_slack(slack_webhook, _build_slack_blocks(report_date, ann, bm, ca), report_date)
                logger.info("Slack sent")
            except Exception as exc:  # noqa: BLE001
                logger.warning("Slack send failed (non-fatal): %s", exc)

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
    parser.add_argument("--preview", metavar="PATH", help="Save HTML to file instead of emailing")
    args    = parser.parse_args()
    override = date.fromisoformat(args.date) if args.date else None
    sys.exit(main(override, preview_path=args.preview))
