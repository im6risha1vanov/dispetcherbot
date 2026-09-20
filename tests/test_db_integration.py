"""Проверка SQL против настоящего PostgreSQL.

Пропускается, если не задан TEST_DATABASE_URL — на машине без базы тесты
остальных модулей должны проходить как обычно.

    TEST_DATABASE_URL=postgresql://... .venv/bin/python -m pytest tests/test_db_integration.py
"""

import asyncio
import os
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

import config
import db
from crm import RequestRow

TEST_DSN = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DSN, reason="нужен TEST_DATABASE_URL")

CITY = 206
MASTER_A, MASTER_B, MASTER_C = 10679, 12330, 10254

ASSIGNABLE = ["Ожидает"]
SILENT = ["В работе СД"]
LEAD = 60


def row(crm_id: int, master_name: str = "", status: str = "Ожидает") -> RequestRow:
    return RequestRow(
        crm_id=crm_id,
        opened_at=datetime(2026, 9, 16, 14, 0, tzinfo=config.TIMEZONE),
        req_type="Впервые",
        status_text=status,
        is_recall=False,
        customer_name="Иванов И И",
        address="ул. Ленина, 10",
        master_name=master_name,
        created_at_local=None,
        closed_at_local=None,
        total_cost=Decimal("0"),
        raw_cells=["x"] * 13,
    )


def run_db(scenario):
    """Пул asyncpg живёт в своём event loop, поэтому весь тест — один run."""
    async def wrapper():
        config.DATABASE_URL = TEST_DSN
        await db.connect()
        try:
            await db._pool.execute("TRUNCATE assignments, shifts, requests, app_state, info_requests RESTART IDENTITY")
            # Справочник мастеров переживает TRUNCATE, поэтому чистим его руками:
            # иначе выключенный в одном тесте мастер ломает следующий.
            await db._pool.execute(
                "UPDATE masters SET telegram_id = NULL, chat_id = NULL,"
                " telegram_username = NULL, is_active = TRUE, pinned_message_id = NULL"
            )
            return await scenario()
        finally:
            await db.close()

    return asyncio.run(wrapper())


def test_новая_заявка_отличается_от_уже_известной():
    async def scenario():
        first = await db.insert_request(row(1001), CITY, notified=False)
        second = await db.insert_request(row(1001), CITY, notified=False)
        return first, second

    assert run_db(scenario) == (True, False)


def test_повторный_опрос_обновляет_статус():
    async def scenario():
        await db.insert_request(row(1001), CITY, notified=True)
        changed = row(1001)
        changed.status_text = "В работе"
        await db.insert_request(changed, CITY, notified=True)
        return await db._pool.fetchval("SELECT status_text FROM requests WHERE crm_id = 1001")

    assert run_db(scenario) == "В работе"


def test_пропавшая_из_грида_заявка_считается_закрытой():
    async def scenario():
        await db.insert_request(row(1001), CITY, notified=True)
        await db.insert_request(row(1002), CITY, notified=True)
        closed = await db.close_requests_absent_from_grid(CITY, [1001])
        still_open = await db._pool.fetchval("SELECT is_open FROM requests WHERE crm_id = 1002")
        return closed, still_open

    assert run_db(scenario) == (1, False)


def test_очередь_нумеруется_порядком_отметок():
    async def scenario():
        day = datetime(2026, 9, 16).date()
        a = await db.mark_shift(day, MASTER_A, CITY)
        b = await db.mark_shift(day, MASTER_B, CITY)
        again = await db.mark_shift(day, MASTER_A, CITY)
        return a, b, again

    assert run_db(scenario) == ((1, True), (2, True), (1, False))


def test_в_очередь_дня_попадают_только_привязанные_и_активные():
    async def scenario():
        day = datetime(2026, 9, 16).date()
        await db.link_telegram(MASTER_A, 555)
        await db.mark_shift(day, MASTER_A, CITY)
        await db.mark_shift(day, MASTER_B, CITY)
        await db.set_master_active(MASTER_B, False)
        slots = await db.shift_slots(day, CITY, SILENT)
        return [(s["employee_id"], s["delivery_chat_id"]) for s in slots]

    assert run_db(scenario) == [(MASTER_A, 555)]


