"""
Daily NSE Announcements email report.

Runs once per trading-day morning (Tue–Sat IST) at 10:00 IST. A separate
intraday scrape captures the day's announcements so the report is complete.

The email is a curated focus edition; the complete data ships as a PDF and a
CSV bundle. Responsibilities are split three ways:

  reports/assembly.py      which rows appear   (rows_body vs rows_all)
  reports/render_email.py  how they look       (BAC house style, design.py)
  this module              fetch, orchestrate, dispatch

Styling belongs in reports/design.py — the same module the daily deals report
renders through, so the two emails are one document family.

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

from reports import design, render_email
from reports import universes as U
from reports.assembly import Assembly, build_assembly
from reports.transforms import (
    category_contains, classify_corp_action, clean_cell, dedup_keep_order,
    find_near_duplicate_issuers, materiality_kind, normalize_currency,
    normalize_headline, normalize_purpose, score_tape_item,
    subtitle_corp_actions, subtitle_of_which, touchpoint_key, truncate,
)

logger = logging.getLogger("nse.announcements.report")

REPORT_TYPE = "daily_announcements_email"
_IST        = pytz.timezone("Asia/Kolkata")

# ─── Announcement category priority ──────────────────────────────────────────
# Section membership is decided in reports/assembly.py from config.yaml. These
# two sets survive only for the price-fetch shortlist in main() and the Slack
# digest, which both want "the categories a human would call interesting".
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
    "Credit Rating",
    "Annual Report",
}

_SAST_CATEGORY = "Disclosure under SEBI Takeover Regulations"


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"Missing required env var: {name}")
    return val


def _fmt_date(value) -> str:
    """'18-Aug' — the compact table dateline."""
    s = clean_cell(value)
    if not s:
        return ""
    try:
        return date.fromisoformat(s[:10]).strftime("%d-%b")
    except ValueError:
        return s


def _fmt_volume(vol) -> str:
    """Share volume in Indian units, with an explicit unit (issue 19)."""
    try:
        v = float(vol)
    except (TypeError, ValueError):
        return ""
    if v >= 1e7:
        return f"{v / 1e7:.1f}Cr sh"
    if v >= 1e5:
        return f"{v / 1e5:.1f}L sh"
    if v >= 1e3:
        return f"{v / 1e3:.0f}K sh"
    return f"{v:.0f} sh"


def _parse_dividend_amount(subject: str) -> float:
    """Pull the rupee amount out of a dividend subject line."""
    m = re.search(r"(?:Rs\.?|INR|&#8377;|₹)\s*([\d.]+)", clean_cell(subject), re.IGNORECASE)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return 0.0
    return 0.0


def _trailing_note(metric: str, current: int, report_date: date) -> str:
    """Issue 21 scaffold: ' — elevated' once the rolling store has history."""
    try:
        from reports.rolling_store import trailing_average, trend_label
        avg = trailing_average(metric, report_date)
        label = trend_label(current, avg)
        return f" — {label}" if label else ""
    except Exception:  # noqa: BLE001
        return ""


def _universe_of(symbol: str) -> str:
    """Tape-scoring tier. Delegates to reports/universes.py."""
    return U.universe_of(symbol)


def _next_trading_days(from_date: date, n: int = 3) -> list[date]:
    from utils import is_trading_day
    days: list[date] = []
    d = from_date + timedelta(days=1)
    while len(days) < n:
        if is_trading_day(d):
            days.append(d)
        d += timedelta(days=1)
    return days


def _rows(source) -> list[dict]:
    """Accept a row list or a DataFrame and return a row list."""
    if source is None:
        return []
    if isinstance(source, pd.DataFrame):
        return [] if source.empty else source.to_dict("records")
    return list(source)


# ─── Forward events (Next N Sessions) ────────────────────────────────────────

def _collect_session_events(
    bm_filings, ec, ca, next_days: list[date],
) -> list[dict]:
    """Unified, deduped list of events falling in the next sessions."""
    day_set = {d.isoformat() for d in next_days}
    events: list[dict] = []

    def _add(sym, seg, typ, text, day_iso, section_label, size=0.0):
        sym = clean_cell(sym)
        if not sym:
            return
        events.append({
            "symbol": sym, "segment": clean_cell(seg).lower(), "type": typ,
            "purpose": normalize_currency(text), "date": day_iso,
            "universe": _universe_of(sym), "kind": materiality_kind(text, size),
            "size": size, "section": section_label,
            "event_type": normalize_purpose(text),
        })

    for r in _rows(bm_filings):
        day = clean_cell(r.get("meeting_date"))
        if day in day_set:
            _add(r.get("symbol"), r.get("segment"), "Board Mtg",
                 r.get("purpose"), day, "Board Meetings")
    for r in _rows(ec):
        day = clean_cell(r.get("meeting_date"))
        if day in day_set:
            _add(r.get("symbol"), r.get("segment"), "Event",
                 r.get("purpose"), day, "Event Calendar")
    for r in _rows(ca):
        day = clean_cell(r.get("ex_date"))
        if day in day_set:
            subj = clean_cell(r.get("subject"))
            _add(r.get("symbol"), r.get("segment"), "Corp Action", subj, day,
                 "Corp Actions", size=_parse_dividend_amount(subj))

    # Dedupe on (symbol, event_type, date): the same fund raise filed under both
    # board_meetings and event_calendar collapses to one row.
    events = dedup_keep_order(
        events,
        key=lambda e: (clean_cell(e["symbol"]).upper(), e["event_type"], e["date"]))

    # …but when the two sources disagree on the DATE for one (symbol, event_type),
    # both rows survive and each is labelled with its source section, so the
    # discrepancy is visible rather than silently resolved in favour of whichever
    # source happened to be appended first.
    by_pair: dict[tuple, list[dict]] = {}
    for e in events:
        by_pair.setdefault((clean_cell(e["symbol"]).upper(), e["event_type"]), []).append(e)
    for group in by_pair.values():
        disagrees = len({e["date"] for e in group}) > 1
        for e in group:
            e["section_note"] = e["section"] if disagrees else ""
    return events


def _session_extras(events: list[dict]) -> tuple[list[dict], str, str]:
    """Split forward events into the body table plus its two caption lines.

    Returns (primary_events, headlines_text, also_text). The table is coverage +
    Pillar I only; everything else is counted in the "also on the tape" line.
    """
    primary = [e for e in events if e["universe"] in ("nifty50", "nifty100", "bac")]
    rest = [e for e in events if e["universe"] == "broader"]
    primary.sort(key=lambda e: (e["date"], -score_tape_item(e)))

    leads = sorted(primary, key=score_tape_item, reverse=True)[:3]
    headlines = ""
    if leads:
        parts = " · ".join(
            f'{_e(e["symbol"])} {_e(clean_cell(e["purpose"]))} ({_fmt_date(e["date"])})'
            for e in leads
        )
        headlines = f"<strong>Headlines:</strong> {parts}."

    also = ""
    if rest:
        kind_label = {
            "fund_raise": ("fund raise", "fund raises"),
            "results": ("result", "results"),
            "dividend": ("dividend", "dividends"),
            "large_dividend": ("dividend", "dividends"),
            "bonus": ("bonus", "bonuses"), "rights": ("rights issue", "rights issues"),
            "split": ("split", "splits"), "delisting": ("delisting", "delistings"),
            "analyst_meet": ("analyst meet", "analyst meets"),
            "agm_egm": ("shareholder meeting", "shareholder meetings"),
            "press_release": ("press release", "press releases"),
            "kmp_change": ("KMP change", "KMP changes"),
            "credit_rating": ("credit rating", "credit ratings"),
            "other": ("other event", "other events"),
        }
        by_label: dict[str, dict] = {}
        for e in rest:
            sing, plur = kind_label.get(
                e["kind"], (e["kind"].replace("_", " "), e["kind"].replace("_", " ") + "s"))
            g = by_label.setdefault(plur, {"count": 0, "sme": 0, "sing": sing})
            g["count"] += 1
            if e["segment"] == "sme":
                g["sme"] += 1
        bits = []
        for plur, g in sorted(by_label.items(), key=lambda kv: -kv[1]["count"]):
            label = g["sing"] if g["count"] == 1 else plur
            sme_note = f" ({g['sme']} SME)" if g["sme"] else ""
            bits.append(f'{g["count"]} {label}{sme_note}')
        also = ("Also across the broader market — " + " · ".join(bits)
                + ". Full list in the attachment.")
    return primary, headlines, also


# ─── Editorial ────────────────────────────────────────────────────────────────

def _tape_candidates(bm_filings, ec, ca) -> list[dict]:
    """Scored, deduped tape candidates across board meetings, events, actions."""
    cands: list[dict] = []
    for src, section in ((bm_filings, "board"), (ec, "event")):
        for row in _rows(src):
            sym = clean_cell(row.get("symbol"))
            if not sym:
                continue
            purpose = clean_cell(row.get("purpose"))
            cands.append({
                "symbol": sym, "date": clean_cell(row.get("meeting_date")),
                "universe": _universe_of(sym), "kind": materiality_kind(purpose),
                "size": 0.0, "purpose": purpose, "section": section,
            })
    for row in _rows(ca):
        sym = clean_cell(row.get("symbol"))
        if not sym:
            continue
        subject = clean_cell(row.get("subject"))
        amount = _parse_dividend_amount(subject)
        cands.append({
            "symbol": sym, "date": clean_cell(row.get("ex_date")),
            "universe": _universe_of(sym),
            "kind": materiality_kind(subject, amount),
            "size": amount, "purpose": subject, "section": "corp_action",
        })

    cands = dedup_keep_order(
        cands, key=lambda c: touchpoint_key(c["symbol"], c["date"], c["purpose"]))
    cands.sort(
        key=lambda c: (score_tape_item(c), c["universe"] == "nifty50", c["date"] or "9999"),
        reverse=True,
    )
    return cands


def _tape_clause(c: dict) -> str:
    """One scored tape lead as an editorial clause (no symbol — that's the head)."""
    d_label = _fmt_date(c["date"])
    kind = c["kind"]
    if kind == "delisting":
        return f"convenes {d_label} on voluntary delisting"
    if kind == "fund_raise":
        verb = "board meets" if c["section"] == "board" else "scheduled"
        return f"{verb} {d_label} on fund raising"
    if kind in ("large_dividend", "dividend"):
        amt = f"&#8377;{c['size']:g} " if c["size"] else ""
        return f"{amt}ex-dividend {d_label}"
    if kind == "bonus":
        ratio = re.search(r"(\d+\s*:\s*\d+)", c["purpose"])
        rr = f" {ratio.group(1).replace(' ', '')}" if ratio else ""
        return f"bonus{rr} ex-date {d_label}"
    if kind == "rights":
        return f"rights issue ex-date {d_label}"
    if kind == "split":
        return f"stock split ex-date {d_label}"
    if kind == "results":
        return f"results {d_label}"
    return f"{_e(truncate(c['purpose'], 70))} &middot; {d_label}"


def _editorial_items(
    report_date: date, ann, bm_filings, ec, ca,
) -> list[tuple[str, str]]:
    """Three numbered editorial items for the gold 'things that matter' callout.

    Returns (headline, body) pairs — the shape design.numbered_list expects.
    """
    items: list[tuple[str, str]] = []

    # 1 — top of the forward tape
    cands = _tape_candidates(bm_filings, ec, ca)[:3]
    if cands:
        clauses = dedup_keep_order(
            [f"<strong>{_e(c['symbol'])}</strong> {_tape_clause(c)}" for c in cands],
            key=lambda s: normalize_headline(re.sub(r"<[^>]+>", "", s)),
        )
        items.append(("On the forward tape &mdash;", " &middot; ".join(clauses) + "."))
    else:
        items.append((
            "On the forward tape &mdash;",
            f"{len(_rows(bm_filings))} board-meeting intimations over the next "
            f"fortnight; {len(_rows(ec))} scheduled proceedings ahead.",
        ))

    # 2 — corporate actions, clustered by ex-date
    ca_rows = _rows(ca)
    if ca_rows:
        divs = [r for r in ca_rows
                if classify_corp_action(r.get("subject")) == "dividend"]
        by_date: dict[str, int] = {}
        for r in divs:
            by_date[clean_cell(r.get("ex_date"))] = by_date.get(
                clean_cell(r.get("ex_date")), 0) + 1
        top = max(divs, key=lambda r: _parse_dividend_amount(r.get("subject")),
                  default=None)
        bits = [f"<strong>{len(ca_rows)}</strong> ex-dates this week"]
        if by_date:
            busiest = max(by_date.items(), key=lambda kv: kv[1])
            bits.append(f"heaviest on {_fmt_date(busiest[0])} with {busiest[1]}")
        if top is not None and _parse_dividend_amount(top.get("subject")):
            bits.append(
                f"<strong>{_e(clean_cell(top.get('symbol')))}</strong> leads at "
                f"&#8377;{_parse_dividend_amount(top.get('subject')):g}")
        items.append(("Corporate actions &mdash;", " &middot; ".join(bits) + "."))
    else:
        items.append(("Corporate actions &mdash;", "None recorded for this period."))

    # 3 — what actually landed yesterday
    ann_rows = _rows(ann)
    n_results = sum(1 for r in ann_rows
                    if "financial result" in clean_cell(r.get("category")).lower())
    n_engage = sum(1 for r in ann_rows if category_contains(
        r.get("category"), ("analyst", "investor meet", "investor presentation")))
    n_sme = sum(1 for r in ann_rows if clean_cell(r.get("segment")).lower() == "sme")
    covered = dedup_keep_order(
        [clean_cell(r.get("symbol")) for r in ann_rows
         if U.is_coverage(clean_cell(r.get("symbol")))],
        key=lambda s: s,
    )
    body = (f"<strong>{len(ann_rows)}</strong> filings — "
            f"<strong>{n_results}</strong> results"
            + (f" ({n_sme} SME)" if n_sme else "")
            + f"; <strong>{n_engage}</strong> engagement.")
    if covered:
        body += (" BAC coverage active: "
                 + ", ".join(f"<strong>{_e(s)}</strong>" for s in covered[:5]) + ".")
    items.append(("Yesterday&#8217;s filings &mdash;", body))
    return items


# ─── Coverage touchpoints (prose) ────────────────────────────────────────────

def _coverage_prose(assembly, bm_filings, ec, ca) -> tuple[str, str]:
    """The two prose lines that replaced the touchpoints table.

    Returns (active_line, pillar_line).
    """
    displayed: set[str] = set()
    for key in ("key_announcements", "other_announcements"):
        for r in assembly.section(key).rows_body:
            sym = clean_cell(r.get("symbol"))
            if sym:
                displayed.add(sym)

    bm_syms = {clean_cell(r.get("symbol")) for r in _rows(bm_filings)}
    ec_syms = {clean_cell(r.get("symbol")) for r in _rows(ec)}
    ca_syms = {clean_cell(r.get("symbol")) for r in _rows(ca)}

    hits: list[str] = []
    for sym in sorted(U.BAC_COVERAGE):
        found = []
        if sym in displayed:
            found.append("Announcements")
        if sym in bm_syms:
            found.append("Board Meetings")
        if sym in ec_syms:
            found.append("Event Calendar")
        if sym in ca_syms:
            found.append("Corp Actions")
        if found:
            hits.append(
                f'<strong>{_e(sym)}</strong> '
                f'<span style="color:{design.INK_FAINT};">({", ".join(found)})</span>')

    active = (
        "<strong>Active coverage on the tape:</strong> " + ", ".join(hits) + "."
        if hits else
        "<strong>Active coverage on the tape:</strong> the BAC active universe is "
        "silent today."
    )

    # Pillar I counts come from the same deduped event set the forward table uses,
    # so the headline number and the table below cannot disagree.
    pillar_events = dedup_keep_order(
        [
            {"symbol": clean_cell(r.get("symbol")),
             "date": clean_cell(r.get(dcol)),
             "purpose": clean_cell(r.get(pcol))}
            for src, dcol, pcol in (
                (bm_filings, "meeting_date", "purpose"),
                (ec, "meeting_date", "purpose"),
                (ca, "ex_date", "subject"),
            )
            for r in _rows(src)
            if U.is_pillar1(clean_cell(r.get("symbol")))
        ],
        key=lambda e: touchpoint_key(e["symbol"], e["date"], e["purpose"]),
    )
    n50 = sum(1 for e in pillar_events if e["symbol"] in U.NIFTY50)
    n100 = len(pillar_events) - n50
    pillar = (
        f"<strong>Pillar I overlay:</strong> {n50} NIFTY50 "
        f'touchpoint{"" if n50 == 1 else "s"} &amp; {n100} NIFTY100 '
        f'touchpoint{"" if n100 == 1 else "s"} across board meetings, the event '
        f"calendar and corporate actions. Forward detail in the table below."
    )
    return active, pillar


# ─── Top movers ───────────────────────────────────────────────────────────────

def _movers_data(ann, prices: dict[str, dict] | None) -> tuple[list[dict], str, str]:
    """Price-overlay rows. Returns (movers, col2_header, subtitle)."""
    prices = prices or {}
    movers: list[dict] = []
    seen: set[str] = set()
    for row in _rows(ann):
        sym = clean_cell(row.get("symbol"))
        if not sym or sym in seen:
            continue
        p = prices.get(sym)
        if not p:
            continue
        close, prev_close = p.get("close"), p.get("prev_close")
        # Drop rows with no usable price rather than printing an em-dash row.
        if not (close and prev_close and prev_close > 0):
            continue
        seen.add(sym)
        movers.append({
            "sym": sym, "seg": clean_cell(row.get("segment")),
            "cat": clean_cell(row.get("category")),
            "summary": clean_cell(row.get("summary")),
            "close": close, "chg": (close - prev_close) / prev_close * 100,
            "vol_str": _fmt_volume(p.get("volume")),
        })

    movers.sort(key=lambda m: abs(m["chg"]), reverse=True)
    movers = movers[:12]

    # When every row shares one filing type, promote it to the subtitle and give
    # the column over to the underlying summary instead of repeating the type.
    kinds = {m["cat"] for m in movers}
    if movers and len(kinds) == 1:
        subtitle = next(iter(kinds))
        col2 = "Filing Summary"
        for m in movers:
            m["col2"] = m["summary"]
    else:
        subtitle = "yesterday&#8217;s filings cross-tagged with price action"
        col2 = "Filing"
        for m in movers:
            m["col2"] = m["cat"]
    return movers, col2, subtitle


# ─── Topline metric cards ─────────────────────────────────────────────────────

def _metric_subtitle_ann(ann) -> str:
    rows = _rows(ann)
    if not rows:
        return "no filings"
    n_results = sum(1 for r in rows
                    if "financial result" in clean_cell(r.get("category")).lower())
    n_engage = sum(1 for r in rows if category_contains(
        r.get("category"), ("analyst", "investor presentation")))
    return subtitle_of_which(len(rows), [(n_results, "results"), (n_engage, "engagement")])


def _metric_subtitle_bm(bm_filings) -> str:
    rows = _rows(bm_filings)
    if not rows:
        return "none scheduled"
    n_fund = sum(1 for r in rows if "FUND RAIS" in clean_cell(r.get("purpose")).upper())
    n_delist = sum(1 for r in rows
                   if "DELIST" in clean_cell(r.get("purpose")).upper())
    return subtitle_of_which(len(rows), [(n_fund, "fund raises"), (n_delist, "delistings")])


def _metric_subtitle_ec(ec) -> str:
    rows = _rows(ec)
    if not rows:
        return "no events ahead"
    n_results = sum(1 for r in rows
                    if "FINANCIAL RESULTS" in clean_cell(r.get("purpose")).upper())
    n_fund = sum(1 for r in rows if "FUND RAIS" in clean_cell(r.get("purpose")).upper())
    return subtitle_of_which(len(rows), [(n_results, "results"), (n_fund, "fund raises")])


def _metric_subtitle_ca(ca) -> str:
    rows = _rows(ca)
    if not rows:
        return "none this week"
    return subtitle_corp_actions(rows)


def _kpi_cards(assembly, report_date: date, ann, bm_filings, ec, ca) -> list[dict]:
    c = assembly.counts
    specs = [
        ("Equity Filings", c.get("equity_filings", 0),
         _metric_subtitle_ann(ann), "equity_filings"),
        ("Board Meetings", c.get("board_meetings", 0),
         _metric_subtitle_bm(bm_filings), "board_meetings"),
        ("Event Calendar", c.get("event_calendar", 0),
         _metric_subtitle_ec(ec), "event_calendar"),
        ("Corporate Actions", c.get("corporate_actions", 0),
         _metric_subtitle_ca(ca), "corporate_actions"),
    ]
    return [
        {"label": label, "value": f"{value:,}",
         "sub": sub + _trailing_note(metric, value, report_date)}
        for label, value, sub, metric in specs
    ]


def _scope_note(assembly) -> str:
    """The justified grey paragraph under the masthead."""
    total_all = sum(s.n_all for s in assembly.sections.values())
    total_body = sum(s.n_body for s in assembly.sections.values())
    return (
        f"Scoped to the BAC coverage book, the NIFTY50/100 overlay and the "
        f"NIFTY500. {total_body} of today&#8217;s {total_all:,} rows are shown "
        f"below; the complete record is attached as a PDF and a CSV bundle."
    )


# ─── Price fetch ─────────────────────────────────────────────────────────────

def _fetch_prices(symbols: list[str], as_of_date: date, lookback: int = 7) -> dict[str, dict]:
    """Most recent close, prev_close and volume per symbol within the lookback."""
    if not symbols:
        return {}
    from database.client import get_client
    client = get_client()
    out: dict[str, dict] = {}
    from_date = (as_of_date - timedelta(days=lookback)).isoformat()
    # in_() has a URL-length ceiling; chunk the symbol list.
    for i in range(0, len(symbols), 200):
        chunk = symbols[i:i + 200]
        try:
            resp = (
                client.table("daily_prices")
                .select("symbol,close,prev_close,volume,value_cr,trade_date")
                .gte("trade_date", from_date)
                .lte("trade_date", as_of_date.isoformat())
                .eq("series", "EQ").in_("symbol", chunk)
                .order("trade_date", desc=True).execute()
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Price fetch failed for a chunk: %s", exc)
            continue
        for r in resp.data or []:
            out.setdefault(r["symbol"], r)
    return out


# ─── Full HTML assembly ───────────────────────────────────────────────────────

def _build_html(
    report_date: date,
    ann: pd.DataFrame,
    bm_filings: pd.DataFrame,
    ec: pd.DataFrame,
    ca: pd.DataFrame,
    generated_at: datetime,
    today: date | None = None,
    prices: dict[str, dict] | None = None,
    assembly: "Assembly | None" = None,
    attachment_note: str = "",
) -> str:
    """Compose the email. Data decisions live in assembly; styling in render_email."""
    today = today or report_date
    prices = prices or {}
    if assembly is None:
        assembly = build_assembly(report_date, ann, bm_filings, ec, ca, today=today)

    # Issue 9: flag likely issuer-name parse errors for manual review.
    names = [clean_cell(r.get("company_name")) for r in _rows(ann)]
    for a, b, dist in find_near_duplicate_issuers(names, threshold=2):
        logger.warning("Possible issuer-name mismatch (edit distance %d): %r vs %r", dist, a, b)

    session_days = _next_trading_days(today, assembly.config.next_sessions.count)
    events = _collect_session_events(bm_filings, ec, ca, session_days)
    primary, headlines, also = _session_extras(events)

    movers, movers_col2, movers_subtitle = _movers_data(ann, prices)
    coverage_active, coverage_pillar = _coverage_prose(assembly, bm_filings, ec, ca)

    sast_rows = [
        r for r in assembly.section("key_announcements").rows_all
        if clean_cell(r.get("category")) == _SAST_CATEGORY
        and (U.is_coverage(clean_cell(r.get("symbol")))
             or U.is_pillar1(clean_cell(r.get("symbol"))))
    ]

    gen_ist = generated_at.astimezone(_IST)
    issue_num = max(1, int((report_date - date(2026, 1, 1)).days * 5 / 7))

    return render_email.build_email_html(
        report_date=report_date,
        today=today,
        assembly=assembly,
        generated_at=gen_ist,
        issue_num=issue_num,
        kpis=_kpi_cards(assembly, report_date, ann, bm_filings, ec, ca),
        editorial_items=_editorial_items(report_date, ann, bm_filings, ec, ca),
        scope_note=_scope_note(assembly),
        coverage_active=coverage_active,
        coverage_pillar=coverage_pillar,
        session_events=primary,
        session_days=session_days,
        session_headlines=headlines,
        session_also=also,
        movers=movers,
        movers_col2=movers_col2,
        movers_subtitle=movers_subtitle,
        sast_rows=sast_rows,
        attachment_note=attachment_note,
    )


# ─── Email ────────────────────────────────────────────────────────────────────

def _send_email(*, sender, password, sender_name, recipients, subject, html,
                attachments: list[tuple[str, bytes, str]] | None = None):
    """Send the report. ``attachments`` is a list of (filename, bytes, mimetype)."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"]    = f"{sender_name} <{sender}>"
    msg["To"]      = ", ".join(recipients)
    msg.set_content("This report requires an HTML-capable email client.")
    msg.add_alternative(html, subtype="html")

    for filename, payload, mimetype in (attachments or []):
        maintype, _, subtype = mimetype.partition("/")
        msg.add_attachment(payload, maintype=maintype or "application",
                           subtype=subtype or "octet-stream", filename=filename)

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

        # Fetch prices for key announcement symbols
        movers_syms: list[str] = []
        if not ann.empty:
            key_ann = ann[ann["category"].isin(_HIGH_PRIORITY | _MEDIUM_PRIORITY)]
            if "symbol" in key_ann.columns:
                movers_syms = [s for s in key_ann["symbol"].dropna().unique().tolist() if s]
        prices = _fetch_prices(movers_syms, today) if movers_syms else {}
        logger.info("Fetched prices for %d/%d movers symbols", len(prices), len(movers_syms))

        # One assembly feeds both the curated body and the complete attachments.
        assembly = build_assembly(report_date, ann, bm_filings, ec, ca, today=today)

        from reports.attachments import ATTACHMENT_NOTICE, build_attachments
        attachments = build_attachments(assembly, generated_at=generated_at)
        # Plain text, not markup: it is appended into the colophon's provenance
        # sentence, so an embedded <p> would render at the wrong size.
        note = ATTACHMENT_NOTICE if attachments else ""

        html = _build_html(report_date, ann, bm_filings, ec, ca, generated_at,
                           today=today, prices=prices, assembly=assembly,
                           attachment_note=note)

        if preview_mode:
            with open(preview_path, "w", encoding="utf-8") as fh:
                fh.write(html)
            logger.info("Preview saved to %s", preview_path)
            for filename, payload, _mime in attachments:
                side = os.path.join(os.path.dirname(os.path.abspath(preview_path)), filename)
                with open(side, "wb") as fh:
                    fh.write(payload)
                logger.info("Preview attachment saved to %s (%d bytes)", side, len(payload))
            return 0

        pretty_date = report_date.strftime("%d %b %Y")
        _send_email(
            sender=smtp_user, password=smtp_password, sender_name=sender_name,
            recipients=recipients,
            subject=f"BAC Announcements — NSE — {pretty_date}",
            html=html,
            attachments=attachments,
        )
        logger.info("Attached: %s", [n for n, _, _ in attachments] or "none")
        _mark_sent(report_date)
        logger.info("Sent to %s", recipients)

        # Issue 21 (scaffold): record today's headline counts for trailing avgs.
        try:
            from reports.rolling_store import record_metrics
            record_metrics(report_date, {
                "equity_filings": len(ann), "board_meetings": len(bm_filings),
                "event_calendar": len(ec), "corporate_actions": len(ca),
            })
        except Exception as exc:  # noqa: BLE001
            logger.debug("rolling_store record skipped: %s", exc)

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
