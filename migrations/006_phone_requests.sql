-- Запрос номера клиента у диспетчеров: мастер жмёт кнопку, бот спрашивает в
-- чате диспетчеров, диспетчер отвечает реплаем, бот передаёт номер мастеру.
-- Связь держится по id сообщения бота в чате диспетчеров: реплай на него
-- однозначно указывает, о какой заявке речь.
-- Применение: psql "$DATABASE_URL" -f migrations/006_phone_requests.sql

CREATE TABLE IF NOT EXISTS phone_requests (
    id                     BIGSERIAL PRIMARY KEY,
    crm_id                 BIGINT NOT NULL,
    employee_id            BIGINT REFERENCES masters(employee_id),
    master_chat_id         BIGINT NOT NULL,
    dispatcher_message_id  BIGINT,
    asked_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    answered_at            TIMESTAMPTZ,
    answered_by            TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS phone_requests_message_idx
    ON phone_requests (dispatcher_message_id)
    WHERE dispatcher_message_id IS NOT NULL;
