-- Закрытие заявки: бот ведёт мастера по полям, которые CRM требует заполнить,
-- и запоминает ответы. Состояние диалога живёт в базе, а не в памяти процесса:
-- мастер может отвечать полчаса, а бот за это время успеет перезапуститься.
-- Применение: psql "$DATABASE_URL" -f migrations/014_closures.sql

CREATE TABLE IF NOT EXISTS closures (
    id                 BIGSERIAL PRIMARY KEY,
    crm_id             BIGINT NOT NULL,
    employee_id        BIGINT NOT NULL REFERENCES masters(employee_id),
    chat_id            BIGINT NOT NULL,
    step               TEXT NOT NULL DEFAULT 'payed',
    state              TEXT NOT NULL DEFAULT 'collecting',
    -- collecting | pending_admin | approved | rejected | written

    payed_by_customer  NUMERIC(12, 2),
    spares_cost        NUMERIC(12, 2),
    with_bso           TEXT,
    with_zip           TEXT,
    receipt_mode       TEXT,
    fback_mode         TEXT,
    photos             JSONB NOT NULL DEFAULT '{}'::jsonb,

    admin_chat_id      BIGINT,
    admin_message_id   BIGINT,
    reject_reason      TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    submitted_at       TIMESTAMPTZ,
    decided_at         TIMESTAMPTZ,
    written_at         TIMESTAMPTZ
);

-- Незавершённый отчёт у мастера может быть только один: иначе непонятно,
-- к какой заявке относится присланное фото.
CREATE UNIQUE INDEX IF NOT EXISTS closures_active_master_idx
    ON closures (employee_id)
    WHERE state IN ('collecting', 'pending_admin', 'rejected');

CREATE INDEX IF NOT EXISTS closures_crm_idx ON closures (crm_id);
