-- Привязка мастера по @username: telegram_id узнаётся только когда человек сам
-- напишет боту, а username можно прописать заранее.
-- Применение: psql "$DATABASE_URL" -f migrations/003_master_username.sql

ALTER TABLE masters ADD COLUMN IF NOT EXISTS telegram_username TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS masters_username_idx
    ON masters (lower(telegram_username))
    WHERE telegram_username IS NOT NULL;
