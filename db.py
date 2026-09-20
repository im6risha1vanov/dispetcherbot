import json
import logging

import asyncpg

import config

log = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


def _director_skip_ids() -> set[int]:
    """Директор не мастер: его id не должен попадать в очередь и занятость."""
    raw = config.DIRECTOR_CHAT_ID
    if not raw:
        return set()
    try:
        return {int(raw)}
    except ValueError:
        return set()


def _json_dumps(value) -> str:
    # insert_request уже передаёт json.dumps(...): повторно не оборачиваем.
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


async def _prepare(conn: asyncpg.Connection) -> None:
    """asyncpg 0.31 без format='text' оставляет jsonb строкой — .items() падает."""
    await conn.set_type_codec(
        "jsonb", encoder=_json_dumps, decoder=json.loads, schema="pg_catalog", format="text"
    )
    await conn.set_type_codec(
        "json", encoder=_json_dumps, decoder=json.loads, schema="pg_catalog", format="text"
    )


async def connect() -> None:
    global _pool
    _pool = await asyncpg.create_pool(
        config.DATABASE_URL, min_size=1, max_size=5, init=_prepare
    )


async def close() -> None:
    if _pool is not None:
        await _pool.close()


async def insert_request(row, city_id: int, *, notified: bool, is_open: bool = True) -> bool:
    """Возвращает True, если заявка увидена впервые.

    Уже известные заявки обновляются: статус и мастер меняются в CRM, а по ним
    работают таймеры. xmax = 0 у Postgres означает вставку, а не обновление.
    """
    is_new = await _pool.fetchval(
        """
        INSERT INTO requests (
            crm_id, city_id, status_text, is_recall, req_type, opened_at,
            customer_name, address, master_name, created_at_local,
            closed_at_local, total_cost, raw_grid_row, notified_at,
            is_open, last_seen_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13::jsonb,
                CASE WHEN $14::boolean THEN now() ELSE NULL END, $15::boolean, now())
        ON CONFLICT (crm_id) DO UPDATE SET
            status_text     = EXCLUDED.status_text,
            is_recall       = EXCLUDED.is_recall,
            master_name     = EXCLUDED.master_name,
            closed_at_local = EXCLUDED.closed_at_local,
            total_cost      = EXCLUDED.total_cost,
            raw_grid_row    = EXCLUDED.raw_grid_row,
            is_open         = EXCLUDED.is_open,
            last_seen_at    = now()
        RETURNING (xmax = 0)
        """,
        row.crm_id,
        city_id,
        row.status_text,
        row.is_recall,
        row.req_type,
        row.opened_at,
        row.customer_name,
        row.address,
        row.master_name,
        row.created_at_local,
        row.closed_at_local,
        row.total_cost,
        json.dumps(row.raw_cells, ensure_ascii=False),
        notified,
        is_open,
    )
    return bool(is_new)


async def close_requests_absent_from_grid(city_id: int, seen_ids: list[int]) -> int:
    """Грид отдаёт только открытые заявки: чего в нём нет — закрыто в CRM."""
    return await _pool.fetchval(
        """
        WITH closed AS (
            UPDATE requests SET is_open = FALSE
            WHERE city_id = $1 AND is_open AND NOT (crm_id = ANY($2::bigint[]))
            RETURNING 1
        )
        SELECT count(*) FROM closed
        """,
        city_id,
        seen_ids,
    )


async def pending_notifications(
    city_id: int, silent_statuses: list[str], limit: int = 50
) -> list[asyncpg.Record]:
    """Заявки в «тихих» статусах помечаем увиденными, но в ленту не шлём."""
    await _pool.execute(
        """
        UPDATE requests SET notified_at = now()
        WHERE notified_at IS NULL AND city_id = $1 AND status_text = ANY($2::text[])
        """,
        city_id,
        silent_statuses,
    )
    return await _pool.fetch(
        """
        SELECT crm_id, status_text, is_recall, req_type, opened_at,
               customer_name, address, info_line, prior_master_name
        FROM requests
        WHERE notified_at IS NULL AND city_id = $1
        ORDER BY crm_id
        LIMIT $2
        """,
        city_id,
        limit,
    )


