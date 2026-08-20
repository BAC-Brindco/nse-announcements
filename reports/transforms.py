"""
Pure data-layer transforms for the daily announcements report.

Everything here is side-effect-free and DataFrame-agnostic (operates on plain
dicts / scalars) so it can be unit-tested without a database or HTML rendering.
The report module imports these and keeps presentation concerns to itself.
"""

from __future__ import annotations

import hashlib
import math
import re

# ─── Scalar cleaning ──────────────────────────────────────────────────────────


def clean_cell(val) -> str:
    """Coerce any cell value to a clean string.

    Critically handles the pandas trap where JSON ``null`` becomes ``float('nan')``
    (which is *truthy*, so ``val or ""`` leaks the literal text ``"nan"``).
    Returns "" for None, NaN/NaT, and the placeholder tokens NSE/pandas emit.
    """
    if val is None:
        return ""
    if isinstance(val, float) and math.isnan(val):
        return ""
    s = str(val).strip()
    if s.lower() in ("nan", "none", "nat", "<na>", "null"):
        return ""
    return s


_CCY_RE = re.compile(r"(?:\bRs\.?|\bINR\b|\bRupees?\b)\s*\.?\s*", re.IGNORECASE)


def normalize_currency(text) -> str:
    """Standardise rupee notation on ₹ (issue 17): 'Rs 25 Per Share' → '₹25 Per
    Share', 'INR 5/-' → '₹5/-'. Leaves existing ₹ untouched."""
    s = clean_cell(text)
    if not s:
        return s
    return _CCY_RE.sub("₹", s)


