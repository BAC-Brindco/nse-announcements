"""
Tests for the assembly layer, the body filters and the attachment builders.

The fixture-backed tests are skipped when tests/fixtures/<name>/ is absent, so a
clean checkout still runs the pure-logic tests.
"""

from __future__ import annotations

import csv
import io
import json
import os
import sys
import zipfile
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from reports import universes as U
from reports.assembly import SECTION_ORDER, build_assembly, trading_sessions
from reports.attachments.csv_bundle import CSV_COLUMNS, SECTION_FILENAMES, build_csv_bundle
from reports.transforms import (
    category_matches, count_line, is_payload_free_outcome, is_routine_pr,
    is_substantive_press_release, merge_same_day_filings,
    other_announcement_bucket,
)

_FIXROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


# ═══ Universes ════════════════════════════════════════════════════════════════

def test_nifty500_list_loaded():
    assert len(U.NIFTY500) == 500
    assert "RELIANCE" in U.NIFTY500


def test_sbin_is_the_live_nifty50_symbol():
    # The old hardcoded list carried "SBI", which is not an NSE symbol, so the
    # NIFTY50 badge and every Pillar I filter silently skipped State Bank.
    assert "SBIN" in U.NIFTY50
    assert "SBI" not in U.NIFTY50
    assert U.index_label("SBIN") == "NIFTY50"


def test_body_universe_is_the_union():
    assert U.in_body_universe("RELIANCE")      # NIFTY50
    assert U.in_body_universe("ATHER")         # BAC coverage, outside N500
    assert not U.in_body_universe("NOTAREALSYMBOL")


def test_universe_of_has_no_nifty500_tier():
    # Next 3 Sessions is coverage + Pillar I only; an N500-but-not-Pillar-I name
    # must score as 'broader' so it does not leak into the forward table.
    n500_only = sorted(U.NIFTY500 - U.PILLAR1 - U.BAC_COVERAGE)
    assert n500_only, "expected some N500-only names"
    assert U.universe_of(n500_only[0]) == "broader"


# ═══ Dedupe / merge rule (Change 2) ═══════════════════════════════════════════

def test_merge_same_day_filings_joins_summaries():
    rows = [
        {"symbol": "TALWALKARS", "summary": "Alpha"},
        {"symbol": "TALWALKARS", "summary": "Beta"},
        {"symbol": "TALWALKARS", "summary": "Gamma"},
    ]
    out = merge_same_day_filings(rows)
    assert len(out) == 1
    assert out[0]["filing_count"] == 3
    assert out[0]["summary"] == "Alpha · Beta · Gamma"


def test_merge_keeps_distinct_symbols_apart():
    rows = [{"symbol": "AAA", "summary": "x"}, {"symbol": "BBB", "summary": "y"}]
    assert len(merge_same_day_filings(rows)) == 2


def test_merge_caps_and_reports_the_remainder():
    rows = [{"symbol": "X", "summary": "s" * 120} for _ in range(5)]
    # Distinct summaries — identical ones are collapsed as duplicates.
    for i, r in enumerate(rows):
        r["summary"] = f"{i}" + "s" * 119
    out = merge_same_day_filings(rows, max_chars=300)
    assert out[0]["filing_count"] == 5
    assert "more filing" in out[0]["summary"]
    assert len(out[0]["summary"]) < 400


def test_merge_truncates_a_single_oversized_summary():
    out = merge_same_day_filings([{"symbol": "X", "summary": "z" * 900}], max_chars=300)
    assert len(out[0]["summary"]) <= 301


def test_merge_groups_null_symbol_by_company():
    rows = [
        {"symbol": None, "company_name": "Acme Industries Limited", "summary": "a"},
        {"symbol": None, "company_name": "Acme Industries Ltd", "summary": "b"},
    ]
    assert len(merge_same_day_filings(rows)) == 1


def test_merge_deduplicates_identical_summaries():
    rows = [{"symbol": "X", "summary": "same"}, {"symbol": "X", "summary": "same"}]
    out = merge_same_day_filings(rows)
    assert out[0]["summary"] == "same"
    assert out[0]["filing_count"] == 2


# ═══ Category matching ════════════════════════════════════════════════════════

