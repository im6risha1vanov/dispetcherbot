-- Принятие заявки в две ступени: напоминание на 5-й минуте, переназначение на
-- 15-й. Флаг нужен, чтобы напоминание ушло один раз, а не на каждом опросе.
-- Применение: psql "$DATABASE_URL" -f migrations/005_accept_reminder.sql

ALTER TABLE assignments ADD COLUMN IF NOT EXISTS reminder_sent_at TIMESTAMPTZ;
