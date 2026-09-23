"""Директор не мастер: закрывает чужие заявки в чате мастеров, заявок не получает."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.dispatcher.event.bases import SkipHandler

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
    kim_chat = -4935665842

    assert bot._is_director_user(DIRECTOR_USER) is True
    assert bot._is_director_user(MagicMock(id=111)) is False
    # Заявки висят в рабочих чатах мастеров, не в утренней группе.
    assert bot._director_closing(DIRECTOR_USER, kim_chat) is True
    assert bot._director_closing(DIRECTOR_USER, MASTERS_CHAT) is True
    assert bot._director_closing(MagicMock(id=111), kim_chat) is False


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


KIM_CHAT = -4935665842


def _director_callback(crm_id: int, chat_id: int = KIM_CHAT):
    callback = AsyncMock()
    callback.data = f"close:{crm_id}"
    callback.from_user = MagicMock(id=708703366)
    callback.message.chat.id = chat_id
    callback.message.message_id = 10
    callback.message.edit_reply_markup = AsyncMock()
    callback.answer = AsyncMock()
    callback.bot = AsyncMock()
    callback.bot.send_message = AsyncMock(return_value=MagicMock(message_id=11))
    return callback


def test_on_close_start_директора_пишет_отчёт_за_назначенного(monkeypatch):
    """Директор жмёт «Закрыть заявку» в чате Кима — closure.employee_id = 10679."""
    bot = _bot(monkeypatch)
    callback = _director_callback(784223)
    assignment = {"id": 8, "employee_id": 10679, "crm_id": 784223}
    closure = {
        "id": 50, "crm_id": 784223, "employee_id": 10679,
        "kind": closing.KIND_CLOSE, "chat_id": KIM_CHAT, "step": "payed",
    }

    async def scenario():
        with patch.object(bot.db, "active_assignment", AsyncMock(return_value=assignment)), \
             patch.object(bot.db, "open_closure", AsyncMock(return_value=closure)) as opened, \
             patch.object(bot.db, "set_closure_step", AsyncMock()), \
             patch.object(bot.db, "remember_closure_message", AsyncMock()):
            await bot.on_close_start(callback)
            return opened.await_args.args

    assert asyncio.run(scenario()) == (784223, 10679, KIM_CHAT, closing.KIND_CLOSE)
    callback.answer.assert_awaited()


def test_директор_без_реплая_не_пишет_сумму(monkeypatch):
    bot = _bot(monkeypatch)
    message = AsyncMock()
    message.from_user = MagicMock(id=708703366)
    message.chat.id = KIM_CHAT
    message.text = "1500"
    message.reply_to_message = None
    message.bot = AsyncMock()
    theirs = {
        "id": 50, "crm_id": 784223, "employee_id": 10679,
        "kind": closing.KIND_CLOSE, "state": "collecting", "step": "total",
        "chat_id": KIM_CHAT,
    }

    async def scenario():
        with patch.object(bot.db, "collecting_closure_by_message", AsyncMock(return_value=None)), \
             patch.object(bot.db, "collecting_closures_in_chat", AsyncMock(return_value=[theirs])), \
             patch.object(bot.db, "save_closure_answer", AsyncMock()) as saved:
            try:
                await bot.on_closing_answer(message)
            except SkipHandler:
                # Чужой текст уходит дальше по цепочке: тем же сообщением
                # диспетчер отвечает мастеру, и глотать его нельзя.
                pass
            return saved.await_count

    assert asyncio.run(scenario()) == 0


def test_директор_реплаем_пишет_сумму_в_чужой_отчёт(monkeypatch):
    bot = _bot(monkeypatch)
    replied = MagicMock(
        message_id=40,
        text="Закрытие заказа 784223\n\nСколько оплатил клиент?",
        caption=None,
    )
    replied.from_user = MagicMock(is_bot=True)
    replied.reply_to_message = None
    message = AsyncMock()
    message.from_user = MagicMock(id=708703366)
    message.chat.id = KIM_CHAT
    message.text = "1500"
    message.message_id = 41
    message.reply_to_message = replied
    message.bot = AsyncMock()
    closure = {
        "id": 50, "crm_id": 784223, "employee_id": 10679,
        "kind": closing.KIND_CLOSE, "state": "collecting", "step": "total",
        "chat_id": KIM_CHAT,
    }

    async def scenario():
        with patch.object(bot.db, "collecting_closure_by_message", AsyncMock(return_value=closure)), \
             patch.object(bot.db, "closure_messages", AsyncMock(return_value=[40])), \
             patch.object(bot.db, "save_closure_answer", AsyncMock()) as saved, \
             patch.object(bot.db, "remember_closure_message", AsyncMock()), \
             patch.object(bot, "_advance_closing", AsyncMock()) as advance:
            await bot.on_closing_answer(message)
            return saved.await_args.args, advance.await_count

    args, advanced = asyncio.run(scenario())
    assert args[0] == 50
    assert args[1] == "payed_by_customer"
    assert advanced == 1


def test_директор_кладёт_фото_в_окно_crm(monkeypatch):
    bot = _bot(monkeypatch)
    message = AsyncMock()
    message.from_user = MagicMock(id=708703366)
    message.chat.id = KIM_CHAT
    message.reply_to_message = None
    message.photo = [MagicMock(file_id="tgfile-bso")]
    message.message_id = 42
    message.reply = AsyncMock(return_value=MagicMock(message_id=43))
    photo_closure = {
        "id": 50, "crm_id": 784223, "employee_id": 10679,
        "kind": closing.KIND_CLOSE, "state": "collecting", "step": "docs_photo",
        "question_message_id": 39,
        "chat_id": KIM_CHAT,
    }

    async def scenario():
        with patch.object(bot.db, "collecting_closure_by_message", AsyncMock(return_value=None)), \
             patch.object(
                 bot.db, "collecting_closures_in_chat", AsyncMock(return_value=[photo_closure])
             ), \
             patch.object(bot, "_keep_photo", AsyncMock(return_value="tgfile-bso")),              patch.object(bot.db, "add_closure_photo", AsyncMock(return_value=1)) as added, \
             patch.object(bot.db, "remember_closure_message", AsyncMock()):
            await bot.on_closing_photo(message)
            return added.await_args.args

    kind_args = asyncio.run(scenario())
    assert kind_args == (50, "bso", "tgfile-bso")


def test_закрытие_за_директора_rmw_мастера_и_finish(monkeypatch):
    """Отчёт директора: карточка пишется как Ким, finish=1, id директора нет."""
    from tests.test_crm_write import (
        CLOSE_CARD, CONDUCTED_FOOTER, FakeCrm, _saved_close_card,
    )
    import config
    from crm import FIELD_EMPLOYEE

    assigned_close = CLOSE_CARD.replace(
        '<option value="" selected>Выберите</option>',
        '<option value="">Выберите</option>',
    ).replace(
        '<option value="10679">Габидуллин Ким (Сык) (650)</option>',
        '<option value="10679" selected>Габидуллин Ким (Сык) (650)</option>',
    ).replace(
        '<input type="hidden" name="_employee_id" value="">',
        '<input type="hidden" name="_employee_id" value="10679">',
    )
    saved = _saved_close_card().replace(
        '<option value="" selected>Выберите</option>',
        '<option value="">Выберите</option>',
    ).replace(
        '<option value="10679">Габидуллин Ким (Сык) (650)</option>',
        '<option value="10679" selected>Габидуллин Ким (Сык) (650)</option>',
    ).replace(
        '<input type="hidden" name="_employee_id" value="">',
        '<input type="hidden" name="_employee_id" value="10679">',
    )
    monkeypatch.setattr(config, "CRM_READ_ONLY", False)
    monkeypatch.setattr(config, "CRM_WRITE_ONLY_FOR", frozenset())
    crm = FakeCrm(card=assigned_close)
    crm.card_after = saved
    crm.card_finish = saved.replace(
        """<select name="CustomerRequest[status]">
    <option value="1" selected>Ожидает</option>
    <option value="4">В пути</option>
  </select>""",
        CONDUCTED_FOOTER,
    )
    payload = closing.crm_payload({
        "kind": closing.KIND_CLOSE,
        "payed_by_customer": 1500,
        "spares_cost": 0,
        "with_bso": "1",
        "fback_mode": "3",
    })
    assert FIELD_EMPLOYEE not in payload

    async def scenario(c):
        return await c.close_request(781594, payload, {"bso": ["tgfile1"]})

    async def with_dl(c):
        async def download(_fid):
            return b"jpeg"
        c._download_file = download
        return await scenario(c)

    from tests.test_crm_write import run_write as _run
    damage = _run(crm, with_dl)
    assert damage == []
    assert "finish=1" in crm.write_urls[1]
    for written in crm.writes:
        if written.get("_multipart"):
            continue
        assert written.get(FIELD_EMPLOYEE) != "708703366"
        assert written.get(FIELD_EMPLOYEE) == "10679"
        assert written.get("_employee_id") != "708703366"


def test_write_closing_директора_не_подменяет_мастера(monkeypatch):
    bot = _bot(monkeypatch)
    monkeypatch.setattr(bot.config, "CRM_ALLOW_CLOSING", True)
    monkeypatch.setattr(bot.config, "ADMIN_CHAT_ID", "1")
    closure = {
        "id": 50,
        "crm_id": 784223,
        "kind": closing.KIND_CLOSE,
        "employee_id": 10679,
        "payed_by_customer": 1500,
        "spares_cost": 0,
        "with_bso": "1",
        "fback_mode": "3",
        "photos": {"bso": ["tgfile1"]},
        "chat_id": KIM_CHAT,
    }
    crm = AsyncMock()
    crm.close_request = AsyncMock(return_value=[])
    tg = AsyncMock()

    async def scenario():
        with patch.object(bot.db, "mark_closure_written", AsyncMock()):
            await bot._write_closing(tg, crm, closure)
        return crm.close_request.await_args.args

    args = asyncio.run(scenario())
    assert args[0] == 784223
    payload = args[1]
    assert FIELD_EMPLOYEE not in payload
    assert payload[closing.FIELD_PAYED] == "1500"
    crm.assign_master.assert_not_called()
