"""
Assembly layer — the single source of truth for every report section.

The report used to go straight from raw DataFrames to HTML, with each renderer
doing its own filtering, dedup and row-capping inline. That made it impossible
for one dataset to render two ways. This module extracts that decision-making:

    build_assembly(...) -> Assembly
        .sections["board_meetings"].rows_all    # every row  -> PDF + CSV
        .sections["board_meetings"].rows_body   # curated    -> email
        .sections["board_meetings"].rollup      # "+N further ..." count line

``rows_all`` is never gated by config. Whatever the materiality rules drop from
``rows_body`` is guaranteed to still be in ``rows_all``, so the attachments are
complete by construction rather than by discipline.

COMPLETENESS INVARIANT
    len(key_announcements.rows_all)
  + len(other_announcements.rows_all)
  + len(debt_market.rows_all)
  == number of announcement rows fetched

Section v therefore carries every equity filing section iv did not claim —
including categories the old _HIGH_PRIORITY / _MEDIUM_PRIORITY sets routed to
neither section and silently dropped from the report entirely. Those rows now
reach the attachments even though they stay out of the body.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

from reports import universes as U
from reports.report_config import ReportConfig, load_config
from reports.transforms import (
    announcement_key, category_contains, category_matches, classify_corp_action,
    clean_cell, count_line, dedup_keep_order, group_debt_rows,
    is_payload_free_outcome, is_substantive_press_release,
    merge_same_day_filings, other_announcement_bucket,
)

logger = logging.getLogger(__name__)

SECTION_TITLES = {
    "board_meetings": "Board Meeting Filings",
    "event_calendar": "Event Calendar",
    "corporate_actions": "Corporate Actions",
    "key_announcements": "Key Announcements",
    "other_announcements": "Other Announcements",
    "debt_market": "Debt Market",
}
SECTION_ORDER = tuple(SECTION_TITLES)


@dataclass
class SectionData:
    """One report section, in both its unfiltered and curated forms."""
    key: str
    title: str
    rows_all: list[dict] = field(default_factory=list)
    rows_body: list[dict] = field(default_factory=list)
    rollup: str = ""
    meta: dict = field(default_factory=dict)
    n_truncated: int = 0

    @property
    def n_all(self) -> int:
        return len(self.rows_all)

    @property
    def n_body(self) -> int:
        return len(self.rows_body)

    @property
    def n_dropped(self) -> int:
        return max(0, self.n_all - self.n_body)


def _apply_cap(sec: SectionData, cap: int) -> SectionData:
    """Bound a section's body rows, recording how many were cut.

    The materiality filters alone do not bound a results-season day: on
    07 Aug 2026 the event calendar still leaves 137 in-universe rows across five
    sessions. The cap is the second lever, and it is safe precisely because the
    attachments are built from ``rows_all`` — a capped row is deferred, never
    lost. Caps are generous enough that an ordinary day is untouched.
    """
    if cap and len(sec.rows_body) > cap:
        sec.n_truncated = len(sec.rows_body) - cap
        sec.rows_body = sec.rows_body[:cap]
    return sec


def _with_truncation_note(sec: SectionData) -> SectionData:
    """Fold any cap overflow into the section's rollup line."""
    if not sec.n_truncated:
        return sec
    note = f"{sec.n_truncated} more not shown"
    sec.rollup = f"{sec.rollup.replace(' — see attachment', '')} · {note} — see attachment" \
        if sec.rollup else f"{note} — see attachment"
    return sec


@dataclass
class Assembly:
    report_date: date
    today: date
    sections: dict[str, SectionData]
    counts: dict[str, int]
    config: ReportConfig

    def section(self, key: str) -> SectionData:
        return self.sections[key]


# ─── helpers ──────────────────────────────────────────────────────────────────

def trading_sessions(from_date: date, n: int) -> list[date]:
    """The next ``n`` trading days strictly after ``from_date``."""
    from utils import is_trading_day
    out: list[date] = []
    d = from_date + timedelta(days=1)
    while len(out) < n:
        if is_trading_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def _records(df: pd.DataFrame | None) -> list[dict]:
    if df is None or (hasattr(df, "empty") and df.empty):
        return []
    return df.to_dict("records")