def test_category_match_is_case_insensitive():
    # NSE emits both spellings on the same day; exact set membership dropped one.
    assert category_matches("Credit Rating", ["Credit rating"])
    assert category_matches("Credit rating", ["Credit rating"])


def test_payload_free_outcome_detection():
    assert is_payload_free_outcome("Outcome of Board Meeting")
    assert is_payload_free_outcome("Outcome of the Board Meeting held on 7th August, 2026")
    assert is_payload_free_outcome(
        "PNB GILTS LTD. has informed the Exchange regarding Outcome of Board "
        "Meeting held on August 07, 2026.")
    assert is_payload_free_outcome("")


def test_payload_free_keeps_substantive_outcomes():
    assert not is_payload_free_outcome(
        "BEML Limited has submitted to the Exchange, the financial results for "
        "the period ended Jun 30, 2026.")
    assert not is_payload_free_outcome(
        "Oil India Limited has informed the Exchange regarding Appointment of Cost Auditor")


# ═══ Press-release classification ═════════════════════════════════════════════

def test_substantive_pr_requires_an_eligible_category():
    # Compliance categories cite regulations constantly; without the category
    # gate they matched the keyword test and flooded section v.
    assert not is_substantive_press_release(
        "Certificate under SEBI (Depositories and Participants) Regulations, 2018",
        "certificate filed under regulation 74(5)")
    assert not is_substantive_press_release("Trading Window", "closure of trading window")


def test_substantive_pr_accepts_deal_and_operations_news():
    assert is_substantive_press_release(
        "Press Release", "NTPC Green Energy Limited wins 200 MW/800 MWh BESS capacity")
    assert is_substantive_press_release(
        "General Updates", "Universal Cables Limited has informed about Capacity Expansion.")


def test_routine_pr_is_excluded():
    assert is_routine_pr("Press Release", "wins Great Place To Work certification")
    assert not is_routine_pr("Press Release", "bags order worth Rs 500 cr, also wins award")


def test_word_boundaries_prevent_false_positives():
    # bare "order" as a substring also matches "in order to" / "recorded".
    assert not is_substantive_press_release("Press Release", "in order to comply, recorded minutes")


def test_other_bucket_labels():
    assert other_announcement_bucket("Shareholders meeting") == "AGM notices"
    assert other_announcement_bucket("Appointment") == "KMP changes"
    assert other_announcement_bucket("Investor Presentation") == "investor presentations"
    assert other_announcement_bucket("Copy of Newspaper Publication") == "press releases"
    assert other_announcement_bucket("Spurt in Volume") == "other filings"


def test_count_line_omits_zero_buckets():
    assert count_line({"a": 2, "b": 0, "c": 1}) == "2 a · 1 c"
    assert count_line({}) == ""


# ═══ Trading sessions ═════════════════════════════════════════════════════════

def test_trading_sessions_skips_the_weekend():
    # 2026-08-07 is a Friday.
    assert trading_sessions(date(2026, 8, 7), 3) == [
        date(2026, 8, 10), date(2026, 8, 11), date(2026, 8, 12)]


# ═══ Fixture-backed assembly tests ════════════════════════════════════════════

def _load(name: str):
    fixdir = os.path.join(_FIXROOT, name)
    if not os.path.isdir(fixdir):
        pytest.skip(f"fixture {name} not captured")
    with open(os.path.join(fixdir, "meta.json"), encoding="utf-8") as fh:
        meta = json.load(fh)

    def frame(n):
        with open(os.path.join(fixdir, f"{n}.json"), encoding="utf-8") as fh:
            return pd.DataFrame(json.load(fh))

    return (date.fromisoformat(meta["report_date"]), date.fromisoformat(meta["today"]),
            frame("announcements"), frame("board_meetings"),
            frame("event_calendar"), frame("corporate_actions"))


@pytest.fixture(params=["aug07", "aug18"])
def assembly(request):
    rd, today, ann, bm, ec, ca = _load(request.param)
    asm = build_assembly(rd, ann, bm, ec, ca, today=today)
    asm.meta_fixture = request.param  # type: ignore[attr-defined]
    asm.meta_n_ann = len(ann)         # type: ignore[attr-defined]
    return asm


