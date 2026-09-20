-- Сложная диагностика: мастер забирает технику, заявка живёт неделями и
-- закрывается отдельным сценарием. Разновидность отчёта хранится явно, потому
-- что вопросы и окна для фото у них разные.
-- Применение: psql "$DATABASE_URL" -f migrations/015_sd.sql

ALTER TABLE closures ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'close';
-- close | sd_open | sd_close

-- Ответы мастера при открытии СД: уходят в «Комментарий филиала» как есть.
ALTER TABLE closures ADD COLUMN IF NOT EXISTS prepayment TEXT;
ALTER TABLE closures ADD COLUMN IF NOT EXISTS agreed_sum TEXT;
ALTER TABLE closures ADD COLUMN IF NOT EXISTS deadline TEXT;
ALTER TABLE closures ADD COLUMN IF NOT EXISTS malfunction TEXT;