async def save_card_facts(crm_id: int, info_line: str, prior_master: str) -> None:
    await _pool.execute(
        """
        UPDATE requests
        SET info_line = coalesce(nullif($2, ''), info_line),
            prior_master_name = coalesce(nullif($3, ''), prior_master_name)
        WHERE crm_id = $1
        """,
        crm_id,
        info_line,
        prior_master,
    )


async def master_by_name(full_name: str) -> asyncpg.Record | None:
    """Ищем по ФИО: в истории заказов мастер указан именем, а не id."""
    if not full_name:
        return None
    return await _pool.fetchrow(
        "SELECT * FROM masters WHERE lower(full_name) = lower($1)", full_name.strip()
    )


async def mark_notified(crm_id: int) -> None:
    await _pool.execute("UPDATE requests SET notified_at = now() WHERE crm_id = $1", crm_id)


async def upsert_master(employee_id: int, full_name: str, city_id: int) -> None:
    await _pool.execute(
        """
        INSERT INTO masters (employee_id, full_name, city_id) VALUES ($1, $2, $3)
        ON CONFLICT (employee_id) DO UPDATE SET full_name = EXCLUDED.full_name
        """,
        employee_id,
        full_name,
        city_id,
    )


async def link_telegram(employee_id: int, telegram_id: int) -> bool:
    updated = await _pool.fetchval(
        "UPDATE masters SET telegram_id = $2 WHERE employee_id = $1 RETURNING employee_id",
        employee_id,
        telegram_id,
    )
    return updated is not None


async def set_master_active(employee_id: int, is_active: bool) -> bool:
    updated = await _pool.fetchval(
        "UPDATE masters SET is_active = $2 WHERE employee_id = $1 RETURNING employee_id",
        employee_id,
        is_active,
    )
    return updated is not None


async def set_master_username(employee_id: int, username: str) -> bool:
    updated = await _pool.fetchval(
        "UPDATE masters SET telegram_username = $2 WHERE employee_id = $1 RETURNING employee_id",
        employee_id,
        username.lstrip("@"),
    )
    return updated is not None


async def claim_master_by_username(username: str, telegram_id: int) -> asyncpg.Record | None:
    """Привязывает telegram_id мастеру, чей @username прописан заранее."""
    if not username:
        return None
    return await _pool.fetchrow(
        """
        UPDATE masters SET telegram_id = $2
        WHERE lower(telegram_username) = lower($1) AND telegram_id IS DISTINCT FROM $2
        RETURNING *
        """,
        username.lstrip("@"),
        telegram_id,
    )


async def master_by_employee(employee_id: int) -> asyncpg.Record | None:
    return await _pool.fetchrow("SELECT * FROM masters WHERE employee_id = $1", employee_id)


async def master_by_telegram(telegram_id: int) -> asyncpg.Record | None:
    return await _pool.fetchrow(
        "SELECT * FROM masters WHERE telegram_id = $1 AND is_active", telegram_id
    )


async def list_masters(city_id: int) -> list[asyncpg.Record]:
    return await _pool.fetch(
        "SELECT * FROM masters WHERE city_id = $1 ORDER BY full_name", city_id
    )


async def active_master_usernames(city_id: int) -> list[str]:
    """Кого тегать на утреннем сборе: все работающие мастера с телеграмом."""
    skip = list(_director_skip_ids())
    return await _pool.fetchval(
        """
        SELECT coalesce(array_agg(telegram_username ORDER BY full_name), '{}')
        FROM masters
        WHERE city_id = $1 AND is_active AND telegram_username IS NOT NULL
          AND employee_id <> ALL($2::bigint[])
          AND (telegram_id IS NULL OR telegram_id <> ALL($2::bigint[]))
        """,
        city_id,
        skip,
    )


