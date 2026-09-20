import asyncio
import logging
import re
from datetime import datetime
from decimal import Decimal

from aiogram import Bot, Dispatcher, F
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

import closing
import config
import db
import messages
import pinning
import reporting
from crm import STATUS_ENROUTE, STATUS_IN_WORK, CrmClient, CrmError

log = logging.getLogger("bot")
dp = Dispatcher()

# «На месте» статус в CRM не меняет: мастер приехал, но ещё осматривает —
# в CRM заявка остаётся «В пути», пока он не нажмёт «В работе».
STATUS_BY_STATE = {"enroute": STATUS_ENROUTE, "inwork": STATUS_IN_WORK}

STEP_REPLY = {"enroute": "В пути", "onsite": "На месте", "inwork": "В работе"}


def _is_admin(message: Message) -> bool:
    return str(message.chat.id) == config.ADMIN_CHAT_ID


async def identify_master(user):
    """Узнаёт мастера по telegram_id в справочнике.

    Директор (@Dedoo4ek, DIRECTOR_CHAT_ID) — не мастер: заявок не получает
    и в очередь смены не встаёт. В рабочем чате мастера он закрывает чужую
    заявку по telegram id, без строки в справочнике.
    При первом контакте мастера привязывает по заранее заданному @username.
    """
    if _is_director_user(user):
        return None

    master = await db.master_by_telegram(user.id)
    if master is not None:
        return master

    claimed = await db.claim_master_by_username(user.username or "", user.id)
    if claimed is not None:
        log.info("мастер %s привязан по @%s", claimed["full_name"], user.username)
        return claimed if claimed["is_active"] else None
    return None


@dp.message(Command("start"))
async def cmd_start(message: Message) -> None:
    master = await identify_master(message.from_user)
    if master is not None:
        await message.answer(
            f"Здравствуйте, {master['full_name']}. Вы подключены к боту диспетчеризации.\n"
            "Утром отмечайтесь кнопкой «На смене» в общем чате — порядок отметок задаёт очередь.\n"
            "Заявки будут приходить сюда с кнопками «В пути» и «На месте»."
        )
        return

    await message.answer(
        "Бот диспетчеризации заявок.\n"
        f"Ваш id: {message.from_user.id}\n"
        "Если вы мастер, передайте этот номер администратору — он вас подключит."
    )


@dp.message(Command("ping"))
async def cmd_ping(message: Message) -> None:
    await message.answer("pong")


@dp.message(Command("chatid"))
async def cmd_chatid(message: Message) -> None:
    await message.answer(f"chat_id: {message.chat.id}\nВаш id: {message.from_user.id}")


@dp.message(Command("masters"))
async def cmd_masters(message: Message) -> None:
    if not _is_admin(message):
        return
    rows = await db.list_masters(config.CITY_ID)
    lines = []
    for row in rows:
        chat = "чат есть" if row["chat_id"] else "ЧАТА НЕТ"
        who = "опознан" if row["telegram_id"] else f"ждём @{row['telegram_username'] or '?'}"
        lines.append(
            f"{row['employee_id']} · {row['full_name']} · {chat} · {who}"
            f"{'' if row['is_active'] else ' · выключен'}"
        )
    await message.answer("Мастера филиала:\n" + "\n".join(lines) if lines else "Справочник пуст")


@dp.message(Command("master_link"))
async def cmd_master_link(message: Message, command: CommandObject) -> None:
    if not _is_admin(message):
        return
    parts = (command.args or "").split()
    if len(parts) != 2 or not all(p.lstrip("-").isdigit() for p in parts):
        await message.answer("Формат: /master_link <employee_id> <telegram_id>")
        return

    employee_id, telegram_id = int(parts[0]), int(parts[1])
    if await db.link_telegram(employee_id, telegram_id):
        await message.answer(f"Мастер {employee_id} привязан к {telegram_id}")
    else:
        await message.answer(f"Мастера {employee_id} нет в справочнике")


@dp.message(Command("master_user"))
async def cmd_master_user(message: Message, command: CommandObject) -> None:
    """Прописывает @username заранее: бот привяжется сам, когда мастер напишет /start."""
    if not _is_admin(message):
        return
    parts = (command.args or "").split()
    if len(parts) != 2 or not parts[0].isdigit():
        await message.answer("Формат: /master_user <employee_id> <@username>")
        return

    employee_id, username = int(parts[0]), parts[1]
    if await db.set_master_username(employee_id, username):
        await message.answer(
            f"Мастеру {employee_id} прописан {username}.\n"
            "Попросите его написать боту /start — подключится сам."
        )
    else:
        await message.answer(f"Мастера {employee_id} нет в справочнике")


