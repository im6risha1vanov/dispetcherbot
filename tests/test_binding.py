"""Привязка гарантий и повторов к прошлому мастеру клиента.

Гарантия идёт мимо очереди: тому, кто делал работу, даже если он утром не
отмечался или помечен «Занят» на день. Очередь — про распределение новых
заявок, а гарантия к распределению отношения не имеет.

Повтор остаётся при прежнем правиле: к тому же мастеру, но по правилам очереди.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import poller
from dispatch_queue import ShiftSlot

FREE = ShiftSlot(10679, 1, "Габидуллин Ким", 100, "kim", is_busy=False)
BUSY = ShiftSlot(11774, 2, "Липин Артем", 200, "lpn", is_busy=True)
OTHER = ShiftSlot(12645, 3, "Кедюлич Николай", 300, "nk", is_busy=False)


def request(req_type: str, crm_id: int = 1001) -> dict:
    return {"crm_id": crm_id, "req_type": req_type}


def master(employee_id: int, name: str, *, active=True, chat=None, tg=None, username=None):
    return {
        "employee_id": employee_id,
        "full_name": name,
        "is_active": active,
        "chat_id": chat,
        "telegram_id": tg,
        "telegram_username": username,
    }


KIM = master(10679, "Габидуллин Ким", chat=100, username="kim")
LIPIN = master(11774, "Липин Артем", chat=200, username="lpn")
QUIT = master(12330, "Гаев Роман", active=False, chat=400)


def bound(rec, prior: str, slots, master_row=None, *, busy=False):
    async def scenario():
        with patch.object(poller.db, "master_by_name", AsyncMock(return_value=master_row)), \
             patch.object(poller.db, "master_is_busy", AsyncMock(return_value=busy)):
            return await poller._bound_master(rec, prior, slots)

    return asyncio.run(scenario())


# --- ветки гарантии ---

def test_гарантия_активному_на_смене():
    slot, wait = bound(request("Гарантия"), "Габидуллин Ким", [FREE, OTHER], KIM)

    assert slot.employee_id == 10679
    assert slot.position == 1, "он в очереди — позиция известна"
    assert wait is False


def test_гарантия_активному_не_на_смене():
    """Утренний «плюс» — правило очереди. Гарантия идёт мимо неё."""
    slot, wait = bound(request("Гарантия"), "Липин Артем", [FREE, OTHER], LIPIN)

    assert slot.employee_id == 11774
    assert slot.delivery_chat_id == 200
    assert slot.position is None, "вне очереди — курсор круга двигать нельзя"
    assert wait is False


def test_гарантия_помеченному_занят_всё_равно_уходит_ему():
    """«Занят» убирает из очереди, но гарантию это не отменяет.

    Помеченный «Занят» выпадает из shift_slots — ровно как мастер, не
    отметившийся утром, поэтому проверяем тот же путь.
    """
    slot, wait = bound(request("Гарантия"), "Габидуллин Ким", [OTHER], KIM)

    assert slot.employee_id == 10679
    assert wait is False


def test_гарантия_занятому_заявкой_ждёт_его():
    slot, wait = bound(request("Гарантия"), "Липин Артем", [BUSY, FREE], LIPIN, busy=True)

    assert slot is None
    assert wait is True, "чужому гарантию не отдаём даже ценой ожидания"


def test_гарантия_выключенного_мастера_идёт_по_кругу():
    """Раньше такая заявка ждала вечно и не доставалась никому."""
    slot, wait = bound(request("Гарантия"), "Гаев Роман", [FREE, OTHER], QUIT)

    assert slot is None
    assert wait is False


def test_гарантия_мастера_вне_справочника_идёт_по_кругу():
    slot, wait = bound(request("Гарантия"), "Иванов Иван", [FREE], None)

    assert slot is None
    assert wait is False


def test_гарантия_без_чата_доставки_идёт_по_кругу():
    """Некуда доставить — ждать бессмысленно."""
    nowhere = master(11982, "Кулемалин Георгий", chat=None, tg=None)
    slot, wait = bound(request("Гарантия"), "Кулемалин Георгий", [FREE], nowhere)

    assert slot is None
    assert wait is False


# --- повторы: правило не менялось ---

def test_повтор_уходит_прошлому_мастеру():
    slot, wait = bound(request("Повтор"), "Габидуллин Ким", [FREE, OTHER], KIM)

    assert slot is FREE
    assert wait is False


def test_повтор_идёт_в_общую_очередь_если_мастер_занят():
    slot, wait = bound(request("Повтор"), "Липин Артем", [BUSY, FREE], LIPIN)

    assert slot is None
    assert wait is False, "повтор не ждёт, уходит в очередь"


def test_повтор_не_на_смене_идёт_в_очередь():
    """В отличие от гарантии, повтор мимо очереди не ходит."""
    slot, wait = bound(request("Повтор"), "Липин Артем", [FREE, OTHER], LIPIN)

    assert slot is None
    assert wait is False


# --- всё остальное ---

def test_обычная_заявка_ни_к_кому_не_привязана():
    slot, wait = bound(request("Впервые"), "Габидуллин Ким", [FREE], KIM)

    assert (slot, wait) == (None, False)


def test_без_прошлого_мастера_привязки_нет():
    slot, wait = bound(request("Гарантия"), "", [FREE], None)

    assert (slot, wait) == (None, False)