async def masters_without_shift(shift_date, city_id: int) -> list[asyncpg.Record]:
    """Кто из работающих мастеров сегодня не отметился."""
    skip = list(_director_skip_ids())
    return await _pool.fetch(
        """
        SELECT m.employee_id, m.full_name
        FROM masters m
        WHERE m.city_id = $2 AND m.is_active
          AND m.employee_id <> ALL($3::bigint[])
          AND (m.telegram_id IS NULL OR m.telegram_id <> ALL($3::bigint[]))
          AND NOT EXISTS (
              SELECT 1 FROM shifts s
              WHERE s.shift_date = $1 AND s.employee_id = m.employee_id
          )
        ORDER BY m.full_name
        """,
        shift_date,
        city_id,
        skip,
    )


async def mark_shift(shift_date, employee_id: int, city_id: int) -> tuple[int, bool]:
    """Ставит мастера в очередь дня. Возвращает (позиция, впервые ли).

    Позиция — порядок утреннего плюса. Мастера жмут кнопку почти одновременно,
    поэтому конкурентная вставка в одну позицию ожидаема: ловим и пробуем снова.
    """
    skip = _director_skip_ids()
    if employee_id in skip:
        raise RuntimeError("директор не встаёт в очередь мастеров")

    existing = await _pool.fetchval(
        "SELECT position FROM shifts WHERE shift_date = $1 AND employee_id = $2",
        shift_date,
        employee_id,
    )
    if existing is not None:
        return existing, False

    for _ in range(5):
        try:
            position = await _pool.fetchval(
                """
                INSERT INTO shifts (shift_date, employee_id, city_id, position)
                SELECT $1, $2, $3, COALESCE(MAX(position), 0) + 1
                FROM shifts WHERE shift_date = $1 AND city_id = $3
                ON CONFLICT (shift_date, employee_id) DO NOTHING
                RETURNING position
                """,
                shift_date,
                employee_id,
                city_id,
            )
        except asyncpg.UniqueViolationError:
            continue
        if position is not None:
            return position, True

    raise RuntimeError(f"не удалось поставить мастера {employee_id} в очередь дня")


async def shift_slots(shift_date, city_id: int, silent_statuses: list[str]) -> list[asyncpg.Record]:
    """Очередь дня с признаком занятости.

    Мастер занят, пока у него есть незакрытая заявка. Сложная диагностика
    («В работе СД») занятости не создаёт: технику увезли, мастер свободен.
    """
    skip = list(_director_skip_ids())
    return await _pool.fetch(
        """
        SELECT s.employee_id, s.position, m.full_name, m.telegram_username,
               coalesce(m.chat_id, m.telegram_id) AS delivery_chat_id,
               EXISTS (
                   SELECT 1 FROM assignments a
                   JOIN requests r USING (crm_id)
                   WHERE a.employee_id = s.employee_id
                     AND a.state <> 'reassigned'
                     AND r.is_open
                     AND NOT (r.status_text = ANY($3::text[]))
               ) AS is_busy
        FROM shifts s JOIN masters m USING (employee_id)
        WHERE s.shift_date = $1 AND s.city_id = $2 AND m.is_active
          AND m.employee_id <> ALL($4::bigint[])
          AND (m.telegram_id IS NULL OR m.telegram_id <> ALL($4::bigint[]))
        ORDER BY s.position
        """,
        shift_date,
        city_id,
        silent_statuses,
        skip,
    )


async def get_pinned_message(employee_id: int) -> int | None:
    return await _pool.fetchval(
        "SELECT pinned_message_id FROM masters WHERE employee_id = $1", employee_id
    )


async def set_pinned_message(employee_id: int, message_id: int | None) -> None:
    await _pool.execute(
        "UPDATE masters SET pinned_message_id = $2 WHERE employee_id = $1",
        employee_id,
        message_id,
    )


async def set_master_chat(employee_id: int, chat_id: int) -> bool:
    updated = await _pool.fetchval(
        "UPDATE masters SET chat_id = $2 WHERE employee_id = $1 RETURNING employee_id",
        employee_id,
        chat_id,
    )
    return updated is not None


