"""Тексты и клавиатуры, общие для bot.py и poller.py."""

from collections.abc import Mapping
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import config

CB_ENROUTE = "enroute"
CB_ONSITE = "onsite"
CB_PHONE = "phone"
CB_APARTMENT = "apartment"
CB_INWORK = "inwork"
CB_CLOSE = "close"
CB_SD = "sd"
CB_SD_CLOSE = "sdclose"
CB_REMOTE = "remote"
CB_REMOTE_DONE = "remotedone"
CB_CLOSE_ANSWER = "cans"
CB_CLOSE_SKIP = "cskip"
CB_CLOSE_OK = "cok"
CB_CLOSE_NO = "cno"
CB_CLOSE_RETRY = "cretry"
CB_DAY_PAUSE = "daypause"
CB_DAY_RESUME = "dayresume"
CB_DAY_ENABLE = "dayenable"
CB_DAY_REFRESH = "dayrefresh"
CB_MOVE = "move"
CB_MOVE_TO = "moveto"
CB_MOVE_CANCEL = "movecancel"
CB_SHIFT_ON = "shift:on"


def _when(rec: Mapping[str, Any]) -> str:
    if not rec["opened_at"]:
        return "время не указано"
    return rec["opened_at"].astimezone(config.TIMEZONE).strftime("%d.%m.%y %H:%M")


def request_text(rec: Mapping[str, Any], *, header: str = "📋 Заявка") -> str:
    title = f"{header} #{rec['crm_id']}"
    if rec.get("req_type"):
        title += f" ({rec['req_type']})"

    status = rec.get("status_text") or "—"
    if rec.get("is_recall"):
        status += " · отзывная"

    lines = [title, f"{_when(rec)} · {status}"]
    if rec.get("customer_name"):
        lines.append(rec["customer_name"])
    if rec.get("address"):
        lines.append(rec["address"])
    return "\n".join(lines)


def master_keyboard(
    crm_id: int, state: str, *, warranty: str = ""
) -> InlineKeyboardMarkup | None:
    """Шаг мастера плюс запрос номера. Кнопки отказа у мастера нет.

    warranty: "" — обычная заявка, "ask" — гарантия до запроса номера,
    "done" — номер уже получен, осталось подтвердить решение.
    """
    phone = InlineKeyboardButton(text="📞 Номер для дозвона", callback_data=f"{CB_PHONE}:{crm_id}")
    apartment = InlineKeyboardButton(
        text="🏠 Запрос квартиры", callback_data=f"{CB_APARTMENT}:{crm_id}"
    )

    if state == "assigned":
        rows = [[InlineKeyboardButton(text="🚗 В пути", callback_data=f"{CB_ENROUTE}:{crm_id}")]]
        # Гарантию иногда решают звонком — тогда ехать незачем.
        if warranty == "ask":
            rows.append([InlineKeyboardButton(
                text="📞 Дистанционное решение", callback_data=f"{CB_REMOTE}:{crm_id}"
            )])
        elif warranty == "done":
            rows.append([InlineKeyboardButton(
                text="✅ Решено дистанционно", callback_data=f"{CB_REMOTE_DONE}:{crm_id}"
            )])
    elif state == "enroute":
        rows = [
            [InlineKeyboardButton(text="📍 На месте", callback_data=f"{CB_ONSITE}:{crm_id}")],
            [phone],
        ]
    elif state == "onsite":
        # Приехал и осматривает: квартиру спрашивают уже стоя у дома, а статус в
        # CRM сменится только когда мастер реально приступит.
        rows = [
            [InlineKeyboardButton(text="🔧 В работе", callback_data=f"{CB_INWORK}:{crm_id}")],
            [apartment],
            [phone],
        ]
    elif state == "inwork":
        # Приступив к работе, мастер либо закрывает заявку, либо забирает
        # технику на сложную диагностику.
        # Номер и квартира здесь уже не нужны: мастер на объекте и работает.
        rows = [
            [InlineKeyboardButton(text="📋 Отчёт", callback_data=f"{CB_CLOSE}:{crm_id}")],
            [InlineKeyboardButton(text="📦 В работе СД", callback_data=f"{CB_SD}:{crm_id}")],
        ]
    else:
        return None
    return InlineKeyboardMarkup(inline_keyboard=rows)


