import asyncio
import logging
import signal
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

import httpx
from aiogram import Bot

import config
import db
import dispatch_queue
import messages
import pinning
import reporting
import roles
from crm import CrmClient, CrmError

log = logging.getLogger("poller")

BASELINE_KEY = "baseline_seeded_at"


def _today() -> datetime:
    return datetime.now(config.TIMEZONE)


def _rotation_key(day) -> str:
    return f"rotation:{config.CITY_ID}:{day.isoformat()}"


def _shift_prompt_key(day) -> str:
    return f"shift_prompt:{config.CITY_ID}:{day.isoformat()}"


def _no_masters_key(day) -> str:
    return f"no_masters_alert:{config.CITY_ID}:{day.isoformat()}"


def format_notification(rec: Mapping[str, Any]) -> str:
    header = f"🆕 Заявка #{rec['crm_id']}"
    if rec["req_type"]:
        header += f" ({rec['req_type']})"

    when = (
        rec["opened_at"].astimezone(config.TIMEZONE).strftime("%d.%m.%y %H:%M")
        if rec["opened_at"]
        else "время не указано"
    )
    status = rec["status_text"] or "—"
    if rec["is_recall"]:
        status += " · отзывная"

    lines = [header, f"{when} · {status}"]
    if rec["customer_name"]:
        lines.append(rec["customer_name"])
    if rec["address"]:
        lines.append(rec["address"])
    return "\n".join(lines)


async def notify_feed(bot: Bot, text: str, *, markup=None) -> None:
    """Лента заявок: что пришло и кому ушло. Видят наблюдатели, не мастера.

    Владельцу — только текст: кнопки в его чате всё равно не сработают,
    решения принимают администратор и директор.
    """
    owner = roles.owner_chat()
    for chat in roles.feed_chats():
        try:
            await bot.send_message(
                chat, text, reply_markup=None if chat == owner else markup
            )
        except Exception:
            log.exception("не отправил в ленту, чат %s", chat)


async def alert(bot: Bot, text: str, *, director: bool = False) -> None:
    """Рабочая тревога. Решают администратор и директор, владелец просто знает."""
    wanted = (await roles.admin_chat(), roles.director_chat() if director else "", roles.owner_chat())
    chats: list[str] = []
    for chat in wanted:
        if chat and chat not in chats:
            chats.append(chat)
    await roles.send_to(bot, chats, text)


async def seed_baseline(crm: CrmClient) -> None:
    """Первый запуск: всё, что уже открыто в CRM, не новость — запоминаем молча."""
    rows = await crm.fetch_open_requests()
    for row in rows:
        await db.insert_request(row, config.CITY_ID, notified=True)
    await db.set_state(BASELINE_KEY, _today().isoformat())
    log.info("стартовый снимок: %d открытых заявок помечены известными, уведомления не слались", len(rows))


async def poll_once(crm: CrmClient, bot: Bot) -> None:
    rows = await crm.fetch_open_requests()
    new_ids = []
    for row in sorted(rows, key=lambda r: r.crm_id):
        # Грид отдаёт и закрытые заявки, поэтому активность определяется статусом.
        is_open = row.status_text in config.ACTIVE_STATUSES
        if await db.insert_request(row, config.CITY_ID, notified=False, is_open=is_open):
            new_ids.append(row.crm_id)
    if new_ids:
        log.info("новых заявок: %d (%s)", len(new_ids), ", ".join(map(str, new_ids)))

    active_ids = [r.crm_id for r in rows if r.status_text in config.ACTIVE_STATUSES]
    closed = await db.close_requests_absent_from_grid(config.CITY_ID, active_ids)
    if closed:
        log.info("закрыто в CRM с прошлого опроса: %d", closed)

    await flush_notifications(bot)
    await maybe_collect_shift(bot)
    await check_shift_roll_call(bot)
    await dispatch_pending(crm, bot)
    await check_accept_reminders(bot)
    await check_accept_timeouts(crm, bot)
    await check_onsite_timeouts(bot)
    await send_payouts(crm, bot)
    await maybe_send_digest(bot)