async def requests_awaiting_assignment(
    city_id: int, statuses: list[str], lead_minutes: int
) -> list[asyncpg.Record]:
    """Заявки, которые пора раздать: нужный статус, нет мастера.

    lead_minutes <= 0 — сразу, в том же опросе что уведомление директору.
    >0 — не раньше чем за N минут до визита (opened_at). Без времени — сразу.
    """
    time_gate = ""
    args: list = [city_id, statuses]
    if lead_minutes > 0:
        time_gate = (
            "AND (r.opened_at IS NULL "
            "OR r.opened_at <= now() + make_interval(mins => $3))"
        )
        args.append(lead_minutes)
    return await _pool.fetch(
        f"""
        SELECT r.crm_id, r.req_type, r.opened_at, r.status_text, r.is_recall,
               r.customer_name, r.address, r.info_line, r.prior_master_name
        FROM requests r
        LEFT JOIN assignments a ON a.crm_id = r.crm_id AND a.state <> 'reassigned'
        WHERE r.city_id = $1 AND r.is_open AND a.id IS NULL
          AND coalesce(r.master_name, '') = ''
          AND r.status_text = ANY($2::text[])
          {time_gate}
        ORDER BY r.opened_at NULLS FIRST, r.crm_id
        """,
        *args,
    )


async def delete_assignment(assignment_id: int) -> None:
    """Откат назначения, которое не удалось доставить мастеру."""
    await _pool.execute("DELETE FROM assignments WHERE id = $1", assignment_id)


async def create_assignment(crm_id: int, employee_id: int, city_id: int) -> int:
    return await _pool.fetchval(
        """
        INSERT INTO assignments (crm_id, employee_id, city_id) VALUES ($1, $2, $3)
        RETURNING id
        """,
        crm_id,
        employee_id,
        city_id,
    )


async def save_message_ref(assignment_id: int, chat_id: int, message_id: int) -> None:
    await _pool.execute(
        "UPDATE assignments SET chat_id = $2, message_id = $3 WHERE id = $1",
        assignment_id,
        chat_id,
        message_id,
    )


async def active_assignment(crm_id: int) -> asyncpg.Record | None:
    return await _pool.fetchrow(
        "SELECT * FROM assignments WHERE crm_id = $1 AND state <> 'reassigned'", crm_id
    )


async def advance_assignment(assignment_id: int, state: str) -> asyncpg.Record | None:
    """Двигает назначение по шагам мастера. Повторное нажатие ничего не меняет."""
    column = {"enroute": "enroute_at", "onsite": "onsite_at", "inwork": "inwork_at"}[state]
    return await _pool.fetchrow(
        f"""
        UPDATE assignments SET state = $2, {column} = now()
        WHERE id = $1 AND state <> $2 AND state <> 'reassigned'
        RETURNING *
        """,
        assignment_id,
        state,
    )


async def reassign(assignment_id: int) -> None:
    await _pool.execute(
        "UPDATE assignments SET state = 'reassigned', reassigned_at = now() WHERE id = $1",
        assignment_id,
    )


async def previous_assignees(crm_id: int) -> set[int]:
    rows = await _pool.fetch("SELECT employee_id FROM assignments WHERE crm_id = $1", crm_id)
    return {row["employee_id"] for row in rows}


_ASSIGNMENT_VIEW = """
    SELECT a.id, a.crm_id, a.employee_id, a.chat_id, a.message_id, a.reminder_message_id,
           m.full_name, m.telegram_username, coalesce(m.chat_id, m.telegram_id) AS delivery_chat_id,
           r.address, r.customer_name, r.req_type, r.opened_at, r.info_line, r.prior_master_name,
           r.status_text, r.is_recall
    FROM assignments a
    JOIN requests r USING (crm_id)
    JOIN masters m ON m.employee_id = a.employee_id
    WHERE a.city_id = $1 AND a.state = 'assigned' AND r.is_open
"""


async def awaiting_reminder(city_id: int, minutes: int) -> list[asyncpg.Record]:
    """Не принял заявку за N минут, напоминание ещё не отправляли."""
    return await _pool.fetch(
        _ASSIGNMENT_VIEW + """
          AND a.reminder_sent_at IS NULL
          AND a.assigned_at < now() - make_interval(mins => $2)
        ORDER BY a.assigned_at
        """,
        city_id,
        minutes,
    )


