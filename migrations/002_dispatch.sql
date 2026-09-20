-- Этап 2: распределение заявок, кнопки мастера, таймауты.
-- Применение: psql "$DATABASE_URL" -f migrations/002_dispatch.sql

-- Заявка, пропавшая из грида, закрыта в CRM: таймеры по ней больше не звонят.
ALTER TABLE requests ADD COLUMN IF NOT EXISTS is_open BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE requests ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS masters (
    employee_id BIGINT PRIMARY KEY,            -- id из CRM
    city_id     INT NOT NULL DEFAULT 206,
    full_name   TEXT NOT NULL,
    telegram_id BIGINT UNIQUE,                 -- NULL, пока админ не привязал
    is_active   BOOLEAN NOT NULL DEFAULT TRUE, -- выключает директор вручную
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Очередь дня: позиция = порядок утреннего плюса, дальше ротация по кругу.
CREATE TABLE IF NOT EXISTS shifts (
    shift_date  DATE NOT NULL,
    employee_id BIGINT NOT NULL REFERENCES masters(employee_id),
    city_id     INT NOT NULL DEFAULT 206,
    position    INT NOT NULL,
    marked_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (shift_date, employee_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS shifts_position_idx
    ON shifts (shift_date, city_id, position);

CREATE TABLE IF NOT EXISTS assignments (
    id                BIGSERIAL PRIMARY KEY,
    crm_id            BIGINT NOT NULL REFERENCES requests(crm_id),
    employee_id       BIGINT NOT NULL REFERENCES masters(employee_id),
    city_id           INT NOT NULL DEFAULT 206,
    state             TEXT NOT NULL DEFAULT 'assigned',  -- assigned | enroute | onsite | reassigned
    assigned_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    enroute_at        TIMESTAMPTZ,
    onsite_at         TIMESTAMPTZ,
    reassigned_at     TIMESTAMPTZ,
    chat_id           BIGINT,
    message_id        BIGINT,
    onsite_alert_sent BOOLEAN NOT NULL DEFAULT FALSE
);

-- Активное назначение у заявки ровно одно.
CREATE UNIQUE INDEX IF NOT EXISTS assignments_active_idx
    ON assignments (crm_id)
    WHERE state <> 'reassigned';

CREATE INDEX IF NOT EXISTS assignments_state_idx
    ON assignments (state, assigned_at);

-- Мастера филиала Сыктывкар из раздела 4 контракта. telegram_id привязывает
-- администратор командой /master_link. Кузиванов уволен — выключен сразу.
INSERT INTO masters (employee_id, full_name, city_id, is_active) VALUES
    (10679, 'Габидуллин Ким',        206, TRUE),
    (12330, 'Гаев Роман',            206, TRUE),
    (10254, 'Делайчук Глеб',         206, TRUE),
    (12645, 'Кедюлич Николай',       206, TRUE),
    (8771,  'Кузиванов Илья',        206, FALSE),
    (11982, 'Кулемалин Георгий',     206, TRUE),
    (11774, 'Липин Артем',           206, TRUE),
    (11391, 'Мишарин Олег',          206, TRUE),
    (9342,  'Немазанников Богдан',   206, TRUE),
    (11236, 'Соловьев Владислав',    206, TRUE)
ON CONFLICT (employee_id) DO NOTHING;
