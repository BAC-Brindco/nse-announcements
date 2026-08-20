# Changelog

## 2026-08-20 — Adopt the BAC house design system

The announcements email rendered its own parchment/burgundy newspaper style with
every colour and size as an inline literal, repeated a few hundred times. It now
renders through `reports/design.py` — the same module the daily deals report
uses — so the two emails are one document family instead of two designs that
happened to share a masthead.

### What moved
- **`reports/design.py`** — vendored from the deals pipeline, byte-identical
  apart from a provenance note. The two pipelines are separate repositories, so
  this is a copy rather than a shared import; a change to one must be ported to
  the other or the house style forks.
- **`reports/render_email.py`** (new) — all presentation. Section tables take
  assembled rows plus an optional cap, so the email (`rows_body`) and the PDF
  (`rows_all`, `cap=None`) render through one code path.
- **`reports/pdf_render.py`** — the deals pipeline's print-CSS renderer, adapted.
- `daily_announcements_report.py` drops from **2,581 to ~1,050 lines**: it now
  fetches, computes editorial/movers/coverage *data*, and dispatches. Every
  colour literal is gone from it.

### Visual changes
- Navy/gold on a white 640px card over a grey page, replacing parchment/burgundy.
- Masthead: gold kicker, navy title, dateline, and a justified scope paragraph
  stating how many of the day's rows the body shows.
- KPI grid of four bordered cards; gold "things that matter today" callout with a
  gold-numbered list; navy callout for the coverage touchpoints prose.
- House `datatable` throughout — navy uppercase head on a band fill, zebra body,
  a source line, and the rollup count line as the italic caption *after* the
  table (house convention: data first, explanation second).
- ✦ coverage marker now gold; NIFTY50/100 and SME/DEBT render as outlined chips.
- Δ% in Top Movers is the one genuinely semantic value in the report, so it takes
  GOOD/BAD; corporate-action types stay structural with a labelled chip.
- Colophon: navy rule, provenance line, italic disclaimer.

### Outlook hardening inherited from design.py
Spacing on `<td>` padding rather than divs (Word drops div padding), every
line-height paired with `mso-line-height-rule:exactly`, `color-scheme: light
only` so dark mode cannot invert text over an explicit background, an XHTML 1.0
Transitional doctype, and a hidden preheader for the inbox preview line.

### PDF
Rebuilt in the same house style: masthead, KPI row, a contents table listing
rows-per-section and how many reached the email, then all six sections
unfiltered. Print CSS targets the `bac-page`/`bac-card`/`bac-data` class hooks
design.py emits, dropping the screen card frame and repeating table headers
across page breaks. A `'Times New Roman', 'Liberation Serif', 'Tinos', …` stack
is injected for the PDF only — Times New Roman does not exist on Linux, and this
keeps `design.py` byte-identical with the deals copy.

Body size is unchanged in substance: 07 Aug renders 25,976 visible characters
against the 219,807 baseline (**8.5×**).

## 2026-08-19 — Tiered email body + full-detail attachments

The email body becomes a curated signal layer; the exhaustive data moves to two
attachments. On 07 Aug 2026 (a heavy results day) the rendered body drops from
**219,807 to 24,594 visible characters — 8.9×** — with every removed row present
in the attachments.

### New architecture
- **`reports/assembly.py`** — the layer the report never had. Each of the six
  sections is now produced once in two forms: `rows_all` (complete, feeds the
  PDF/CSV) and `rows_body` (curated, feeds the email), plus a `rollup` count
  line. Filtering, dedup and row caps moved out of the renderers into here, so
  no config setting can hide a row from the attachments.
- **Completeness invariant** — `key_announcements + other_announcements +
  debt_market` rows_all now equals the announcement count exactly (asserted in
  tests). Section v deliberately widened to carry every equity filing section iv
  did not claim; previously *General Updates*, newspaper copies and ~20 other
  categories routed to neither section and were dropped from the report
  entirely.
- **`reports/config.yaml` + `report_config.py`** — thresholds, category
  whitelists, horizons and per-section row caps are editable without code
  changes. Every key has a dataclass default, so a malformed or missing file
  degrades to defaults rather than failing the 10:00 dispatch.
- **`reports/universes.py`** — BAC_COVERAGE / PILLAR1 / NIFTY500 moved out of
  the report module. NIFTY500 is vendored at `reports/data/nifty500.csv` from
  NSE's published constituent list; re-fetch after an index rebalance.