async def clear_reminder_message(assignment_id: int) -> None:
    await _pool.execute(
        "UPDATE assignments SET reminder_message_id = NULL WHERE id = $1", assignment_id
    )


async def mark_reminder_sent(assignment_id: int, message_id: int) -> None:
    await _pool.execute(
        "UPDATE assignments SET reminder_sent_at = now(), reminder_message_id = $2 WHERE id = $1",
        assignment_id,
        message_id,
    )


async def overdue_acceptance(city_id: int, minutes: int) -> list[asyncpg.Record]:
    """Не принял заявку за N минут — пора отдавать следующему."""
    return await _pool.fetch(
        _ASSIGNMENT_VIEW + """
          AND a.assigned_at < now() - make_interval(mins => $2)
        ORDER BY a.assigned_at
        """,
        city_id,
        minutes,
    )


async def overdue_onsite(
    city_id: int, minutes: int, silent_statuses: list[str]
) -> list[asyncpg.Record]:
    return await _pool.fetch(
        """
        SELECT a.id, a.crm_id, a.employee_id, m.full_name, r.address, r.customer_name
        FROM assignments a
        JOIN requests r USING (crm_id)
        JOIN masters m ON m.employee_id = a.employee_id
        WHERE a.city_id = $1 AND a.state = 'enroute' AND r.is_open
          AND NOT a.onsite_alert_sent
          AND a.enroute_at < now() - make_interval(mins => $2)
          AND NOT (r.status_text = ANY($3::text[]))
        ORDER BY a.enroute_at
        """,
        city_id,
        minutes,
        silent_statuses,
    )


async def assignments_awaiting_payout(city_id: int) -> list[asyncpg.Record]:
    """Заявка закрыта администратором, а расчёт мастеру ещё не ушёл."""
    return await _pool.fetch(
        """
        SELECT a.id, a.crm_id, a.employee_id, m.full_name,
               coalesce(m.chat_id, m.telegram_id) AS delivery_chat_id
        FROM assignments a
        JOIN requests r USING (crm_id)
        JOIN masters m ON m.employee_id = a.employee_id
        WHERE a.city_id = $1 AND a.payout_sent_at IS NULL
          AND a.state <> 'reassigned' AND NOT r.is_open
          AND coalesce(m.chat_id, m.telegram_id) IS NOT NULL
        ORDER BY a.crm_id
        """,
        city_id,
    )


async def mark_payout_sent(assignment_id: int) -> None:
    await _pool.execute(
        "UPDATE assignments SET payout_sent_at = now() WHERE id = $1", assignment_id
    )


async def mark_onsite_alert_sent(assignment_id: int) -> None:
    await _pool.execute(
        "UPDATE assignments SET onsite_alert_sent = TRUE WHERE id = $1", assignment_id
    )


class request_lock:
    """Блокировка заявки на время записи в CRM.

    Запись — read-modify-write целой формы, поэтому два одновременных изменения
    одной заявки затрут друг друга. Процессов два (bot и poller), и блокировка
    внутри процесса их не развела бы — берём advisory lock у Postgres, он общий
    для всех подключений.
    """

    def __init__(self, crm_id: int):
        self._crm_id = crm_id
        self._conn = None

    async def __aenter__(self):
        if _pool is None:
            return self  # разовые скрипты и тесты работают без базы
        self._conn = await _pool.acquire()
        await self._conn.execute("SELECT pg_advisory_lock($1)", self._crm_id)
        return self

    async def __aexit__(self, *exc):
        if self._conn is None:
            return
        try:
            await self._conn.execute("SELECT pg_advisory_unlock($1)", self._crm_id)
        finally:
            await _pool.release(self._conn)


async def request_brief(crm_id: int) -> asyncpg.Record | None:
    return await _pool.fetchrow(
        """
        SELECT crm_id, req_type, opened_at, status_text, is_recall,
               customer_name, address, info_line, prior_master_name
        FROM requests WHERE crm_id = $1
        """,
        crm_id,
    )


