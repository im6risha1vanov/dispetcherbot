"""Директор не мастер: закрывает чужие заявки в чате мастеров, заявок не получает."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import closing
from crm import FIELD_EMPLOYEE


KIM = {
    "employee_id": 10679,
    "telegram_id": 111,
    "full_name": "Габидуллин Ким",
}
ASSIGNMENT = {"employee_id": 10679, "crm_id": 783000}
MASTERS_CHAT = -4925800420
DIRECTOR_USER = MagicMock(id=708703366)


def _bot(monkeypatch):
    import bot as bot_module

    monkeypatch.setattr(bot_module.config, "DIRECTOR_CHAT_ID", "708703366")
    monkeypatch.setattr(bot_module.config, "MASTERS_CHAT_ID", str(MASTERS_CHAT))
    return bot_module


def test_директор_это_telegram_id_а_не_мастер(monkeypatch):
    bot = _bot(monkeypatch)

    assert bot._is_director_user(DIRECTOR_USER) is True
    assert bot._is_director_user(MagicMock(id=111)) is False
    assert bot._director_closing(DIRECTOR_USER, MASTERS_CHAT) is True
    assert bot._director_closing(DIRECTOR_USER, 111) is False
    assert bot._director_closing(MagicMock(id=111), MASTERS_CHAT) is False


def test_identify_master_директора_не_считает_мастером(monkeypatch):
    bot = _bot(monkeypatch)

    async def scenario():
        with patch.object(bot.db, "master_by_telegram", AsyncMock()) as lookup, \
             patch.object(bot.db, "claim_master_by_username", AsyncMock()) as claim:
            found = await bot.identify_master(DIRECTOR_USER)
            lookup.assert_not_awaited()
            claim.assert_not_awaited()
            return found

    assert asyncio.run(scenario()) is None


def test_директор_закрывает_чужую_заявку_от_имени_мастера(monkeypatch):
    bot = _bot(monkeypatch)

    assert bot._report_employee_id(None, ASSIGNMENT, is_director=True) == 10679
    assert bot._report_employee_id(KIM, ASSIGNMENT) == 10679
    assert bot._report_employee_id(None, None, is_director=True) is None
    assert bot._report_employee_id(None, ASSIGNMENT) is None


def test_чужой_мастер_не_жмёт_закрыть(monkeypatch):
    bot = _bot(monkeypatch)
    other = {"employee_id": 12330, "telegram_id": 222, "full_name": "Гаев Роман"}

    assert bot._report_employee_id(other, ASSIGNMENT) is None


def test_чужой_мастер_по_сд_остаётся_собой(monkeypatch):
    """Закрытие СД по упоминанию раньше не требовало назначения — не ломаем."""
    bot = _bot(monkeypatch)
    other = {"employee_id": 12330, "telegram_id": 222, "full_name": "Гаев Роман"}

    assert bot._report_employee_id(other, ASSIGNMENT, foreign_ok=True) == 12330
    assert bot._report_employee_id(None, ASSIGNMENT, foreign_ok=True, is_director=True) == 10679
    assert bot._report_employee_id(other, None, foreign_ok=True) == 12330
    assert bot._report_employee_id(None, None, foreign_ok=True, is_director=True) is None


def test_поля_закрытия_не_подставляют_сотрудника():
    row = {
        "kind": closing.KIND_CLOSE,
        "payed_by_customer": 3000,
        "spares_cost": 0,
        "with_bso": "0",
        "fback_mode": "3",
    }

    assert FIELD_EMPLOYEE not in closing.crm_payload(row)
    assert FIELD_EMPLOYEE not in closing.REMOTE_PAYLOAD


def test_директор_отвечает_на_вопрос_чужого_отчёта(monkeypatch):
    bot = _bot(monkeypatch)
    theirs = {
        "id": 9,
        "employee_id": 10679,
        "crm_id": 783000,
        "state": "collecting",
        "step": "payed",
        "kind": closing.KIND_CLOSE,
    }
    reply = MagicMock(message_id=42, text="Закрытие заказа 783000\n\nСумма", caption=None)
    reply.reply_to_message = None

    async def scenario():
        with patch.object(bot.db, "collecting_closure_by_message", AsyncMock(return_value=theirs)), \
             patch.object(bot.db, "active_closure", AsyncMock()) as own:
            found = await bot._actor_closure(
                None, is_director=True, chat_id=MASTERS_CHAT, reply=reply
            )
            own.assert_not_awaited()
            return found

    assert asyncio.run(scenario())["employee_id"] == 10679


def test_мастер_не_перехватывает_чужой_отчёт_по_реплаю(monkeypatch):
    bot = _bot(monkeypatch)
    own = {
        "id": 1,
        "employee_id": 10679,
        "state": "collecting",
        "kind": closing.KIND_CLOSE,
        "step": "payed",
    }
    theirs = {
        "id": 2,
        "employee_id": 12330,
        "state": "collecting",
        "kind": closing.KIND_CLOSE,
        "step": "payed",
    }
    reply = MagicMock(message_id=99, text="Закрытие заказа 1", caption=None)
    reply.reply_to_message = None

    async def scenario():
        with patch.object(bot.db, "collecting_closure_by_message", AsyncMock(return_value=theirs)), \
             patch.object(bot.db, "active_closure", AsyncMock(return_value=own)):
            return await bot._actor_closure(KIM, chat_id=MASTERS_CHAT, reply=reply)

    assert asyncio.run(scenario()) is own


def test_директор_без_якоря_не_берёт_чужой_текстовый_отчёт(monkeypatch):
    """Сумма и комментарий — только реплаем, иначе чужой отчёт не трогаем."""
    bot = _bot(monkeypatch)
    theirs = {
        "id": 9,
        "employee_id": 10679,
        "state": "collecting",
        "step": "payed",
        "kind": closing.KIND_CLOSE,
        "chat_id": MASTERS_CHAT,
    }

    async def scenario():
        with patch.object(bot.db, "collecting_closure_by_message", AsyncMock(return_value=None)), \
             patch.object(bot.db, "collecting_closure_by_crm", AsyncMock(return_value=None)), \
             patch.object(bot.db, "active_closure", AsyncMock()) as own, \
             patch.object(
                 bot.db, "collecting_closures_in_chat", AsyncMock(return_value=[theirs])
             ):
            found = await bot._actor_closure(None, is_director=True, chat_id=MASTERS_CHAT)
            own.assert_not_awaited()
            return found

    assert asyncio.run(scenario()) is None


def test_директор_кладёт_фото_в_чужой_фотошаг_без_реплая(monkeypatch):
    bot = _bot(monkeypatch)
    photo = {
        "id": 9,
        "employee_id": 10679,
        "state": "collecting",
        "step": "bso_photo",
        "kind": closing.KIND_CLOSE,
        "chat_id": MASTERS_CHAT,
    }

    async def scenario():
        with patch.object(bot.db, "collecting_closure_by_message", AsyncMock(return_value=None)), \
             patch.object(bot.db, "active_closure", AsyncMock()) as own, \
             patch.object(
                 bot.db, "collecting_closures_in_chat", AsyncMock(return_value=[photo])
             ):
            found = await bot._actor_closure(
                None, is_director=True, chat_id=MASTERS_CHAT, prefer_photo=True
            )
            own.assert_not_awaited()
            return found

    assert asyncio.run(scenario())["id"] == 9


def test_директор_не_в_списке_пропуска_очереди_если_id_не_задан(monkeypatch):
    import db as db_module

    monkeypatch.setattr(db_module.config, "DIRECTOR_CHAT_ID", "708703366")
    assert 708703366 in db_module._director_skip_ids()
    monkeypatch.setattr(db_module.config, "DIRECTOR_CHAT_ID", "")
    assert db_module._director_skip_ids() == set()
