"""Владелец смотрит, администратор и директор решают.

Владельца отключили от кнопок: он получает ленту событий и сообщения о сбоях,
но подтверждает отчёты не он. Администратор известен только по @username —
chat_id бот запоминает, когда тот напишет ему первым.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import closing
import config
import roles


def _state(monkeypatch, stored=None):
    """Подменяет app_state словарём: база в этих тестах не нужна."""
    box = dict(stored or {})

    async def get_state(key):
        return box.get(key)

    async def set_state(key, value):
        box[key] = value

    monkeypatch.setattr(roles.db, "get_state", get_state)
    monkeypatch.setattr(roles.db, "set_state", set_state)
    roles.forget_admin_chat()
    return box


def _roles(monkeypatch, *, admin="adm", director="dir", owner="own", username="AdmSikBt"):
    monkeypatch.setattr(roles.config, "ADMIN_CHAT_ID", admin)
    monkeypatch.setattr(roles.config, "TELEGRAM_TEST_CHAT_ID", "")
    monkeypatch.setattr(roles.config, "DIRECTOR_CHAT_ID", director)
    monkeypatch.setattr(roles.config, "OWNER_CHAT_ID", owner)
    monkeypatch.setattr(roles.config, "ADMIN_USERNAME", username)


def test_решают_администратор_и_директор_а_владелец_смотрит(monkeypatch):
    _roles(monkeypatch)
    _state(monkeypatch)

    assert asyncio.run(roles.deciders()) == ["adm", "dir"]
    assert asyncio.run(roles.is_decider("own")) is False
    assert asyncio.run(roles.is_decider("adm")) is True
    assert asyncio.run(roles.is_decider("dir")) is True
    # Служебные команды остаются и у владельца: он не решает, но смотрит.
    assert asyncio.run(roles.is_supervisor("own")) is True
    assert asyncio.run(roles.is_supervisor("кто-то")) is False


def test_лента_идёт_владельцу_и_директору(monkeypatch):
    _roles(monkeypatch)

    assert roles.feed_chats() == ["own", "dir"]


def test_без_owner_chat_id_лента_остаётся_на_прежнем_чате(monkeypatch):
    """Выкладка без правки .env не должна оставить ленту без адресата."""
    _roles(monkeypatch, owner="")
    assert roles.owner_chat() == "adm"

    # До разделения ролей лента ходила в TELEGRAM_TEST_CHAT_ID — туда и вернётся.
    monkeypatch.setattr(roles.config, "TELEGRAM_TEST_CHAT_ID", "лента")
    assert roles.owner_chat() == "лента"


def test_тревога_идёт_всем_троим_без_повторов(monkeypatch):
    _roles(monkeypatch)
    _state(monkeypatch)

    assert asyncio.run(roles.alert_chats()) == ["adm", "dir", "own"]

    _roles(monkeypatch, admin="one", director="one", owner="one")
    _state(monkeypatch)
    assert asyncio.run(roles.alert_chats()) == ["one"]


def test_администратор_узнаётся_по_username_в_любом_регистре(monkeypatch):
    _roles(monkeypatch)

    assert roles.is_admin_username("AdmSikBt") is True
    assert roles.is_admin_username("@admsikbt") is True
    assert roles.is_admin_username("AdmSikBt2") is False
    assert roles.is_admin_username(None) is False

    # Пустой ADMIN_USERNAME не должен принимать за админа безымянного.
    _roles(monkeypatch, username="")
    assert roles.is_admin_username(None) is False
    assert roles.is_admin_username("кто угодно") is False


def test_чат_администратора_запоминается_один_раз(monkeypatch):
    _roles(monkeypatch)
    box = _state(monkeypatch)

    assert asyncio.run(roles.remember_admin_chat(555)) is True
    assert box[roles.ADMIN_CHAT_KEY] == "555"
    assert asyncio.run(roles.admin_chat()) == "555"
    # Повторное сообщение того же администратора базу не трогает.
    assert asyncio.run(roles.remember_admin_chat(555)) is False
    assert asyncio.run(roles.deciders()) == ["555", "dir"]


def test_привязка_срабатывает_даже_если_чат_уже_в_env(monkeypatch):
    """Вписанный руками номер не доказывает, что человек на связи."""
    _roles(monkeypatch, admin="555")
    box = _state(monkeypatch)

    assert asyncio.run(roles.admin_bound()) is False
    assert asyncio.run(roles.remember_admin_chat(555)) is True
    assert box[roles.ADMIN_CHAT_KEY] == "555"
    assert asyncio.run(roles.admin_bound()) is True


def test_запомненный_чат_переживает_перезапуск(monkeypatch):
    _roles(monkeypatch)
    _state(monkeypatch, {roles.ADMIN_CHAT_KEY: "777"})

    assert asyncio.run(roles.admin_chat()) == "777"


def test_сбой_базы_не_глушит_тревогу(monkeypatch):
    """Не прочитали запомненный чат — берём из .env, а не молчим."""
    _roles(monkeypatch)

    async def broken(_key):
        raise RuntimeError("база недоступна")

    monkeypatch.setattr(roles.db, "get_state", broken)
    roles.forget_admin_chat()

    assert asyncio.run(roles.admin_chat()) == "adm"


def test_упавший_чат_не_мешает_остальным(monkeypatch):
    sent = []

    async def send_message(chat, text, reply_markup=None):
        if chat == "плохой":
            raise RuntimeError("нет такого чата")
        sent.append(chat)
        return MagicMock(chat=MagicMock(id=chat), message_id=1)

    tg = MagicMock()
    tg.send_message = send_message

    asyncio.run(roles.send_to(tg, ["плохой", "хороший"], "текст"))
    assert sent == ["хороший"]


def _closure(**extra):
    base = {
        "id": 50,
        "crm_id": 784223,
        "employee_id": 10679,
        "kind": closing.KIND_CLOSE,
        "state": "pending_admin",
        "step": "feedback",
        "chat_id": -100500,
        "payed_by_customer": 1500,
        "spares_cost": 0,
        "with_bso": "1",
        "fback_mode": "3",
        "photos": {},
    }
    base.update(extra)
    return base


def test_отчёт_уходит_и_администратору_и_директору(monkeypatch):
    import bot as bot_module

    _roles(monkeypatch)
    _state(monkeypatch)
    monkeypatch.setattr(bot_module.config, "ADMIN_CHAT_ID", "adm")
    monkeypatch.setattr(bot_module.config, "DIRECTOR_CHAT_ID", "dir")
    monkeypatch.setattr(bot_module.config, "OWNER_CHAT_ID", "own")
    monkeypatch.setattr(bot_module.config, "TELEGRAM_TEST_CHAT_ID", "")

    tg = AsyncMock()
    tg.send_message = AsyncMock(
        side_effect=lambda chat, *a, **kw: MagicMock(
            chat=MagicMock(id=chat), message_id=hash(chat) % 1000
        )
    )

    async def scenario():
        with patch.object(bot_module.db, "closure_by_id", AsyncMock(return_value=_closure())), \
             patch.object(bot_module.db, "master_by_employee",
                          AsyncMock(return_value={"full_name": "Габидуллин Ким"})), \
             patch.object(bot_module.db, "submit_closure", AsyncMock()) as submitted, \
             patch.object(bot_module, "_clear_master_chat", AsyncMock()), \
             patch.object(bot_module.closing, "next_step_for", MagicMock(return_value=None)):
            await bot_module._advance_closing(tg, 50)
            return submitted.await_args.args

    args = asyncio.run(scenario())
    copies = args[1]
    assert [chat for chat, _ in copies] == ["adm", "dir"]
    # Владельцу — сообщение без кнопок, решать не ему.
    owner_calls = [c for c in tg.send_message.await_args_list if c.args[0] == "own"]
    assert len(owner_calls) == 1
    assert owner_calls[0].kwargs.get("reply_markup") is None


def test_у_второго_проверяющего_кнопки_гаснут(monkeypatch):
    import bot as bot_module

    tg = AsyncMock()
    pressed = MagicMock(chat=MagicMock(id="adm"), message_id=11)

    async def scenario():
        with patch.object(
            bot_module.db,
            "closure_decider_messages",
            AsyncMock(return_value=[("adm", 11), ("dir", 22)]),
        ):
            await bot_module._drop_decision_buttons(tg, 50, pressed)
        return tg.edit_message_reply_markup.await_args_list

    calls = asyncio.run(scenario())
    # У нажавшего кнопки уже сняты своим обработчиком — трогаем только второго.
    assert len(calls) == 1
    assert calls[0].kwargs["chat_id"] == "dir"
    assert calls[0].kwargs["message_id"] == 22


def test_владелец_не_подтверждает_отчёт(monkeypatch):
    import bot as bot_module

    _roles(monkeypatch)
    _state(monkeypatch)
    monkeypatch.setattr(bot_module.config, "ADMIN_CHAT_ID", "adm")
    monkeypatch.setattr(bot_module.config, "DIRECTOR_CHAT_ID", "dir")
    monkeypatch.setattr(bot_module.config, "OWNER_CHAT_ID", "own")
    monkeypatch.setattr(bot_module.config, "TELEGRAM_TEST_CHAT_ID", "")

    callback = AsyncMock()
    callback.message = AsyncMock()
    callback.message.chat.id = "own"
    callback.data = "close_ok:50"

    async def scenario():
        with patch.object(bot_module.db, "closure_by_id", AsyncMock()) as lookup:
            await bot_module._decide_closing(callback, AsyncMock(), approved=True)
            return lookup.await_count

    assert asyncio.run(scenario()) == 0
    callback.answer.assert_awaited()

def test_хватает_одного_чата_наблюдателя(monkeypatch):
    """Владельца можно задать вместо администратора — служба должна стартовать."""
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setattr(config, "DATABASE_URL", "postgres://")
    monkeypatch.setattr(config, "ADMIN_CHAT_ID", "")
    monkeypatch.setattr(config, "TELEGRAM_TEST_CHAT_ID", "")
    monkeypatch.setattr(config, "OWNER_CHAT_ID", "own")

    config.validate(config.BOT_REQUIRED)  # не должно бросить

    # Ни одного чата наблюдателя — это уже ошибка настройки.
    monkeypatch.setattr(config, "OWNER_CHAT_ID", "")
    try:
        config.validate(config.BOT_REQUIRED)
    except config.ConfigError as exc:
        assert "OWNER_CHAT_ID" in str(exc)
    else:
        raise AssertionError("без чатов наблюдателя запуск должен падать")
