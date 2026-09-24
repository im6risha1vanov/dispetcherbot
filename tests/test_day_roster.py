"""Панель дня и учёт миграций.

Панель показывает не только «кому дать заявку», но и «почему не дали»:
кто не отмечался, кого убрали на день, кто выключен совсем.
"""

import asyncio
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import access
import messages
import migrate


def row(name, *, active=True, position=None, paused=None, busy=False, order=None, eid=1):
    return {
        "employee_id": eid,
        "full_name": name,
        "is_active": active,
        "position": position,
        "paused_at": paused,
        "is_busy": busy,
        "busy_with": order,
        "delivery_chat_id": 100,
    }


ROWS = [
    row("Габидуллин Ким", position=1, eid=1),
    row("Кузиванов Илья", position=2, busy=True, order=786780, eid=2),
    row("Мишарин Олег", position=3, paused="сейчас", eid=3),
    row("Липин Артем", eid=4),
    row("Гаев Роман", active=False, eid=5),
]


def test_панель_разделяет_очередь_неотмеченных_и_выключенных():
    text = messages.roster_text(date(2026, 9, 24), ROWS)

    assert "1. Габидуллин Ким — свободен" in text
    assert "2. Кузиванов Илья — на заявке 786780" in text
    assert "3. Мишарин Олег — занят до конца дня" in text
    assert "Не отмечались сегодня:\n· Липин Артем" in text
    assert "Выключены:\n· Гаев Роман" in text


def test_кнопка_описывает_действие_а_не_состояние():
    titles = [
        b[0].text for b in messages.roster_keyboard(ROWS, is_director=True).inline_keyboard
    ]

    assert "⏸ Занят: Габидуллин Ким" in titles
    assert "▶️ В работе: Мишарин Олег" in titles, "помеченного снимаем обратно"
    assert "▶️ В работе: Липин Артем" in titles, "не отмечался — можно поставить"
    assert "✅ Включить: Гаев Роман" in titles
    assert "🔄 Обновить" in titles


def test_включение_выключенного_только_у_директора():
    titles = [
        b[0].text for b in messages.roster_keyboard(ROWS, is_director=False).inline_keyboard
    ]

    assert not any("Включить" in t for t in titles)
    assert any("Занят" in t for t in titles), "остальное администратору доступно"


def test_пустая_очередь_не_ломает_панель():
    text = messages.roster_text(date(2026, 9, 24), [row("Липин Артем")])

    assert "В очереди пусто." in text


def _bot(monkeypatch):
    import bot as bot_module
    import roles

    monkeypatch.setattr(roles.config, "OWNER_CHAT_ID", "own")
    monkeypatch.setattr(roles.config, "TELEGRAM_TEST_CHAT_ID", "")
    monkeypatch.setattr(roles.config, "ADMIN_CHAT_ID", "22")
    monkeypatch.setattr(roles.config, "DIRECTOR_CHAT_ID", "33")
    roles.forget_admin_chat()
    monkeypatch.setattr(roles.db, "get_state", AsyncMock(return_value=None))
    return bot_module


def _callback(user_id, data):
    callback = AsyncMock()
    callback.from_user = MagicMock(id=user_id)
    callback.data = data
    callback.message = AsyncMock()
    callback.bot = AsyncMock()
    return callback


def test_занят_без_отметки_отвечает_всплывающим(monkeypatch):
    """Строку смены не заводим: она дала бы позицию, которой у мастера не было."""
    bot = _bot(monkeypatch)
    callback = _callback(22, f"{messages.CB_DAY_PAUSE}:4")

    async def scenario():
        with patch.object(bot.db, "master_by_telegram", AsyncMock(return_value=None)), \
             patch.object(bot.db, "master_by_employee", AsyncMock(return_value=row("Липин Артем"))), \
             patch.object(bot.db, "pause_master", AsyncMock(return_value=False)) as paused, \
             patch.object(bot.db, "day_roster", AsyncMock(return_value=ROWS)):
            await bot.on_day_pause(callback)
            return paused.await_count

    assert asyncio.run(scenario()) == 1
    said = str(callback.answer.await_args)
    assert "не отмечался" in said
    callback.bot.send_message.assert_not_awaited()


def test_мастер_в_панель_не_попадает(monkeypatch):
    bot = _bot(monkeypatch)
    callback = _callback(44, f"{messages.CB_DAY_PAUSE}:1")

    async def scenario():
        with patch.object(bot.db, "master_by_telegram", AsyncMock(return_value={"employee_id": 1})), \
             patch.object(bot.db, "pause_master", AsyncMock()) as paused:
            await bot.on_day_pause(callback)
            return paused.await_count

    assert asyncio.run(scenario()) == 0
    assert "администратора и директора" in str(callback.answer.await_args)


def test_включать_мастеров_может_только_директор(monkeypatch):
    bot = _bot(monkeypatch)
    callback = _callback(22, f"{messages.CB_DAY_ENABLE}:5")

    async def scenario():
        with patch.object(bot.db, "master_by_telegram", AsyncMock(return_value=None)), \
             patch.object(bot.db, "set_master_active", AsyncMock()) as switched:
            await bot.on_day_enable(callback)
            return switched.await_count

    assert asyncio.run(scenario()) == 0
    assert "только директор" in str(callback.answer.await_args)


def test_переключение_уходит_в_ленту_владельцу(monkeypatch):
    bot = _bot(monkeypatch)
    callback = _callback(33, f"{messages.CB_DAY_RESUME}:4")

    async def scenario():
        with patch.object(bot.db, "master_by_telegram", AsyncMock(return_value=None)), \
             patch.object(bot.db, "master_by_employee", AsyncMock(return_value=row("Липин Артем"))), \
             patch.object(bot.db, "resume_master", AsyncMock(return_value=(4, True))), \
             patch.object(bot.db, "day_roster", AsyncMock(return_value=ROWS)):
            await bot.on_day_resume(callback)

    asyncio.run(scenario())
    sent = callback.bot.send_message.await_args.args
    assert sent[0] == "own"
    assert "Липин Артем" in sent[1] and "4-й" in sent[1] and "директор" in sent[1]


# --- учёт миграций ---

def test_все_миграции_на_месте_и_по_порядку():
    found = migrate.known()

    assert len(found) >= 24
    assert [m.version for m in found] == sorted(m.version for m in found)
    assert found[0].version == "001"


def test_неприменённые_считаются_по_учёту():
    found = migrate.known()
    applied = {m.version for m in found[:-1]}

    left = migrate.pending(applied)

    assert [m.version for m in left] == [found[-1].version]
    assert migrate.pending({m.version for m in found}) == []


def test_без_учёта_применять_надо_всё():
    assert len(migrate.pending(set())) == len(migrate.known())