def test_заявка_уходит_в_рабочий_чат_а_не_в_личку():
    """У мастера свой чат для заявок; личка — только если чата ещё нет."""
    async def scenario():
        day = datetime(2026, 9, 16).date()
        await db.link_telegram(MASTER_A, 555)
        await db.set_master_chat(MASTER_A, -100500)
        await db.link_telegram(MASTER_B, 666)
        await db.mark_shift(day, MASTER_A, CITY)
        await db.mark_shift(day, MASTER_B, CITY)
        slots = await db.shift_slots(day, CITY, SILENT)
        return {s["employee_id"]: s["delivery_chat_id"] for s in slots}

    assert run_db(scenario) == {MASTER_A: -100500, MASTER_B: 666}




def at(hours_from_now: float) -> datetime:
    return datetime.now(config.TIMEZONE) + timedelta(hours=hours_from_now)


def test_заявка_на_вечер_придерживается_если_задан_lead():
    """lead>0: мастер не получает утром заявку, до которой ещё полдня."""
    async def scenario():
        soon = row(1001)
        soon.opened_at = at(0.5)      # через полчаса — пора
        later = row(1002)
        later.opened_at = at(6)       # через шесть часов — рано
        overdue = row(1003)
        overdue.opened_at = at(-2)    # время прошло — раздать немедленно
        for r in (soon, later, overdue):
            await db.insert_request(r, CITY, notified=True)

        pending = await db.requests_awaiting_assignment(CITY, ASSIGNABLE, LEAD)
        return sorted(r["crm_id"] for r in pending)

    assert run_db(scenario) == [1001, 1003]


def test_при_lead_ноль_вечерняя_заявка_раздаётся_сразу():
    """ASSIGN_LEAD_MIN=0: в очередь на раздачу в том же опросе, что уведомление."""
    async def scenario():
        later = row(1002)
        later.opened_at = at(6)
        await db.insert_request(later, CITY, notified=True)
        pending = await db.requests_awaiting_assignment(CITY, ASSIGNABLE, 0)
        return [r["crm_id"] for r in pending]

    assert run_db(scenario) == [1002]


def test_заявка_без_времени_раздаётся_сразу():
    async def scenario():
        no_time = row(1001)
        no_time.opened_at = None
        await db.insert_request(no_time, CITY, notified=True)
        pending = await db.requests_awaiting_assignment(CITY, ASSIGNABLE, LEAD)
        return [r["crm_id"] for r in pending]

    assert run_db(scenario) == [1001]


def test_заявку_с_мастером_из_crm_бот_не_забирает():
    async def scenario():
        await db.insert_request(row(1001), CITY, notified=True)
        await db.insert_request(row(1002, master_name="Кузиванов И А"), CITY, notified=True)
        pending = await db.requests_awaiting_assignment(CITY, ASSIGNABLE, LEAD)
        return [r["crm_id"] for r in pending]

    assert run_db(scenario) == [1001]


def test_закрытые_заявки_из_грида_мастерам_не_раздаются():
    """Грид отдаёт и закрытые заявки — раздавать можно только «Ожидает»."""
    async def scenario():
        await db.insert_request(row(1001), CITY, notified=True)
        for crm_id, status in [(1002, "Отмена Филиала"), (1003, "Отказ"), (1004, "Готов")]:
            await db.insert_request(row(crm_id, status=status), CITY, notified=True)
        pending = await db.requests_awaiting_assignment(CITY, ASSIGNABLE, LEAD)
        return [r["crm_id"] for r in pending]

    assert run_db(scenario) == [1001]


def test_назначенная_заявка_уходит_из_очереди_на_распределение():
    async def scenario():
        await db.insert_request(row(1001), CITY, notified=True)
        await db.create_assignment(1001, MASTER_A, CITY)
        return await db.requests_awaiting_assignment(CITY, ASSIGNABLE, LEAD)

    assert run_db(scenario) == []


def test_повторное_нажатие_кнопки_ничего_не_меняет():
    async def scenario():
        await db.insert_request(row(1001), CITY, notified=True)
        assignment_id = await db.create_assignment(1001, MASTER_A, CITY)
        first = await db.advance_assignment(assignment_id, "enroute")
        second = await db.advance_assignment(assignment_id, "enroute")
        return first is not None, second is None

    assert run_db(scenario) == (True, True)


