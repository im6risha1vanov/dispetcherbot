-- Сбои копятся, чтобы вечером собрать сводку владельцу.
--
-- Сообщение о сбое уходит сразу, но одинаковые схлопываются и поток ограничен
-- дюжиной в час: иначе заваленный чат читают так же, как журнал сервера.
-- Поэтому в 21:00 нужна честная сводка — включая то, о чём промолчали.
--
-- Применение: psql "$DATABASE_URL" -f migrations/022_failures.sql

CREATE TABLE IF NOT EXISTS failures (
    id           BIGSERIAL PRIMARY KEY,
    happened_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    source       TEXT NOT NULL,   -- где: «боте» или «опросе CRM»
    category     TEXT NOT NULL,   -- класс сбоя, по нему даётся совет
    summary      TEXT NOT NULL,   -- одна русская строка для человека
    level        TEXT NOT NULL    -- warning или error
);

CREATE INDEX IF NOT EXISTS failures_happened_idx ON failures (happened_at);
