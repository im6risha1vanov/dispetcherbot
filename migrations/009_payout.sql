-- Расчёт уходит мастеру один раз, когда администратор закрыл заявку.
-- Применение: psql "$DATABASE_URL" -f migrations/009_payout.sql

ALTER TABLE assignments ADD COLUMN IF NOT EXISTS payout_sent_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS assignments_payout_pending_idx
    ON assignments (city_id)
    WHERE payout_sent_at IS NULL;