def test_у_заявки_одно_активное_назначение():
    async def scenario():
        await db.insert_request(row(1001), CITY, notified=True)
        first = await db.create_assignment(1001, MASTER_A, CITY)
        try:
            await db.create_assignment(1001, MASTER_B, CITY)
            return "второе назначение прошло"
        except Exception:
            await db.reassign(first)
            await db.create_assignment(1001, MASTER_B, CITY)
            return "после переназначения можно"

    assert run_db(scenario) == "после переназначения можно"


def test_просрочка_принятия_видна_и_помнит_прежних_мастеров():
    async def scenario():
        await db.insert_request(row(1001), CITY, notified=True)
        assignment_id = await db.create_assignment(1001, MASTER_A, CITY)
        await db._pool.execute(
            "UPDATE assignments SET assigned_at = now() - interval '16 minutes' WHERE id = $1",
            assignment_id,
        )
        overdue = await db.overdue_acceptance(CITY, 15)
        await db.reassign(assignment_id)
        await db.create_assignment(1001, MASTER_B, CITY)
        return [o["crm_id"] for o in overdue], sorted(await db.previous_assignees(1001))

    assert run_db(scenario) == ([1001], sorted([MASTER_A, MASTER_B]))


def test_напоминание_уходит_один_раз_и_не_мешает_переназначению():
    """На 5-й минуте напоминание, на 15-й — другому мастеру."""
    async def scenario():
        await db.insert_request(row(1001), CITY, notified=True)
        assignment_id = await db.create_assignment(1001, MASTER_A, CITY)
        await db._pool.execute(
            "UPDATE assignments SET assigned_at = now() - interval '6 minutes' WHERE id = $1",
            assignment_id,
        )
        due_first = len(await db.awaiting_reminder(CITY, 5))
        await db.mark_reminder_sent(assignment_id, 9001)
        due_again = len(await db.awaiting_reminder(CITY, 5))
        not_yet_reassigned = len(await db.overdue_acceptance(CITY, 15))

        await db._pool.execute(
            "UPDATE assignments SET assigned_at = now() - interval '16 minutes' WHERE id = $1",
            assignment_id,
        )
        now_reassigned = len(await db.overdue_acceptance(CITY, 15))
        return due_first, due_again, not_yet_reassigned, now_reassigned

    assert run_db(scenario) == (1, 0, 0, 1)


def test_закрытая_заявка_не_поднимает_тревогу():
    async def scenario():
        await db.insert_request(row(1001), CITY, notified=True)
        assignment_id = await db.create_assignment(1001, MASTER_A, CITY)
        await db._pool.execute(
            "UPDATE assignments SET assigned_at = now() - interval '16 minutes' WHERE id = $1",
            assignment_id,
        )
        await db.close_requests_absent_from_grid(CITY, [])
        return await db.overdue_acceptance(CITY, 15)

    assert run_db(scenario) == []


def test_просрочка_прибытия_срабатывает_один_раз():
    async def scenario():
        await db.insert_request(row(1001), CITY, notified=True)
        assignment_id = await db.create_assignment(1001, MASTER_A, CITY)
        await db.advance_assignment(assignment_id, "enroute")
        await db._pool.execute(
            "UPDATE assignments SET enroute_at = now() - interval '41 minutes' WHERE id = $1",
            assignment_id,
        )
        before = await db.overdue_onsite(CITY, 40, SILENT)
        await db.mark_onsite_alert_sent(assignment_id)
        after = await db.overdue_onsite(CITY, 40, SILENT)
        return len(before), len(after)

    assert run_db(scenario) == (1, 0)


def test_мастер_привязывается_сам_по_заранее_заданному_username():
    async def scenario():
        await db.set_master_username(MASTER_A, "@ivanov")
        claimed = await db.claim_master_by_username("ivanov", 777)
        found = await db.master_by_telegram(777)
        return claimed["employee_id"], found["employee_id"]

    assert run_db(scenario) == (MASTER_A, MASTER_A)


def test_чужой_username_никого_не_привязывает():
    async def scenario():
        await db.set_master_username(MASTER_A, "ivanov")
        return await db.claim_master_by_username("posторонний", 778)

    assert run_db(scenario) is None


def test_повторный_start_не_перепривязывает():
    async def scenario():
        await db.set_master_username(MASTER_A, "ivanov")
        first = await db.claim_master_by_username("ivanov", 777)
        second = await db.claim_master_by_username("ivanov", 777)
        return first is not None, second is None

    assert run_db(scenario) == (True, True)