async def flush_notifications(bot: Bot) -> None:
    for rec in await db.pending_notifications(config.CITY_ID, sorted(config.SILENT_STATUSES)):
        try:
            await notify_feed(bot, format_notification(rec))
        except Exception:
            # Порядок важнее скорости: не пропускаем заявку вперёд упавшей.
            log.exception("заявка %s не отправлена, повтор на следующем опросе", rec["crm_id"])
            return
        await db.mark_notified(rec["crm_id"])


async def maybe_collect_shift(bot: Bot) -> None:
    """В SHIFT_CHECK_TIME зовёт мастеров отметиться. Порядок плюсов задаёт очередь дня."""
    now = _today()
    if now.time() < config.SHIFT_CHECK_TIME:
        return
    key = _shift_prompt_key(now.date())
    if await db.get_state(key):
        return

    usernames = await db.active_master_usernames(config.CITY_ID)
    await bot.send_message(
        config.MASTERS_CHAT_ID,
        messages.shift_prompt_text(usernames),
        reply_markup=messages.shift_keyboard(),
    )
    await db.set_state(key, now.isoformat())
    log.info("сбор смены на %s отправлен", now.date())


async def check_shift_roll_call(bot: Bot) -> None:
    """Через SHIFT_ROLL_CALL_MIN после сбора сверяем список с отметившимися."""
    now = _today()
    key = f"roll_call:{config.CITY_ID}:{now.date().isoformat()}"
    if await db.get_state(key):
        return

    sent_at = await db.get_state(_shift_prompt_key(now.date()))
    if not sent_at:
        return
    if (now - datetime.fromisoformat(sent_at)).total_seconds() < config.SHIFT_ROLL_CALL_MIN * 60:
        return

    missing = await db.masters_without_shift(now.date(), config.CITY_ID)
    if missing:
        names = "\n".join(f"· {m['full_name']}" for m in missing)
        await alert(bot, f"📋 Не отметились на смену ({len(missing)}):\n{names}", director=True)
    else:
        await alert(bot, "📋 Смена собрана полностью", director=True)

    await db.set_state(key, now.isoformat())
    log.info("сверка смены: не отметились %d", len(missing))


async def maybe_send_digest(bot: Bot) -> None:
    """Итоги дня директору. Считаем по суммам из CRM, ничего не досчитывая."""
    now = _today()
    if now.time() < config.DIGEST_TIME:
        return
    key = f"digest:{config.CITY_ID}:{now.date().isoformat()}"
    if await db.get_state(key):
        return

    totals = await db.daily_totals(now.date(), config.CITY_ID, sorted(config.DONE_STATUSES))
    leftovers = await db.day_leftovers(now.date(), config.CITY_ID, sorted(config.SILENT_STATUSES))
    await notify_feed(bot, messages.digest_text(now.date(), totals, leftovers))
    await db.set_state(key, now.isoformat())
    log.info("дайджест за %s отправлен: заявок %s", now.date(), totals["orders"])


async def dispatch_pending(crm: CrmClient, bot: Bot) -> None:
    now = _today()
    if not dispatch_queue.in_assign_window(now, config.ASSIGN_WINDOW_START, config.ASSIGN_WINDOW_END):
        return

    pending = await db.requests_awaiting_assignment(
        config.CITY_ID, sorted(config.ASSIGNABLE_STATUSES), config.ASSIGN_LEAD_MIN
    )
    # SQL уже режет по времени; повторная проверка — чтобы 0 больше не значило «без задержки».
    pending = [
        rec
        for rec in pending
        if _visit_lead_due(rec["opened_at"], now=now, lead_minutes=config.ASSIGN_LEAD_MIN)
    ]
    if not pending:
        return

    slots = await _slots(now.date())
    if not slots:
        await _warn_no_masters(bot, now.date(), len(pending))
        return

    for rec in pending:
        if await assign_request(crm, bot, rec, slots):
            # Занятость из начала опроса устарела: второй заявке нужен уже другой свободный.
            slots = await _slots(now.date())


