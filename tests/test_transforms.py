"""
Unit tests for reports/transforms.py — the pure data layer behind the report.

Covers the Bucket 1 fixes called out in the brief:
  - dedup keys (issues 1, 4, 6)
  - KPI reconciliation (issue 3)
  - null-symbol handling (issue 7)
  - section routing (issue 8)
  - issuer normalisation (issue 9)

Run:  python -m pytest tests/test_transforms.py -q
"""

from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reports.transforms import (  # noqa: E402
    announcement_key, announcement_tag, classify_corp_action, clean_cell,
    dedup_keep_order, find_near_duplicate_issuers, format_inr, group_debt_rows,
    is_debt_instrument, is_material_capital_raise, kpi_reconciles,
    materiality_kind, normalize_currency, normalize_headline, resolve_symbol,
    route_section, score_tape_item, subtitle_corp_actions, subtitle_of_which,
    touchpoint_key,
)

HIGH = {"Financial Results", "Outcome of Board Meeting", "Record Date"}
MEDIUM = {"Analysts/Institutional Investor Meet/Con. Call Updates", "Press Release"}


# ─── clean_cell / null handling (issue 7) ──────────────────────────────────────

def test_clean_cell_handles_nan_none_placeholders():
    assert clean_cell(float("nan")) == ""
    assert clean_cell(None) == ""
    assert clean_cell("nan") == ""
    assert clean_cell("None") == ""
    assert clean_cell("  POWERGRID ") == "POWERGRID"


def test_resolve_symbol_never_emits_nan():
    # Debt issuer with null ticker → truncated issuer name, never "nan"
    out = resolve_symbol(float("nan"), "Sammaan Capital Limited", "debt")
    assert "nan" not in out.lower()
    assert out.startswith("Sammaan")

    # Long debt issuer name truncated to 24 chars
    long_name = "National Bank For Financing Infrastructure And Development"
    out = resolve_symbol(None, long_name, "debt", max_len=24)
    assert len(out) <= 24
    assert out.endswith("…")

    # Real equity ticker passes through untouched
    assert resolve_symbol("POWERGRID", "Power Grid Corp", "equities") == "POWERGRID"

    # The exact old bug: pandas NaN symbol must not become "nanDEBT"
    assert resolve_symbol(float("nan"), "Some Issuer Ltd", "debt") != "nan"


# ─── headline dedup (issues 1) ─────────────────────────────────────────────────

def test_normalize_headline_collapses_middot_and_whitespace():
    a = normalize_headline("POWERGRID  board meets · fund raising")
    b = normalize_headline("powergrid board meets   fund raising")
    assert a == b


def test_dedup_two_identical_headlines_yields_one():
    items = [
        "POWERGRID board meets 10 Jun on fund raising",
        "POWERGRID board meets 10 Jun on fund raising",
    ]
    out = dedup_keep_order(items, key=normalize_headline)
    assert len(out) == 1


def test_dedup_keeps_first_occurrence_order():
    items = ["A", "B", "A", "C", "B"]
    assert dedup_keep_order(items, key=lambda s: s) == ["A", "B", "C"]


# ─── touchpoint dedup (issue 4) ────────────────────────────────────────────────

def test_touchpoint_key_dedups_same_event_across_sections():
    # Same POWERGRID fund-raise filed under Board Meetings and Event Calendar
    bm = {"symbol": "POWERGRID", "date": "2026-06-10", "event": "Fund Raising — board meeting"}
    ec = {"symbol": "POWERGRID", "date": "2026-06-10", "event": "Fund Raising"}
    rows = [bm, ec]
    out = dedup_keep_order(rows, key=lambda r: touchpoint_key(r["symbol"], r["date"], r["event"]))
    assert len(out) == 1
    # Board Meetings copy (first) is the one kept — issue 4 default
    assert out[0]["event"].endswith("board meeting")


def test_touchpoint_key_keeps_distinct_events():
    rows = [
        {"symbol": "INFY", "date": "2026-06-10", "event": "Dividend"},
        {"symbol": "INFY", "date": "2026-06-12", "event": "Dividend"},
    ]
    out = dedup_keep_order(rows, key=lambda r: touchpoint_key(r["symbol"], r["date"], r["event"]))
    assert len(out) == 2


# ─── announcement dedup (issue 6) ──────────────────────────────────────────────

