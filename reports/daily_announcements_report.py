"""
Daily NSE Announcements email report — humanised newspaper format (v2).

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

# ─── NIFTY50 constituents (hardcoded — update from DB later) ─────────────────
_NIFTY50 = frozenset({
    "RELIANCE", "TCS", "HDFCBANK", "BHARTIARTL", "ICICIBANK", "INFY", "SBI",
    "HINDUNILVR", "ITC", "LT", "KOTAKBANK", "AXISBANK", "BAJFINANCE", "BAJAJFINSV",
    "SBILIFE", "HCLTECH", "MARUTI", "TITAN", "SUNPHARMA", "ULTRACEMCO", "NTPC",
    "ONGC", "POWERGRID", "TATAMOTORS", "WIPRO", "TECHM", "NESTLEIND", "ASIANPAINT",
    "DRREDDY", "JSWSTEEL", "TATASTEEL", "ADANIENT", "ADANIPORTS", "CIPLA", "DIVISLAB",
    "APOLLOHOSP", "TRENT", "AMBUJACEM", "SBICARD", "BPCL", "COALINDIA", "HINDALCO",
    "GRASIM", "EICHERMOT", "HEROMOTOCO", "BAJAJ-AUTO", "INDUSINDBK", "HDFCLIFE",
    "BRITANNIA", "SHRIRAMFIN", "M&M",
})

# ─── NIFTY100 members NOT in NIFTY50 ─────────────────────────────────────────
_NIFTY100_ONLY = frozenset({
    "TATAELXSI", "COFORGE", "INDIACEM", "ASTRAMICRO", "CANBK", "INDIANB",
    "NAVINFLUOR", "JMFINANCIL", "VOLTAS", "GODREJCP", "BERGEPAINT", "SIEMENS",
    "HAVELLS", "ABB", "PIDILITIND", "TORNTPHARM", "DABUR", "MARICO", "COLPAL",
    "ICICIPRULI", "HDFCAMC", "BANDHANBNK", "FEDERALBNK", "IDFCFIRSTB", "PNB",
    "BANKBARODA", "CANFINHOME", "MUTHOOTFIN", "CHOLAFIN", "GAIL", "IOC",
    "HINDPETRO", "ATGL", "APLAPOLLO", "JKCEMENT", "SHREECEM", "TATACOMM",
    "MCDOWELL-N", "LTIM", "PERSISTENT", "DMART", "ZOMATO", "ETERNAL",
    "PAYTM", "NYKAA", "POLICYBZR", "IRCTC", "HAL", "BEL", "RVNL",
    # Portfolio additions
    "ADANIGREEN", "INDUSTOWER", "JSWENERGY", "TVSMOTOR", "VBL",
    "TATAPOWER", "POLYCAB", "NMDC", "CGPOWER", "MAZDOCK", "GRSE",
})

# ─── BAC active coverage universe (full portfolio — 52 holdings) ──────────────
_BAC_ACTIVE = frozenset({
    "ACUTAAS",    # Acutaas Chemicals
    "ADANIGREEN", # Adani Green Energy
    "ADANIPOWER", # Adani Power
    "AFFLE",      # Affle (India)
    "ATHER",      # Ather Energy
    "BSE",        # BSE
    "BAJAJ-AUTO", # Bajaj Auto
    "BEL",        # Bharat Electronics
    "BHARTIARTL", # Bharti Airtel
    "BLUESTONE",  # BlueStone Jewellery & Lifestyle
    "CGPOWER",    # CG Power & Industrial Solutions
    "CEMINDIA",   # Cemindia Projects
    "CHALET",     # Chalet Hotels
    "CIPLA",      # Cipla
    "COFORGE",    # Coforge
    "DATAPATTNS", # Data Patterns (India)
    "EICHERMOT",  # Eicher Motors
    "EMMVEE",     # Emmvee Photovoltaic Power
    "ETERNAL",    # Eternal (formerly Zomato parent)
    "FORCEMOT",   # Force Motors
    "FRACTAL",    # Fractal Analytics
    "FUJIYAMA",   # Fujiyama Power Systems
    "GRSE",       # Garden Reach Shipbuilders
    "HFCL",       # HFCL
    "HAL",        # Hindustan Aeronautics
    "INDUSTOWER", # Indus Towers
    "JSWENERGY",  # JSW Energy
    "LT",         # Larsen & Toubro
    "LLOYDSME",   # Lloyds Metals & Energy
    "M&M",        # Mahindra & Mahindra
    "MAZDOCK",    # Mazagon Dock Shipbuilders
    "MUTHOOTFIN", # Muthoot Finance
    "NMDC",       # NMDC
    "NTPC",       # NTPC
    "NETWEB",     # Netweb Technologies
    "NIPPONLIFE", # Nippon Life India AMC
    "PERSISTENT", # Persistent Systems
    "POLYCAB",    # Polycab India
    "PREMIERENE", # Premier Energies
    "RRKABEL",    # RR Kabel
    "RELIANCE",   # Reliance Industries
    "SKIPPER",    # Skipper
    "TVSMOTOR",   # TVS Motor
    "TATAPOWER",  # Tata Power
    "THYROCARE",  # Thyrocare Technologies
    "TRITURBINE", # Triveni Turbines
    "WABAG",      # VA Tech Wabag
    "VBL",        # Varun Beverages
    "WAAREEENER", # Waaree Energies
    "WAAREERTL",  # Waaree Renewable Technologies
    "YATHARTH",   # Yatharth Hospital
    "ZYDUSLIFE",  # Zydus Lifesciences
})

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


# ─── Index / badge helpers ────────────────────────────────────────────────────

def _nifty_badge(symbol: str) -> str:
    """Returns HTML badge span for NIFTY50 or NIFTY100, or empty string."""
    if symbol in _NIFTY50:
        return (
            f'<span style="font-size:9px; color:{_BURGUNDY}; border:1px solid {_BURGUNDY}; '
            f'padding:1px 4px; margin-left:6px; letter-spacing:0.1em; font-weight:500;">NIFTY50</span>'
        )
    if symbol in _NIFTY100_ONLY:
        return (
            f'<span style="font-size:9px; color:{_NAVY}; border:1px solid {_NAVY}; '
            f'padding:1px 4px; margin-left:6px; letter-spacing:0.1em; font-weight:500;">NIFTY100</span>'
        )
    return ""


def _is_notable(symbol: str, purpose: str, segment: str) -> bool:
    """Returns True if a star (✦) should prefix the symbol."""
    p = (purpose or "").upper()
    if symbol in _NIFTY50:
        return True
    if symbol in _NIFTY100_ONLY and any(k in p for k in ("FINANCIAL RESULTS", "FUND RAIS")):
        return True
    if "VOLUNTARY DELIST" in p:
        return True
    if "FUND RAIS" in p and segment == "equities":
        return True
    return False


def _star_prefix(symbol: str, purpose: str, segment: str) -> str:
    if _is_notable(symbol, purpose, segment):
        return f'<span style="color:{_BURGUNDY}; font-size:14px; margin-right:5px;">&#10022;</span>'
    return ""


# ─── Description cleaner ──────────────────────────────────────────────────────

def _clean_desc(text: str, max_chars: int = 65) -> str:
    """Strip boilerplate from board-meeting descriptions and truncate."""
    if not text:
        return ""
    # Remove rescheduling boilerplate
    text = re.sub(
        r"The Company has informed the Exchange that a Board meeting to be held on\s+[^.]+\s+"
        r"has been re-scheduled\.\s+Further,\s+",
        "",
        text,
        flags=re.IGNORECASE,
    )
    # Remove standard preamble
    text = re.sub(
        r"the Company has informed the Exchange that the meeting of the Board of Directors "
        r"of the Company will be held on\s+[^,]+,\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"has informed the Exchange about Board Meeting to be held on\s+[^,]+\s+to consider\s+",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"The Company has informed the Exchange that\s+",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = text.strip()
    if text:
        text = text[0].upper() + text[1:]
    if text and not text.endswith("."):
        text += "."
    if len(text) > max_chars:
        text = text[:max_chars - 1] + "…"
    return text


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


def _fmt_weekday(d) -> str:
    """Return '7 Jun Mon' style label."""
    if isinstance(d, str):
        try:
            d = date.fromisoformat(d)
        except ValueError:
            return d
    return f"{d.day} {d.strftime('%b')} {d.strftime('%a')}"


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


def _section_hdr(
    num: str,
    title: str,
    subtitle: str = "",
    desc: str = "",
    net_note: str = "",
) -> str:
    sub = (
        f'<td align="right" valign="baseline" style="font-family:\'Times New Roman\',Times,serif; '
        f'font-size:12.5px; color:{_STONE}; font-style:italic;">{subtitle}</td>'
    ) if subtitle else ""
    desc_html = (
        f'<p style="font-family:\'Times New Roman\',Times,serif; font-size:13.5px; '
        f'color:{_INK_SOFT}; margin:10px 0 0 0; line-height:1.55; max-width:540px;">{desc}</p>'
    ) if desc else ""
    net_html = (
        f'<p style="font-family:\'Times New Roman\',Times,serif; font-size:12.5px; '
        f'color:{_BURGUNDY}; margin:8px 0 0 0; line-height:1.5; max-width:620px; '
        f'font-style:italic;">Net &#8212; {net_note}</p>'
    ) if net_note else ""
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
    {net_html}
  </td></tr>
</table>"""