### Change 1 — Coverage Touchpoints vs Next 3 Sessions de-duplicated
- **Coverage Touchpoints is prose only.** Its table duplicated Next 3 Sessions
  almost row for row (18 Aug: ICICIBANK/FEDERALBNK, Fund Raising, 21 Aug in
  both). The two prose lines and the Pillar I counts remain.
- **Next 3 Sessions is the single canonical forward table** — coverage ∪ Pillar I,
  deduped on `(symbol, event_type, date)`, keeping index badges, the ✦ coverage
  marker, the Headlines line and the "Also on the tape" roll-up. When two source
  sections disagree on the date for one event, both rows survive and each is
  labelled with its section rather than the conflict being silently resolved.

### Change 2 — materiality filters (body only)
Per-section rules in `config.yaml`: universe gating for i/ii/iii, a category
whitelist for iv, coverage + substantive-press-release rows for v, and
credit-ratings-any-issuer for vi. Each section's remainder becomes a count line.
- Multi-filing symbols merge into one row (summaries joined by ` · `, capped at
  300 chars with `(+n more filings)`) and carry an "N filings" chip.
- Payload-free "Outcome of Board Meeting held on `<date>`." rows are dropped —
  42 of 179 such filings across the two test days.
- Row caps are the second lever: the universe filters alone still left 137
  in-universe event-calendar rows on 07 Aug. Caps are generous enough that an
  ordinary day (18 Aug) is untouched in i/ii/iii.

### Change 3 — attachments
- **PDF** — `BAC Announcements — NSE — <DD Mon YYYY> — Full Tables.pdf`: cover
  with counts, table of contents, then all six sections unfiltered through the
  *same* renderers the email uses (`cap=None`), so column layouts cannot drift.
  Rendered by headless Chromium; WeasyPrint is supported as a lighter fallback
  (`NSE_PDF_ENGINE=weasyprint`) but needs GTK, which Windows lacks. Font stack is
  `'Times New Roman', 'Liberation Serif', …` — Liberation Serif is metrically
  identical and ships on ubuntu, so CI output matches local previews. No personal
  name or credential is written to the PDF metadata, headers or footers.
- **CSV** — `BAC_Announcements_NSE_<YYYYMMDD>_data.zip`: one CSV per section plus
  a README, UTF-8-**sig** so Excel renders ₹ and `·` correctly. Includes derived
  `index_membership` / `is_coverage` / `is_sme` columns; `raw_payload` is never
  emitted. CSV row counts equal the unfiltered assembly counts (asserted).
- `_send_email` gained an `attachments` parameter; the colophon carries
  "Full tables attached — PDF for reading, CSV bundle for analysis."
- An attachment failure is logged and skipped — it can never block the email.

### Bugs found and fixed along the way
1. **`SBI` is not an NSE symbol** — it is `SBIN`. The hardcoded NIFTY50 set never
   matched State Bank, so it received no index badge and was invisible to every
   Pillar I filter. Also corrected: `TATAMOTORS` → `TMCV`/`TMPV` (demerged),
   `MCDOWELL-N` → `UNITDSPR` (renamed).
2. **`Credit Rating` vs `Credit rating`** — NSE emits both casings as distinct
   categories on the same day. `_MEDIUM_PRIORITY` held only the lowercase form,
   so ~22 credit-rating filings across the two test days never reached section v.
   Category matching is now case-insensitive throughout.

### Tests & tooling
- `tests/test_assembly.py` — 40 tests covering the dedupe rule, universe filters,
  multi-filing grouping, payload-free detection, press-release classification,
  cap enforcement, the completeness invariant and CSV↔assembly row equality.
- `tests/capture_fixture.py` now takes `--date/--today/--name`; fixtures captured
  for 07 Aug and 18 Aug 2026.
- `tests/render_fixture.py` takes `--fixture` and `--attachments`, reports body
  character counts and per-section all/body row counts.
- `requirements.txt` += `PyYAML`, `playwright`; `daily_report.yml` installs
  Chromium (cached) and `fonts-liberation`.

### Known limitation
A replayed edition is not byte-identical to the original email: the
event-calendar query has no upper date bound, so re-rendering 07 Aug today pulls
1,246 forward rows against the 947 the original saw. The 8.9× figure above is
therefore measured before-vs-after on the *same* fixture, which is the only
apples-to-apples comparison available.

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
