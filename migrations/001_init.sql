-- Этап 1: чтение и уведомления.
-- Применение: psql "$DATABASE_URL" -f migrations/001_init.sql

CREATE TABLE IF NOT EXISTS requests (
    crm_id           BIGINT PRIMARY KEY,
    city_id          INT NOT NULL DEFAULT 206,
    status_code      INT,
    status_text      TEXT,
    is_recall        BOOLEAN NOT NULL DEFAULT FALSE,
    req_type         TEXT,
    opened_at        TIMESTAMPTZ,
    customer_name    TEXT,
    address          TEXT,
    master_name      TEXT,
    created_at_local TIMESTAMPTZ,
    closed_at_local  TIMESTAMPTZ,
    total_cost       NUMERIC(12, 2),
    raw_grid_row     JSONB,
    first_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    notified_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS requests_pending_notify_idx
    ON requests (city_id, crm_id)
    WHERE notified_at IS NULL;

CREATE TABLE IF NOT EXISTS app_state (
    key   TEXT PRIMARY KEY,
    value TEXT
);