@dp.message(Command("master_chat"))
async def cmd_master_chat(message: Message, command: CommandObject) -> None:
    """Вызывается в рабочем чате мастера: привязывает этот чат к нему."""
    if not (command.args or "").strip().isdigit():
        await message.answer(
            "Отправьте в рабочем чате мастера: /master_chat <employee_id>\n"
            "Список id — команда /masters в чате администратора."
        )
        return

    employee_id = int(command.args.strip())
    if await db.set_master_chat(employee_id, message.chat.id):
        master = await db.master_by_employee(employee_id)
        await message.answer(f"Этот чат закреплён за мастером: {master['full_name']}.\nСюда будут приходить заявки.")
    else:
        await message.answer(f"Мастера {employee_id} нет в справочнике")


@dp.message(Command("master_off", "master_on"))
async def cmd_master_toggle(message: Message, command: CommandObject) -> None:
    if not _is_admin(message):
        return
    if not (command.args or "").strip().isdigit():
        await message.answer(f"Формат: /{command.command} <employee_id>")
        return

    employee_id = int(command.args.strip())
    is_active = command.command == "master_on"
    if await db.set_master_active(employee_id, is_active):
        await message.answer(f"Мастер {employee_id} {'включён' if is_active else 'выключен'}")
    else:
        await message.answer(f"Мастера {employee_id} нет в справочнике")


@dp.callback_query(F.data == messages.CB_SHIFT_ON)
async def on_shift(callback: CallbackQuery) -> None:
    if _is_director_user(callback.from_user):
        await callback.answer("Директор не встаёт в очередь мастеров", show_alert=True)
        return

    master = await identify_master(callback.from_user)
    if master is None:
        await callback.answer("Вас нет в списке мастеров — обратитесь к администратору", show_alert=True)
        return

    today = datetime.now(config.TIMEZONE).date()
    position, is_new = await db.mark_shift(today, master["employee_id"], config.CITY_ID)
    if is_new:
        await callback.answer(f"Отметил. Вы {position}-й в очереди на сегодня")
        log.info("мастер %s на смене, позиция %d", master["full_name"], position)
    else:
        await callback.answer(f"Уже отмечены, вы {position}-й в очереди")


def _is_supervisor(chat_id: int) -> bool:
    return str(chat_id) in (config.ADMIN_CHAT_ID, config.DIRECTOR_CHAT_ID)


def _director_id() -> str:
    return str(config.DIRECTOR_CHAT_ID) if config.DIRECTOR_CHAT_ID else ""


def _is_director_user(user) -> bool:
    """Директор — человек с telegram id = DIRECTOR_CHAT_ID, не сотрудник филиала."""
    wanted = _director_id()
    return bool(wanted) and user is not None and str(user.id) == wanted


def _director_closing(user, chat_id=None) -> bool:
    """Директор закрывает чужие заявки там, где висит кнопка — в чате мастера.

    Утренний MASTERS_CHAT_ID — сбор смены; сами заявки уходят в рабочие чаты,
    где сидит и директор. Проверяем человека, не id утренней группы.
    """
    return _is_director_user(user)


def _report_employee_id(actor, assignment, *, foreign_ok: bool = False, is_director: bool = False) -> int | None:
    """Чей employee_id живёт в отчёте. На карточке CRM это должен быть мастер.

    Директор не мастер: закрывает чужую заявку от имени назначенного.
    В селекте филиала его нет — выдумывать сотрудника нельзя.
    Без назначения директор отчёт не стартует.
    foreign_ok — чужой мастер может нажать СД/дистанционку, как раньше.
    """
    if is_director:
        return int(assignment["employee_id"]) if assignment is not None else None
    if actor is None:
        return None
    if assignment is not None:
        owner = int(assignment["employee_id"])
        if owner == int(actor["employee_id"]):
            return owner
        return int(actor["employee_id"]) if foreign_ok else None
    return int(actor["employee_id"])


CLOSE_PROMPT_RE = re.compile(r"Закрытие заказа (\d+)")


async def _hinted_collecting_closure(hint_message_id, reply):
    """Ищем живой отчёт по кнопке или реплаю на вопрос бота — не по telegram_id."""
    ids = []
    if hint_message_id:
        ids.append(hint_message_id)
    if reply is not None:
        ids.append(reply.message_id)
        nested = getattr(reply, "reply_to_message", None)
        if nested is not None:
            ids.append(nested.message_id)
    seen: set[int] = set()
    for mid in ids:
        if not mid or mid in seen:
            continue
        seen.add(mid)
        found = await db.collecting_closure_by_message(mid)
        if found is not None:
            return found
    if reply is None:
        return None
    text = reply.text or reply.caption or ""
    match = CLOSE_PROMPT_RE.search(text)
    if match is None:
        return None
    return await db.collecting_closure_by_crm(int(match.group(1)))


async def _actor_closure(
    master,
    *,
    is_director: bool = False,
    chat_id=None,
    hint_message_id=None,
    reply=None,
    prefer_photo: bool = False,
):
    """Отчёт, который сейчас заполняет этот человек.

    У мастера — свой единственный незавершённый.
    У директора в чате мастеров — отчёт той заявки, на чей вопрос он нажал
    или ответил реплаем. Фото без реплая — только к фото-шагу в этом чате.
    """
    if is_director:
        hinted = await _hinted_collecting_closure(hint_message_id, reply)
        if hinted is not None:
            return hinted
        if not prefer_photo or chat_id is None:
            return None
        rows = await db.collecting_closures_in_chat(chat_id)
        photo_rows = [
            row for row in rows
            if (step := _closing_step(row)) is not None and step.kind == "photo"
        ]
        return photo_rows[0] if photo_rows else None
    if master is None:
        return None
    return await db.active_closure(master["employee_id"])


