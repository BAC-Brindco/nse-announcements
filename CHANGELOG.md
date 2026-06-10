# Changelog

## 2026-06-10 — Issue №113 fixes

Pipeline, content/editorial and structural fixes surfaced by a review of Issue
№113 (2026-06-09). All data-layer logic moved into a pure, unit-tested module
(`reports/transforms.py`); the report module (`reports/daily_announcements_report.py`)
keeps presentation. Visual house-style (Times New Roman, navy/burgundy headers,
alternating shading) is unchanged except where a fix explicitly touches styling
(issues 2, 18, 19, 20).

### Bucket 1 — Pipeline bugs
1. **Tape lead duplication** — the same item filed under both `board_meetings`
   and `event_calendar` (e.g. POWERGRID/HITECHCORP) no longer prints twice.
   Lead bullets are deduped on the normalised, tag-stripped headline.
2. **Ticker / universe-badge concatenation** — NIFTY50/100 badges now render in
   a `universe-badge` span with `margin-left:0.35em` and a subtle background, so
   `POWERGRIDNIFTY50` reads as `POWERGRID  NIFTY50`. SME/DEBT chips share the
   same treatment via a single `_seg_badge` helper.
3. **KPI cards reconcile** — partial breakdowns use explicit *"of which …"*
   phrasing; Corporate Actions fully reconciles and names the residual when ≤3
   (`19 dividends · 1 bonus (CUB 1:3)`, not `1 other`).
4. **POWERGRID double-count** — coverage touchpoints dedupe on
   `(symbol, date, purpose)` before counting; the Board Meetings copy is kept.
5. **Active-coverage join** — "Active coverage on the tape" only cites a symbol
   that actually routes to a displayed body section (iv/v), not anything merely
   present in the raw announcement set.
6. **Duplicate rows in iv/v** — announcement rows dedupe on
   `(identity, sha1(summary))`; the 6 duplicate SME pairs are gone.
7. **`nanDEBT` leak** — root cause was `str(row.get(x) or "")` returning the
   pandas `float('nan')` (truthy). New `clean_cell()` + `resolve_symbol()` never
   emit `nan*`; debt issuers with no equity ticker show the issuer name (≤24 ch).
8. **Debt section bleed** — announcements are split by instrument type up front;
   debt filings (e.g. NBFID board outcomes) route to vi only, never iv/v.
9. **Issuer-name normalisation** — near-duplicate issuers (e.g. "IIFL Finance
   Limited" vs "IFL Finance Limited", edit distance ≤2) are logged for review
   (no CIN/LEI master available).

### Bucket 2 — Content / editorial
10. **Tape leads by score** — new `score_tape_item()` ranks by materiality tier,
    then universe (NIFTY50 > NIFTY100 > BAC > broader), then size. The tape now
    leads with POWERGRID fund raise, INFY ₹25 ex-div, TATAELXSI ₹75 ex-div.
    *(Judgement call — see "Manual verification" below re: universe vs materiality.)*
11. **Material capital raises flagged** — preferential allotments, QIP, rights
    and OFS get a bold `[MATERIAL]` tag (e.g. the SANGINITA preferential allotment).
12. **Top Movers overlay** — null-price rows dropped (no em-dash filler), sorted
    by `abs(Δ%)` desc; when every row shares one filing type it becomes the
    subtitle and the column shows the underlying filing summary.
13. **Debt Market grouping** — filings collapse to one row per
    `(issuer, payment nature)` with an ISIN count and date window.
14. **Section v scope** — analyst meets are routed to Top Movers; section v now
    carries comms/KMP/governance only (tag taxonomy in `announcement_tag`).
15. **Next-3-Sessions headlines** — reweighted via `score_tape_item`, SME
    excluded; headlines are now NIFTY/BAC names, not SME fund raises.

### Bucket 3 — Structural
16. **Next-3-Sessions split** — primary table = NIFTY100 + BAC coverage only;
    everything else rolls into an "Also on the tape" block with counts and date
    ranges. *(Implemented alongside issue 15 in the same function.)*
17. **Currency standardised on ₹** — `normalize_currency()` / `format_inr()`;
    "Rs 25 Per Share" → "₹25 Per Share" across the document.
18. **✦ legend** — one-line legend under sections i & ii explaining the marker.
19. **Volume notation** — explicit `sh` unit ("0.9L sh", "90K sh").
20. **Corporate Actions colour legend** — rows now carry a left-border tint
    matching the dividend/bonus/split/rights/buyback legend.

### Bucket 4 — Optional polish (scaffolds where data is not yet available)
21. **Trailing 4-wk averages** — `reports/rolling_store.py` scaffold + graceful
    no-op annotation hook; records headline counts on each live run. TODO: move
    the store to Supabase and backfill ~20 sessions before enabling in the UI.
22. **ICYMI row** — renderer ready (`_icymi_html`, capped at 2); TODO: wire the
    prior-session fetch in `main()` to feed it.
23. **SAST Reg 31(4) micro-section** — takeover disclosures pulled out of the
    main Key Announcements table into a sub-section showing only BAC/NIFTY
    touchpoints, with the remainder rolled into a single count.

### Tests & tooling
- `reports/transforms.py` — pure, tested data layer.
- `tests/test_transforms.py` — 26 unit tests (dedup keys, KPI reconciliation,
  null-symbol handling, section routing, tape scoring, tagging, currency, debt
  grouping, issuer normalisation).
- `tests/capture_fixture.py` / `tests/render_fixture.py` — deterministic offline
  fixture capture + render of Issue №113 for regression diffing.