KIND_PHONE = "phone"
KIND_APARTMENT = "apartment"
KIND_REMOTE = "remote"

_KINDS = {
    KIND_PHONE: ("мс на месте номер для стыка", "📞", "Номер по заявке"),
    KIND_APARTMENT: ("мс на месте кВ", "🏠", "Квартира по заявке"),
    KIND_REMOTE: ("номер для дист решения", "📞", "Номер по заявке"),
}


def info_request_text(kind: str, rec: Mapping[str, Any], master_name: str) -> str:
    """Короткий скрипт в том виде, как его привыкли читать диспетчеры."""
    script, _, _ = _KINDS[kind]
    return f"{rec['crm_id']} {script}"


def info_answer_text(kind: str, crm_id: int, answer: str) -> str:
    _, icon, title = _KINDS[kind]
    return f"{icon} {title} #{crm_id}:\n{answer}"


def _order_line(rec: Mapping[str, Any]) -> str:
    """Строка заявки из CRM. Пока карточка не прочитана — собираем из грида."""
    return rec.get("info_line") or request_text(rec, header="Заказ")


def mention(rec: Mapping[str, Any]) -> str:
    """Тег мастера: без него сообщение в общем чате легко пролистать."""
    username = rec.get("telegram_username")
    return f"@{username}\n" if username else ""


def reminder_text(rec: Mapping[str, Any], minutes_left: int) -> str:
    return mention(rec) + "⏰ Напоминание\n\n" + _order_line(rec)


def _kind_note(rec: Mapping[str, Any]) -> str:
    """Гарантию мастер должен видеть сразу: она оплачивается иначе."""
    req_type = rec.get("req_type")
    prior = rec.get("prior_master_name")
    if req_type == "Гарантия":
        note = "⚠️ ГАРАНТИЯ"
        return f"{note} (первым был: {prior})\n" if prior else f"{note}\n"
    if req_type == "Повтор" and prior:
        return f"🔁 Повтор (первым был: {prior})\n"
    return ""


def assignment_text(rec: Mapping[str, Any], state: str) -> str:
    hint = {
        "assigned": f"\n\nВремя ожидания принятия — {config.ACCEPT_REMINDER_MIN} минут",
        "enroute": "\n\nВ пути. Нажмите «На месте», когда приедете.",
        "onsite": "\n\nНа месте. Нажмите «В работе», когда приступите.",
        "inwork": "\n\nВ работе.",
        "reassigned": "\n\nЗаявка передана другому мастеру.",
    }[state]
    prefix = (mention(rec) + _kind_note(rec)) if state == "assigned" else _kind_note(rec)
    return prefix + _order_line(rec) + hint


def move_keyboard(crm_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="🔄 Передать другому", callback_data=f"{CB_MOVE}:{crm_id}")
        ]]
    )


def master_choice_keyboard(crm_id: int, masters: list) -> InlineKeyboardMarkup:
    """Выбор мастера вручную: на смене, с чатом, кроме текущего исполнителя."""
    rows = [
        [InlineKeyboardButton(
            text=f"{m['position']}. {m['full_name']}" + (" · занят" if m["is_busy"] else ""),
            callback_data=f"{CB_MOVE_TO}:{crm_id}:{m['employee_id']}",
        )]
        for m in masters
    ]
    rows.append([InlineKeyboardButton(text="✖️ Отмена", callback_data=f"{CB_MOVE_CANCEL}:{crm_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def closing_keyboard(step) -> InlineKeyboardMarkup | None:
    """Кнопки под вопросом: выбор вариантов или пропуск необязательного фото."""
    if step.kind == "choice":
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=title, callback_data=f"{CB_CLOSE_ANSWER}:{step.key}:{value}")
            for value, title in step.choices
        ]])
    if step.kind == "photo" and step.multi:
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Готово", callback_data=f"{CB_CLOSE_SKIP}:{step.key}:-")
        ]])
    if step.kind == "photo" and not step.required:
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="Пропустить", callback_data=f"{CB_CLOSE_SKIP}:{step.key}:-"
            )
        ]])
    return None