async def _callback_closure(callback: CallbackQuery, master, *, is_director: bool = False):
    message = callback.message
    return await _actor_closure(
        master,
        is_director=is_director,
        chat_id=message.chat.id if message else None,
        hint_message_id=message.message_id if message else None,
    )


async def _message_closure(message: Message, master, *, prefer_photo: bool = False, is_director: bool = False):
    return await _actor_closure(
        master,
        is_director=is_director,
        chat_id=message.chat.id,
        reply=message.reply_to_message,
        prefer_photo=prefer_photo,
    )


async def _remove_from_master(bot, previous, request, crm_id: int) -> None:
    """Заявку забрали — убираем её из чата мастера, чтобы он по ней не поехал."""
    await pinning.unpin_assignment(bot, previous["employee_id"], previous["chat_id"])
    await pinning.drop_message(bot, previous["chat_id"], previous["reminder_message_id"])
    if await pinning.drop_message(bot, previous["chat_id"], previous["message_id"]):
        return

    # Telegram не даёт удалять сообщения старше двух суток — тогда хотя бы
    # снимаем кнопки, чтобы мастер не отметился по чужой заявке.
    try:
        await bot.edit_message_text(
            chat_id=previous["chat_id"],
            message_id=previous["message_id"],
            text=messages.assignment_text(dict(request), "reassigned"),
            reply_markup=None,
        )
    except Exception:
        log.warning("заявка %s: не убрал кнопки у прежнего мастера", crm_id)


@dp.callback_query(F.data.startswith(f"{messages.CB_MOVE}:"))
async def on_move_start(callback: CallbackQuery) -> None:
    if not _is_supervisor(callback.message.chat.id):
        await callback.answer("Передавать заявки может администратор или директор", show_alert=True)
        return

    crm_id = int(callback.data.split(":", 1)[1])
    current = await db.active_assignment(crm_id)
    busy = current["employee_id"] if current else None

    slots = await db.shift_slots(
        datetime.now(config.TIMEZONE).date(), config.CITY_ID, sorted(config.SILENT_STATUSES)
    )
    # Занятых показываем с пометкой, но не прячем: это назначение принудительное.
    available = [s for s in slots if s["delivery_chat_id"] and s["employee_id"] != busy]
    if not available:
        await callback.answer("Некому передать: на смене больше никого нет", show_alert=True)
        return

    await callback.message.edit_reply_markup(
        reply_markup=messages.master_choice_keyboard(crm_id, available)
    )
    await callback.answer("Кому передать?")


@dp.callback_query(F.data.startswith(f"{messages.CB_MOVE_CANCEL}:"))
async def on_move_cancel(callback: CallbackQuery) -> None:
    crm_id = int(callback.data.split(":", 1)[1])
    await callback.message.edit_reply_markup(reply_markup=messages.move_keyboard(crm_id))
    await callback.answer("Отменено")


@dp.callback_query(F.data.startswith(f"{messages.CB_MOVE_TO}:"))
async def on_move_to(callback: CallbackQuery, crm: CrmClient) -> None:
    if not _is_supervisor(callback.message.chat.id):
        await callback.answer("Передавать заявки может администратор или директор", show_alert=True)
        return

    _, raw_crm_id, raw_employee_id = callback.data.split(":")
    crm_id, employee_id = int(raw_crm_id), int(raw_employee_id)

    request = await db.request_brief(crm_id)
    target = await db.master_by_employee(employee_id)
    if request is None or target is None:
        await callback.answer("Заявка или мастер не найдены", show_alert=True)
        return

    previous = await db.active_assignment(crm_id)
    if previous:
        await db.reassign(previous["id"])
        if previous["chat_id"] and previous["message_id"]:
            await _remove_from_master(callback.bot, previous, request, crm_id)

    chat_id = target["chat_id"] or target["telegram_id"]
    rec = dict(request) | {"telegram_username": target["telegram_username"]}
    assignment_id = await db.create_assignment(crm_id, employee_id, config.CITY_ID)
    try:
        sent = await callback.bot.send_message(
            chat_id,
            messages.assignment_text(rec, "assigned"),
            reply_markup=messages.master_keyboard(crm_id, "assigned"),
        )
    except Exception:
        await db.delete_assignment(assignment_id)
        await callback.answer("Не смог отправить заявку этому мастеру", show_alert=True)
        log.exception("ручной перенос заявки %s мастеру %s не доставлен", crm_id, employee_id)
        return

    await db.save_message_ref(assignment_id, sent.chat.id, sent.message_id)
    await pinning.pin_assignment(callback.bot, employee_id, sent.chat.id, sent.message_id)
    try:
        await crm.assign_master(crm_id, employee_id)
    except CrmError:
        log.exception("ручной перенос заявки %s: в CRM не записан", crm_id)

    await callback.message.edit_text(
        f"🔄 Заказ {crm_id} → {target['full_name']} (передал вручную)",
        reply_markup=messages.move_keyboard(crm_id),
    )
    await callback.answer(f"Передано: {target['full_name']}")
    log.info("заявка %s передана вручную мастеру %s", crm_id, target["full_name"])