def test_ответ_диспетчера_находится_по_реплаю():
    """Запросов в чате может висеть несколько — различаем по id сообщения бота."""
    async def scenario():
        phone_id = await db.create_info_request("phone", 1001, MASTER_A, -777)
        flat_id = await db.create_info_request("apartment", 1002, MASTER_B, -888)
        await db.link_info_request(phone_id, 5001)
        await db.link_info_request(flat_id, 5002)

        found = await db.info_request_by_message(5002)
        await db.mark_info_answered(found["id"], "Диспетчер")
        answered = await db.info_request_by_message(5002)
        return found["crm_id"], found["kind"], found["master_chat_id"], answered["answered_at"] is not None

    assert run_db(scenario) == (1002, "apartment", -888, True)


def test_реплай_на_чужое_сообщение_ничего_не_находит():
    async def scenario():
        request_id = await db.create_info_request("phone", 1001, MASTER_A, -777)
        await db.link_info_request(request_id, 5001)
        return await db.info_request_by_message(9999)

    assert run_db(scenario) is None


def test_по_заявкам_на_сложной_диагностике_бот_молчит():
    """Техника у мастера на руках — торопить некого, тревог быть не должно."""
    async def scenario():
        await db.insert_request(row(1001, status="В работе СД"), CITY, notified=True)
        assignment_id = await db.create_assignment(1001, MASTER_A, CITY)
        await db.advance_assignment(assignment_id, "enroute")
        await db._pool.execute(
            "UPDATE assignments SET enroute_at = now() - interval '41 minutes' WHERE id = $1",
            assignment_id,
        )
        silent = await db.overdue_onsite(CITY, 40, SILENT)
        loud = await db.overdue_onsite(CITY, 40, [])
        return len(silent), len(loud)

    assert run_db(scenario) == (0, 1)


def test_тихие_заявки_не_попадают_в_ленту():
    async def scenario():
        await db.insert_request(row(1001, status="В работе СД"), CITY, notified=False)
        await db.insert_request(row(1002, status="Ожидает"), CITY, notified=False)
        pending = await db.pending_notifications(CITY, SILENT)
        return [r["crm_id"] for r in pending]

    assert run_db(scenario) == [1002]


def test_расчёт_уходит_после_закрытия_и_только_один_раз():
    async def scenario():
        await db.insert_request(row(1001), CITY, notified=True)
        await db.link_telegram(MASTER_A, 555)
        assignment_id = await db.create_assignment(1001, MASTER_A, CITY)

        while_open = len(await db.assignments_awaiting_payout(CITY))
        await db.close_requests_absent_from_grid(CITY, [])
        after_close = len(await db.assignments_awaiting_payout(CITY))
        await db.mark_payout_sent(assignment_id)
        after_send = len(await db.assignments_awaiting_payout(CITY))
        return while_open, after_close, after_send

    assert run_db(scenario) == (0, 1, 0)


def test_мастеру_без_чата_расчёт_не_шлётся():
    async def scenario():
        await db.insert_request(row(1001), CITY, notified=True)
        await db.create_assignment(1001, MASTER_A, CITY)
        await db.close_requests_absent_from_grid(CITY, [])
        return await db.assignments_awaiting_payout(CITY)

    assert run_db(scenario) == []


def test_на_сборе_смены_тегаются_только_работающие_мастера():
    async def scenario():
        await db.set_master_username(MASTER_A, "kim")
        await db.set_master_username(MASTER_B, "roman")
        await db.set_master_username(MASTER_C, "gleb")
        await db.set_master_active(MASTER_C, False)
        return sorted(await db.active_master_usernames(CITY))

    assert run_db(scenario) == ["kim", "roman"]


def test_закреплённая_заявка_помнится_и_снимается():
    async def scenario():
        await db.set_pinned_message(MASTER_A, 4321)
        pinned = await db.get_pinned_message(MASTER_A)
        await db.set_pinned_message(MASTER_A, None)
        cleared = await db.get_pinned_message(MASTER_A)
        return pinned, cleared

    assert run_db(scenario) == (4321, None)


def test_состояние_переживает_перезапуск():
    async def scenario():
        await db.set_state("rotation:206:2026-09-16", "2")
        await db.set_state("rotation:206:2026-09-16", "3")
        return await db.get_state("rotation:206:2026-09-16")

    assert run_db(scenario) == "3"
