"""СД на одной заявке не блокирует закрытие другой у того же мастера."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import closing


LIPIN = {
    "employee_id": 11774,
    "telegram_id": 578211863,
    "full_name": "Липин Артем",
}
LIPIN_CHAT = -5290882470
ALERT = "Сначала завершите предыдущий отчёт"


def _bot(monkeypatch):
    import bot as bot_module

    monkeypatch.setattr(bot_module.config, "DIRECTOR_CHAT_ID", "708703366")
    return bot_module


def _close_callback(crm_id: int, *, director: bool = False):
    callback = AsyncMock()
    callback.data = f"close:{crm_id}"
    callback.from_user = MagicMock(id=708703366 if director else LIPIN["telegram_id"])
    callback.message.chat.id = LIPIN_CHAT
    callback.message.message_id = 1231
    callback.message.edit_reply_markup = AsyncMock()
    callback.answer = AsyncMock()
    callback.bot = AsyncMock()
    callback.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1300))
    return callback


def test_сд_на_другой_заявке_не_мешает_закрыть_текущую(monkeypatch):
    """Collecting sd_open на 783783: «Закрыть заявку» на 784734 вызывает open_closure и проходит."""
    bot = _bot(monkeypatch)
    callback = _close_callback(784734)
    assignment = {"id": 34, "employee_id": 11774, "crm_id": 784734}
    closure = {
        "id": 80,
        "crm_id": 784734,
        "employee_id": 11774,
        "kind": closing.KIND_CLOSE,
        "chat_id": LIPIN_CHAT,
        "step": "payed",
    }

    async def scenario():
        with patch.object(bot, "identify_master", AsyncMock(return_value=LIPIN)), \
             patch.object(bot.db, "active_assignment", AsyncMock(return_value=assignment)), \
             patch.object(bot.db, "open_closure", AsyncMock(return_value=closure)) as opened, \
             patch.object(bot.db, "set_closure_step", AsyncMock()), \
             patch.object(bot.db, "remember_closure_message", AsyncMock()):
            await bot.on_close_start(callback)
            return opened.await_args.args

    assert asyncio.run(scenario()) == (784734, 11774, LIPIN_CHAT, closing.KIND_CLOSE)
    callback.answer.assert_awaited_with("Заполним отчёт")
    callback.message.edit_reply_markup.assert_awaited()


def test_директор_закрывает_б_пока_у_мастера_сд_на_а(monkeypatch):
    bot = _bot(monkeypatch)
    callback = _close_callback(784734, director=True)
    assignment = {"id": 34, "employee_id": 11774, "crm_id": 784734}
    closure = {
        "id": 80,
        "crm_id": 784734,
        "employee_id": 11774,
        "kind": closing.KIND_CLOSE,
        "chat_id": LIPIN_CHAT,
        "step": "payed",
    }

    async def scenario():
        with patch.object(bot, "identify_master", AsyncMock(return_value=None)), \
             patch.object(bot.db, "active_assignment", AsyncMock(return_value=assignment)), \
             patch.object(bot.db, "open_closure", AsyncMock(return_value=closure)) as opened, \
             patch.object(bot.db, "set_closure_step", AsyncMock()), \
             patch.object(bot.db, "remember_closure_message", AsyncMock()):
            await bot.on_close_start(callback)
            return opened.await_args.args

    assert asyncio.run(scenario()) == (784734, 11774, LIPIN_CHAT, closing.KIND_CLOSE)
    callback.answer.assert_awaited_with("Заполним отчёт")


def test_второе_закрытие_той_же_заявки_блокируется(monkeypatch):
    bot = _bot(monkeypatch)
    callback = _close_callback(784734)
    assignment = {"id": 34, "employee_id": 11774, "crm_id": 784734}

    async def scenario():
        with patch.object(bot, "identify_master", AsyncMock(return_value=LIPIN)), \
             patch.object(bot.db, "active_assignment", AsyncMock(return_value=assignment)), \
             patch.object(bot.db, "open_closure", AsyncMock(return_value=None)):
            await bot.on_close_start(callback)

    asyncio.run(scenario())
    callback.answer.assert_awaited_with(ALERT, show_alert=True)
    callback.message.edit_reply_markup.assert_not_awaited()


def test_мастер_по_кнопке_попадает_в_свой_сд_а_не_в_новое_закрытие(monkeypatch):
    """Готово на вопросе 783783 не должно продвигать отчёт 784734."""
    bot = _bot(monkeypatch)
    sd = {
        "id": 4,
        "employee_id": 11774,
        "crm_id": 783783,
        "state": "collecting",
        "kind": closing.KIND_SD_OPEN,
        "step": "safety_photo",
    }
    close = {
        "id": 80,
        "employee_id": 11774,
        "crm_id": 784734,
        "state": "collecting",
        "kind": closing.KIND_CLOSE,
        "step": "payed",
    }

    async def scenario():
        with patch.object(bot.db, "collecting_closure_by_message", AsyncMock(return_value=sd)), \
             patch.object(bot.db, "active_closure", AsyncMock(return_value=close)) as latest:
            found = await bot._actor_closure(
                LIPIN, chat_id=LIPIN_CHAT, hint_message_id=902
            )
            latest.assert_not_awaited()
            return found

    assert asyncio.run(scenario())["crm_id"] == 783783