async def _slots(day) -> list[dispatch_queue.ShiftSlot]:
    rows = await db.shift_slots(day, config.CITY_ID, sorted(config.SILENT_STATUSES))
    return [
        dispatch_queue.ShiftSlot(
            employee_id=row["employee_id"],
            position=row["position"],
            full_name=row["full_name"],
            delivery_chat_id=row["delivery_chat_id"],
            username=row["telegram_username"],
            is_busy=row["is_busy"],
        )
        for row in rows
    ]


def _visit_lead_due(opened_at, *, now: datetime | None = None, lead_minutes: int) -> bool:
    """Пора действовать относительно времени визита.

    0 минут — в момент визита, не «сразу как заявка появилась в CRM».
    Без opened_at ждать нечего — сразу.
    """
    if opened_at is None:
        return True
    now = now or _today()
    return opened_at <= now + timedelta(minutes=max(int(lead_minutes), 0))


def _alarm_is_due(opened_at, *, now: datetime | None = None, lead_minutes: int | None = None) -> bool:
    """«Нет свободных» бьём за час до визита. Без времени визита — сразу."""
    lead = config.ESCALATE_LEAD_MIN if lead_minutes is None else lead_minutes
    return _visit_lead_due(opened_at, now=now, lead_minutes=lead)


async def _escalation_chats() -> list[str]:
    """Одно и то же «нет свободных» — директору, администратору и владельцу."""
    return await roles.alert_chats()


async def _warn_no_masters(bot: Bot, day, pending_count: int) -> None:
    key = _no_masters_key(day)
    if await db.get_state(key):
        return
    await alert(
        bot,
        f"⚠️ Некому отдать заявки: на смену сегодня никто не отметился.\n"
        f"Ждут распределения: {pending_count}",
        director=True,
    )
    await db.set_state(key, _today().isoformat())


async def _load_card_facts(crm: CrmClient, rec) -> tuple[str, str]:
    """Строка заявки и прошлый мастер клиента — обоих нет в гриде.

    Читаем карточку один раз: и то и другое нужно при распределении.
    """
    if rec["info_line"]:
        return rec["info_line"], rec["prior_master_name"] or ""
    try:
        card = await crm.fetch_request_card(rec["crm_id"])
    except CrmError:
        log.exception("заявка %s: не прочитал карточку, отправлю данными из грида", rec["crm_id"])
        return "", ""

    await db.save_card_facts(rec["crm_id"], card.info_line, card.previous_master)
    return card.info_line, card.previous_master


async def _order_line(crm: CrmClient, rec) -> str:
    line, _ = await _load_card_facts(crm, rec)
    return line


async def _warn_warranty_waiting(bot: Bot, rec, prior_master: str, info_line: str) -> None:
    """Гарантия ждёт своего мастера — директор должен об этом знать."""
    key = f"warranty_wait:{rec['crm_id']}"
    if await db.get_state(key):
        return

    line = info_line or f"Заказ {rec['crm_id']}"
    await alert(
        bot,
        f"⏳ Гарантия ждёт своего мастера: {prior_master or 'не определён'}\n\n"
        f"{line}\n\n"
        f"Заявка закреплена за ним и будет ждать, пока он не освободится.",
        director=True,
    )
    await db.set_state(key, _today().isoformat())
    log.info("заявка %s: гарантия ждёт мастера %s", rec["crm_id"], prior_master)


async def _escalate_to_director(crm_bot: Bot, crm: CrmClient, rec, slots) -> None:
    """Свободных нет — одно сообщение директору и администратору, один раз на заявку."""
    key = f"escalated:{rec['crm_id']}"
    if await db.get_state(key):
        return

    line = await _order_line(crm, rec) or f"Заказ {rec['crm_id']}"
    busy = ", ".join(s.full_name for s in slots if s.is_busy) or "нет данных"
    text = (
        f"🚨 Некому отдать заявку — все мастера заняты\n\n{line}\n\n"
        f"Заняты: {busy}\n"
        f"Назначьте вручную или дождитесь освобождения."
    )

    chats = await _escalation_chats()
    if not await roles.admin_chat():
        log.warning(
            "ADMIN_CHAT_ID не задан — «нет свободных» по заявке %s уйдёт только директору",
            rec["crm_id"],
        )
    if not chats:
        log.error(
            "заявка %s: некуда слать «нет свободных» — нет DIRECTOR_CHAT_ID и ADMIN_CHAT_ID",
            rec["crm_id"],
        )

    # Владельцу — без кнопки «Передать другому»: назначают администратор и директор.
    owner = roles.owner_chat()
    for chat in chats:
        try:
            await crm_bot.send_message(
                chat,
                text,
                reply_markup=None if chat == owner else messages.move_keyboard(rec["crm_id"]),
            )
        except Exception:
            log.exception("не отправил эскалацию в чат %s", chat)

    await db.set_state(key, _today().isoformat())
    log.info("заявка %s: свободных мастеров нет, эскалация директору и администратору", rec["crm_id"])


