"""Права и справка по ролям: один список на то и на другое.

Роль определяется по человеку, а не по чату. В рабочем чате мастера сидят и
мастер, и директор — чат про них обоих ничего не говорит.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import access
import roles

OWNER_ID, ADMIN_ID, DIRECTOR_ID, MASTER_ID, STRANGER_ID = 11, 22, 33, 44, 55

KIM = {"employee_id": 10679, "full_name": "Габидуллин Ким", "is_active": True}


def _setup(monkeypatch, *, admin_bound=True):
    monkeypatch.setattr(roles.config, "OWNER_CHAT_ID", str(OWNER_ID))
    monkeypatch.setattr(roles.config, "TELEGRAM_TEST_CHAT_ID", "")
    monkeypatch.setattr(roles.config, "ADMIN_CHAT_ID", str(ADMIN_ID if admin_bound else OWNER_ID))
    monkeypatch.setattr(roles.config, "DIRECTOR_CHAT_ID", str(DIRECTOR_ID))
    roles.forget_admin_chat()

    async def get_state(_key):
        return None

    monkeypatch.setattr(roles.db, "get_state", get_state)


def _whois(user_id, master=None):
    async def scenario():
        with patch.object(access.db, "master_by_telegram", AsyncMock(return_value=master)):
            return await access.whois(MagicMock(id=user_id))

    return asyncio.run(scenario())


def _may(user_id, command, master=None):
    async def scenario():
        with patch.object(access.db, "master_by_telegram", AsyncMock(return_value=master)):
            return await access.may(MagicMock(id=user_id), command)

    return asyncio.run(scenario())


def test_роль_по_человеку_а_не_по_чату(monkeypatch):
    _setup(monkeypatch)

    assert _whois(OWNER_ID)[0] == {access.OWNER}
    assert _whois(ADMIN_ID)[0] == {access.ADMIN}
    assert _whois(DIRECTOR_ID)[0] == {access.DIRECTOR}
    assert _whois(MASTER_ID, KIM)[0] == {access.MASTER}
    assert _whois(STRANGER_ID)[0] == set()


def test_владелец_пока_и_администратор(monkeypatch):
    """Пока администратор не привязан, отчёты идут владельцу — он в обеих ролях."""
    _setup(monkeypatch, admin_bound=False)

    assert _whois(OWNER_ID)[0] == {access.OWNER, access.ADMIN}


def test_права_берутся_из_того_же_списка(monkeypatch):
    _setup(monkeypatch)

    assert _may(DIRECTOR_ID, "today") is True
    assert _may(ADMIN_ID, "today") is True
    assert _may(OWNER_ID, "today") is False, "владелец не управляет сменой"
    assert _may(MASTER_ID, "today", KIM) is False
    assert _may(MASTER_ID, "masters", KIM) is False
    assert _may(STRANGER_ID, "masters") is False


def test_чужой_чат_мастера_больше_не_перепривязать(monkeypatch):
    """Раньше /master_chat не проверялся вовсе."""
    _setup(monkeypatch)

    assert _may(MASTER_ID, "master_chat", KIM) is False
    assert _may(ADMIN_ID, "master_chat") is True
    assert _may(DIRECTOR_ID, "master_chat") is True


def test_неизвестная_команда_запрещена(monkeypatch):
    _setup(monkeypatch)

    assert _may(DIRECTOR_ID, "drop_database") is False


def test_у_каждой_команды_есть_владелец_роли():
    """Команда без ролей выпала бы и из справки, и из проверки прав."""
    for entry in access.COMMANDS + access.BUTTONS:
        assert entry.roles, entry.name
        assert entry.what, entry.name


def test_справка_у_мастера_и_директора_разная():
    master = access.help_text({access.MASTER}, KIM)
    director = access.help_text({access.DIRECTOR})

    assert "Габидуллин Ким" in master
    assert "/today" not in master, "мастеру нечего делать в панели дня"
    assert "📋 Отчёт" in master
    assert "/today" in director
    assert "✅ Провести" in director
    assert "🚗 В пути" not in director, "кнопки мастера директору не показываем"


def test_справка_владельца_без_кнопок():
    text = access.help_text({access.OWNER})

    assert "Кнопки:" not in text, "владелец ничего не нажимает"
    assert "21:00" in text and "22:00" in text


def test_включение_мастера_только_у_директора():
    assert "✅ Включить в работу" in access.help_text({access.DIRECTOR})
    assert "✅ Включить в работу" not in access.help_text({access.ADMIN})


def test_человеку_без_роли_короткий_ответ():
    assert access.help_text(set()) == access.NO_ROLE
