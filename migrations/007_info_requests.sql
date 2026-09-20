-- Мастер спрашивает у диспетчеров не только телефон, но и номер квартиры.
-- Механизм один, отличается только вид запроса.
-- Применение: psql "$DATABASE_URL" -f migrations/007_info_requests.sql

ALTER TABLE phone_requests RENAME TO info_requests;
ALTER TABLE info_requests ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'phone';

ALTER INDEX IF EXISTS phone_requests_message_idx RENAME TO info_requests_message_idx;
ALTER INDEX IF EXISTS phone_requests_pkey RENAME TO info_requests_pkey;
