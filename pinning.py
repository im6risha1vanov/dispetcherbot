"""Сообщения заявки в чате мастера: закрепление и уборка неактуального.

Закреплено всегда не больше одной заявки: перед новым закреплением снимается
предыдущее. Открепляем точечно по сохранённому id, а не через «снять все
закрепления» — в чате могут висеть закрепления администратора.

Неактуальные сообщения удаляются, чтобы мастер не поехал по отозванной заявке.
Никогда не удаляются отчёты мастера и заявки, которые он взял в работу.
"""

import asyncio
import logging

from aiogram.exceptions import TelegramRetryAfter

import db

log = logging.getLogger(__name__)


RETRIES = 3


async def _with_retry(action, what: str, chat_id: int) -> bool:
    """Telegram придерживает частые запросы — повторяем, сколько он просит."""
    for attempt in range(RETRIES):
        try:
            await action()
            return True
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after + 1)
        except Exception as exc:
            log.warning("%s в чате %s не удалось: %s", what, chat_id, exc)
            return False
    log.warning("%s в чате %s не удалось за %d попытки", what, chat_id, RETRIES)
    return False


async def pin_assignment(bot, employee_id: int, chat_id: int, message_id: int) -> None:
    await unpin_assignment(bot, employee_id, chat_id)
    ok = await _with_retry(
        lambda: bot.pin_chat_message(chat_id=chat_id, message_id=message_id),
        "закрепление", chat_id
    )
    if ok:
        await db.set_pinned_message(employee_id, message_id)


async def unpin_assignment(bot, employee_id: int, chat_id: int) -> None:
    pinned = await db.get_pinned_message(employee_id)
    if not pinned:
        return

    ok = await _with_retry(
        lambda: bot.unpin_chat_message(chat_id=chat_id, message_id=pinned),
        "открепление", chat_id
    )
    # Номер забываем только после успеха: иначе повторить будет нечем и заявка
    # останется висеть в закрепе навсегда.
    if ok:
        await db.set_pinned_message(employee_id, None)


async def drop_message(bot, chat_id: int | None, message_id: int | None) -> bool:
    """Убирает сообщение, потерявшее смысл. True — если удалось удалить."""
    if not chat_id or not message_id:
        return False
    # Telegram не отдаёт ботам сообщения старше двух суток — тогда просто False.
    return await _with_retry(
        lambda: bot.delete_message(chat_id=chat_id, message_id=message_id),
        f"удаление {message_id}", chat_id
    )
