-- Директор @Dedoo4ek не мастер: выключаем строку из 018, чтобы он не попадал
-- в очередь смены и раздачу заявок. Алерты директору идут по DIRECTOR_CHAT_ID,
-- закрытие чужих заявок в чате мастеров — по telegram id, без этой строки.
-- Не трогаем остальных мастеров. Применение:
--   psql "$DATABASE_URL" -f migrations/019_director_not_master.sql

UPDATE masters
SET is_active = FALSE
WHERE employee_id = 708703366
   OR telegram_id = 708703366
   OR lower(telegram_username) = 'dedoo4ek';

DELETE FROM shifts
WHERE employee_id IN (
    SELECT employee_id FROM masters
    WHERE employee_id = 708703366
       OR telegram_id = 708703366
       OR lower(coalesce(telegram_username, '')) = 'dedoo4ek'
);