async def create_info_request(kind: str, crm_id: int, employee_id: int, master_chat_id: int) -> int:
    return await _pool.fetchval(
        """
        INSERT INTO info_requests (kind, crm_id, employee_id, master_chat_id)
        VALUES ($1, $2, $3, $4) RETURNING id
        """,
        kind,
        crm_id,
        employee_id,
        master_chat_id,
    )


async def link_info_request(request_id: int, dispatcher_message_id: int) -> None:
    await _pool.execute(
        "UPDATE info_requests SET dispatcher_message_id = $2 WHERE id = $1",
        request_id,
        dispatcher_message_id,
    )


async def info_request_by_message(dispatcher_message_id: int) -> asyncpg.Record | None:
    return await _pool.fetchrow(
        "SELECT * FROM info_requests WHERE dispatcher_message_id = $1", dispatcher_message_id
    )


async def mark_info_answered(request_id: int, answered_by: str) -> None:
    await _pool.execute(
        "UPDATE info_requests SET answered_at = now(), answered_by = $2 WHERE id = $1",
        request_id,
        answered_by,
    )


async def open_closure(
    crm_id: int, employee_id: int, chat_id: int, kind: str = "close"
) -> asyncpg.Record | None:
    """Начинает отчёт. None — если по этой заявке уже есть незавершённый.

    СД на другой карточке не мешает: уникальность по (мастер, заявка),
    не по мастеру целиком. Два закрытия одной и той же заявки по-прежнему
    не стартуют параллельно.
    """
    try:
        return await _pool.fetchrow(
            """
            INSERT INTO closures (crm_id, employee_id, chat_id, kind)
            VALUES ($1, $2, $3, $4) RETURNING *
            """,
            crm_id,
            employee_id,
            chat_id,
            kind,
        )
    except asyncpg.UniqueViolationError:
        return None


async def active_closure(employee_id: int) -> asyncpg.Record | None:
    return await _pool.fetchrow(
        """
        SELECT * FROM closures
        WHERE employee_id = $1 AND state IN ('collecting', 'pending_admin', 'rejected')
        ORDER BY id DESC LIMIT 1
        """,
        employee_id,
    )


async def collecting_closure_by_message(message_id: int) -> asyncpg.Record | None:
    """Отчёт, в чьей переписке уже есть это сообщение бота — якорь для реплая/кнопки."""
    return await _pool.fetchrow(
        """
        SELECT * FROM closures
        WHERE state = 'collecting'
          AND chat_messages @> to_jsonb($1::bigint)
        ORDER BY id DESC
        LIMIT 1
        """,
        message_id,
    )


async def collecting_closure_by_crm(crm_id: int) -> asyncpg.Record | None:
    return await _pool.fetchrow(
        """
        SELECT * FROM closures
        WHERE crm_id = $1 AND state = 'collecting'
        ORDER BY id DESC
        LIMIT 1
        """,
        crm_id,
    )


async def collecting_closures_in_chat(chat_id: int) -> list[asyncpg.Record]:
    return await _pool.fetch(
        """
        SELECT * FROM closures
        WHERE chat_id = $1 AND state = 'collecting'
        ORDER BY id DESC
        """,
        chat_id,
    )


async def closure_by_id(closure_id: int) -> asyncpg.Record | None:
    return await _pool.fetchrow("SELECT * FROM closures WHERE id = $1", closure_id)


async def save_closure_answer(closure_id: int, column: str, value, next_step: str | None) -> None:
    """Колонку подставляем из белого списка в closing.answer_field, не из ввода."""
    await _pool.execute(
        f"UPDATE closures SET {column} = $2, step = coalesce($3, step) WHERE id = $1",
        closure_id,
        value,
        next_step,
    )


async def add_closure_photo(closure_id: int, kind: str, file_id: str) -> None:
    await _pool.execute(
        """
        UPDATE closures
        SET photos = jsonb_set(
            photos, ARRAY[$2],
            coalesce(photos -> $2, '[]'::jsonb) || to_jsonb($3::text), true
        )
        WHERE id = $1
        """,
        closure_id,
        kind,
        file_id,
    )