def _sym(row: dict) -> str:
    return clean_cell(row.get("symbol"))


def _is_sme(row: dict) -> bool:
    return clean_cell(row.get("segment")).lower() == "sme"


def _plural(n: int, sing: str, plur: str | None = None) -> str:
    return sing if n == 1 else (plur or sing + "s")


# ─── i. Board Meetings ────────────────────────────────────────────────────────

def _build_board_meetings(rows: list[dict], cfg: ReportConfig) -> SectionData:
    c = cfg.board_meetings
    body: list[dict] = []
    for r in rows:
        sym = _sym(r)
        purpose_up = clean_cell(r.get("purpose")).upper()

        # Fund raises and delistings are material at any size, any universe.
        always = any(k.upper() in purpose_up for k in c.always_keep_purpose_contains)

        # Routine "Other business matters" from a non-coverage SME is noise.
        drop_sme_routine = (
            _is_sme(r)
            and not U.is_coverage(sym)
            and any(clean_cell(p).upper() == purpose_up
                    for p in c.drop_purpose_for_non_coverage_sme)
        )

        if always or (U.in_body_universe(sym) and not drop_sme_routine):
            body.append(r)

    # Identity, not equality: two board meetings can be value-identical dicts,
    # and `r not in body` would drop both.
    shown = {id(r) for r in body}
    dropped = [r for r in rows if id(r) not in shown]
    n_sme = sum(1 for r in dropped if _is_sme(r))
    rollup = ""
    if dropped:
        rollup = (
            f"+{len(dropped)} further board {_plural(len(dropped), 'meeting')}"
            + (f" incl. {n_sme} SME" if n_sme else "")
            + " — see attachment"
        )
    return SectionData("board_meetings", SECTION_TITLES["board_meetings"],
                       rows_all=rows, rows_body=body, rollup=rollup,
                       meta={"sme_dropped": n_sme})


# ─── ii. Event Calendar ───────────────────────────────────────────────────────

def _build_event_calendar(rows: list[dict], cfg: ReportConfig, today: date) -> SectionData:
    horizon = cfg.event_calendar.horizon_sessions
    sessions = trading_sessions(today, horizon)
    cutoff = sessions[-1] if sessions else today

    body = [
        r for r in rows
        if U.in_body_universe(_sym(r))
        and clean_cell(r.get("meeting_date")) <= cutoff.isoformat()
    ]

    n_drop = len(rows) - len(body)
    rollup = ""
    if n_drop:
        beyond = sum(
            1 for r in rows
            if clean_cell(r.get("meeting_date")) > cutoff.isoformat()
        )
        bits = {}
        off_universe = n_drop - beyond
        if off_universe > 0:
            bits["outside the coverage universe"] = off_universe
        if beyond:
            bits[f"beyond session {horizon}"] = beyond
        inner = count_line(bits)
        rollup = (
            f"Also on the tape — {n_drop} further scheduled "
            f"{_plural(n_drop, 'event')}"
            + (f" ({inner})" if inner else "")
            + " — see attachment"
        )
    return SectionData("event_calendar", SECTION_TITLES["event_calendar"],
                       rows_all=rows, rows_body=body, rollup=rollup,
                       meta={"cutoff": cutoff.isoformat(), "sessions": horizon})


# ─── iii. Corporate Actions ───────────────────────────────────────────────────

def _build_corporate_actions(rows: list[dict], cfg: ReportConfig) -> SectionData:
    keep_kinds = {k.lower() for k in cfg.corporate_actions.non_universe_keep_kinds}
    body: list[dict] = []
    for r in rows:
        if U.in_body_universe(_sym(r)):
            body.append(r)
            continue
        if classify_corp_action(r.get("subject")) in keep_kinds:
            body.append(r)

    shown = {id(r) for r in body}
    dropped = [r for r in rows if id(r) not in shown]
    rollup = ""
    if dropped:
        n_div = sum(1 for r in dropped if classify_corp_action(r.get("subject")) == "dividend")
        bits = {}
        if n_div:
            bits[f"small-cap {_plural(n_div, 'dividend')}"] = n_div
        rest = len(dropped) - n_div
        if rest:
            bits[_plural(rest, "other action")] = rest
        rollup = f"+{len(dropped)} further ({count_line(bits)}) — see attachment"
    return SectionData("corporate_actions", SECTION_TITLES["corporate_actions"],
                       rows_all=rows, rows_body=body, rollup=rollup)


