-- В чате мастера закреплена его текущая заявка. Помним, что именно закреплено,
-- чтобы откреплять точечно, а не сбрасывать все закрепления чата.
-- Применение: psql "$DATABASE_URL" -f migrations/011_pinned.sql

ALTER TABLE masters ADD COLUMN IF NOT EXISTS pinned_message_id BIGINT;
