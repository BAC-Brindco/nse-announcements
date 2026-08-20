"""
Symbol universes used to decide what reaches the email body.

Three universes matter to the report:

  BAC_COVERAGE  the active coverage book (52 holdings)
  PILLAR1       NIFTY50 ∪ NIFTY100 — the index overlay
  NIFTY500      broad-market membership, vendored from NSE

``BODY_UNIVERSE`` is their union and is the gate for the materiality filters in
``reports/assembly.py``. Attachments are never gated — they carry every row.

Two distinct notions of "universe" live here and must not be conflated:

  universe_of(sym)       tape-scoring tier: nifty50 > nifty100 > bac > broader.
                         Deliberately has no NIFTY500 tier — the forward
                         "Next 3 Sessions" table is coverage + Pillar I only,
                         so an N500-but-not-Pillar-I name scores as 'broader'.
  in_body_universe(sym)  membership test for the section filters, which DO
                         include NIFTY500.
"""

from __future__ import annotations

import csv
import io
import logging
import os

logger = logging.getLogger(__name__)

_HERE = os.path.dirname(os.path.abspath(__file__))
_N500_PATH = os.path.join(_HERE, "data", "nifty500.csv")


# ─── NIFTY50 constituents ─────────────────────────────────────────────────────
# Corrections applied 2026-08-19 when cross-checked against the vendored NIFTY500
# list (which is authoritative for live NSE symbols):
#   SBI          → SBIN      (State Bank of India; the old entry never matched)
#   TATAMOTORS   → TMCV/TMPV (demerged into commercial and passenger vehicles)
# Both stale symbols meant the NIFTY50 badge and the Pillar I filters silently
# skipped those names.
NIFTY50 = frozenset({
    "RELIANCE", "TCS", "HDFCBANK", "BHARTIARTL", "ICICIBANK", "INFY", "SBIN",
    "HINDUNILVR", "ITC", "LT", "KOTAKBANK", "AXISBANK", "BAJFINANCE", "BAJAJFINSV",
    "SBILIFE", "HCLTECH", "MARUTI", "TITAN", "SUNPHARMA", "ULTRACEMCO", "NTPC",
    "ONGC", "POWERGRID", "TMCV", "TMPV", "WIPRO", "TECHM", "NESTLEIND", "ASIANPAINT",
    "DRREDDY", "JSWSTEEL", "TATASTEEL", "ADANIENT", "ADANIPORTS", "CIPLA", "DIVISLAB",
    "APOLLOHOSP", "TRENT", "AMBUJACEM", "SBICARD", "BPCL", "COALINDIA", "HINDALCO",
    "GRASIM", "EICHERMOT", "HEROMOTOCO", "BAJAJ-AUTO", "INDUSINDBK", "HDFCLIFE",
    "BRITANNIA", "SHRIRAMFIN", "M&M",
})

# ─── NIFTY100 members NOT in NIFTY50 ─────────────────────────────────────────
# MCDOWELL-N → UNITDSPR (renamed). ZOMATO retained as a dead alias: the company
# now files as ETERNAL, which is already listed, so the entry is inert.
NIFTY100_ONLY = frozenset({
    "TATAELXSI", "COFORGE", "INDIACEM", "ASTRAMICRO", "CANBK", "INDIANB",
    "NAVINFLUOR", "JMFINANCIL", "VOLTAS", "GODREJCP", "BERGEPAINT", "SIEMENS",
    "HAVELLS", "ABB", "PIDILITIND", "TORNTPHARM", "DABUR", "MARICO", "COLPAL",
    "ICICIPRULI", "HDFCAMC", "BANDHANBNK", "FEDERALBNK", "IDFCFIRSTB", "PNB",
    "BANKBARODA", "CANFINHOME", "MUTHOOTFIN", "CHOLAFIN", "GAIL", "IOC",
    "HINDPETRO", "ATGL", "APLAPOLLO", "JKCEMENT", "SHREECEM", "TATACOMM",
    "UNITDSPR", "LTIM", "PERSISTENT", "DMART", "ZOMATO", "ETERNAL",
    "PAYTM", "NYKAA", "POLICYBZR", "IRCTC", "HAL", "BEL", "RVNL",
    # Portfolio additions
    "ADANIGREEN", "INDUSTOWER", "JSWENERGY", "TVSMOTOR", "VBL",
    "TATAPOWER", "POLYCAB", "NMDC", "CGPOWER", "MAZDOCK", "GRSE",
})

# ─── BAC active coverage universe (full portfolio — 52 holdings) ──────────────
BAC_COVERAGE = frozenset({
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

NIFTY100 = NIFTY50 | NIFTY100_ONLY
PILLAR1 = NIFTY100


def _load_nifty500(path: str = _N500_PATH) -> frozenset[str]:
    """Read the vendored NIFTY500 constituent list.

    The file carries a ``#``-prefixed provenance header that csv.DictReader would
    otherwise consume as the header row, so comment lines are stripped first.
    A missing or unreadable file degrades to an empty set: the section filters
    then fall back to BAC_COVERAGE ∪ PILLAR1 rather than failing the report.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            body = "".join(l for l in fh if not l.lstrip().startswith("#"))
    except OSError as exc:
        logger.warning("NIFTY500 list unavailable (%s) — falling back to BAC ∪ Pillar I", exc)
        return frozenset()

    syms = {
        (row.get("symbol") or "").strip().upper()
        for row in csv.DictReader(io.StringIO(body))
    }
    syms.discard("")
    if not syms:
        logger.warning("NIFTY500 list at %s parsed to zero symbols", path)
    return frozenset(syms)


NIFTY500 = _load_nifty500()

# The gate for the body-section materiality filters.
BODY_UNIVERSE = BAC_COVERAGE | PILLAR1 | NIFTY500


def universe_of(symbol: str | None) -> str:
    """Tape-scoring tier. See the module docstring on why NIFTY500 is absent."""
    sym = (symbol or "").strip()
    if sym in NIFTY50:
        return "nifty50"
    if sym in NIFTY100_ONLY:
        return "nifty100"
    if sym in BAC_COVERAGE:
        return "bac"
    return "broader"


def in_body_universe(symbol: str | None) -> bool:
    """True when a symbol earns a row in the filtered email body."""
    return (symbol or "").strip() in BODY_UNIVERSE


def is_coverage(symbol: str | None) -> bool:
    return (symbol or "").strip() in BAC_COVERAGE


def is_pillar1(symbol: str | None) -> bool:
    return (symbol or "").strip() in PILLAR1


def index_label(symbol: str | None) -> str:
    """'NIFTY50' | 'NIFTY100' | '' — the badge text for a symbol."""
    sym = (symbol or "").strip()
    if sym in NIFTY50:
        return "NIFTY50"
    if sym in NIFTY100_ONLY:
        return "NIFTY100"
    return ""
