"""
Rolling 20-session metric store (scaffold — issue 21).

Trailing-average annotations on the KPI cards ("22 board meetings, 4-wk avg 18 —
elevated") need a small persisted history of daily headline counts. This module
defines the interface and a JSON-file fallback; the production path should write
to a Supabase table instead.

STATUS: scaffold. Wired into the report as a graceful no-op — when no history is
available, annotations are simply omitted, so the report is unaffected.

TODO(issue 21):
  - Create a `report_metrics` table: (report_date PK, equity_filings, board_meetings,
    event_calendar, corporate_actions, created_at).
  - On each successful run, upsert today's headline counts (call record_metrics()).
  - Backfill ~20 trading sessions before enabling the annotations in the UI.
  - Replace the JSON fallback below with Supabase reads/writes.
"""

from __future__ import annotations

import json
import os
from datetime import date

_STORE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_metrics_history.json")
_WINDOW = 20  # trailing sessions (~4 weeks)


def _load() -> dict:
    if not os.path.exists(_STORE_PATH):
        return {}
    try:
        with open(_STORE_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def record_metrics(report_date: date, metrics: dict[str, int]) -> None:
    """Persist today's headline counts. TODO: point at Supabase report_metrics."""
    data = _load()
    data[report_date.isoformat()] = {k: int(v) for k, v in metrics.items()}
    try:
        with open(_STORE_PATH, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
    except OSError:
        pass


def trailing_average(metric: str, before: date, window: int = _WINDOW) -> float | None:
    """Mean of ``metric`` over the most recent ``window`` stored sessions strictly
    before ``before``. Returns None when there isn't enough history yet."""
    data = _load()
    rows = sorted(
        (d, m) for d, m in data.items()
        if d < before.isoformat() and metric in m
    )
    recent = rows[-window:]
    if len(recent) < max(3, window // 4):  # need a minimum sample before annotating
        return None
    return sum(m[metric] for _, m in recent) / len(recent)


def trend_label(current: int, avg: float | None, tol: float = 0.15) -> str:
    """'elevated' / 'subdued' / '' relative to the trailing average."""
    if avg is None or avg <= 0:
        return ""
    if current >= avg * (1 + tol):
        return "elevated"
    if current <= avg * (1 - tol):
        return "subdued"
    return "in line"