def test_announcement_key_dedups_identical_rows():
    rows = [
        {"symbol": "ANURAS", "company_name": "Anupam Rasayan", "summary": "Board outcome text"},
        {"symbol": "ANURAS", "company_name": "Anupam Rasayan", "summary": "Board outcome text"},
        {"symbol": "ANURAS", "company_name": "Anupam Rasayan", "summary": "Different text"},
    ]
    out = dedup_keep_order(rows, key=lambda r: announcement_key(
        r["symbol"], r["company_name"], r["summary"]))
    assert len(out) == 2


def test_announcement_key_falls_back_to_company_when_symbol_null():
    a = announcement_key(float("nan"), "Magnum Ventures Limited", "Record date update")
    b = announcement_key(None, "Magnum Ventures Limited", "Record date update")
    assert a == b  # both null symbols collapse to the same normalised company identity


# ─── section routing (issue 8) ─────────────────────────────────────────────────

def test_debt_routes_to_debt_regardless_of_category():
    # NBFID board outcome with a HIGH-priority category but debt segment → vi only
    row = {"segment": "debt", "category": "Outcome of Board Meeting"}
    assert route_section(row, HIGH, MEDIUM) == "debt"
    assert is_debt_instrument(row)


def test_equity_routing_by_priority():
    assert route_section({"segment": "equities", "category": "Financial Results"}, HIGH, MEDIUM) == "key"
    assert route_section({"segment": "sme", "category": "Press Release"}, HIGH, MEDIUM) == "other"
    assert route_section({"segment": "equities", "category": "Trading Window"}, HIGH, MEDIUM) is None


# ─── KPI reconciliation (issue 3) ──────────────────────────────────────────────

def test_corp_action_subtitle_names_residual_when_small():
    rows = [{"symbol": "INFY", "subject": "Dividend - Rs 25 Per Share"} for _ in range(21)]
    rows.append({"symbol": "CUB", "subject": "Bonus 1:3"})
    sub = subtitle_corp_actions(rows)
    assert "21 dividends" in sub
    assert "bonus" in sub
    assert "CUB" in sub and "1:3" in sub
    # Fully reconciles: 21 + 1 == 22
    assert kpi_reconciles(22, [21, 1], sub)


def test_corp_action_subtitle_collapses_when_many_others():
    rows = [{"symbol": f"X{i}", "subject": "Buy Back"} for i in range(5)]
    sub = subtitle_corp_actions(rows)
    assert "5 other" in sub


def test_of_which_phrasing_marks_partial_breakdown():
    sub = subtitle_of_which(212, [(1, "results"), (35, "engagement")])
    assert sub.startswith("of which")
    # 1 + 35 != 212, but "of which" makes the partial nature explicit → acceptable
    assert kpi_reconciles(212, [1, 35], sub)


def test_kpi_reconciles_rejects_silent_mismatch():
    # No "of which", and 1 + 35 != 212 → not acceptable
    assert not kpi_reconciles(212, [1, 35], "1 results · 35 engagement")


def test_classify_corp_action():
    assert classify_corp_action("Dividend - Rs 5") == "dividend"
    assert classify_corp_action("Bonus 1:3") == "bonus"
    assert classify_corp_action("Face Value Split") == "split"
    assert classify_corp_action("Rights 1:2") == "rights"
    assert classify_corp_action("Buy Back") == "buyback"
    assert classify_corp_action("Scheme of Arrangement") == "other"


# ─── issuer normalisation (issue 9) ────────────────────────────────────────────

def test_find_near_duplicate_issuers_flags_parse_error():
    names = ["IIFL Finance Limited", "IFL Finance Limited", "Reliance Industries Limited"]
    pairs = find_near_duplicate_issuers(names, threshold=2)
    flagged = {(a, b) for a, b, _ in pairs}
    assert ("IFL Finance Limited", "IIFL Finance Limited") in flagged or \
           ("IIFL Finance Limited", "IFL Finance Limited") in flagged
    # The unrelated name is not flagged against either
    assert all("Reliance" not in a and "Reliance" not in b for a, b, _ in pairs)


def test_find_near_duplicate_issuers_ignores_distant_names():
    names = ["Tata Steel Limited", "Infosys Limited"]
    assert find_near_duplicate_issuers(names, threshold=2) == []


# ─── tape scoring (issue 10) ───────────────────────────────────────────────────

