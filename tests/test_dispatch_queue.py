from datetime import datetime, time

import pytest

import config
from dispatch_queue import ShiftSlot, in_assign_window, next_master


def slot(employee_id: int, position: int, delivery_chat_id: int | None = 100) -> ShiftSlot:
    return ShiftSlot(
        employee_id=employee_id,
        position=position,
        full_name=f"Мастер {employee_id}",
        delivery_chat_id=delivery_chat_id,
    )


SHIFT = [slot(10, 1), slot(20, 2), slot(30, 3)]


def test_первая_заявка_уходит_первому_отметившемуся():
    assert next_master(SHIFT, None).employee_id == 10


def test_очередь_идёт_по_порядку_плюсов():
    assert next_master(SHIFT, 1).employee_id == 20
    assert next_master(SHIFT, 2).employee_id == 30


def test_после_последнего_круг_замыкается():
    assert next_master(SHIFT, 3).employee_id == 10


def test_опоздавший_встаёт_в_конец_а_не_в_начало():
    late = SHIFT + [slot(40, 4)]

    assert next_master(late, 3).employee_id == 40


def test_просрочивший_не_получает_ту_же_заявку_снова():
    assert next_master(SHIFT, 1, exclude={20}).employee_id == 30


def test_когда_круг_пройден_никого_не_возвращаем():
    assert next_master(SHIFT, 1, exclude={10, 20, 30}) is None


def test_мастер_без_рабочего_чата_пропускается():
    slots = [slot(10, 1, delivery_chat_id=None), slot(20, 2)]

    assert next_master(slots, None).employee_id == 20


def test_пустая_смена():
    assert next_master([], None) is None


def test_курсор_на_выбывшем_мастере_не_ломает_очередь():
    """Мастера могли выключить после того, как он получил заявку."""
    assert next_master([slot(20, 2), slot(30, 3)], 1).employee_id == 20


@pytest.mark.parametrize(
    "moment,expected",
    [
        (time(8, 59), False),
        (time(9, 0), True),
        (time(14, 30), True),
        (time(22, 0), True),
        (time(22, 1), False),
    ],
)
def test_окно_распределения(moment, expected):
    now = datetime(2026, 9, 16, moment.hour, moment.minute, tzinfo=config.TIMEZONE)

    assert in_assign_window(now, time(9, 0), time(22, 0)) is expected


def test_занятый_мастер_заявку_не_получает():
    """Мастер везёт незакрытую заявку — следующая идёт мимо него."""
    busy = ShiftSlot(20, 2, "Мастер 20", 100, None, is_busy=True)

    assert next_master([slot(10, 1), busy, slot(30, 3)], 1).employee_id == 30


def test_когда_заняты_все_никого_не_возвращаем():
    slots = [
        ShiftSlot(10, 1, "Мастер 10", 100, None, is_busy=True),
        ShiftSlot(20, 2, "Мастер 20", 100, None, is_busy=True),
    ]

    assert next_master(slots, None) is None
