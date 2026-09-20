-- Переписка по отчёту — вопросы бота, фото мастера и подтверждения приёма —
-- после отправки администратору теряет смысл и убирается из чата мастера.
-- У администратора она остаётся: там это рабочий документ.
-- Применение: psql "$DATABASE_URL" -f migrations/016_closure_messages.sql

ALTER TABLE closures ADD COLUMN IF NOT EXISTS chat_messages JSONB NOT NULL DEFAULT '[]'::jsonb;