# ─── iv / v. Announcements split ──────────────────────────────────────────────

def _is_key_category(row: dict, cfg: ReportConfig) -> bool:
    """Section-iv membership: whitelist by category, before any universe gate."""
    c = cfg.key_announcements
    cat = row.get("category")
    if category_matches(cat, c.record_date_categories):
        return True
    return category_matches(cat, c.categories) or category_contains(cat, c.category_contains)


def _build_key_announcements(rows: list[dict], cfg: ReportConfig) -> SectionData:
    """rows_all = every equity filing whose category belongs to section iv.

    rows_all is a faithful dump — no dedup, no cap — so the CSV reproduces the
    source exactly. Dedup happens on the body path only.
    """
    c = cfg.key_announcements
    rows_all = rows
    deduped = dedup_keep_order(
        rows,
        key=lambda r: announcement_key(r.get("symbol"), r.get("company_name"), r.get("summary")),
    )

    body: list[dict] = []
    for r in deduped:
        # Record dates are high-volume across the small-cap tail; keep only the
        # ones that touch a name we follow.
        if (c.record_date_universe_only
                and category_matches(r.get("category"), c.record_date_categories)
                and not U.in_body_universe(_sym(r))):
            continue
        # "Outcome of Board Meeting held on <date>." with no other content adds
        # nothing the section header does not already say.
        if c.drop_payload_free_outcomes and is_payload_free_outcome(r.get("summary")):
            continue
        body.append(r)

    n_pre_merge = len(body)
    body = merge_same_day_filings(
        body, join=cfg.merge.join, max_chars=cfg.merge.summary_max_chars)

    rollup = ""
    n_drop = len(rows_all) - n_pre_merge
    n_merged = n_pre_merge - len(body)
    bits = {}
    if n_drop:
        bits["filtered out"] = n_drop
    if n_merged:
        bits["merged into multi-filing rows"] = n_merged
    if bits:
        rollup = f"{count_line(bits)} — full detail in the attachment"

    return SectionData("key_announcements", SECTION_TITLES["key_announcements"],
                       rows_all=rows_all, rows_body=body, rollup=rollup,
                       meta={"pre_merge": n_pre_merge, "deduped": len(deduped)})


def _build_other_announcements(rows: list[dict], cfg: ReportConfig) -> SectionData:
    """rows_all = every equity filing section iv did not claim.

    This is deliberately wider than the old _MEDIUM_PRIORITY set, which routed
    General Updates, newspaper copies and similar to nothing at all — they never
    appeared in the report. They now reach the attachments.
    """
    c = cfg.other_announcements
    rows_all = rows
    deduped = dedup_keep_order(
        rows,
        key=lambda r: announcement_key(r.get("symbol"), r.get("company_name"), r.get("summary")),
    )

    selected: list[dict] = []
    for r in deduped:
        sym = _sym(r)
        if U.is_coverage(sym):
            selected.append(r)
            continue
        if (c.include_substantive_press_releases
                and is_substantive_press_release(r.get("category"), r.get("summary"))):
            selected.append(r)

    # Capture identity BEFORE merging: merge_same_day_filings builds new dicts,
    # so ids taken from its output would match nothing in rows_all.
    shown = {id(r) for r in selected}
    n_pre_merge = len(selected)
    body = merge_same_day_filings(
        selected, join=cfg.merge.join, max_chars=cfg.merge.summary_max_chars)

    # Categorical count line over everything not rendered as a row.
    buckets: dict[str, int] = {}
    for r in rows_all:
        if id(r) in shown:
            continue
        label = other_announcement_bucket(r.get("category"), r.get("summary"))
        buckets[label] = buckets.get(label, 0) + 1
    ordered = dict(sorted(buckets.items(), key=lambda kv: -kv[1]))
    rollup = count_line(ordered, tail=" — see attachment")

    return SectionData("other_announcements", SECTION_TITLES["other_announcements"],
                       rows_all=rows_all, rows_body=body, rollup=rollup,
                       meta={"buckets": ordered, "pre_merge": n_pre_merge,
                             "deduped": len(deduped)})


