-- Строка заявки в том виде, в каком её копирует администратор из CRM.
-- Храним, чтобы напоминание не ходило за карточкой второй раз.
-- Применение: psql "$DATABASE_URL" -f migrations/008_info_line.sql

ALTER TABLE requests ADD COLUMN IF NOT EXISTS info_line TEXT;