def closing_question(step, crm_id: int, accepted: int = 0) -> str:
    """Вопрос мастеру. На фото-шаге дописываем, сколько снимков уже приняли.

    Счётчик живёт в самом вопросе: отдельные «Принято» на каждое фото
    засоряют чат мастера, а он читает его с телефона на объекте.
    """
    text = f"Закрытие заказа {crm_id}\n\n{step.question}"
    if step.kind in ("amount", "text"):
        text += "\n\n↩️ Ответьте на это сообщение."
    if step.kind == "photo" and accepted:
        text += f"\n\n📷 Принято снимков: {accepted}"
    return text


def admin_decision_keyboard(closure_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Провести", callback_data=f"{CB_CLOSE_OK}:{closure_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"{CB_CLOSE_NO}:{closure_id}"),
    ]])


def retry_conduct_keyboard(closure_id: int) -> InlineKeyboardMarkup:
    """CRM не приняла закрытие — отчёт цел, нужна только повторная попытка."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="🔄 Повторить проведение", callback_data=f"{CB_CLOSE_RETRY}:{closure_id}"
        )
    ]])


def _roster_state(row) -> str:
    if row["paused_at"]:
        return "занят до конца дня"
    if row["is_busy"]:
        return f"на заявке {row['busy_with']}" if row["busy_with"] else "на заявке"
    return "свободен"


def roster_text(day, rows) -> str:
    """Панель дня: кто в очереди, кто выпал и почему."""
    queue, idle, off = [], [], []
    for row in rows:
        if not row["is_active"]:
            off.append(f"· {row['full_name']}")
        elif row["position"] is None:
            idle.append(f"· {row['full_name']}")
        else:
            queue.append(f"{row['position']}. {row['full_name']} — {_roster_state(row)}")

    blocks = [f"👥 Мастера на {day.strftime('%d.%m')}"]
    blocks.append("В очереди:\n" + "\n".join(queue) if queue else "В очереди пусто.")
    if idle:
        blocks.append("Не отмечались сегодня:\n" + "\n".join(idle))
    if off:
        blocks.append("Выключены:\n" + "\n".join(off))
    return "\n\n".join(blocks)


def roster_keyboard(rows, *, is_director: bool) -> InlineKeyboardMarkup:
    """По кнопке на мастера. Подпись описывает действие, а не состояние."""
    buttons = []
    for row in rows:
        name, who = row["full_name"], row["employee_id"]
        if not row["is_active"]:
            if is_director:
                buttons.append([InlineKeyboardButton(
                    text=f"✅ Включить: {name}",
                    callback_data=f"{CB_DAY_ENABLE}:{who}",
                )])
        elif row["paused_at"] or row["position"] is None:
            buttons.append([InlineKeyboardButton(
                text=f"▶️ В работе: {name}",
                callback_data=f"{CB_DAY_RESUME}:{who}",
            )])
        else:
            buttons.append([InlineKeyboardButton(
                text=f"⏸ Занят: {name}",
                callback_data=f"{CB_DAY_PAUSE}:{who}",
            )])
    buttons.append([InlineKeyboardButton(
        text="🔄 Обновить", callback_data=f"{CB_DAY_REFRESH}:0"
    )])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def payout_text(crm_id: int, payout: str) -> str:
    return f"💰 Заказ {crm_id} закрыт\n\n{payout}"


def shift_prompt_text(usernames: list[str] | None = None) -> str:
    """Теги нужны, чтобы сбор смены не потерялся в общем чате."""
    text = "Доброе утро. Кто на смене - нажмите +"
    if usernames:
        text += "\n\n" + " ".join(f"@{name}" for name in usernames)
    return text


def shift_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="➕ На смене", callback_data=CB_SHIFT_ON)]]
    )


def sd_close_offer(rec: Mapping[str, Any]) -> str:
    return "Нашёл заявку:\n\n" + _order_line(rec)


def sd_close_keyboard(crm_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Закрыть СД", callback_data=f"{CB_SD_CLOSE}:{crm_id}")
    ]])


def digest_text(day, totals, leftovers=()) -> str:
    """Итоги дня: цифры из CRM, без домыслов, плюс что осталось незакрытым."""
    head = f"📊 Итоги {day.strftime('%d.%m.%Y')}"

    if totals["orders"]:
        biggest = (
            f"{_money(totals['biggest'])} — {totals['top_master'] or 'мастер не указан'}"
            f" (заказ {totals['top_order']})"
        )
        body = (
            f"Закрыто заявок: {totals['orders']}\n"
            f"Оборот: {_money(totals['turnover'])}\n"
            f"Средний чек: {_money(totals['average'])}\n"
            f"Самый крупный: {biggest}"
        )
    else:
        body = "Закрытых заявок нет."

    return "\n\n".join([head, body] + _leftover_blocks(leftovers))


def _leftover_blocks(leftovers) -> list[str]:
    """Что к вечеру осталось в работе: незакрытое отдельно, диагностика отдельно."""
    on_sd = [r for r in leftovers if r["on_sd"]]
    hanging = [r for r in leftovers if not r["on_sd"]]

    blocks = []
    if hanging:
        lines = "\n".join(
            f"· {r['crm_id']} — {r['master_name'] or 'мастер не назначен'} ({r['status_text']})"
            for r in hanging
        )
        blocks.append(f"⚠️ Не закрыто: {len(hanging)}\n{lines}")
    else:
        blocks.append("✅ Все заявки дня закрыты")

    if on_sd:
        lines = "\n".join(
            f"· {r['crm_id']} — {r['master_name'] or 'мастер не назначен'}" for r in on_sd
        )
        blocks.append(f"📦 Забрали на сложную диагностику: {len(on_sd)}\n{lines}")
    return blocks


# Совет на случай, если класс сбоя добавили, а что с ним делать — забыли.
# Пустая строка в сводке хуже честного «не знаю».
UNKNOWN_FIX = "Причина непонятная. Перешлите мне эту строку — разберу по журналу."


def failure_report_text(now, since, rows, fixes) -> str:
    """Вечерняя сводка сбоев владельцу: что было и что с этим делать.

    Владелец не разработчик, поэтому к каждой причине идёт совет, а не имя
    исключения. Совсем тихий день — тоже новость: значит всё шло штатно.
    """
    period = f"{since.strftime('%d.%m %H:%M')} — {now.strftime('%d.%m %H:%M')}"
    head = f"🧾 Сводка сбоев за сутки ({period})"

    if not rows:
        return f"{head}\n\n✅ Всё штатно: сбоев не было."

    total = sum(int(r["times"]) for r in rows)
    blocks = [head, f"Всего {total}, причин {len(rows)}."]
    for number, row in enumerate(rows, 1):
        times = int(row["times"])
        mark = "🛑" if row["had_error"] else "⚠️"
        when = row["last_at"].strftime("%H:%M")
        count = (
            f"один раз, в {when}"
            if times == 1
            else f"{times} {_times_word(times)}, последний в {when}"
        )
        blocks.append(
            f"{number}. {mark} {row['summary']}\n"
            f"   {count}, в {row['source']}\n"
            f"   → {fixes.get(row['category'], UNKNOWN_FIX)}"
        )
    return "\n\n".join(blocks)


def _times_word(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return "раз"
    return "раза" if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14 else "раз"


def _money(value) -> str:
    return f"{int(value):,}".replace(",", " ") + " р."