# ─── vi. Debt Market ──────────────────────────────────────────────────────────

def _build_debt_market(rows: list[dict], cfg: ReportConfig) -> SectionData:
    c = cfg.debt_market
    body: list[dict] = []
    for r in rows:
        is_rating = category_contains(r.get("category"), c.credit_rating_category_contains)
        if c.credit_rating_any_issuer and is_rating:
            body.append(r)
            continue
        # Debt rows usually carry a null symbol; this fires for the issuers that
        # do resolve to a ticker we follow.
        if U.in_body_universe(_sym(r)):
            body.append(r)

    shown = {id(r) for r in body}
    rest = [r for r in rows if id(r) not in shown]
    groups = group_debt_rows(rest) if rest else []
    rollup = ""
    if rest:
        rollup = (
            f"{len(rest)} further debt {_plural(len(rest), 'filing')} collapsed into "
            f"{len(groups)} issuer/payment {_plural(len(groups), 'group')} — see attachment"
        )
    return SectionData("debt_market", SECTION_TITLES["debt_market"],
                       rows_all=rows, rows_body=body, rollup=rollup,
                       meta={"groups": groups, "rest": rest})


# ─── entry point ──────────────────────────────────────────────────────────────

def build_assembly(
    report_date: date,
    ann: pd.DataFrame,
    bm_filings: pd.DataFrame,
    ec: pd.DataFrame,
    ca: pd.DataFrame,
    today: date | None = None,
    config: ReportConfig | None = None,
) -> Assembly:
    """Turn the four fetched frames into six two-form sections."""
    today = today or report_date
    cfg = config or load_config()

    ann_rows = _records(ann)
    equity = [r for r in ann_rows if clean_cell(r.get("segment")).lower() != "debt"]
    debt = [r for r in ann_rows if clean_cell(r.get("segment")).lower() == "debt"]

    key_rows = [r for r in equity if _is_key_category(r, cfg)]
    key_ids = {id(r) for r in key_rows}
    other_rows = [r for r in equity if id(r) not in key_ids]

    sections = {
        "board_meetings": _build_board_meetings(_records(bm_filings), cfg),
        "event_calendar": _build_event_calendar(_records(ec), cfg, today),
        "corporate_actions": _build_corporate_actions(_records(ca), cfg),
        "key_announcements": _build_key_announcements(key_rows, cfg),
        "other_announcements": _build_other_announcements(other_rows, cfg),
        "debt_market": _build_debt_market(debt, cfg),
    }

    caps = {
        "board_meetings": cfg.board_meetings.body_cap,
        "event_calendar": cfg.event_calendar.body_cap,
        "corporate_actions": cfg.corporate_actions.body_cap,
        "key_announcements": cfg.key_announcements.body_cap,
        "other_announcements": cfg.other_announcements.body_cap,
        "debt_market": cfg.debt_market.body_cap,
    }
    for key, cap in caps.items():
        _with_truncation_note(_apply_cap(sections[key], cap))

    counts = {
        "equity_filings": len(ann_rows),
        "board_meetings": len(_records(bm_filings)),
        "event_calendar": len(_records(ec)),
        "corporate_actions": len(_records(ca)),
    }

    asm = Assembly(report_date=report_date, today=today, sections=sections,
                   counts=counts, config=cfg)

    for k, s in sections.items():
        logger.info("Section %-20s all=%-5d body=%-4d dropped=%d",
                    k, s.n_all, s.n_body, s.n_dropped)
    return asm