async def _bound_master(rec, prior_master: str, slots) -> tuple[object | None, bool]:
    """Мастер, за которым закреплена заявка. Второе значение — ждать ли его.

    Гарантия возвращается тому, кто делал работу, и ждёт его сколько нужно.
    Повтор идёт к нему же, но если он занят или не на смене — в общую очередь.
    """
    if rec["req_type"] not in ("Гарантия", "Повтор") or not prior_master:
        return None, False

    master = await db.master_by_name(prior_master)
    if master is None:
        log.warning("заявка %s: прошлый мастер %r не найден в справочнике",
                    rec["crm_id"], prior_master)
        return None, rec["req_type"] == "Гарантия"

    slot = next((s for s in slots if s.employee_id == master["employee_id"]), None)
    if slot and not slot.is_busy and slot.delivery_chat_id:
        return slot, False

    # Гарантию чужому мастеру не отдаём даже ценой ожидания.
    return None, rec["req_type"] == "Гарантия"


async def assign_request(crm: CrmClient, bot: Bot, rec, slots: list[dispatch_queue.ShiftSlot]) -> bool:
    day = _today().date()
    info_line, prior_master = await _load_card_facts(crm, rec)

    master, must_wait = await _bound_master(rec, prior_master, slots)
    if must_wait:
        await _warn_warranty_waiting(bot, rec, prior_master, info_line)
        return False

    if master is None:
        cursor = await db.get_state(_rotation_key(day))
        exclude = await db.previous_assignees(rec["crm_id"])
        master = dispatch_queue.next_master(slots, int(cursor) if cursor else None, exclude)
    if master is None:
        if _alarm_is_due(rec["opened_at"]):
            await _escalate_to_director(bot, crm, rec, slots)
        else:
            log.info(
                "заявка %s: свободных нет, тревогу отложил до часа перед визитом (%s)",
                rec["crm_id"], rec["opened_at"],
            )
        return False

    rec = dict(rec) | {
        "info_line": info_line,
        "telegram_username": master.username,
        "prior_master_name": prior_master,
    }

    assignment_id = await db.create_assignment(rec["crm_id"], master.employee_id, config.CITY_ID)
    try:
        sent = await bot.send_message(
            master.delivery_chat_id,
            messages.assignment_text(rec, "assigned"),
            reply_markup=messages.master_keyboard(
                rec["crm_id"], "assigned",
                warranty="ask" if rec["req_type"] == "Гарантия" else "",
            ),
        )
    except Exception:
        # Мастер не увидел заявку — назначения не было, пусть цикл попробует снова.
        await db.delete_assignment(assignment_id)
        log.exception("не доставил заявку %s мастеру %s", rec["crm_id"], master.employee_id)
        return False

    await db.save_message_ref(assignment_id, sent.chat.id, sent.message_id)
    await pinning.pin_assignment(bot, master.employee_id, sent.chat.id, sent.message_id)
    await db.set_state(_rotation_key(day), str(master.position))

    try:
        damage = await crm.assign_master(rec["crm_id"], master.employee_id)
        if damage:
            await alert(
                bot,
                f"‼️ Заказ {rec['crm_id']}: запись мастера задела чужие поля в CRM.\n"
                + "\n".join(damage[:5])
                + "\nПроверьте карточку руками.",
                director=True,
            )
    except CrmError:
        log.exception("заявка %s отдана мастеру %s, но в CRM не записана", rec["crm_id"], master.employee_id)
        await alert(bot, f"⚠️ Заявка #{rec['crm_id']}: мастер назначен в боте, но CRM не приняла запись")

    await notify_feed(
        bot,
        f"📤 Заказ {rec['crm_id']} → {master.full_name} ({master.position}-й в очереди)",
        markup=messages.move_keyboard(rec["crm_id"]),
    )
    log.info("заявка %s отдана мастеру %s (позиция %s)", rec["crm_id"], master.full_name, master.position)
    return True


