-- У каждого мастера свой рабочий чат: туда уходит заявка и туда же он шлёт
-- отчёт. Человек и адрес доставки — разные вещи: telegram_id говорит, КТО
-- нажал кнопку, chat_id — КУДА слать заявку.
-- Применение: psql "$DATABASE_URL" -f migrations/004_master_chat.sql

ALTER TABLE masters ADD COLUMN IF NOT EXISTS chat_id BIGINT;

COMMENT ON COLUMN masters.telegram_id IS 'кто нажал кнопку';
COMMENT ON COLUMN masters.chat_id IS 'рабочий чат мастера, куда уходит заявка';