def test_completeness_every_announcement_reaches_a_section(assembly):
    """Acceptance 1: no row may vanish between the feed and the attachments."""
    total = sum(assembly.section(k).n_all for k in
                ("key_announcements", "other_announcements", "debt_market"))
    assert total == assembly.meta_n_ann


def test_body_is_a_subset_of_rows_all(assembly):
    for key in ("board_meetings", "event_calendar", "corporate_actions", "debt_market"):
        sec = assembly.section(key)
        assert sec.n_body <= sec.n_all


def test_body_rows_respect_the_configured_caps(assembly):
    cfg = assembly.config
    caps = {
        "board_meetings": cfg.board_meetings.body_cap,
        "event_calendar": cfg.event_calendar.body_cap,
        "corporate_actions": cfg.corporate_actions.body_cap,
        "key_announcements": cfg.key_announcements.body_cap,
        "other_announcements": cfg.other_announcements.body_cap,
        "debt_market": cfg.debt_market.body_cap,
    }
    for key, cap in caps.items():
        assert assembly.section(key).n_body <= cap, key


def test_event_calendar_body_is_in_universe_and_in_horizon(assembly):
    sec = assembly.section("event_calendar")
    cutoff = sec.meta["cutoff"]
    for r in sec.rows_body:
        assert U.in_body_universe(r.get("symbol") or "")
        assert (r.get("meeting_date") or "") <= cutoff


def test_corporate_actions_body_filter(assembly):
    from reports.transforms import classify_corp_action
    keep = set(assembly.config.corporate_actions.non_universe_keep_kinds)
    for r in assembly.section("corporate_actions").rows_body:
        sym = r.get("symbol") or ""
        assert U.in_body_universe(sym) or classify_corp_action(r.get("subject")) in keep


def test_board_meetings_body_filter(assembly):
    cfg = assembly.config.board_meetings
    for r in assembly.section("board_meetings").rows_body:
        purpose = (r.get("purpose") or "").upper()
        assert (U.in_body_universe(r.get("symbol") or "")
                or any(k.upper() in purpose for k in cfg.always_keep_purpose_contains))


def test_debt_body_is_ratings_or_universe(assembly):
    from reports.transforms import category_contains
    cfg = assembly.config.debt_market
    for r in assembly.section("debt_market").rows_body:
        assert (category_contains(r.get("category"), cfg.credit_rating_category_contains)
                or U.in_body_universe(r.get("symbol") or ""))


def test_no_payload_free_outcomes_in_the_body(assembly):
    for r in assembly.section("key_announcements").rows_body:
        assert not is_payload_free_outcome(r.get("summary"))


def test_rollup_lines_present_when_rows_were_dropped(assembly):
    for key in SECTION_ORDER:
        sec = assembly.section(key)
        if sec.n_dropped:
            assert sec.rollup, f"{key} dropped {sec.n_dropped} rows with no rollup line"


# ═══ Attachments ══════════════════════════════════════════════════════════════

def test_csv_row_counts_equal_unfiltered_assembly_counts(assembly):
    """Acceptance 3."""
    blob = build_csv_bundle(assembly)
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        for key, fname in SECTION_FILENAMES.items():
            text = zf.read(fname).decode("utf-8-sig")
            rows = list(csv.reader(io.StringIO(text)))
            assert rows[0] == CSV_COLUMNS[key]
            assert len(rows) - 1 == assembly.section(key).n_all, fname


def test_csv_bundle_has_a_readme(assembly):
    with zipfile.ZipFile(io.BytesIO(build_csv_bundle(assembly))) as zf:
        assert "README.txt" in zf.namelist()


def test_csv_never_leaks_raw_payload(assembly):
    with zipfile.ZipFile(io.BytesIO(build_csv_bundle(assembly))) as zf:
        for fname in SECTION_FILENAMES.values():
            assert "raw_payload" not in zf.read(fname).decode("utf-8-sig").splitlines()[0]


def test_full_tables_html_contains_every_section(assembly):
    from reports.attachments.pdf import build_full_tables_html
    html = build_full_tables_html(assembly)
    for key in SECTION_ORDER:
        assert f'id="sec-{key}"' in html
    assert "Liberation Serif" in html  # metric-compatible Linux fallback