@dp.callback_query(F.data.startswith(f"{messages.CB_REMOTE}:"))
async def on_remote_ask(callback: CallbackQuery) -> None:
    """Гарантию можно решить звонком — просим у диспетчеров номер клиента."""
    crm_id = int(callback.data.split(":", 1)[1])
    master = await identify_master(callback.from_user)
    if master is None:
        await callback.answer("Вас нет в списке мастеров", show_alert=True)
        return
    if not config.DISPATCHERS_CHAT_ID:
        await callback.answer("Чат диспетчеров не настроен, скажите администратору", show_alert=True)
        return

    request = await db.request_brief(crm_id)
    if request is None:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    request_id = await db.create_info_request(
        messages.KIND_REMOTE, crm_id, master["employee_id"], callback.message.chat.id
    )
    asked = await callback.bot.send_message(
        config.DISPATCHERS_CHAT_ID,
        messages.info_request_text(messages.KIND_REMOTE, request, master["full_name"]),
    )
    await db.link_info_request(request_id, asked.message_id)

    # Номер придёт отдельным сообщением, а кнопка уже ждёт подтверждения решения.
    await callback.message.edit_reply_markup(
        reply_markup=messages.master_keyboard(crm_id, "assigned", warranty="done")
    )
    await callback.answer("Запросил номер у диспетчеров")
    log.info("заявка %s: %s просит номер для дистанционного решения", crm_id, master["full_name"])


@dp.callback_query(F.data.startswith(f"{messages.CB_REMOTE_DONE}:"))
async def on_remote_done(callback: CallbackQuery) -> None:
    """Мастер решил гарантию звонком: отчёт без вопросов, всё по нулям."""
    crm_id = int(callback.data.split(":", 1)[1])
    master = await identify_master(callback.from_user)
    director = _director_closing(callback.from_user, callback.message.chat.id)
    if master is None and not director:
        await callback.answer("Вас нет в списке мастеров", show_alert=True)
        return

    assignment = await db.active_assignment(crm_id)
    owner_id = _report_employee_id(master, assignment, foreign_ok=True, is_director=director)
    if owner_id is None:
        await callback.answer(
            "Нет назначения по этой заявке" if assignment is None else "Эта заявка не за вами",
            show_alert=True,
        )
        return

    if director:
        log.info("заявка %s: директор закрывает дистанционно за мастера %s", crm_id, owner_id)

    closure = await db.open_closure(
        crm_id, owner_id, callback.message.chat.id, closing.KIND_REMOTE
    )
    if closure is None:
        await callback.answer("Сначала завершите предыдущий отчёт", show_alert=True)
        return

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Отправил администратору")
    await _advance_closing(callback.bot, closure["id"])

@dp.callback_query(F.data.startswith(f"{messages.CB_PHONE}:"))
async def on_phone_request(callback: CallbackQuery) -> None:
    await _ask_dispatchers(callback, messages.KIND_PHONE)


@dp.callback_query(F.data.startswith(f"{messages.CB_APARTMENT}:"))
async def on_apartment_request(callback: CallbackQuery) -> None:
    await _ask_dispatchers(callback, messages.KIND_APARTMENT)


async def _ask_dispatchers(callback: CallbackQuery, kind: str) -> None:
    crm_id = int(callback.data.split(":", 1)[1])

    master = await identify_master(callback.from_user)
    if master is None:
        await callback.answer("Вас нет в списке мастеров", show_alert=True)
        return
    if not config.DISPATCHERS_CHAT_ID:
        await callback.answer("Чат диспетчеров не настроен, скажите администратору", show_alert=True)
        return

    request = await db.request_brief(crm_id)
    if request is None:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    request_id = await db.create_info_request(
        kind, crm_id, master["employee_id"], callback.message.chat.id
    )
    asked = await callback.bot.send_message(
        config.DISPATCHERS_CHAT_ID,
        messages.info_request_text(kind, request, master["full_name"]),
    )
    await db.link_info_request(request_id, asked.message_id)
    await callback.answer("Спросил у диспетчеров")
    log.info("заявка %s: %s запросил %s", crm_id, master["full_name"], kind)


@dp.callback_query(F.data.startswith(f"{messages.CB_CLOSE}:"))
async def on_close_start(callback: CallbackQuery) -> None:
    await _start_report(callback, closing.KIND_CLOSE, "Заполним отчёт")


@dp.callback_query(F.data.startswith(f"{messages.CB_SD}:"))
async def on_sd_start(callback: CallbackQuery) -> None:
    await _start_report(callback, closing.KIND_SD_OPEN, "Оформим сложную диагностику")