# ─── Next trading day ─────────────────────────────────────────────────────────

def _next_trading_day(from_date: date) -> tuple[date, bool]:
    """Return (next_trading_date, skipped_weekend)."""
    d = from_date + timedelta(days=1)
    skipped = False
    while d.weekday() >= 5:  # Saturday=5, Sunday=6
        d += timedelta(days=1)
        skipped = True
    return d, skipped


# ─── "Today on the Tape" editorial ───────────────────────────────────────────

def _parse_dividend_amount(subject: str) -> float:
    """Extract numeric dividend amount from subject string."""
    m = re.search(r"(?:rs\.?|inr|rupees?)\s*([\d,]+(?:\.\d+)?)", subject, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            pass
    m = re.search(r"([\d,]+(?:\.\d+)?)\s*(?:per\s+share|/-|rs)", subject, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            pass
    return 0.0


def _build_editorial(
    report_date: date,
    ann: pd.DataFrame,
    bm_filings: pd.DataFrame,
    ec: pd.DataFrame,
    ca: pd.DataFrame,
) -> str:
    """Build 3 editorial paragraphs for 'Today on the Tape' section."""

    # ── Para 1: Board meeting / event calendar highlights ──
    para1_parts: list[str] = []

    # Voluntary delisting items from bm_filings + ec
    delist_items: list[dict] = []
    for df_src in [bm_filings, ec]:
        if df_src.empty:
            continue
        for _, row in df_src.iterrows():
            purpose = str(row.get("purpose") or "")
            if "VOLUNTARY DELIST" in purpose.upper():
                delist_items.append({
                    "symbol": str(row.get("symbol") or ""),
                    "date": str(row.get("meeting_date") or ""),
                    "desc": str(row.get("description") or ""),
                })

    for item in delist_items[:2]:
        sym = item["symbol"]
        d_label = _fmt_date(item["date"]) if item["date"] else ""
        note = _clean_desc(item["desc"], 60) or "voluntary delisting"
        para1_parts.append(
            f'<strong style="color:{_BURGUNDY};">{_e(sym)}</strong> convenes '
            f'{d_label} to consider voluntary delisting &#8212; {_e(note)}'
        )

    # NIFTY50 fund raising items
    if len(para1_parts) < 2:
        fund_n50: list[dict] = []
        for df_src in [bm_filings, ec]:
            if df_src.empty:
                continue
            for _, row in df_src.iterrows():
                sym = str(row.get("symbol") or "")
                purpose = str(row.get("purpose") or "")
                if sym in _NIFTY50 and "FUND RAIS" in purpose.upper():
                    fund_n50.append({
                        "symbol": sym,
                        "date": str(row.get("meeting_date") or ""),
                    })

        for item in fund_n50[:2 - len(para1_parts)]:
            sym = item["symbol"]
            d_label = _fmt_date(item["date"]) if item["date"] else ""
            para1_parts.append(
                f'<strong style="color:{_BURGUNDY};">{_e(sym)}</strong> board meets '
                f'{d_label} on fund raising &#8212; a NIFTY50 board action'
            )

    # General notable items to fill para1
    if len(para1_parts) < 1:
        notable: list[dict] = []
        for df_src in [bm_filings, ec]:
            if df_src.empty:
                continue
            for _, row in df_src.iterrows():
                sym = str(row.get("symbol") or "")
                purpose = str(row.get("purpose") or "")
                if sym in _NIFTY50 or sym in _NIFTY100_ONLY:
                    notable.append({"symbol": sym, "purpose": purpose,
                                    "date": str(row.get("meeting_date") or "")})
        for item in notable[:2]:
            sym = item["symbol"]
            d_label = _fmt_date(item["date"]) if item["date"] else ""
            para1_parts.append(
                f'<strong style="color:{_BURGUNDY};">{_e(sym)}</strong> &#8212; '
                f'{_e(item["purpose"])} · {d_label}'
            )

    if para1_parts:
        para1_html = (
            f'<span style="font-family:\'Times New Roman\',Times,serif; font-size:13px; '
            f'color:{_INK}; line-height:1.6;">'
            + " · ".join(para1_parts)
            + ".</span>"
        )
    else:
        bm_count = len(bm_filings)
        ec_count = len(ec)
        para1_html = (
            f'<span style="font-family:\'Times New Roman\',Times,serif; font-size:13px; '
            f'color:{_INK}; line-height:1.6;">'
            f'Board meeting intimations: <strong>{bm_count}</strong> filings across the next fortnight. '
            f'Event calendar carries <strong>{ec_count}</strong> scheduled proceedings ahead.'
            f'</span>'
        )

    # ── Para 2: Corporate actions ──
    para2_html = ""
    if not ca.empty:
        # Collect dividend ex-dates and find highest value
        div_by_date: dict[str, list[tuple[str, float]]] = {}
        rights_items: list[dict] = []

        for _, row in ca.iterrows():
            subject = str(row.get("subject") or "")
            sym = str(row.get("symbol") or "")
            ex_d = str(row.get("ex_date") or "")
            s_up = subject.upper()

            if "DIVIDEND" in s_up or "INTERIM" in s_up:
                amount = _parse_dividend_amount(subject)
                if ex_d not in div_by_date:
                    div_by_date[ex_d] = []
                div_by_date[ex_d].append((sym, amount))
            elif "RIGHTS" in s_up:
                rights_items.append({"symbol": sym, "subject": subject, "ex_date": ex_d})

        if div_by_date:
            # Find max dividend
            best_sym, best_amt = "", 0.0
            for date_str, entries in div_by_date.items():
                for sym, amt in entries:
                    if amt > best_amt:
                        best_amt = amt
                        best_sym = sym

            # Find dates with most dividends
            sorted_dates = sorted(div_by_date.keys(), key=lambda x: len(div_by_date[x]), reverse=True)
            date_labels = [_fmt_date(d) for d in sorted_dates[:2]]

            rights_note = ""
            if rights_items:
                ri = rights_items[0]
                rights_note = (
                    f' Rights on <strong style="color:{_BURGUNDY};">{_e(ri["symbol"])}</strong> '
                    f'ex-date {_fmt_date(ri["ex_date"])}.'
                )

            lead_note = ""
            if best_sym and best_amt > 0:
                lead_note = (
                    f' <strong style="color:{_BURGUNDY};">{_e(best_sym)}</strong> '
                    f'(&#8377;{best_amt:g}) leads the table.'
                )
            elif best_sym:
                lead_note = (
                    f' <strong style="color:{_BURGUNDY};">{_e(best_sym)}</strong> leads the table.'
                )

            date_str_joined = " &amp; ".join(date_labels) if len(date_labels) > 1 else (date_labels[0] if date_labels else "")
            n_divs = sum(len(v) for v in div_by_date.values())

            para2_html = (
                f'<span style="font-family:\'Times New Roman\',Times,serif; font-size:13px; '
                f'color:{_INK}; line-height:1.6;">'
                f'Dividend cluster around {date_str_joined} &#8212; '
                f'<strong>{n_divs}</strong> ex-dates this week.{lead_note}{rights_note}'
                f'</span>'
            )
        elif rights_items:
            ri = rights_items[0]
            para2_html = (
                f'<span style="font-family:\'Times New Roman\',Times,serif; font-size:13px; '
                f'color:{_INK}; line-height:1.6;">'
                f'Rights issue: <strong style="color:{_BURGUNDY};">{_e(ri["symbol"])}</strong> '
                f'ex-date {_fmt_date(ri["ex_date"])}. {len(ca)} corporate action(s) this week.'
                f'</span>'
            )
        else:
            para2_html = (
                f'<span style="font-family:\'Times New Roman\',Times,serif; font-size:13px; '
                f'color:{_INK}; line-height:1.6;">'
                f'{len(ca)} corporate action(s) on the tape this week; no dividend clusters of note.'
                f'</span>'
            )
    else:
        para2_html = (
            f'<span style="font-family:\'Times New Roman\',Times,serif; font-size:13px; '
            f'color:{_STONE}; line-height:1.6; font-style:italic;">'
            f'No corporate actions recorded for this period.</span>'
        )

    # ── Para 3: Yesterday's filings summary ──
    n_results = 0
    n_engage = 0
    bac_hits: list[str] = []
    if not ann.empty:
        for _, row in ann.iterrows():
            cat = str(row.get("category") or "")
            sym = str(row.get("symbol") or "")
            if cat in ("Financial Results", "Integrated Filing- Financial"):
                n_results += 1
            if "Analyst" in cat or "Investor" in cat or "Investor Presentation" == cat:
                n_engage += 1
            if sym in _BAC_ACTIVE:
                bac_hits.append(sym)

    sme_count = 0
    eq_count = 0
    if not ann.empty and "segment" in ann.columns:
        sme_count = int((ann["segment"] == "sme").sum())
        eq_count  = int((ann["segment"] == "equities").sum())

    bac_note = ""
    if bac_hits:
        unique_bac = list(dict.fromkeys(bac_hits))
        bac_note = (
            f' BAC coverage active: '
            + ", ".join(
                f'<strong style="color:{_BURGUNDY};">{_e(s)}</strong>'
                for s in unique_bac[:4]
            )
            + "."
        )

    sme_note = ""
    if sme_count > 0 and n_results > 0:
        sme_note = f" ({sme_count} SME)"

    para3_html = (
        f'<span style="font-family:\'Times New Roman\',Times,serif; font-size:13px; '
        f'color:{_INK}; line-height:1.6;">'
        f'Yesterday&#8217;s filings &#8212; '
        f'<strong>{n_results}</strong> results{sme_note}; '
        f'<strong>{n_engage}</strong> engagement set.'
        f'{bac_note}'
        f'</span>'
    )

    # ── Assemble pilcrow table ──
    def _pilcrow_row(para_html: str, pb: str = "8px") -> str:
        return (
            f'<tr>'
            f'<td valign="top" width="22" style="font-size:18px; color:{_BURGUNDY}; '
            f'padding-right:8px; padding-top:2px; font-family:\'Times New Roman\',Times,serif;">&#182;</td>'
            f'<td valign="top" style="padding-bottom:{pb};">{para_html}</td>'
            f'</tr>'
        )

    return f"""
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
  <tr><td style="padding:24px 36px 4px 36px;">
    <div style="border-top:1px solid {_TAN}; padding-top:18px;"></div>
    <div style="font-family:'Times New Roman',Times,serif; font-size:10.5px; letter-spacing:0.32em;
      text-transform:uppercase; color:{_BURGUNDY}; font-weight:600; margin-bottom:10px;">Today on the Tape</div>
    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
      {_pilcrow_row(para1_html)}
      {_pilcrow_row(para2_html)}
      {_pilcrow_row(para3_html, pb="4px")}
    </table>
  </td></tr>
</table>"""


# ─── Topline metric subtitles ─────────────────────────────────────────────────

def _metric_subtitle_ann(ann: pd.DataFrame) -> str:
    if ann.empty:
        return "no filings"
    n_results = int(ann["category"].isin({"Financial Results", "Integrated Filing- Financial"}).sum())
    n_engage = int(ann["category"].isin({
        "Analysts/Institutional Investor Meet/Con. Call Updates",
        "Investor Presentation",
    }).sum())
    return f"{n_results} results · {n_engage} engagement"


def _metric_subtitle_bm(bm_filings: pd.DataFrame) -> str:
    if bm_filings.empty:
        return "none scheduled"
    n_fund = sum(1 for p in bm_filings.get("purpose", pd.Series(dtype=str))
                 if "FUND RAIS" in str(p).upper())
    n_delist = sum(1 for p in bm_filings.get("purpose", pd.Series(dtype=str))
                   if "VOLUNTARY DELIST" in str(p).upper())
    return f"{n_fund} fund raises · {n_delist} delisting"


def _metric_subtitle_ec(ec: pd.DataFrame) -> str:
    if ec.empty:
        return "no events ahead"
    n_results = sum(1 for p in ec.get("purpose", pd.Series(dtype=str))
                    if "FINANCIAL RESULTS" in str(p).upper())
    n_fund = sum(1 for p in ec.get("purpose", pd.Series(dtype=str))
                 if "FUND RAIS" in str(p).upper())
    return f"{n_results} results · {n_fund} fund raises"


def _metric_subtitle_ca(ca: pd.DataFrame) -> str:
    if ca.empty:
        return "none this week"
    n_div = 0
    n_other = 0
    for subj in ca.get("subject", pd.Series(dtype=str)):
        s = str(subj).upper()
        if "DIVIDEND" in s or "INTERIM" in s:
            n_div += 1
        else:
            n_other += 1
    return f"{n_div} dividends · {n_other} other"


# ─── BAC Coverage Touchpoints panel ──────────────────────────────────────────

def _bac_coverage_panel(
    ann: pd.DataFrame,
    bm_filings: pd.DataFrame,
    ec: pd.DataFrame,
    ca: pd.DataFrame,
) -> str:
    # Find BAC active symbols in all data
    bac_hits: list[tuple[str, str, str]] = []  # (symbol, section, event_desc)
    for sym in sorted(_BAC_ACTIVE):
        found_in: list[str] = []
        if not ann.empty and "symbol" in ann.columns:
            if (ann["symbol"] == sym).any():
                found_in.append("Announcements")
        if not bm_filings.empty and "symbol" in bm_filings.columns:
            if (bm_filings["symbol"] == sym).any():
                found_in.append("Board Meetings")
        if not ec.empty and "symbol" in ec.columns:
            if (ec["symbol"] == sym).any():
                found_in.append("Event Calendar")
        if not ca.empty and "symbol" in ca.columns:
            if (ca["symbol"] == sym).any():
                found_in.append("Corp Actions")
        if found_in:
            bac_hits.append((sym, ", ".join(found_in), ""))

    if bac_hits:
        syms_html = ", ".join(
            f'<strong style="color:{_BURGUNDY};">{_e(sym)}</strong> ({_e(section)})'
            for sym, section, _ in bac_hits
        )
        active_para = (
            f'<div style="font-family:\'Times New Roman\',Times,serif; font-size:12.5px; '
            f'color:{_INK}; line-height:1.6; margin-bottom:10px;">'
            f'<span style="font-weight:600;">Active coverage on the tape:</span> {syms_html}.</div>'
        )
    else:
        active_para = (
            f'<div style="font-family:\'Times New Roman\',Times,serif; font-size:12.5px; '
            f'color:{_STONE}; font-style:italic; line-height:1.6; margin-bottom:10px;">'
            f'BAC active universe is silent on the tape today.</div>'
        )

    # Find all NIFTY50/100 symbols across bm_filings, ec, ca
    nifty_rows: list[dict] = []

    def _tier(sym: str) -> int:
        if sym in _NIFTY50:
            return 0
        if sym in _NIFTY100_ONLY:
            return 1
        return 2

    for _, row in (bm_filings.iterrows() if not bm_filings.empty else iter([])):
        sym = str(row.get("symbol") or "")
        if sym in _NIFTY50 or sym in _NIFTY100_ONLY:
            purpose = str(row.get("purpose") or "")
            nifty_rows.append({
                "tier": _tier(sym),
                "symbol": sym,
                "section": "Board Meetings",
                "event": purpose + " — board meeting",
                "date": str(row.get("meeting_date") or ""),
            })

    for _, row in (ec.iterrows() if not ec.empty else iter([])):
        sym = str(row.get("symbol") or "")
        if sym in _NIFTY50 or sym in _NIFTY100_ONLY:
            purpose = str(row.get("purpose") or "")
            nifty_rows.append({
                "tier": _tier(sym),
                "symbol": sym,
                "section": "Event Calendar",
                "event": purpose,
                "date": str(row.get("meeting_date") or ""),
            })

    for _, row in (ca.iterrows() if not ca.empty else iter([])):
        sym = str(row.get("symbol") or "")
        subject = str(row.get("subject") or "")
        if sym in _NIFTY50 or sym in _NIFTY100_ONLY:
            s_up = subject.upper()
            if "DIVIDEND" in s_up or "INTERIM" in s_up:
                amt = _parse_dividend_amount(subject)
                evt = f"Dividend &#8377;{amt:g} ex-date" if amt else "Dividend ex-date"
            else:
                evt = subject[:50]
            nifty_rows.append({
                "tier": _tier(sym),
                "symbol": sym,
                "section": "Corp Actions",
                "event": evt,
                "date": str(row.get("ex_date") or ""),
            })

    nifty_rows.sort(key=lambda r: (r["tier"], r["date"]))

    n50_count = sum(1 for r in nifty_rows if r["tier"] == 0)
    n100_count = sum(1 for r in nifty_rows if r["tier"] == 1)
    pillar_para = (
        f'<div style="font-family:\'Times New Roman\',Times,serif; font-size:12.5px; '
        f'color:{_INK}; line-height:1.6; margin-bottom:10px;">'
        f'<span style="font-weight:600;">Pillar I overlay:</span> '
        f'{n50_count} NIFTY50 touch{"point" if n50_count == 1 else "points"} &amp; '
        f'{n100_count} NIFTY100 touch{"point" if n100_count == 1 else "points"} '
        f'across board meetings, event calendar &amp; corporate actions.</div>'
    )

    # Build nifty table
    if nifty_rows:
        table_rows_html = ""
        for r in nifty_rows[:20]:
            badge = _nifty_badge(r["symbol"])
            table_rows_html += (
                f'<tr>'
                f'<td style="padding:5px 10px 5px 0; border-bottom:1px solid {_SAND}; '
                f'font-family:\'Times New Roman\',Times,serif; font-size:12px; '
                f'font-weight:600; white-space:nowrap;">'
                f'{_e(r["symbol"])}{badge}</td>'
                f'<td style="padding:5px 10px; border-bottom:1px solid {_SAND}; '
                f'font-family:\'Times New Roman\',Times,serif; font-size:11.5px; '
                f'color:{_STONE};">{_e(r["section"])}</td>'
                f'<td style="padding:5px 10px; border-bottom:1px solid {_SAND}; '
                f'font-family:\'Times New Roman\',Times,serif; font-size:11.5px; '
                f'color:{_INK_SOFT};">{r["event"]}</td>'
                f'<td style="padding:5px 0 5px 10px; border-bottom:1px solid {_SAND}; '
                f'font-family:\'Times New Roman\',Times,serif; font-size:11.5px; '
                f'color:{_BURGUNDY}; white-space:nowrap;">{_e(_fmt_date(r["date"]))}</td>'
                f'</tr>'
            )
        nifty_table = (
            f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">'
            f'<thead><tr>'
            f'{_th("Symbol")}{_th("Section")}{_th("Event")}{_th("Date")}'
            f'</tr></thead>'
            f'<tbody>{table_rows_html}</tbody>'
            f'</table>'
        )
    else:
        nifty_table = (
            f'<div style="font-family:\'Times New Roman\',Times,serif; font-size:12px; '
            f'color:{_STONE}; font-style:italic;">No NIFTY50/100 touchpoints in scope.</div>'
        )

    return f"""
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
  <tr><td style="padding:28px 36px 0 36px;">
    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
      style="background:{_WARM_GREY}; border:1px solid {_TAN};">
      <tr><td style="padding:18px 22px 18px 22px;">
        <div style="padding-bottom:8px;">
          <span style="font-family:'Times New Roman',Times,serif; font-size:10.5px;
            letter-spacing:0.26em; text-transform:uppercase; color:{_BURGUNDY}; font-weight:600;">
            BAC Coverage Touchpoints</span>
          <span style="font-family:'Times New Roman',Times,serif; font-size:12px;
            color:{_STONE}; font-style:italic; margin-left:10px;">
            active universe &amp; Pillar I overlay across all sections</span>
        </div>
        <div style="border-top:1px solid {_TAN}; margin-bottom:14px;"></div>
        {active_para}
        {pillar_para}
        {nifty_table}
      </td></tr>
    </table>
  </td></tr>
</table>"""


# ─── Next Trading Session panel ───────────────────────────────────────────────

def _next_session_panel(
    today: date,
    bm_filings: pd.DataFrame,
    ec: pd.DataFrame,
    ca: pd.DataFrame,
) -> str:
    next_day, skipped_weekend = _next_trading_day(today)
    next_day_iso = next_day.isoformat()

    next_day_label = f"{next_day.strftime('%A')}, {next_day.day} {next_day.strftime('%b')}"
    weekend_note = " · weekend skip" if skipped_weekend else ""

    table_rows: list[str] = []

    # Board meetings on next_day
    if not bm_filings.empty:
        for _, row in bm_filings.iterrows():
            if str(row.get("meeting_date") or "") == next_day_iso:
                sym = str(row.get("symbol") or "")
                purpose = str(row.get("purpose") or "")
                badge = _nifty_badge(sym)
                table_rows.append(
                    f'<tr>'
                    f'<td style="padding:5px 10px 5px 0; border-bottom:1px solid {_SAND}; '
                    f'font-family:\'Times New Roman\',Times,serif; font-size:11.5px; '
                    f'color:{_BURGUNDY}; font-weight:600; white-space:nowrap;">Board Mtg</td>'
                    f'<td style="padding:5px 10px; border-bottom:1px solid {_SAND}; '
                    f'font-family:\'Times New Roman\',Times,serif; font-size:12px; font-weight:600;">'
                    f'{_e(sym)}{badge}</td>'
                    f'<td style="padding:5px 0 5px 10px; border-bottom:1px solid {_SAND}; '
                    f'font-family:\'Times New Roman\',Times,serif; font-size:11.5px; '
                    f'color:{_INK_SOFT};">{_e(purpose)}</td>'
                    f'</tr>'
                )

    # Event calendar on next_day
    if not ec.empty:
        for _, row in ec.iterrows():
            if str(row.get("meeting_date") or "") == next_day_iso:
                sym = str(row.get("symbol") or "")
                purpose = str(row.get("purpose") or "")
                badge = _nifty_badge(sym)
                table_rows.append(
                    f'<tr>'
                    f'<td style="padding:5px 10px 5px 0; border-bottom:1px solid {_SAND}; '
                    f'font-family:\'Times New Roman\',Times,serif; font-size:11.5px; '
                    f'color:{_BURGUNDY}; font-weight:600; white-space:nowrap;">Event</td>'
                    f'<td style="padding:5px 10px; border-bottom:1px solid {_SAND}; '
                    f'font-family:\'Times New Roman\',Times,serif; font-size:12px; font-weight:600;">'
                    f'{_e(sym)}{badge}</td>'
                    f'<td style="padding:5px 0 5px 10px; border-bottom:1px solid {_SAND}; '
                    f'font-family:\'Times New Roman\',Times,serif; font-size:11.5px; '
                    f'color:{_INK_SOFT};">{_e(purpose)}</td>'
                    f'</tr>'
                )

    # Corporate actions ex on next_day
    if not ca.empty:
        for _, row in ca.iterrows():
            if str(row.get("ex_date") or "") == next_day_iso:
                sym = str(row.get("symbol") or "")
                subject = str(row.get("subject") or "")
                badge = _nifty_badge(sym)
                s_up = subject.upper()
                subj_style = f"color:{_BURGUNDY}; font-weight:600;" if "RIGHTS" in s_up else f"color:{_INK_SOFT};"
                table_rows.append(
                    f'<tr>'
                    f'<td style="padding:5px 10px 5px 0; border-bottom:1px solid {_SAND}; '
                    f'font-family:\'Times New Roman\',Times,serif; font-size:11.5px; '
                    f'color:{_BURGUNDY}; font-weight:600; white-space:nowrap;">Corp Action</td>'
                    f'<td style="padding:5px 10px; border-bottom:1px solid {_SAND}; '
                    f'font-family:\'Times New Roman\',Times,serif; font-size:12px; font-weight:600;">'
                    f'{_e(sym)}{badge}</td>'
                    f'<td style="padding:5px 0 5px 10px; border-bottom:1px solid {_SAND}; '
                    f'font-family:\'Times New Roman\',Times,serif; font-size:11.5px; '
                    f'{subj_style}">{_e(subject)}</td>'
                    f'</tr>'
                )

    if table_rows:
        session_table = (
            f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">'
            f'<thead><tr>'
            f'{_th("Type")}{_th("Symbol")}{_th("Event")}'
            f'</tr></thead>'
            f'<tbody>{"".join(table_rows)}</tbody>'
            f'</table>'
        )
    else:
        session_table = (
            f'<div style="font-family:\'Times New Roman\',Times,serif; font-size:12px; '
            f'color:{_STONE}; font-style:italic;">No scheduled events for this session.</div>'
        )

    return f"""
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
  <tr><td style="padding:22px 36px 0 36px;">
    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
      style="background:{_WARM_GREY}; border:1px solid {_TAN};">
      <tr><td style="padding:18px 22px 18px 22px;">
        <div style="padding-bottom:8px;">
          <span style="font-family:'Times New Roman',Times,serif; font-size:10.5px;
            letter-spacing:0.26em; text-transform:uppercase; color:{_BURGUNDY}; font-weight:600;">
            Next Trading Session</span>
          <span style="font-family:'Times New Roman',Times,serif; font-size:12px;
            color:{_STONE}; font-style:italic; margin-left:10px;">{next_day_label}{weekend_note}</span>
        </div>
        <div style="border-top:1px solid {_TAN}; margin-bottom:14px;"></div>
        {session_table}
      </td></tr>
    </table>
  </td></tr>
</table>"""


# ─── Top Movers Overlay panel ─────────────────────────────────────────────────

def _top_movers_panel(ann: pd.DataFrame) -> str:
    overlay_cats = {"Financial Results", "Integrated Filing- Financial",
                    "Analysts/Institutional Investor Meet/Con. Call Updates"}
    if not ann.empty:
        mask = (
            ann["category"].isin(overlay_cats)
            & ann["segment"].isin(["equities", "sme"])
        )
        sub = ann[mask].head(12)
    else:
        sub = pd.DataFrame()

    n_total = len(sub)

    if n_total > 0:
        intro = (
            f'<div style="font-family:\'Times New Roman\',Times,serif; font-size:12.5px; '
            f'color:{_INK}; line-height:1.6; margin-bottom:10px;">'
            f'Cross-reference result: {n_total} announcement symbol{"s" if n_total != 1 else ""} '
            f'tagged for price overlay.</div>'
        )
        price_note = (
            f'<div style="font-family:\'Times New Roman\',Times,serif; font-size:12px; '
            f'color:{_STONE}; font-style:italic; margin-bottom:12px;">'
            f'Price data pending bhavcopy — all cells show &#8212; until batch runs.</div>'
        )
        table_rows_html = ""
        for _, row in sub.iterrows():
            sym = str(row.get("symbol") or row.get("company_name") or "")
            cat = str(row.get("category") or "")
            seg = str(row.get("segment") or "")
            seg_badge = (
                f'<span style="font-size:9px; color:{_STONE}; border:1px solid {_TAN}; '
                f'padding:1px 4px; margin-left:5px;">{_e(seg.upper())}</span>'
            ) if seg == "sme" else ""
            table_rows_html += (
                f'<tr>'
                f'<td style="padding:5px 10px 5px 0; border-bottom:1px solid {_SAND}; '
                f'font-family:\'Times New Roman\',Times,serif; font-size:12px; '
                f'font-weight:600; white-space:nowrap;">{_e(sym)}{seg_badge}</td>'
                f'<td style="padding:5px 10px; border-bottom:1px solid {_SAND}; '
                f'font-family:\'Times New Roman\',Times,serif; font-size:11.5px; '
                f'color:{_INK_SOFT};">{_e(cat)}</td>'
                f'<td style="padding:5px 10px; border-bottom:1px solid {_SAND}; '
                f'font-family:\'Times New Roman\',Times,serif; font-size:12px; '
                f'color:{_STONE}; text-align:right;">&#8212;</td>'
                f'<td style="padding:5px 10px; border-bottom:1px solid {_SAND}; '
                f'font-family:\'Times New Roman\',Times,serif; font-size:12px; '
                f'color:{_STONE}; text-align:right;">&#8212;</td>'
                f'<td style="padding:5px 0 5px 10px; border-bottom:1px solid {_SAND}; '
                f'font-family:\'Times New Roman\',Times,serif; font-size:12px; '
                f'color:{_STONE}; text-align:right;">&#8212;</td>'
                f'</tr>'
            )
        overlay_table = (
            f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">'
            f'<thead><tr>'
            f'{_th("Symbol")}{_th("Filing")}'
            f'{_th("Close", "right")}{_th("&#916;%", "right")}{_th("Vol/ADV", "right")}'
            f'</tr></thead>'
            f'<tbody>{table_rows_html}</tbody>'
            f'</table>'
        )
    else:
        intro = (
            f'<div style="font-family:\'Times New Roman\',Times,serif; font-size:12.5px; '
            f'color:{_INK}; line-height:1.6; margin-bottom:10px;">'
            f'Cross-reference result: none of the announcement symbols are in scope for overlay.</div>'
        )
        price_note = (
            f'<div style="font-family:\'Times New Roman\',Times,serif; font-size:12px; '
            f'color:{_STONE}; font-style:italic; margin-bottom:12px;">'
            f'Price data pending bhavcopy &#8212; batch runs after market close.</div>'
        )
        overlay_table = ""

    return f"""
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
  <tr><td style="padding:22px 36px 0 36px;">
    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
      style="background:{_WARM_GREY}; border:1px solid {_TAN};">
      <tr><td style="padding:18px 22px 18px 22px;">
        <div style="padding-bottom:8px;">
          <span style="font-family:'Times New Roman',Times,serif; font-size:10.5px;
            letter-spacing:0.26em; text-transform:uppercase; color:{_BURGUNDY}; font-weight:600;">
            Top Movers &#183; Announcement Overlay</span>
          <span style="font-family:'Times New Roman',Times,serif; font-size:12px;
            color:{_STONE}; font-style:italic; margin-left:10px;">
            yesterday&#8217;s filings cross-tagged with price action</span>
        </div>
        <div style="border-top:1px solid {_TAN}; margin-bottom:14px;"></div>
        {intro}
        {price_note}
        {overlay_table}
      </td></tr>
    </table>
  </td></tr>
</table>"""


# ─── "Underlying Filings" separator ──────────────────────────────────────────

def _underlying_filings_separator() -> str:
    return f"""
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
  <tr><td style="padding:32px 36px 0 36px;">
    <div style="border-top:1px solid {_INK}; padding-top:2px;"></div>
    <div style="border-top:3px solid {_INK}; margin-top:2px;"></div>
    <div style="font-size:10.5px; letter-spacing:0.32em; text-transform:uppercase;
      color:{_BURGUNDY}; font-weight:600; margin-top:14px;
      font-family:'Times New Roman',Times,serif;">Underlying Filings</div>
    <div style="font-size:12px; color:{_STONE}; font-style:italic; margin-top:4px;
      font-family:'Times New Roman',Times,serif;">
      canonical sections &#8212; board meetings, events, corporate actions, results, engagement
    </div>
  </td></tr>
</table>"""


# ─── HTML renderers ───────────────────────────────────────────────────────────

def _bm_table_enhanced(df: pd.DataFrame) -> str:
    """Enhanced board meeting filings table with star prefix and nifty badges."""
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

        star = _star_prefix(symbol, purpose, segment)
        nifty_b = _nifty_badge(symbol)

        sym_cell = (
            f'<td style="padding:7px 10px; border-bottom:1px solid {_SAND}; '
            f'font-weight:600; color:{_INK}; font-family:\'Times New Roman\',Times,serif; '
            f'font-size:13px; vertical-align:top;">{star}{_e(symbol)}{seg_badge}{nifty_b}</td>'
        )
        co_cell = (
            f'<td style="padding:7px 10px; border-bottom:1px solid {_SAND}; '
            f'font-family:\'Times New Roman\',Times,serif; font-style:italic; '
            f'color:{_INK_SOFT}; font-size:12.5px; vertical-align:top;">{_e(company)}</td>'
        )
        purpose_cell = (
            f'<td style="padding:7px 12px 7px 6px; border-bottom:1px solid {_SAND}; '
            f'font-family:\'Times New Roman\',Times,serif; font-size:12px; '
            f'color:{p_color}; font-weight:500; vertical-align:top;">{_e(purpose)}</td>'
        )
        rows.append(f'<tr>{date_cell}{sym_cell}{co_cell}{purpose_cell}</tr>')

    thead = f'<tr>{_th("Date")}{_th("Symbol")}{_th("Company")}{_th("Purpose")}</tr>'
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
        f'style="border-collapse:collapse; font-family:\'Times New Roman\',Times,serif; font-size:12.5px;">'
        f'<thead>{thead}</thead><tbody>{"".join(rows)}</tbody></table>'
    )


def _ec_table_enhanced(df: pd.DataFrame) -> str:
    """Enhanced event calendar table with Symbol+Company+Agenda merged cell."""
    if df.empty:
        return (
            f'<p style="color:{_STONE}; font-style:italic; '
            f'font-family:\'Times New Roman\',Times,serif; font-size:13px; padding:0 36px;">'
            f'No entries.</p>'
        )

    rows: list[str] = []
    prev_date = None
    for _, row in df.iterrows():
        d       = str(row.get("meeting_date") or "")
        purpose = str(row.get("purpose") or "")
        symbol  = str(row.get("symbol") or "")
        company = str(row.get("company_name") or "")
        segment = str(row.get("segment") or "")
        desc    = str(row.get("description") or "")
        p_color = _purpose_color(purpose)

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

        star = _star_prefix(symbol, purpose, segment)
        nifty_b = _nifty_badge(symbol)
        short_desc = _clean_desc(desc, 60)

        sym_co_cell = (
            f'<td style="padding:7px 10px; border-bottom:1px solid {_SAND}; vertical-align:top;">'
            f'<div style="font-weight:600; color:{_INK}; font-size:13px; '
            f'font-family:\'Times New Roman\',Times,serif;">'
            f'{star}{_e(symbol)}{seg_badge}{nifty_b}</div>'
            f'<div style="font-style:italic; color:{_INK_SOFT}; font-size:11.5px; margin-top:2px; '
            f'font-family:\'Times New Roman\',Times,serif;">{_e(company)}</div>'
            + (
                f'<div style="color:{_STONE}; font-size:11px; margin-top:4px; line-height:1.45; '
                f'font-family:\'Times New Roman\',Times,serif;">{_e(short_desc)}</div>'
                if short_desc else ""
            )
            + f'</td>'
        )
        purpose_cell = (
            f'<td style="padding:7px 12px 7px 6px; border-bottom:1px solid {_SAND}; '
            f'font-family:\'Times New Roman\',Times,serif; font-size:12px; '
            f'color:{p_color}; font-weight:500; vertical-align:top;">{_e(purpose)}</td>'
        )
        rows.append(f'<tr>{date_cell}{sym_co_cell}{purpose_cell}</tr>')

    thead = f'<tr>{_th("Date")}{_th("Symbol / Company / Agenda")}{_th("Purpose")}</tr>'
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
        symbol  = str(row.get("symbol") or "")
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

        # Star prefix for rights and NIFTY members
        is_rights = "RIGHTS" in s_up
        star = ""
        if is_rights or symbol in _NIFTY50 or symbol in _NIFTY100_ONLY:
            star = f'<span style="color:{_BURGUNDY}; font-size:14px; margin-right:5px;">&#10022;</span>'

        nifty_b = _nifty_badge(symbol)

        rows.append(
            f'<tr>'
            f'<td style="padding:7px 10px 7px 12px; border-bottom:1px solid {_SAND}; '
            f'font-family:\'Times New Roman\',Times,serif; font-size:12px; '
            f'font-weight:600; color:{_BURGUNDY}; white-space:nowrap;">'
            f'{_e(_fmt_date(str(row.get("ex_date") or "")))}</td>'
            f'<td style="padding:7px 10px; border-bottom:1px solid {_SAND}; '
            f'font-weight:600; color:{_INK}; font-family:\'Times New Roman\',Times,serif; font-size:13px;">'
            f'{star}{_e(symbol)}{nifty_b}</td>'
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
    today: date | None = None,
) -> str:
    if today is None:
        today = report_date

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

    # Smart subtitles
    sub_ann = _metric_subtitle_ann(ann)
    sub_bm  = _metric_subtitle_bm(bm_filings)
    sub_ec  = _metric_subtitle_ec(ec)
    sub_ca  = _metric_subtitle_ca(ca)

    # Net notes for section headers
    bm_fund_n = sum(1 for p in bm_filings.get("purpose", pd.Series(dtype=str))
                    if "FUND RAIS" in str(p).upper()) if not bm_filings.empty else 0
    bm_delist_n = sum(1 for p in bm_filings.get("purpose", pd.Series(dtype=str))
                      if "VOLUNTARY DELIST" in str(p).upper()) if not bm_filings.empty else 0
    bm_net = f"{bm_fund_n} fund raises · {bm_delist_n} voluntary delistings among {n_bm} board actions" if n_bm else ""

    ec_results_n = sum(1 for p in ec.get("purpose", pd.Series(dtype=str))
                       if "FINANCIAL RESULTS" in str(p).upper()) if not ec.empty else 0
    ec_net = f"{ec_results_n} results dates on the forward schedule" if n_ec else ""

    n_divs = 0
    n_rights = 0
    if not ca.empty:
        for subj in ca.get("subject", pd.Series(dtype=str)):
            s = str(subj).upper()
            if "DIVIDEND" in s or "INTERIM" in s:
                n_divs += 1
            elif "RIGHTS" in s:
                n_rights += 1
    ca_net = f"{n_divs} dividend ex-dates · {n_rights} rights" if n_ca else ""

    # Section HTML
    bm_html      = _bm_table_enhanced(bm_filings)
    ec_html      = _ec_table_enhanced(ec)
    ca_html      = _corporate_actions_html(ca)
    key_ann_html = _announcements_html(ann, _HIGH_PRIORITY)
    other_html   = _announcements_html(ann, _MEDIUM_PRIORITY)
    debt_html    = _announcements_html(
        ann[ann["segment"] == "debt"] if not ann.empty and "segment" in ann.columns else ann,
        set(),
    )

    # New panels
    editorial_html   = _build_editorial(report_date, ann, bm_filings, ec, ca)
    bac_panel_html   = _bac_coverage_panel(ann, bm_filings, ec, ca)
    session_html     = _next_session_panel(today, bm_filings, ec, ca)
    movers_html      = _top_movers_panel(ann)
    separator_html   = _underlying_filings_separator()

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
            <td style="padding:0 10px; color:{_TAN}; font-size:12px;">&#9670;</td>
            <td style="font-family:'Times New Roman',Times,serif; font-size:11px;
              font-style:italic; color:{_STONE};">a daily note from the quant desk</td>
          </tr></table>
        </td>
        <td align="right" style="font-family:'Times New Roman',Times,serif; font-size:11px;
          color:{_STONE}; font-style:italic;">&#8470; {issue_num}</td>
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

  <!-- TODAY ON THE TAPE -->
  {editorial_html}

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
              Equity Filings</div>
            <div style="font-family:'Times New Roman',Times,serif; font-size:28px;
              font-weight:500; color:{_INK}; margin-top:6px; line-height:1;">{n_ann}</div>
            <div style="font-family:'Times New Roman',Times,serif; font-size:12px;
              color:{_STONE}; margin-top:6px; font-style:italic;">{sub_ann}</div>
          </td>
          <td width="25%" valign="top"
            style="padding:14px 12px 14px 14px; border-right:1px solid {_TAN};">
            <div style="font-family:'Times New Roman',Times,serif; font-size:9.5px;
              letter-spacing:0.24em; text-transform:uppercase; color:{_STONE}; font-weight:500;">
              Board Meetings</div>
            <div style="font-family:'Times New Roman',Times,serif; font-size:28px;
              font-weight:500; color:{_INK}; margin-top:6px; line-height:1;">{n_bm}</div>
            <div style="font-family:'Times New Roman',Times,serif; font-size:12px;
              color:{_STONE}; margin-top:6px; font-style:italic;">{sub_bm}</div>
          </td>
          <td width="25%" valign="top"
            style="padding:14px 12px 14px 14px; border-right:1px solid {_TAN};">
            <div style="font-family:'Times New Roman',Times,serif; font-size:9.5px;
              letter-spacing:0.24em; text-transform:uppercase; color:{_STONE}; font-weight:500;">
              Event Calendar</div>
            <div style="font-family:'Times New Roman',Times,serif; font-size:28px;
              font-weight:500; color:{_INK}; margin-top:6px; line-height:1;">{n_ec}</div>
            <div style="font-family:'Times New Roman',Times,serif; font-size:12px;
              color:{_STONE}; margin-top:6px; font-style:italic;">{sub_ec}</div>
          </td>
          <td width="25%" valign="top" style="padding:14px 18px 14px 14px;">
            <div style="font-family:'Times New Roman',Times,serif; font-size:9.5px;
              letter-spacing:0.24em; text-transform:uppercase; color:{_STONE}; font-weight:500;">
              Corporate Actions</div>
            <div style="font-family:'Times New Roman',Times,serif; font-size:28px;
              font-weight:500; color:{_INK}; margin-top:6px; line-height:1;">{n_ca}</div>
            <div style="font-family:'Times New Roman',Times,serif; font-size:12px;
              color:{_STONE}; margin-top:6px; font-style:italic;">{sub_ca}</div>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>

  <!-- BAC COVERAGE TOUCHPOINTS -->
  {bac_panel_html}

  <!-- NEXT TRADING SESSION -->
  {session_html}

  <!-- TOP MOVERS OVERLAY -->
  {movers_html}

  <!-- UNDERLYING FILINGS SEPARATOR -->
  {separator_html}

  <!-- i. BOARD MEETING FILINGS -->
  {_section_hdr("i", "Board Meeting Filings", "next 14 days",
    "Recent board meeting intimations filed with NSE — upcoming meetings and their agenda.",
    net_note=bm_net)}
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
    <tr><td style="padding:18px 36px 6px 36px;">{bm_html}</td></tr>
  </table>

  <!-- ii. EVENT CALENDAR -->
  {_section_hdr("ii", "Event Calendar", "full forward schedule",
    "NSE&#8217;s published event calendar — results dates, fund raises, and key board agendas scheduled weeks ahead.",
    net_note=ec_net)}
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
    <tr><td style="padding:18px 36px 6px 36px;">{ec_html}</td></tr>
  </table>

  <!-- iii. CORPORATE ACTIONS -->
  {_section_hdr("iii", "Corporate Actions", "ex-dates this week",
    "Dividends, bonuses, splits, and rights issues going ex in the next seven days.",
    net_note=ca_net)}
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
            NSE corporate filings &#8212; announcements, event calendar, board meetings, and corporate actions.<br>
            Write to <a href="mailto:bac@brindco.com"
              style="color:{_BURGUNDY}; text-decoration:none;">bac@brindco.com</a> with corrections.
          </div>
        </td>
      </tr></table>
      <div style="margin-top:18px; text-align:center; font-family:'Times New Roman',Times,serif;
        font-size:11px; font-style:italic; color:{_STONE};">&#9670;&nbsp;&nbsp;&nbsp;&#9670;&nbsp;&nbsp;&nbsp;&#9670;</div>
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

        html = _build_html(report_date, ann, bm_filings, ec, ca, generated_at, today=today)

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
