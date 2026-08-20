"""
Typed loader for reports/config.yaml.

The report should never crash because a config key was renamed or removed, so
every field carries a default and unknown keys are ignored. ``load_config()``
is cached — the file is read once per process.

Editing config.yaml changes the email body without touching code. Nothing here
can affect the PDF/CSV attachments, which are built from the unfiltered
assembly by construction.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field, fields

logger = logging.getLogger(__name__)

_HERE = os.path.dirname(os.path.abspath(__file__))
_CONFIG_PATH = os.path.join(_HERE, "config.yaml")


@dataclass(frozen=True)
class UniverseCfg:
    use_bac_coverage: bool = True
    use_pillar1: bool = True
    use_nifty500: bool = True


@dataclass(frozen=True)
class BoardMeetingsCfg:
    horizon_days: int = 14
    body_cap: int = 25
    always_keep_purpose_contains: tuple[str, ...] = ("FUND RAIS", "DELIST")
    drop_purpose_for_non_coverage_sme: tuple[str, ...] = ("Other business matters",)


@dataclass(frozen=True)
class EventCalendarCfg:
    horizon_sessions: int = 5
    body_cap: int = 30


@dataclass(frozen=True)
class CorporateActionsCfg:
    horizon_days: int = 7
    body_cap: int = 20
    non_universe_keep_kinds: tuple[str, ...] = ("rights", "bonus", "split", "buyback")


@dataclass(frozen=True)
class KeyAnnouncementsCfg:
    categories: tuple[str, ...] = ()
    category_contains: tuple[str, ...] = ()
    record_date_categories: tuple[str, ...] = ("Record Date", "Record Date Updates")
    record_date_universe_only: bool = True
    drop_payload_free_outcomes: bool = True
    body_cap: int = 40


@dataclass(frozen=True)
class OtherAnnouncementsCfg:
    rows_for_coverage_only: bool = True
    include_substantive_press_releases: bool = True
    body_cap: int = 25


@dataclass(frozen=True)
class DebtMarketCfg:
    credit_rating_any_issuer: bool = True
    credit_rating_category_contains: tuple[str, ...] = ("credit rating",)
    body_cap: int = 20


@dataclass(frozen=True)
class MergeCfg:
    join: str = " · "
    summary_max_chars: int = 300


@dataclass(frozen=True)
class NextSessionsCfg:
    count: int = 3


@dataclass(frozen=True)
class AttachmentsCfg:
    pdf_enabled: bool = True
    csv_enabled: bool = True
    max_pdf_rows_per_section: int = 0


@dataclass(frozen=True)
class ReportConfig:
    universe: UniverseCfg = field(default_factory=UniverseCfg)
    board_meetings: BoardMeetingsCfg = field(default_factory=BoardMeetingsCfg)
    event_calendar: EventCalendarCfg = field(default_factory=EventCalendarCfg)
    corporate_actions: CorporateActionsCfg = field(default_factory=CorporateActionsCfg)
    key_announcements: KeyAnnouncementsCfg = field(default_factory=KeyAnnouncementsCfg)
    other_announcements: OtherAnnouncementsCfg = field(default_factory=OtherAnnouncementsCfg)
    debt_market: DebtMarketCfg = field(default_factory=DebtMarketCfg)
    merge: MergeCfg = field(default_factory=MergeCfg)
    next_sessions: NextSessionsCfg = field(default_factory=NextSessionsCfg)
    attachments: AttachmentsCfg = field(default_factory=AttachmentsCfg)


# Mapped explicitly rather than via ``f.type``: ``from __future__ import
# annotations`` makes every field annotation a *string*, so f.type would hand
# _coerce the name of a class instead of the class.
_SECTION_TYPES: dict[str, type] = {
    "universe": UniverseCfg,
    "board_meetings": BoardMeetingsCfg,
    "event_calendar": EventCalendarCfg,
    "corporate_actions": CorporateActionsCfg,
    "key_announcements": KeyAnnouncementsCfg,
    "other_announcements": OtherAnnouncementsCfg,
    "debt_market": DebtMarketCfg,
    "merge": MergeCfg,
    "next_sessions": NextSessionsCfg,
    "attachments": AttachmentsCfg,
}
assert _SECTION_TYPES.keys() == {f.name for f in fields(ReportConfig)}


def _coerce(cls, raw: dict):
    """Build a frozen dataclass from a dict, ignoring unknown keys.

    Tuple-typed fields accept a YAML list; everything else passes through.
    """
    if not isinstance(raw, dict):
        return cls()
    kwargs = {}
    for f in fields(cls):
        if f.name not in raw:
            continue
        val = raw[f.name]
        if isinstance(val, list):
            val = tuple(val)
        kwargs[f.name] = val
    try:
        return cls(**kwargs)
    except TypeError as exc:
        logger.warning("Bad config for %s (%s) — using defaults", cls.__name__, exc)
        return cls()


_cached: ReportConfig | None = None


def load_config(path: str | None = None, force: bool = False) -> ReportConfig:
    """Read config.yaml into a ReportConfig. Missing file → all defaults."""
    global _cached
    if _cached is not None and not force and path is None:
        return _cached

    target = path or _CONFIG_PATH
    raw: dict = {}
    try:
        import yaml
    except ImportError:
        logger.warning("PyYAML not installed — using built-in report defaults")
        yaml = None
    if yaml is not None:
        try:
            with open(target, encoding="utf-8") as fh:
                raw = yaml.safe_load(fh) or {}
        except OSError as exc:
            logger.warning("Config %s unreadable (%s) — using defaults", target, exc)
        except Exception as exc:  # noqa: BLE001 — malformed YAML must not kill the report
            logger.warning("Config %s failed to parse (%s) — using defaults", target, exc)

    cfg = ReportConfig(**{
        name: _coerce(_SECTION_TYPES[name], raw.get(name, {}))
        for name in _SECTION_TYPES
    })
    if path is None:
        _cached = cfg
    return cfg
