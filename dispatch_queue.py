"""Очередь мастеров на день и выбор следующего.

Порядок очереди задаётся утренними плюсами: кто первым отметился на смену, тот
первый в очереди. Дальше заявки идут по кругу в этом же порядке.
"""

from dataclasses import dataclass
from datetime import datetime, time


@dataclass(slots=True, frozen=True)
class ShiftSlot:
    employee_id: int
    position: int
    full_name: str
    delivery_chat_id: int | None  # рабочий чат мастера; без него заявку доставить некуда
    username: str | None = None
    is_busy: bool = False  # везёт незакрытую заявку — новую не даём


def next_master(
    slots: list[ShiftSlot],
    cursor_position: int | None,
    exclude: set[int] = frozenset(),
) -> ShiftSlot | None:
    """Следующий по кругу после cursor_position, пропуская exclude.

    exclude — те, кому эту заявку уже отдавали: круг не должен вернуть заявку
    мастеру, который её уже просрочил.
    """
    available = sorted(
        (
            s
            for s in slots
            if s.employee_id not in exclude
            and s.delivery_chat_id is not None
            and not s.is_busy
        ),
        key=lambda s: s.position,
    )
    if not available:
        return None
    if cursor_position is None:
        return available[0]

    after_cursor = [s for s in available if s.position > cursor_position]
    return after_cursor[0] if after_cursor else available[0]


def in_assign_window(now: datetime, start: time, end: time) -> bool:
    return start <= now.time() <= end
