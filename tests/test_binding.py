"""Привязка гарантий и повторов к прошлому мастеру клиента."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

import poller
from dispatch_queue import ShiftSlot

FREE = ShiftSlot(10679, 1, "Габидуллин Ким", 100, "kim", is_busy=False)
BUSY = ShiftSlot(11774, 2, "Липин Артем", 200, "lpn", is_busy=True)
OTHER = ShiftSlot(12645, 3, "Кедюлич Николай", 300, "nk", is_busy=False)


def request(req_type: str, crm_id: int = 1001) -> dict:
    return {"crm_id": crm_id, "req_type": req_type}


def bound(rec, prior: str, slots, master_row=None):
    async def scenario():
        with patch.object(poller.db, "master_by_name", AsyncMock(return_value=master_row)):
            return await poller._bound_master(rec, prior, slots)

    return asyncio.run(scenario())


KIM_ROW = {"employee_id": 10679, "full_name": "Габидуллин Ким"}
LIPIN_ROW = {"employee_id": 11774, "full_name": "Липин Артем"}


def test_гарантия_уходит_прошлому_мастеру():
    slot, wait = bound(request("Гарантия"), "Габидуллин Ким", [FREE, OTHER], KIM_ROW)

    assert slot is FREE
    assert wait is False


def test_гарантия_ждёт_если_прошлый_мастер_занят():
    """Чужому гарантию не отдаём даже ценой ожидания."""
    slot, wait = bound(request("Гарантия"), "Липин Артем", [BUSY, FREE], LIPIN_ROW)

    assert slot is None
    assert wait is True


def test_гарантия_ждёт_если_мастера_нет_на_смене():
    slot, wait = bound(request("Гарантия"), "Липин Артем", [FREE, OTHER], LIPIN_ROW)

    assert slot is None
    assert wait is True


def test_повтор_уходит_прошлому_мастеру():
    slot, wait = bound(request("Повтор"), "Габидуллин Ким", [FREE, OTHER], KIM_ROW)

    assert slot is FREE


def test_повтор_идёт_в_общую_очередь_если_мастер_занят():
    slot, wait = bound(request("Повтор"), "Липин Артем", [BUSY, FREE], LIPIN_ROW)

    assert slot is None
    assert wait is False, "повтор не ждёт, уходит в очередь"


def test_обычная_заявка_ни_к_кому_не_привязана():
    slot, wait = bound(request("Впервые"), "Габидуллин Ким", [FREE], KIM_ROW)

    assert (slot, wait) == (None, False)


def test_без_прошлого_мастера_привязки_нет():
    slot, wait = bound(request("Гарантия"), "", [FREE], None)

    assert (slot, wait) == (None, False)


def test_неизвестный_мастер_не_отдаёт_гарантию_чужому():
    """Мастер уволен и вычищен из справочника — гарантия всё равно ждёт."""
    slot, wait = bound(request("Гарантия"), "Иванов Иван", [FREE], None)

    assert slot is None
    assert wait is True
