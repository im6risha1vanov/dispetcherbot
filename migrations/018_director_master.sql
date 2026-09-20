-- Директор @Dedoo4ek получает права мастера: те же кнопки и очередь,
-- что у включённых мастеров. В CRM сотрудника с таким id нет — запись
-- назначения в карточку CRM для него не уйдёт (см. crm.assign_master).
-- telegram_id = DIRECTOR_CHAT_ID (личка). Не трогаем выключенных мастеров.
-- Применение: psql "$DATABASE_URL" -f migrations/018_director_master.sql

INSERT INTO masters (
    employee_id, full_name, city_id, is_active, telegram_id, telegram_username
) VALUES (
    708703366, 'Dedo4ek', 206, TRUE, 708703366, 'Dedoo4ek'
)
ON CONFLICT (employee_id) DO UPDATE SET
    full_name = EXCLUDED.full_name,
    telegram_id = EXCLUDED.telegram_id,
    telegram_username = EXCLUDED.telegram_username,
    is_active = TRUE;