async def _start_report(callback: CallbackQuery, kind: str, reply: str) -> None:
    crm_id = int(callback.data.split(":", 1)[1])
    master = await identify_master(callback.from_user)
    director = _director_closing(callback.from_user, callback.message.chat.id)
    if master is None and not director:
        await callback.answer("Вас нет в списке мастеров", show_alert=True)
        return

    assignment = await db.active_assignment(crm_id)
    owner_id = _report_employee_id(master, assignment, is_director=director)
    if assignment is None or owner_id is None:
        await callback.answer("Эта заявка не за вами", show_alert=True)
        return

    if director:
        log.info("заявка %s: директор ведёт отчёт за мастера %s", crm_id, owner_id)

    closure = await db.open_closure(crm_id, owner_id, callback.message.chat.id, kind)
    if closure is None:
        await callback.answer("Сначала завершите предыдущий отчёт", show_alert=True)
        return

    # Пока мастер отвечает, кнопки заявки только мешают.
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    await callback.answer(reply)
    await _ask_closing_step(callback.bot, closure, closing.FIRST_STEP_BY_KIND[kind])


async def _ask_closing_step(bot, closure, step_key: str) -> None:
    step = closing.STEPS_BY_KIND[closure["kind"]][step_key]
    await db.set_closure_step(closure["id"], step_key)
    sent = await bot.send_message(
        closure["chat_id"],
        messages.closing_question(step, closure["crm_id"]),
        reply_markup=messages.closing_keyboard(step),
    )
    await db.remember_closure_message(closure["id"], sent.message_id)


async def _advance_closing(bot, closure_id: int) -> None:
    """Переходит к следующему вопросу или отправляет отчёт администратору."""
    closure = await db.closure_by_id(closure_id)
    following = closing.next_step_for(closure["kind"], closure["step"], closure)
    if following:
        await _ask_closing_step(bot, closure, following)
        return

    master = await db.master_by_employee(closure["employee_id"])
    if not config.ADMIN_CHAT_ID:
        await bot.send_message(closure["chat_id"], "Отчёт принят, но чат администратора не настроен.")
        return

    sent = await bot.send_message(
        config.ADMIN_CHAT_ID,
        closing.summary(closure, master["full_name"]),
        reply_markup=messages.admin_decision_keyboard(closure_id),
    )
    for kind, file_ids in closing.as_dict(closure["photos"]).items():
        for file_id in file_ids:
            try:
                await bot.send_photo(config.ADMIN_CHAT_ID, file_id, caption=f"{kind} · заказ {closure['crm_id']}")
            except Exception:
                log.warning("не переслал фото %s администратору", kind)

    await db.submit_closure(closure_id, sent.chat.id, sent.message_id)
    # У администратора переписка остаётся, у мастера — убирается: фото уже в базе.
    await _clear_master_chat(bot, closure)
    await bot.send_message(closure["chat_id"], "Отчёт отправлен администратору на проверку.")
    log.info("заявка %s: отчёт отправлен администратору", closure["crm_id"])


async def _clear_master_chat(bot, closure) -> None:
    """Убирает переписку по отчёту из чата мастера.

    Фото уже сохранены и пересланы администратору, поэтому в чате мастера они
    только мешают: заявки и отчёты там читают с телефона.
    """
    for message_id in await db.closure_messages(closure["id"]):
        await pinning.drop_message(bot, closure["chat_id"], message_id)
    await db.clear_closure_messages(closure["id"])

@dp.callback_query(F.data.startswith(f"{messages.CB_CLOSE_ANSWER}:"))
async def on_closing_choice(callback: CallbackQuery) -> None:
    _, step_key, value = callback.data.split(":")
    master = await identify_master(callback.from_user)
    director = _director_closing(callback.from_user, callback.message.chat.id)
    closure = await _callback_closure(callback, master, is_director=director)
    if closure is None or closure["step"] != step_key:
        await callback.answer("Этот вопрос уже не актуален", show_alert=True)
        return

    await db.save_closure_answer(closure["id"], closing.answer_field(step_key), value, None)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer()
    await _advance_closing(callback.bot, closure["id"])


@dp.callback_query(F.data.startswith(f"{messages.CB_CLOSE_SKIP}:"))
async def on_closing_skip(callback: CallbackQuery) -> None:
    master = await identify_master(callback.from_user)
    director = _director_closing(callback.from_user, callback.message.chat.id)
    closure = await _callback_closure(callback, master, is_director=director)
    if closure is None:
        await callback.answer("Отчёт уже закрыт", show_alert=True)
        return

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Пропущено")
    await _advance_closing(callback.bot, closure["id"])


@dp.callback_query(F.data.startswith(f"{messages.CB_CLOSE_OK}:"))
async def on_closing_approved(callback: CallbackQuery, crm: CrmClient) -> None:
    await _decide_closing(callback, crm, approved=True)


