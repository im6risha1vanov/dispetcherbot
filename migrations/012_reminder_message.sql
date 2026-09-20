-- Напоминание о непринятой заявке — дубль в чате мастера. Как только заявка
-- принята или ушла другому, напоминание теряет смысл и удаляется, поэтому
-- запоминаем его id.
-- Применение: psql "$DATABASE_URL" -f migrations/012_reminder_message.sql

ALTER TABLE assignments ADD COLUMN IF NOT EXISTS reminder_message_id BIGINT;