async def _clear_assignment_messages(bot: Bot, row) -> None:
    """Убирает и саму заявку, и напоминание по ней: оба потеряли смысл."""
    removed = await pinning.drop_message(bot, row["chat_id"], row["message_id"])
    await pinning.drop_message(bot, row["chat_id"], row["reminder_message_id"])

    if not removed and row["chat_id"] and row["message_id"]:
        # Удалить не дали — хотя бы гасим кнопки, чтобы не отметился по чужой.
        try:
            await bot.edit_message_text(
                chat_id=row["chat_id"],
                message_id=row["message_id"],
                text=messages.assignment_text(row, "reassigned"),
                reply_markup=None,
            )
        except Exception:
            log.warning("не убрал кнопки у мастера %s", row["employee_id"])


async def check_accept_reminders(bot: Bot) -> None:
    """Первая ступень: повтор заявки мастеру и сигнал директору, заявка пока за ним."""
    for row in await db.awaiting_reminder(config.CITY_ID, config.ACCEPT_REMINDER_MIN):
        minutes_left = config.ACCEPT_TIMEOUT_MIN - config.ACCEPT_REMINDER_MIN
        try:
            sent = await bot.send_message(
                row["delivery_chat_id"],
                messages.reminder_text(row, minutes_left),
                reply_markup=messages.master_keyboard(row["crm_id"], "assigned"),
            )
        except Exception:
            log.exception("не повторил заявку %s мастеру %s", row["crm_id"], row["employee_id"])
            continue

        await db.mark_reminder_sent(row["id"], sent.message_id)
        await alert(
            bot,
            f"🔔 Заявка #{row['crm_id']}: {row['full_name']} не принял её за "
            f"{config.ACCEPT_REMINDER_MIN} мин.\n"
            f"{row['address'] or 'адрес не указан'}\n"
            f"Через {minutes_left} мин заявка уйдёт следующему мастеру.",
            director=True,
        )
        log.info("заявка %s: напоминание мастеру %s", row["crm_id"], row["full_name"])


async def check_accept_timeouts(crm: CrmClient, bot: Bot) -> None:
    """Вторая ступень: заявка уходит следующему по кругу."""
    overdue = await db.overdue_acceptance(config.CITY_ID, config.ACCEPT_TIMEOUT_MIN)
    if not overdue:
        return

    slots = await _slots(_today().date())
    for row in overdue:
        await db.reassign(row["id"])
        await notify_feed(
            bot,
            f"🔄 Заказ {row['crm_id']}: {row['full_name']} не принял за "
            f"{config.ACCEPT_TIMEOUT_MIN} мин, передаю дальше",
        )
        log.info("заявка %s: мастер %s не принял её за %d мин",
                 row["crm_id"], row["full_name"], config.ACCEPT_TIMEOUT_MIN)

        # Мастер заявку не взял — убираем её из чата совсем, чтобы не поехал.
        await pinning.unpin_assignment(bot, row["employee_id"], row["chat_id"])
        await _clear_assignment_messages(bot, row)

        if not await assign_request(crm, bot, row, slots):
            await alert(
                bot,
                f"⚠️ Заявка #{row['crm_id']} никому не ушла: "
                f"{row['full_name']} её не принял, свободных мастеров нет",
                director=True,
            )


def _payout_card_due(status_text: str | None) -> bool:
    """Карточку читаем только у проведённой заявки.

    Блок расчёта в CRM есть у «Готов» и «Готов ОФ». Отказ и отмена его не
    дают, а статус грид обновляет каждый опрос: когда отказ всё-таки проведут,
    карточка прочитается на том же цикле. Иначе закрытый отказ бьёт CRM
    каждую минуту вхолостую.
    """
    return bool(status_text) and status_text in config.DONE_STATUSES


