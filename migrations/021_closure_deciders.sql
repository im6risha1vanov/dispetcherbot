-- Отчёт уходит сразу администратору и директору: решает тот, кто нажал первым.
-- Чтобы погасить кнопки у второго, помним все копии сообщения, а не одну.
-- Старые admin_chat_id/admin_message_id остаются: по ним видно, куда ушёл
-- отчёт до разделения ролей, и переносить историю незачем.
--
-- Применение: psql "$DATABASE_URL" -f migrations/021_closure_deciders.sql

ALTER TABLE closures
    ADD COLUMN IF NOT EXISTS decider_messages JSONB NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE closures ADD COLUMN IF NOT EXISTS decided_by TEXT;