def test_score_orders_by_universe_then_materiality():
    # NIFTY50 fund raise must outrank a NIFTY100 fund raise
    n50 = {"universe": "nifty50", "kind": "fund_raise", "size": 0}
    n100 = {"universe": "nifty100", "kind": "fund_raise", "size": 0}
    assert score_tape_item(n50) > score_tape_item(n100)
    # Within the same universe, fund raise outranks a routine dividend
    fund = {"universe": "nifty50", "kind": "fund_raise"}
    div = {"universe": "nifty50", "kind": "dividend"}
    assert score_tape_item(fund) > score_tape_item(div)
    # Size only breaks ties within the same universe+materiality
    big = {"universe": "nifty50", "kind": "large_dividend", "size": 100}
    small = {"universe": "nifty50", "kind": "large_dividend", "size": 1}
    assert score_tape_item(big) > score_tape_item(small)


def test_issue_113_expected_leads_outrank_sme():
    # ZEEL/POWERGRID fund raises & INFY dividend should beat SME fund raises
    zeel = {"universe": "nifty100", "kind": "fund_raise"}
    sme = {"universe": "broader", "kind": "fund_raise"}
    assert score_tape_item(zeel) > score_tape_item(sme)


def test_materiality_kind_buckets():
    assert materiality_kind("Fund Raising") == "fund_raise"
    assert materiality_kind("Dividend", amount=25) == "large_dividend"
    assert materiality_kind("Dividend", amount=2) == "dividend"
    assert materiality_kind("Bonus 1:3") == "bonus"
    assert materiality_kind("Voluntary Delisting") == "delisting"
    assert materiality_kind("Analysts/Institutional Investor Meet") == "analyst_meet"


# ─── tag taxonomy (issue 14) ───────────────────────────────────────────────────

def test_announcement_tag_taxonomy():
    assert announcement_tag("Analysts/Institutional Investor Meet/Con. Call Updates") == "analyst_meet"
    assert announcement_tag("Appointment") == "kmp_change"
    assert announcement_tag("Press Release") == "press_release"
    assert announcement_tag("Shareholders meeting") == "agm_egm"
    assert announcement_tag("Credit Rating") == "credit_rating"
    assert announcement_tag("Outcome of Board Meeting") == "material_event"


# ─── material capital raise (issue 11) ─────────────────────────────────────────

def test_material_capital_raise_detection():
    assert is_material_capital_raise("Preferential allotment of 1,52,87,356 equity shares")
    assert is_material_capital_raise("Approval of QIP")
    assert is_material_capital_raise("Rights Issue of equity shares")
    assert is_material_capital_raise("Offer for Sale by promoter")
    assert not is_material_capital_raise("Analysts meet scheduled")
    # No false positive from substrings (EQUIPMENT must not match QIP/OFS)
    assert not is_material_capital_raise("Purchase of new equipment for the plant")


# ─── debt grouping (issue 13) ──────────────────────────────────────────────────

def test_normalize_currency_standardises_on_rupee():
    assert normalize_currency("Dividend - Rs 25 Per Share") == "Dividend - ₹25 Per Share"
    assert normalize_currency("INR 5/-") == "₹5/-"
    assert normalize_currency("Rs. 100 per share") == "₹100 per share"
    assert normalize_currency("Rupees 12") == "₹12"
    # Already-₹ text is untouched
    assert normalize_currency("₹75") == "₹75"


def test_format_inr():
    assert format_inr(25) == "₹25 per share"
    assert format_inr(25, with_unit=False) == "₹25"
    assert format_inr(None) == ""


def test_group_debt_rows_collapses_repeated_filings():
    rows = []
    for i in range(11):
        rows.append({
            "company_name": "NPCIL", "symbol": None, "isin": f"INE206D08{i:03d}",
            "category": "Record Date Updates", "summary": "record date 8-Jul-2026",
        })
    rows.append({
        "company_name": "Sammaan Capital", "symbol": None, "isin": "INE001X08001",
        "category": "Confirmation of Redemption/Payment of Interest and Principal",
        "summary": "interest payment 10-Jun-2026",
    })
    groups = group_debt_rows(rows)
    npcil = next(g for g in groups if g["issuer"] == "NPCIL")
    assert npcil["count"] == 11
    assert len(npcil["isins"]) == 11
    assert "record-date" in npcil["nature"]
    # Largest group sorts first
    assert groups[0]["issuer"] == "NPCIL"