@dp.callback_query(F.data.startswith(f"{messages.CB_CLOSE_NO}:"))
async def on_closing_rejected(callback: CallbackQuery, crm: CrmClient) -> None:
    await _decide_closing(callback, crm, approved=False)


async def _decide_closing(callback: CallbackQuery, crm: CrmClient, *, approved: bool) -> None:
    if not _is_supervisor(callback.message.chat.id):
        await callback.answer("Решение принимает администратор", show_alert=True)
        return

    closure_id = int(callback.data.split(":", 1)[1])
    closure = await db.closure_by_id(closure_id)
    if closure is None or closure["state"] != "pending_admin":
        await callback.answer("Отчёт уже обработан", show_alert=True)
        return

    await db.decide_closure(closure_id, approved)
    await callback.message.edit_reply_markup(reply_markup=None)

    if not approved:
        await callback.bot.send_message(
            closure["chat_id"],
            f"❌ Отчёт по заказу {closure['crm_id']} отклонён администратором.\n"
            "Заполните заново: нажмите «Закрыть заявку» в сообщении заявки.",
        )
        await callback.answer("Отклонено, отчёт вернулся мастеру")
        return

    await callback.answer("Подтверждено")
    await _write_closing(callback.bot, crm, closure)


async def _write_closing(bot, crm: CrmClient, closure) -> None:
    """Запись закрытия в CRM. Пока выключена — проводит администратор руками."""
    if not config.CRM_ALLOW_CLOSING:
        log.warning(
            "закрытие заявки %s не записано: CRM_ALLOW_CLOSING=false. Поля: %s",
            closure["crm_id"], closing.crm_payload(closure),
        )
        await bot.send_message(
            config.ADMIN_CHAT_ID,
            f"✅ Отчёт по заказу {closure['crm_id']} подтверждён.\n"
            "Запись закрытия в CRM пока выключена — проведите заявку вручную.",
        )
        await bot.send_message(
            closure["chat_id"], f"✅ Отчёт по заказу {closure['crm_id']} принят."
        )
        await db.mark_closure_written(closure["id"])
        return

    try:
        # Карточка читается целиком и пишется как есть: employee_id не трогаем.
        # Директора нет в селекте филиала — отчёт держит id назначенного мастера.
        if closure["kind"] == closing.KIND_REMOTE:
            await crm.close_remote(closure["crm_id"], closing.crm_payload(closure))
        elif closure["kind"] == closing.KIND_SD_OPEN:
            await crm.open_sd(
                closure["crm_id"], closing.sd_comment(closure), closure["photos"]
            )
        else:
            await crm.close_request(
                closure["crm_id"], closing.crm_payload(closure), closure["photos"]
            )
    except CrmError:
        log.exception("заявка %s: закрытие не записано в CRM", closure["crm_id"])
        await bot.send_message(
            config.ADMIN_CHAT_ID,
            f"⚠️ Заказ {closure['crm_id']}: CRM не приняла закрытие, проведите вручную.",
        )
        return

    await db.mark_closure_written(closure["id"])
    await bot.send_message(closure["chat_id"], f"✅ Заказ {closure['crm_id']} закрыт.")
    log.info("заявка %s: закрытие записано в CRM", closure["crm_id"])


@dp.message(F.photo)
async def on_closing_photo(message: Message) -> None:
    """Фото во время отчёта ложится в то окно CRM, о котором был вопрос."""
    master = await identify_master(message.from_user)
    director = _director_closing(message.from_user, message.chat.id)
    closure = await _message_closure(
        message, master, prefer_photo=True, is_director=director
    )
    if closure is None or closure["state"] != "collecting":
        return

    step = _closing_step(closure)
    if step is None or step.kind != "photo":
        return

    await db.add_closure_photo(closure["id"], step.photo_kind, message.photo[-1].file_id)
    await db.remember_closure_message(closure["id"], message.message_id)

    if step.multi:
        # Ждём остальные снимки: мастер закончит кнопкой «Готово».
        reply = await message.reply("Принято. Пришлите ещё или нажмите «Готово».")
        await db.remember_closure_message(closure["id"], reply.message_id)
        return

    reply = await message.reply("Принято")
    await db.remember_closure_message(closure["id"], reply.message_id)
    await _advance_closing(message.bot, closure["id"])


SD_MENTION_RE = re.compile(r"@\w*bot\b.*?(\d{5,})", re.IGNORECASE | re.DOTALL)


@dp.message(F.text.regexp(SD_MENTION_RE.pattern))
async def on_sd_close_request(message: Message) -> None:
    """Мастер упомянул бота с номером заявки — предлагаем закрыть её СД."""
    match = SD_MENTION_RE.search(message.text or "")
    if match is None:
        return

    master = await identify_master(message.from_user)
    director = _director_closing(message.from_user, message.chat.id)
    if master is None and not director:
        return

    crm_id = int(match.group(1))
    request = await db.request_brief(crm_id)
    if request is None:
        await message.reply(f"Заявку {crm_id} не нахожу. Проверьте номер.")
        return

    await message.reply(
        messages.sd_close_offer(request),
        reply_markup=messages.sd_close_keyboard(crm_id),
    )
    who = "директор" if director else master["full_name"]
    log.info("мастер %s запросил закрытие СД по заявке %s", who, crm_id)