async def send_payouts(crm: CrmClient, bot: Bot) -> None:
    """Заявку закрыл администратор — передаём мастеру расчёт из CRM как есть."""
    for row in await db.assignments_awaiting_payout(config.CITY_ID):
        if not _payout_card_due(row["status_text"]):
            continue
        try:
            payout = (await crm.fetch_request_card(row["crm_id"])).payout
        except CrmError:
            log.exception("заявка %s: не прочитал расчёт", row["crm_id"])
            continue

        if not payout:
            # Заявка закрыта отказом или ещё не проведена — расчёта нет, ждём.
            continue

        try:
            await bot.send_message(
                row["delivery_chat_id"], messages.payout_text(row["crm_id"], payout)
            )
        except Exception:
            log.exception("заявка %s: не отправил расчёт мастеру", row["crm_id"])
            continue

        await db.mark_payout_sent(row["id"])
        # Заявка закрыта — снимаем закрепление, она больше не текущая.
        await pinning.unpin_assignment(bot, row["employee_id"], row["delivery_chat_id"])
        await notify_feed(bot, f"💰 Заказ {row['crm_id']}: расчёт отправлен, {row['full_name']}")
        log.info("заявка %s: расчёт отправлен мастеру %s", row["crm_id"], row["full_name"])


async def check_onsite_timeouts(bot: Bot) -> None:
    """Выехал, но не отметился на месте за ONSITE_TIMEOUT_MIN — зовём админа и директора."""
    for row in await db.overdue_onsite(config.CITY_ID, config.ONSITE_TIMEOUT_MIN, sorted(config.SILENT_STATUSES)):
        await alert(
            bot,
            f"🔔 Заявка #{row['crm_id']}: {row['full_name']} в пути больше "
            f"{config.ONSITE_TIMEOUT_MIN} мин и не отметился на месте.\n"
            f"{row['address'] or 'адрес не указан'}",
            director=True,
        )
        await db.mark_onsite_alert_sent(row["id"])


def _install_signal_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass  # Windows


async def main() -> None:
    logging.basicConfig(
        level=config.LOG_LEVEL,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    reporting.quiet_http_client_logs()
    config.validate(config.POLLER_REQUIRED)

    stop = asyncio.Event()
    _install_signal_handlers(stop)
    failures = 0

    await db.connect()
    crm = CrmClient()
    bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
    reporting.attach(bot, roles.owner_chat(), "опросе CRM")
    log.info(
        "опрос каждые %d c, город %d, окно %s–%s, раздача за %d мин до визита, запись в CRM %s",
        config.POLL_INTERVAL_SEC,
        config.CITY_ID,
        config.ASSIGN_WINDOW_START,
        config.ASSIGN_WINDOW_END,
        config.ASSIGN_LEAD_MIN,
        "выключена (read-only)" if config.CRM_READ_ONLY else "ВКЛЮЧЕНА",
    )

    try:
        while not stop.is_set():
            try:
                if await db.get_state(BASELINE_KEY):
                    await poll_once(crm, bot)
                else:
                    await seed_baseline(crm)
            except (CrmError, httpx.HTTPError) as exc:
                # CRM иногда просто не отвечает вовремя. Одна заминка — не повод
                # будить человека: тревожим, только если она не отвечает подряд.
                failures += 1
                if failures >= config.CRM_FAILURES_BEFORE_ALERT:
                    log.error("CRM не отвечает %d опросов подряд: %s", failures, exc)
                else:
                    log.info("CRM не ответила (%s), повтор через %d c",
                             type(exc).__name__, config.POLL_INTERVAL_SEC)
            except Exception:
                log.exception("непредвиденная ошибка цикла опроса")
            else:
                if failures:
                    log.info("CRM снова отвечает")
                failures = 0

            try:
                await asyncio.wait_for(stop.wait(), timeout=config.POLL_INTERVAL_SEC)
            except asyncio.TimeoutError:
                pass
    finally:
        await crm.close()
        await bot.session.close()
        await db.close()
        log.info("поллер остановлен")


if __name__ == "__main__":
    asyncio.run(main())