async def remember_closure_message(closure_id: int, message_id: int) -> None:
    """Копим переписку по отчёту, чтобы потом убрать её из чата мастера."""
    await _pool.execute(
        """
        UPDATE closures
        SET chat_messages = chat_messages || to_jsonb($2::bigint)
        WHERE id = $1
        """,
        closure_id,
        message_id,
    )


async def closure_messages(closure_id: int) -> list[int]:
    rows = await _pool.fetchval("SELECT chat_messages FROM closures WHERE id = $1", closure_id)
    if isinstance(rows, str):
        rows = json.loads(rows)
    return [int(x) for x in (rows or [])]


async def clear_closure_messages(closure_id: int) -> None:
    await _pool.execute(
        "UPDATE closures SET chat_messages = '[]'::jsonb WHERE id = $1", closure_id
    )


async def set_closure_step(closure_id: int, step: str) -> None:
    await _pool.execute("UPDATE closures SET step = $2 WHERE id = $1", closure_id, step)


async def submit_closure(closure_id: int, admin_chat_id: int, admin_message_id: int) -> None:
    await _pool.execute(
        """
        UPDATE closures
        SET state = 'pending_admin', submitted_at = now(),
            admin_chat_id = $2, admin_message_id = $3
        WHERE id = $1
        """,
        closure_id,
        admin_chat_id,
        admin_message_id,
    )


async def decide_closure(closure_id: int, approved: bool, reason: str = "") -> None:
    await _pool.execute(
        """
        UPDATE closures
        SET state = $2, decided_at = now(), reject_reason = $3,
            step = CASE WHEN $2 = 'rejected' THEN 'payed' ELSE step END
        WHERE id = $1
        """,
        closure_id,
        "approved" if approved else "rejected",
        reason,
    )


async def mark_closure_written(closure_id: int) -> None:
    await _pool.execute(
        "UPDATE closures SET state = 'written', written_at = now() WHERE id = $1", closure_id
    )


async def daily_totals(day, city_id: int, done_statuses: list[str]) -> asyncpg.Record:
    """Итоги дня по закрытым заявкам: считаем по суммам из CRM, ничего не выдумывая."""
    return await _pool.fetchrow(
        """
        WITH done AS (
            SELECT crm_id, total_cost, master_name
            FROM requests
            WHERE city_id = $2
              AND status_text = ANY($3::text[])
              AND coalesce(closed_at_local, last_seen_at)::date = $1
              AND total_cost IS NOT NULL
        )
        SELECT
            count(*)                                   AS orders,
            coalesce(sum(total_cost), 0)               AS turnover,
            coalesce(round(avg(total_cost)), 0)        AS average,
            coalesce(max(total_cost), 0)               AS biggest,
            (SELECT master_name FROM done ORDER BY total_cost DESC NULLS LAST LIMIT 1) AS top_master,
            (SELECT crm_id FROM done ORDER BY total_cost DESC NULLS LAST LIMIT 1)      AS top_order
        FROM done
        """,
        day,
        city_id,
        done_statuses,
    )


async def day_leftovers(day, city_id: int, sd_statuses: list[str]) -> list[asyncpg.Record]:
    """Заявки сегодняшнего дня, которые к вечеру так и не закрыли."""
    return await _pool.fetch(
        """
        SELECT crm_id, status_text, master_name,
               status_text = ANY($3::text[]) AS on_sd
        FROM requests
        WHERE city_id = $2 AND is_open
          AND coalesce(opened_at, first_seen_at)::date = $1
        ORDER BY on_sd, crm_id
        """,
        day,
        city_id,
        sd_statuses,
    )


async def get_state(key: str) -> str | None:
    return await _pool.fetchval("SELECT value FROM app_state WHERE key = $1", key)


async def set_state(key: str, value: str) -> None:
    await _pool.execute(
        """
        INSERT INTO app_state (key, value) VALUES ($1, $2)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """,
        key,
        value,
    )