@dp.callback_query(F.data.startswith(f"{messages.CB_SD_CLOSE}:"))
async def on_sd_close_start(callback: CallbackQuery) -> None:
    crm_id = int(callback.data.split(":", 1)[1])
    master = await identify_master(callback.from_user)
    director = _director_closing(callback.from_user, callback.message.chat.id)
    if master is None and not director:
        await callback.answer("Вас нет в списке мастеров", show_alert=True)
        return

    assignment = await db.active_assignment(crm_id)
    owner_id = _report_employee_id(master, assignment, foreign_ok=True, is_director=director)
    if owner_id is None:
        await callback.answer(
            "Нет назначения по этой заявке" if assignment is None else "Эта заявка не за вами",
            show_alert=True,
        )
        return

    if director:
        log.info("заявка %s: директор закрывает СД за мастера %s", crm_id, owner_id)

    closure = await db.open_closure(
        crm_id, owner_id, callback.message.chat.id, closing.KIND_SD_CLOSE
    )
    if closure is None:
        await callback.answer("Сначала завершите предыдущий отчёт", show_alert=True)
        return

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Заполним отчёт по СД")
    await _ask_closing_step(callback.bot, closure, closing.FIRST_STEP_BY_KIND[closing.KIND_SD_CLOSE])


def _looks_like_closing_prompt(text: str, crm_id: int) -> bool:
    """И вопрос, и хвост «Ответьте на это сообщение» — это промпт бота."""
    if not text:
        return False
    return (
        f"Закрытие заказа {crm_id}" in text
        or "Ответьте на это сообщение" in text
    )


async def _answers_question(message: Message, closure) -> bool:
    """Текст засчитывается, только если это ответ на конкретный вопрос бота.

    В чате мастера сидят ещё директор и администратор, и обычная переписка
    шла в отчёт как ответы. Reply снимает эту двусмысленность.

    Считаем и реплай на сам вопрос, и на то же сообщение с подсказкой
    «Ответьте на это сообщение», и реплай на свой предыдущий ответ.
    """
    replied = message.reply_to_message
    if replied is None:
        return False
    known = await db.closure_messages(closure["id"])
    if replied.message_id in known:
        return True
    nested = replied.reply_to_message
    if nested is not None and nested.message_id in known:
        return True
    text = replied.text or replied.caption or ""
    from_bot = bool(replied.from_user and replied.from_user.is_bot)
    if from_bot and _looks_like_closing_prompt(text, closure["crm_id"]):
        await db.remember_closure_message(closure["id"], replied.message_id)
        return True
    return False


async def _need_reply_to_question(message: Message, closure, step) -> None:
    """Молчание хуже лишнего напоминания: мастер должен понять, что ответ не принят."""
    log.info(
        "заявка %s: ответ без реплая на шаг %s пропущен",
        closure["crm_id"], step.key,
    )
    sent = await message.reply(
        "↩️ Ответьте реплаем на вопрос бота — так я отличу отчёт от переписки в чате.\n\n"
        + messages.closing_question(step, closure["crm_id"]),
        reply_markup=messages.closing_keyboard(step),
    )
    await db.remember_closure_message(closure["id"], sent.message_id)


def _closing_step(closure):
    key = closing.canonical_step(closure["kind"], closure["step"])
    return closing.STEPS_BY_KIND[closure["kind"]].get(key)

@dp.message(F.text.regexp(r"^\s*\d+([.,]\d+)?\s*$"))
async def on_closing_amount(message: Message) -> None:
    master = await identify_master(message.from_user)
    director = _director_closing(message.from_user, message.chat.id)
    closure = await _message_closure(message, master, is_director=director)
    if closure is None or closure["state"] != "collecting":
        return

    step = _closing_step(closure)
    if step is None or step.kind != "amount":
        # «4000» на текстовом шаге (предоплата / комментарий филиала) — не глотать.
        raise SkipHandler()
    if not await _answers_question(message, closure):
        await _need_reply_to_question(message, closure, step)
        return

    amount = Decimal(message.text.strip().replace(",", "."))
    await db.save_closure_answer(closure["id"], closing.answer_field(step.key), amount, step.key)
    await db.remember_closure_message(closure["id"], message.message_id)
    log.info("заявка %s: шаг %s = %s", closure["crm_id"], step.key, amount)
    await _advance_closing(message.bot, closure["id"])