def format_inr(value, with_unit: bool = True) -> str:
    """Format a numeric rupee value as '₹25' or '₹25 per share'."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return ""
    num = f"{v:g}"
    return f"₹{num} per share" if with_unit else f"₹{num}"


def truncate(text: str, max_len: int) -> str:
    text = text.strip()
    if len(text) > max_len:
        return text[: max_len - 1].rstrip() + "…"  # …
    return text


# ─── Order-preserving dedup ────────────────────────────────────────────────────


def dedup_keep_order(items, key):
    """Return items with duplicates (by key(item)) removed, first occurrence kept."""
    seen = set()
    out = []
    for it in items:
        k = key(it)
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out


# ─── Headline / purpose normalisation ──────────────────────────────────────────


def normalize_headline(s) -> str:
    """Normalise a tape headline for dedup: strip whitespace, lowercase, collapse
    middot separators and runs of whitespace."""
    s = clean_cell(s).lower()
    s = s.replace("·", " ").replace("&#183;", " ").replace("&middot;", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def normalize_purpose(p) -> str:
    """Collapse a board-meeting / event purpose to a canonical bucket for dedup."""
    up = clean_cell(p).upper()
    if "FUND RAIS" in up:
        return "FUND_RAISING"
    if "FINANCIAL RESULT" in up:
        return "FINANCIAL_RESULTS"
    if "DIVIDEND" in up:
        return "DIVIDEND"
    if "DELIST" in up:
        return "DELISTING"
    if "BONUS" in up:
        return "BONUS"
    if "BUY BACK" in up or "BUYBACK" in up:
        return "BUYBACK"
    if "SPLIT" in up or "SUB-DIVISION" in up:
        return "SPLIT"
    return re.sub(r"\s+", " ", up).strip()


# ─── Symbol resolution (issue 7 — never emit "nan*") ───────────────────────────


def resolve_symbol(symbol, company_name, segment, name_map=None, max_len=24) -> str:
    """Resolve a display ticker.

    - Real equity/SME ticker present  → return it.
    - Null ticker + debt instrument    → issuer name, truncated to ``max_len``.
    - Null ticker otherwise            → name_map lookup, else issuer name.
    Never returns a ``nan``/``None`` placeholder string.
    """
    sym = clean_cell(symbol)
    if sym:
        return sym

    company = clean_cell(company_name)
    seg = clean_cell(segment).lower()

    if seg != "debt" and name_map:
        hit = _name_lookup(company, name_map)
        if hit:
            return hit

    return truncate(company, max_len) if company else ""


def _normalize_company(name: str) -> str:
    s = clean_cell(name).upper()
    for suffix in (
        " LIMITED", " LTD.", " LTD", " PRIVATE", " PVT.", " PVT",
        " COMPANY", " CORPORATION", " CORP.", " CORP",
        " BANK", " FINANCE", " FINANCIAL", " CAPITAL",
        " INDUSTRIES", " INDUSTRY", " ENTERPRISES", " TECHNOLOGIES",
        " TECHNOLOGY", " SOLUTIONS", " SERVICES", " INDIA",
        " (INDIA)", " HOLDINGS", " VENTURES",
    ):
        s = s.replace(suffix, "")
    return re.sub(r"\s+", " ", s).strip()


def _name_lookup(company: str, name_map: dict) -> str | None:
    norm = _normalize_company(company)
    if not norm:
        return None
    if norm in name_map:
        return name_map[norm]
    for key, sym in name_map.items():
        if len(norm) >= 5 and (norm in key or key in norm):
            return sym
    return None


# ─── Section routing (issue 8 — debt never bleeds into equity sections) ─────────


def is_debt_instrument(row) -> bool:
    return clean_cell(row.get("segment")).lower() == "debt"


def route_section(row, high_priority: set, medium_priority: set) -> str | None:
    """Route an announcement to exactly one section.

    Returns 'debt', 'key' (section iv), 'other' (section v), or None (drop).
    Debt instruments route to 'debt' regardless of category — they must never
    appear in Key/Other Announcements.
    """
    if is_debt_instrument(row):
        return "debt"
    cat = clean_cell(row.get("category"))
    if cat in high_priority:
        return "key"
    if cat in medium_priority:
        return "other"
    return None


# ─── Dedup keys (issues 4 & 6) ─────────────────────────────────────────────────


def touchpoint_key(symbol, event_date, purpose):
    """Key for deduping a coverage touchpoint across sections (issue 4)."""
    return (clean_cell(symbol).upper(), clean_cell(event_date), normalize_purpose(purpose))


def announcement_key(symbol, company_name, summary):
    """Key for deduping announcement rows (issue 6): (identity, sha1(summary))."""
    ident = clean_cell(symbol).upper() or _normalize_company(company_name)
    digest = hashlib.sha1(clean_cell(summary).encode("utf-8")).hexdigest()
    return (ident, digest)


# ─── Issuer name normalisation (issue 9) ───────────────────────────────────────


def levenshtein(a: str, b: str) -> int:
    a, b = a or "", b or ""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def find_near_duplicate_issuers(names, threshold: int = 2):
    """Return list of (name_a, name_b, distance) for issuer names within
    ``threshold`` edits of each other (likely parse errors on one entity).

    No CIN/LEI master is available, so the caller logs these for review.
    """
    uniq = sorted({clean_cell(n) for n in names if clean_cell(n)})
    pairs = []
    for i in range(len(uniq)):
        for j in range(i + 1, len(uniq)):
            a, b = uniq[i], uniq[j]
            if abs(len(a) - len(b)) > threshold:
                continue
            d = levenshtein(a.upper(), b.upper())
            if 0 < d <= threshold:
                pairs.append((a, b, d))
    return pairs


# ─── KPI reconciliation (issue 3) ──────────────────────────────────────────────


def _parse_bonus_ratio(subject: str) -> str:
    m = re.search(r"(\d+\s*:\s*\d+)", subject)
    return m.group(1).replace(" ", "") if m else ""


def classify_corp_action(subject: str) -> str:
    up = clean_cell(subject).upper()
    if "DIVIDEND" in up or "INTERIM" in up:
        return "dividend"
    if "BONUS" in up:
        return "bonus"
    if "SPLIT" in up or "SUB-DIVISION" in up:
        return "split"
    if "RIGHTS" in up:
        return "rights"
    if "BUY BACK" in up or "BUYBACK" in up:
        return "buyback"
    return "other"


def subtitle_corp_actions(rows) -> str:
    """Fully-reconciling CA subtitle. Names the residual action(s) when ≤3
    (e.g. '21 dividends · 1 bonus (CUB 1:3)')."""
    if not rows:
        return "none this week"
    n_div = 0
    others = []
    for r in rows:
        subj = clean_cell(r.get("subject"))
        kind = classify_corp_action(subj)
        if kind == "dividend":
            n_div += 1
        else:
            others.append((kind, clean_cell(r.get("symbol")), subj))

    parts = [f"{n_div} dividends"] if n_div else []
    if not others:
        return " · ".join(parts) or "0 dividends"
    if len(others) <= 3:
        for kind, sym, subj in others:
            label = kind
            if kind == "bonus":
                ratio = _parse_bonus_ratio(subj)
                detail = f"{sym} {ratio}".strip() if (sym or ratio) else ""
            else:
                detail = sym
            parts.append(f"1 {label} ({detail})" if detail else f"1 {label}")
    else:
        parts.append(f"{len(others)} other")
    return " · ".join(parts)


def subtitle_of_which(total: int, breakdowns: list[tuple[int, str]]) -> str:
    """Partial-breakdown subtitle that does not pretend to reconcile.

    breakdowns: list of (count, label). Emits 'of which N label · M label'.
    """
    if total == 0:
        return "none"
    inner = " · ".join(f"{n} {label}" for n, label in breakdowns)
    return f"of which {inner}" if inner else f"{total} filings"


def kpi_reconciles(total: int, breakdown_counts: list[int], subtitle: str) -> bool:
    """Validation predicate used by tests: a KPI card is acceptable iff the
    breakdown sums to the headline total OR the subtitle uses 'of which'."""
    return sum(breakdown_counts) == total or "of which" in subtitle.lower()


# ─── Tape scoring (issues 10, 15) ──────────────────────────────────────────────

_UNIVERSE_WEIGHT = {"nifty50": 3, "nifty100": 2, "bac": 1, "broader": 0}

# Materiality TIER is the dominant ranking term (issue 10 rule ii). The brief's
# worked example wants material broader-universe events (ZEEL fund raise, CUB
# bonus 1:3) to lead over a NIFTY50 *routine* event, so materiality must be able
# to override universe — universe is the secondary tiebreak, size the tertiary.
# (This is a deliberate reading: rule (i) is listed first, but the example only
#  reconciles if materiality dominates. Flagged in the change summary.)
_MATERIALITY_TIER = {
    "fund_raise": 5, "delisting": 5, "bonus": 5, "rights": 5, "split": 5,
    "large_dividend": 5,
    "results": 4,
    "dividend": 3, "credit_rating": 3,
    "agm_egm": 2, "press_release": 2, "kmp_change": 2, "other": 2,
    "analyst_meet": 1,
}

# Fine-grained materiality within a tier (keeps fund_raise > large_dividend etc.)
_MATERIALITY_WEIGHT = {
    "fund_raise": 50, "delisting": 50, "bonus": 48, "rights": 46, "split": 45,
    "large_dividend": 40, "results": 30, "dividend": 20, "credit_rating": 14,
    "agm_egm": 12, "press_release": 10, "kmp_change": 10, "analyst_meet": 5,
    "other": 8,
}


def materiality_kind(text, amount: float = 0.0) -> str:
    """Map a purpose/subject string to a materiality bucket (issue 10)."""
    up = clean_cell(text).upper()
    if "FUND RAIS" in up:
        return "fund_raise"
    if "DELIST" in up:
        return "delisting"
    if "BONUS" in up:
        return "bonus"
    if "RIGHTS" in up:
        return "rights"
    if "SPLIT" in up or "SUB-DIVISION" in up:
        return "split"
    if "DIVIDEND" in up or "INTERIM" in up:
        return "large_dividend" if amount >= 10 else "dividend"
    if "FINANCIAL RESULT" in up or "RESULTS" in up:
        return "results"
    if "ANALYST" in up or "INVESTOR MEET" in up or "CON. CALL" in up:
        return "analyst_meet"
    return "other"


def score_tape_item(event: dict) -> float:
    """Score a candidate tape item. Higher = leads the tape.

    Composite (issue 10): materiality TIER dominates, then universe
    (NIFTY50 > NIFTY100 > BAC > broader), then fine materiality, then absolute
    size. So a material broader-universe event (ZEEL fund raise) leads a NIFTY50
    routine result, while two equally-material events order by universe then size.

    ``event`` keys: universe ('nifty50'|'nifty100'|'bac'|'broader'),
    kind (materiality bucket), size (numeric, optional).
    """
    tier = _MATERIALITY_TIER.get(event.get("kind", "other"), 2)
    u = _UNIVERSE_WEIGHT.get(event.get("universe", "broader"), 0)
    m = _MATERIALITY_WEIGHT.get(event.get("kind", "other"), 8)
    size = float(event.get("size", 0) or 0)
    size_component = min(size, 1000.0) / 1000.0  # 0..1, never overtakes the tier
    return tier * 100_000 + u * 10_000 + m * 100 + size_component


# ─── Announcement tag taxonomy (issue 14) ──────────────────────────────────────

def announcement_tag(category) -> str:
    """Classify an announcement category into the section-v tag taxonomy."""
    c = clean_cell(category).lower()
    if "analyst" in c or "investor meet" in c or "con. call" in c:
        return "analyst_meet"
    if "appointment" in c or "cessation" in c or "resignation" in c:
        return "kmp_change"
    if "press release" in c or "newspaper" in c:
        return "press_release"
    if "shareholders meeting" in c or "agm" in c or "egm" in c or "postal ballot" in c:
        return "agm_egm"
    if "credit rating" in c:
        return "credit_rating"
    return "material_event"


# ─── Material capital-raise detection (issue 11) ───────────────────────────────

_CAPITAL_RAISE_RE = re.compile(
    r"\bQIP\b|\bOFS\b|QUALIFIED\s+INSTITUTION|RIGHTS\s+ISSUE|OFFER\s+FOR\s+SALE",
    re.IGNORECASE,
)


def is_material_capital_raise(text) -> bool:
    """True for preferential allotments, QIP, rights, OFS — material raises to flag."""
    up = clean_cell(text).upper()
    if "PREFERENTIAL" in up and "ALLOT" in up:
        return True
    return bool(_CAPITAL_RAISE_RE.search(up))


# ─── Debt market grouping (issue 13) ───────────────────────────────────────────

def _payment_nature(text) -> str:
    up = clean_cell(text).upper()
    if "RECORD DATE" in up:
        return "record-date update"
    if "INTEREST" in up and ("PRINCIPAL" in up or "REDEMPTION" in up):
        return "interest/principal payment"
    if "INTEREST" in up:
        return "interest payment"
    if "REDEMPTION" in up or "MATURITY" in up:
        return "redemption"
    if "ALLOTMENT" in up:
        return "allotment"
    return clean_cell(text).lower()[:40] or "update"


def group_debt_rows(rows):
    """Collapse debt filings to one entry per (issuer, payment_nature).

    Returns list of dicts: {issuer, nature, count, dates(sorted unique),
    isins(sorted unique)} — for a compact 'NPCIL — 11 record-date updates …' row.
    Sorted by descending group size then issuer.
    """
    groups: dict[tuple, dict] = {}
    for r in rows:
        issuer = clean_cell(r.get("company_name")) or clean_cell(r.get("symbol"))
        nature = _payment_nature(r.get("category"))
        key = (issuer.upper(), nature)
        g = groups.setdefault(key, {
            "issuer": issuer, "nature": nature, "count": 0,
            "dates": set(), "isins": set(),
        })
        g["count"] += 1
        rd = clean_cell(r.get("scrape_date"))
        # prefer an explicit record date if present in the raw payload/summary
        g["isins"].add(clean_cell(r.get("isin")))
        summ = clean_cell(r.get("summary"))
        for m in re.findall(r"\b(\d{1,2}[-/ ][A-Za-z]{3,9}[-/ ]\d{2,4})\b", summ):
            g["dates"].add(m)
    out = []
    for g in groups.values():
        out.append({
            "issuer": g["issuer"], "nature": g["nature"], "count": g["count"],
            "dates": sorted(d for d in g["dates"] if d),
            "isins": sorted(i for i in g["isins"] if i),
        })
    out.sort(key=lambda g: (-g["count"], g["issuer"].upper()))
    return out


# ─── Body-filter primitives (tiered-email restructure) ─────────────────────────
#
# Everything below supports the split between the curated email body and the
# unfiltered PDF/CSV attachments. All of it is pure: assembly decides *whether*
# to apply a rule, these functions decide *what the rule says*.


def category_matches(category, patterns) -> bool:
    """Case-insensitive membership test for an NSE category.

    NSE is not consistent about casing — the feed carries both ``Credit Rating``
    and ``Credit rating`` as distinct category strings on the same day. Exact
    set membership therefore silently drops one of them, which is what the
    original _MEDIUM_PRIORITY set did. Compare casefolded.
    """
    c = clean_cell(category).casefold()
    if not c:
        return False
    return any(c == clean_cell(p).casefold() for p in patterns)


def category_contains(category, needles) -> bool:
    """True when the category contains any needle (casefolded substring)."""
    c = clean_cell(category).casefold()
    if not c:
        return False
    return any(clean_cell(n).casefold() in c for n in needles)


# A filing whose entire summary just restates "Outcome of Board Meeting" carries
# no information the section header doesn't already give. NSE wraps these in
# boilerplate ("X has informed the Exchange regarding ..."), so strip the
# wrapper before testing the remainder.
_WRAPPER_RE = re.compile(
    r"^\s*.{0,80}?\b(?:has\s+informed\s+the\s+Exchange\s+(?:regarding|about)|"
    r"has\s+submitted\s+to\s+the\s+Exchange[,]?)\s*",
    re.IGNORECASE,
)
_OUTCOME_ONLY_RE = re.compile(
    r"^\s*(?:the\s+)?outcome\s+of\s+(?:the\s+)?board\s+meeting"
    r"(?:\s+held\s+on\b[^.]{0,40})?\s*[.]?\s*$",
    re.IGNORECASE,
)


def is_payload_free_outcome(summary) -> bool:
    """True for 'Outcome of Board Meeting held on <date>.' rows with no content."""
    s = clean_cell(summary)
    if not s:
        return True
    stripped = _WRAPPER_RE.sub("", s)
    return bool(_OUTCOME_ONLY_RE.match(stripped))


def merge_same_day_filings(rows, join: str = " · ", max_chars: int = 300):
    """Collapse multiple filings by one issuer into a single row.

    Talwalkars' three filings on one day become one row whose summary is the
    three summaries joined by ``join``, truncated to ``max_chars`` with a
    ``(+n more filings)`` suffix naming what was elided.

    Identity is the resolved ticker when present, else the normalised company
    name — so a null-symbol issuer still groups with itself. Row order and the
    first row's other fields are preserved; ``filing_count``, ``summaries`` and
    ``attachment_urls`` are added.
    """
    groups: dict[str, dict] = {}
    order: list[str] = []
    for r in rows:
        ident = clean_cell(r.get("symbol")).upper() or _normalize_company(r.get("company_name"))
        if not ident:
            ident = clean_cell(r.get("seq_id")) or f"__row{len(order)}"
        if ident not in groups:
            merged = dict(r)
            merged["filing_count"] = 0
            merged["summaries"] = []
            merged["attachment_urls"] = []
            groups[ident] = merged
            order.append(ident)
        g = groups[ident]
        g["filing_count"] += 1
        s = clean_cell(r.get("summary"))
        if s and s not in g["summaries"]:
            g["summaries"].append(s)
        u = clean_cell(r.get("attachment_url"))
        if u and u not in g["attachment_urls"]:
            g["attachment_urls"].append(u)

    out = []
    for ident in order:
        g = groups[ident]
        parts = g["summaries"]
        joined = join.join(parts)
        if len(joined) > max_chars:
            kept, used = [], 0
            for p in parts:
                add = len(p) + (len(join) if kept else 0)
                if used + add > max_chars:
                    break
                kept.append(p)
                used += add
            if not kept:  # a single summary already over the cap
                kept = [truncate(parts[0], max_chars)]
            n_more = len(parts) - len(kept)
            joined = join.join(kept)
            if n_more > 0:
                joined += f" (+{n_more} more filing{'s' if n_more != 1 else ''})"
        g["summary"] = joined
        out.append(g)
    return out


# Section v renders individual rows only for coverage names and *substantive*
# press releases. "Substantive" is deal/operations news; awards, CSR and routine
# corporate-citizenship PR collapse into the count line.
#
# Two guards keep this honest, both learned from the 07 Aug corpus:
#
#  - Only narrative categories are eligible. Without this, the keyword test runs
#    against every filing and "Trading Window" / "Spurt in Volume" / "Certificate
#    under SEBI (Depositories and Participants) Regulations" match on their own
#    compliance boilerplate.
#  - Word boundaries, not substrings. Bare "order" also matches "in order to",
#    "recorded" and "border"; bare "sebi" matches every regulation citation.
_PR_ELIGIBLE_CATEGORIES = (
    "press release", "newspaper publication", "general update", "updates",
)
_PR_VETO_CATEGORIES = (
    "trading window", "spurt in volume", "price movement", "certificate under",
    "structural digital database", "news verification", "monitoring agency",
    "compliance", "statement of deviation", "security cover",
)

# "in order to" / "in order that" are ordinary English, not order wins. Word
# boundaries do not help — "order" really is a standalone word there — so the
# phrase is stripped before the substantive test runs.
_ORDER_IDIOM_RE = re.compile(r"\bin\s+order\s+(?:to|that|for)\b", re.IGNORECASE)

_PR_SUBSTANTIVE_RE = re.compile(
    r"\b(?:"
    r"acquisitions?|acquires?|acquired|merger|amalgamation|demerger|"
    r"stake\s+(?:sale|purchase|acquisition)|divest\w*|"
    r"orders?|contracts?|loi|letter\s+of\s+intent|work\s+order|purchase\s+order|"
    r"capacity|capex|expansion|expands?|commission\w*|greenfield|brownfield|"
    r"new\s+plant|manufacturing\s+facility|"
    r"regulatory\s+approval|environmental\s+clearance|"
    r"fund\s+rais\w*|qip|preferential\s+(?:issue|allotment)|rights\s+issue"
    r")\b",
    re.IGNORECASE,
)
_PR_ROUTINE_RE = re.compile(
    r"\b(?:"
    r"awards?|awarded|recognition|recognised|recognized|csr|corporate\s+social|"
    r"felicitat\w*|honour\w*|honor\w*|great\s+place\s+to\s+work|"
    r"certification|anniversary|celebrat\w*|sponsors?\w*"
    r")\b",
    re.IGNORECASE,
)


def is_press_release_like(category) -> bool:
    """True for the narrative categories eligible for the substantive test."""
    c = clean_cell(category).casefold()
    if not c:
        return False
    if any(v in c for v in _PR_VETO_CATEGORIES):
        return False
    return any(e in c for e in _PR_ELIGIBLE_CATEGORIES)


def is_substantive_press_release(category, summary) -> bool:
    """True for deal/operations PR; False for awards, CSR and routine notices.

    Substantive markers win outright: "bags order, wins award" is an order win
    that happens to mention an award, so it stays. A row with only routine
    markers — or with neither — collapses into the count line.
    """
    if not is_press_release_like(category):
        return False
    text = _ORDER_IDIOM_RE.sub(" ", f"{clean_cell(category)} {clean_cell(summary)}")
    if not text.strip():
        return False
    return bool(_PR_SUBSTANTIVE_RE.search(text))


def is_routine_pr(category, summary) -> bool:
    """True when a filing looks like awards/CSR/anniversary PR and nothing more."""
    text = _ORDER_IDIOM_RE.sub(" ", f"{clean_cell(category)} {clean_cell(summary)}")
    return (bool(_PR_ROUTINE_RE.search(text))
            and not bool(_PR_SUBSTANTIVE_RE.search(text)))


# Buckets for section v's categorical count line.
_OTHER_BUCKETS = (
    ("AGM notices",            ("shareholders meeting", "agm", "egm", "postal ballot")),
    ("scrutinizer reports",    ("scrutinizer", "scrutiniser", "voting results")),
    ("KMP changes",            ("appointment", "cessation", "resignation", "change in director",
                                "change in management", "change in auditors", "demise")),
    ("investor presentations", ("investor presentation", "analyst", "investor meet", "con. call")),
    ("press releases",         ("press release", "newspaper publication")),
)


def other_announcement_bucket(category, summary="") -> str:
    """Label a section-v filing for the categorical count line.

    Category is tried first so a press release *about* an appointment counts as
    a press release; the summary is only consulted when the category is silent.
    """
    text = f"{clean_cell(category)} {clean_cell(summary)}".casefold()
    cat = clean_cell(category).casefold()
    for label, needles in _OTHER_BUCKETS:
        if any(n in cat for n in needles):
            return label
    for label, needles in _OTHER_BUCKETS:
        if any(n in text for n in needles):
            return label
    return "other filings"


def count_line(buckets, tail: str = "") -> str:
    """Render '12 AGM notices · 4 KMP changes' from a {label: count} mapping.

    Zero-count buckets are omitted; an empty mapping yields "".
    """
    parts = [f"{n} {label}" for label, n in buckets.items() if n]
    if not parts:
        return ""
    line = " · ".join(parts)
    return f"{line}{tail}" if tail else line
