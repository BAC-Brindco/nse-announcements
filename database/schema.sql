-- NSE Announcements Pipeline — Supabase schema
-- Run once on the new Supabase project before first scrape.

-- ── Corporate Announcements ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS corporate_announcements (
    id              BIGSERIAL PRIMARY KEY,
    seq_id          TEXT        NOT NULL UNIQUE,   -- NSE sequence ID
    segment         TEXT        NOT NULL,           -- 'equities' | 'sme' | 'debt'
    symbol          TEXT,                           -- null for debt segment
    company_name    TEXT,
    isin            TEXT,
    industry        TEXT,
    category        TEXT,                           -- desc field from NSE
    summary         TEXT,                           -- attchmntText
    attachment_url  TEXT,
    attachment_size TEXT,
    announced_at    TIMESTAMPTZ,                    -- an_dt parsed to IST→UTC
    scrape_date     DATE        NOT NULL,
    raw_payload     JSONB,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_announcements_announced_at ON corporate_announcements (announced_at);
CREATE INDEX IF NOT EXISTS idx_announcements_segment      ON corporate_announcements (segment);
CREATE INDEX IF NOT EXISTS idx_announcements_category     ON corporate_announcements (category);
CREATE INDEX IF NOT EXISTS idx_announcements_symbol       ON corporate_announcements (symbol);

-- ── Board Meetings ─────────────────────────────────────────────────────────────
-- Sourced from BOTH /api/corporate-board-meetings AND /api/event-calendar.
-- event-calendar rows use source='event_calendar'; board-meetings rows use source='board_meetings'.
CREATE TABLE IF NOT EXISTS board_meetings (
    id             BIGSERIAL PRIMARY KEY,
    symbol         TEXT        NOT NULL,
    segment        TEXT        NOT NULL,   -- 'equities' | 'sme'
    source         TEXT        NOT NULL,   -- 'board_meetings' | 'event_calendar'
    meeting_date   DATE        NOT NULL,
    purpose        TEXT,
    description    TEXT,
    company_name   TEXT,
    isin           TEXT,
    attachment_url TEXT,
    filed_at       TIMESTAMPTZ,
    scrape_date    DATE        NOT NULL,
    raw_payload    JSONB,
    created_at     TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (symbol, meeting_date, purpose, source)
);

CREATE INDEX IF NOT EXISTS idx_bm_meeting_date ON board_meetings (meeting_date);
CREATE INDEX IF NOT EXISTS idx_bm_symbol       ON board_meetings (symbol);

-- ── Corporate Actions ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS corporate_actions (
    id           BIGSERIAL PRIMARY KEY,
    symbol       TEXT        NOT NULL,
    company      TEXT,
    isin         TEXT,
    series       TEXT,
    segment      TEXT        NOT NULL,   -- 'equities' | 'sme'
    ex_date      DATE,
    record_date  DATE,
    subject      TEXT,
    face_val     TEXT,
    broadcast_at TIMESTAMPTZ,
    scrape_date  DATE        NOT NULL,
    raw_payload  JSONB,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (symbol, ex_date, subject)
);

CREATE INDEX IF NOT EXISTS idx_ca_ex_date ON corporate_actions (ex_date);
CREATE INDEX IF NOT EXISTS idx_ca_symbol  ON corporate_actions (symbol);

-- ── Report log (idempotency) ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS report_log (
    id          BIGSERIAL PRIMARY KEY,
    report_type TEXT        NOT NULL,
    report_date DATE        NOT NULL,
    status      TEXT        NOT NULL DEFAULT 'pending',
    recipients  TEXT,
    error_message TEXT,
    sent_at     TIMESTAMPTZ,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (report_type, report_date)
);