@dp.message(F.text)
async def on_closing_text(message: Message) -> None:
    """Свободный ответ мастера: комментарий филиала бот записывает как есть."""
    master = await identify_master(message.from_user)
    director = _director_closing(message.from_user, message.chat.id)
    closure = await _message_closure(message, master, is_director=director)
    if closure is None or closure["state"] != "collecting":
        return

    step = _closing_step(closure)
    if step is None or step.kind != "text":
        return
    if not await _answers_question(message, closure):
        await _need_reply_to_question(message, closure, step)
        return

    text = message.text.strip()
    if closure["kind"] == closing.KIND_SD_OPEN and step.key == "comment":
        if closing.parse_sd_ready_at(text) is None:
            sent = await message.reply(
                closing.SD_COMMENT_REJECT
                + "\n\n"
                + messages.closing_question(step, closure["crm_id"]),
                reply_markup=messages.closing_keyboard(step),
            )
            await db.remember_closure_message(closure["id"], sent.message_id)
            return

    await db.save_closure_answer(
        closure["id"], closing.answer_field(step.key), text, step.key
    )
    await db.remember_closure_message(closure["id"], message.message_id)
    log.info("заявка %s: шаг %s принят", closure["crm_id"], step.key)
    await _advance_closing(message.bot, closure["id"])


@dp.message(F.reply_to_message)
async def on_dispatcher_reply(message: Message) -> None:
    """Диспетчер ответил реплаем — передаём ответ мастеру."""
    if str(message.chat.id) != config.DISPATCHERS_CHAT_ID:
        return

    request = await db.info_request_by_message(message.reply_to_message.message_id)
    if request is None:
        return

    answer = (message.text or message.caption or "").strip()
    if not answer:
        return

    await message.bot.send_message(
        request["master_chat_id"],
        messages.info_answer_text(request["kind"], request["crm_id"], answer),
    )
    await db.mark_info_answered(request["id"], message.from_user.full_name)
    await message.reply("Передал мастеру")
    log.info("заявка %s: ответ (%s) передан мастеру", request["crm_id"], request["kind"])


@dp.callback_query(F.data.startswith(f"{messages.CB_ENROUTE}:"))
async def on_enroute(callback: CallbackQuery, crm: CrmClient) -> None:
    await _advance(callback, crm, "enroute")


@dp.callback_query(F.data.startswith(f"{messages.CB_ONSITE}:"))
async def on_onsite(callback: CallbackQuery, crm: CrmClient) -> None:
    await _advance(callback, crm, "onsite")


@dp.callback_query(F.data.startswith(f"{messages.CB_INWORK}:"))
async def on_inwork(callback: CallbackQuery, crm: CrmClient) -> None:
    await _advance(callback, crm, "inwork")


async def _advance(callback: CallbackQuery, crm: CrmClient, state: str) -> None:
    crm_id = int(callback.data.split(":", 1)[1])

    master = await identify_master(callback.from_user)
    if master is None:
        await callback.answer("Вас нет в списке мастеров", show_alert=True)
        return

    assignment = await db.active_assignment(crm_id)
    if assignment is None or assignment["employee_id"] != master["employee_id"]:
        await callback.answer("Эта заявка сейчас не за вами", show_alert=True)
        return

    updated = await db.advance_assignment(assignment["id"], state)
    if updated is None:
        await callback.answer("Уже отмечено")
        return

    await callback.answer(STEP_REPLY[state])
    await callback.message.edit_reply_markup(reply_markup=messages.master_keyboard(crm_id, state))

    # Заявку приняли — напоминание о ней стало мусором в чате.
    if state == "enroute" and assignment["reminder_message_id"]:
        await pinning.drop_message(
            callback.bot, assignment["chat_id"], assignment["reminder_message_id"]
        )
        await db.clear_reminder_message(assignment["id"])

    if state not in STATUS_BY_STATE:
        log.info("заявка %s: %s отметил «%s», статус в CRM не меняем",
                 crm_id, master["full_name"], state)
        return

    try:
        damage = await crm.set_status(crm_id, STATUS_BY_STATE[state])
        if damage and config.ADMIN_CHAT_ID:
            await callback.bot.send_message(
                config.ADMIN_CHAT_ID,
                f"‼️ Заказ {crm_id}: смена статуса задела чужие поля в CRM.\n"
                + "\n".join(damage[:5])
                + "\nПроверьте карточку руками.",
            )
    except CrmError:
        log.exception("заявка %s: статус %s не записан в CRM", crm_id, state)

    log.info("заявка %s: %s отметил «%s»", crm_id, master["full_name"], state)


async def main() -> None:
    logging.basicConfig(
        level=config.LOG_LEVEL,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    config.validate(config.BOT_REQUIRED)

    await db.connect()
    crm = CrmClient()
    bot = Bot(token=config.TELEGRAM_BOT_TOKEN)

    async def download(file_id: str) -> bytes | None:
        """Фото отчёта живут в Telegram — забираем их перед загрузкой в CRM."""
        try:
            info = await bot.get_file(file_id)
            buffer = await bot.download_file(info.file_path)
            return buffer.read()
        except Exception:
            log.exception("не скачал файл %s из Telegram", file_id)
            return None

    crm._download_file = download
    reporting.attach(bot, config.ADMIN_CHAT_ID, "боте")
    bot._crm_download = download  # СД пишется из своего клиента

    try:
        await dp.start_polling(bot, crm=crm)
    finally:
        await crm.close()
        await bot.session.close()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
