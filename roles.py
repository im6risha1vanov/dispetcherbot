"""Кто что получает: владелец смотрит, администратор и директор решают.

Владелец филиала не нажимает кнопок — ему идёт лента событий и сбои.
Отчёты на подтверждение уходят администратору и директору сразу обоим:
кто первым нажал, тот и решил, у второго кнопки гаснут.

Telegram не даёт написать человеку по @username — нужен chat_id, а он
появляется только когда человек сам напишет боту. Поэтому chat_id
администратора бот запоминает при первом его сообщении и кладёт в базу:
перезапуск службы его не теряет, и обе службы (бот и опрос CRM) видят
один и тот же чат.
"""

from __future__ import annotations

import logging

import config
import db

log = logging.getLogger(__name__)

ADMIN_CHAT_KEY = "admin_chat_id"

# Между опросами chat_id администратора не меняется, а спрашивать базу на
# каждое сообщение в чате мастеров незачем.
_cached_admin: str | None = None


def owner_chat() -> str:
    """Чат владельца: лента событий и технические сбои.

    Пока OWNER_CHAT_ID не задан, владельцем считается тот чат, куда лента
    ходила до разделения ролей. Так выкладка без правки .env ничего не теряет.
    """
    return config.OWNER_CHAT_ID or config.TELEGRAM_TEST_CHAT_ID or config.ADMIN_CHAT_ID


def director_chat() -> str:
    return config.DIRECTOR_CHAT_ID


async def admin_chat() -> str:
    """Чат администратора: запомненный по @username или заданный в .env.

    Заминка в базе не должна глушить тревогу: не прочитали — берём .env.
    """
    global _cached_admin
    if _cached_admin is None:
        try:
            _cached_admin = await db.get_state(ADMIN_CHAT_KEY) or ""
        except Exception:
            log.exception("не прочитал чат администратора из базы")
            return config.ADMIN_CHAT_ID
    return _cached_admin or config.ADMIN_CHAT_ID


async def admin_bound() -> bool:
    """Написал ли администратор боту: иначе отчёты идут по ADMIN_CHAT_ID из .env."""
    await admin_chat()
    return bool(_cached_admin)


def forget_admin_chat() -> None:
    """Сбрасывает кеш — нужен тестам и повторной привязке."""
    global _cached_admin
    _cached_admin = None


def is_admin_username(username: str | None) -> bool:
    wanted = config.ADMIN_USERNAME.lstrip("@").lower()
    return bool(wanted) and (username or "").lstrip("@").lower() == wanted


async def remember_admin_chat(chat_id) -> bool:
    """Запоминает личный чат администратора. True — если запомнили впервые.

    Пишем в базу даже если тот же чат уже стоял в .env: так видно, что человек
    действительно на связи, а не что номер просто вписали руками.
    """
    global _cached_admin
    value = str(chat_id)
    await admin_chat()  # прогреваем кеш из базы, чтобы не перезаписывать зря
    if _cached_admin == value:
        return False
    await db.set_state(ADMIN_CHAT_KEY, value)
    _cached_admin = value
    log.info("чат администратора запомнен")
    return True


async def deciders() -> list[str]:
    """Кто подтверждает отчёты: администратор и директор. Владельца здесь нет."""
    chats: list[str] = []
    for chat in (await admin_chat(), director_chat()):
        if chat and chat not in chats:
            chats.append(chat)
    return chats


async def is_decider(chat_id) -> bool:
    return str(chat_id) in await deciders()


async def is_supervisor(chat_id) -> bool:
    """Решающие плюс владелец: служебные команды доступны всем троим."""
    chat = str(chat_id)
    return chat == owner_chat() or await is_decider(chat)


async def alert_chats() -> list[str]:
    """Рабочие тревоги — всем: решать администратору и директору, знать владельцу."""
    chats = await deciders()
    owner = owner_chat()
    if owner and owner not in chats:
        chats.append(owner)
    return chats


def feed_chats() -> list[str]:
    """Лента заявок: владелец и директор. Мастера её не видят."""
    chats: list[str] = []
    for chat in (owner_chat(), director_chat()):
        if chat and chat not in chats:
            chats.append(chat)
    return chats


async def send_to(bot, chats, text: str, *, markup=None) -> list:
    """Одно и то же сообщение в несколько чатов. Упавший чат не глушит остальные."""
    sent = []
    for chat in chats:
        try:
            sent.append(await bot.send_message(chat, text, reply_markup=markup))
        except Exception:
            log.exception("не отправил сообщение в чат %s", chat)
    return sent


async def notify_owner(bot, text: str, *, markup=None) -> None:
    """Владельцу — то, что ему знать, а не решать: шаги мастера, запись в CRM."""
    owner = owner_chat()
    if not owner:
        return
    await send_to(bot, [owner], text, markup=markup)
