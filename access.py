"""Кто что вправе делать — один список на справку и на проверку прав.

Если держать права в обработчиках, а справку писать отдельно, они разойдутся
на первой же новой команде. Поэтому и `/help`, и проверки берут данные отсюда.

Роль определяется по человеку, а не по чату: в рабочем чате мастера сидят и
мастер, и директор, и `/help` у них должен быть разный.
"""

from __future__ import annotations

from dataclasses import dataclass

import db
import roles as who

OWNER = "owner"
ADMIN = "admin"
DIRECTOR = "director"
MASTER = "master"

DECIDERS = frozenset({ADMIN, DIRECTOR})
STAFF = frozenset({OWNER, ADMIN, DIRECTOR})
EVERYONE = frozenset({OWNER, ADMIN, DIRECTOR, MASTER})


@dataclass(frozen=True)
class Entry:
    name: str
    what: str
    roles: frozenset


COMMANDS = (
    Entry("/help", "эта справка", EVERYONE),
    Entry("/today", "мастера на сегодня: очередь, кто свободен, кто занят", DECIDERS),
    Entry("/roles", "кто сейчас владелец, администратор и директор", STAFF),
    Entry("/masters", "список мастеров и привязок", STAFF),
    Entry("/master_link <id> <telegram_id>", "привязать телеграм мастера", STAFF),
    Entry("/master_user <id> <@username>", "прописать username заранее", STAFF),
    Entry("/master_chat <id>", "закрепить этот чат за мастером", DECIDERS),
    Entry("/master_on <id> · /master_off <id>", "включить или выключить мастера", STAFF),
    Entry("/chatid", "показать id этого чата", STAFF),
)

# Имя команды в реестре человеческое, с образцом аргументов. Для проверки прав
# нужен голый глагол — по нему обработчик и спрашивает.
def _verb(name: str) -> str:
    return name.split()[0].lstrip("/")


COMMAND_ROLES = {}
for entry in COMMANDS:
    for part in entry.name.split(" · "):
        COMMAND_ROLES[_verb(part)] = entry.roles


BUTTONS = (
    Entry("🚗 В пути", "выехал, в CRM ставится «В пути»", frozenset({MASTER})),
    Entry("📍 На месте", "приехал, осматриваю", frozenset({MASTER})),
    Entry("🔧 В работе", "приступил, в CRM ставится «В работе»", frozenset({MASTER})),
    Entry("📋 Отчёт", "закрыть заявку: фото, суммы, отзыв", frozenset({MASTER})),
    Entry("📦 В работе СД", "забрал технику на сложную диагностику", frozenset({MASTER})),
    Entry(
        "📞 Номер для дозвона · 🏠 Запрос квартиры",
        "спросить у диспетчеров",
        frozenset({MASTER}),
    ),
    Entry("✅ Провести", "записать отчёт в CRM и провести заявку", DECIDERS),
    Entry("❌ Отклонить", "вернуть мастеру, анкета заполняется заново", DECIDERS),
    Entry("🔄 Повторить проведение", "если CRM отказала, попробовать ещё раз", DECIDERS),
    Entry("🔄 Передать другому", "отдать заявку другому мастеру", DECIDERS),
    Entry(
        "⏸ Занят · ▶️ В работе",
        "убрать мастера из очереди на сегодня или вернуть",
        DECIDERS,
    ),
    Entry("✅ Включить в работу", "вернуть выключенного мастера", frozenset({DIRECTOR})),
)

TITLES = {
    OWNER: "👁 Вы — владелец.",
    ADMIN: "🧾 Вы — администратор.",
    DIRECTOR: "🎩 Вы — директор.",
    MASTER: "🔧 Вы — мастер",
}

INTRO = {
    OWNER: (
        "Решения принимают администратор и директор. Вам идёт лента всех событий,\n"
        "тревоги и сообщения о сбоях."
    ),
    ADMIN: "Вы подтверждаете отчёты мастеров и проводите заявки в CRM.",
    DIRECTOR: "Вы подтверждаете отчёты наравне с администратором и управляете сменой.",
    MASTER: "",
}

TAIL = {
    OWNER: (
        "Само приходит:\n"
        "21:00 — сводка сбоев за сутки, что сломалось и что с этим делать\n"
        "22:00 — итоги дня: заявок, оборот, средний и крупнейший чек"
    ),
    DIRECTOR: "В чате мастера вы можете вести и подтверждать его отчёт вместо него.",
    MASTER: (
        "Утром в общем чате:\n"
        "➕ На смене — встать в очередь на день. Порядок нажатий задаёт очередь.\n"
        "\n"
        "В отчёте:\n"
        "Отвечайте реплаем на вопрос бота — и на текст, и на фото.\n"
        "Несколько снимков отправляйте одним альбомом: хватит одного реплая.\n"
        "Закрыть СД — упомяните бота и напишите номер заявки."
    ),
}

NO_ROLE = "Вы не зарегистрированы. Обратитесь к администратору."


async def whois(user) -> tuple[set[str], object | None]:
    """Роли человека и его строка в справочнике мастеров, если он мастер.

    Ролей может быть несколько: пока администратор не привязан, владелец
    заодно и администратор.
    """
    if user is None:
        return set(), None

    found: set[str] = set()
    person = str(user.id)
    if person == who.director_chat():
        found.add(DIRECTOR)
    if person == await who.admin_chat():
        found.add(ADMIN)
    if person == who.owner_chat():
        found.add(OWNER)

    master = await db.master_by_telegram(user.id)
    if master is not None:
        found.add(MASTER)
    return found, master


async def may(user, command: str) -> bool:
    """Вправе ли человек вызвать команду. Неизвестная команда — запрещена."""
    allowed = COMMAND_ROLES.get(command.lstrip("/"))
    if not allowed:
        return False
    found, _ = await whois(user)
    return bool(found & allowed)


def _lines(entries, found: set[str]) -> list[str]:
    return [f"{e.name} — {e.what}" for e in entries if e.roles & found]


def help_text(found: set[str], master=None) -> str:
    """Справка ровно по тем ролям, которые есть у человека."""
    if not found:
        return NO_ROLE

    # Порядок важен: у владельца, который заодно администратор, заголовок
    # должен быть про владельца — это его главная роль.
    main = next(r for r in (OWNER, DIRECTOR, ADMIN, MASTER) if r in found)
    title = TITLES[main]
    if main == MASTER and master is not None:
        title += f": {master['full_name']}"

    blocks = [title]
    intro = INTRO.get(main)
    if intro:
        blocks.append(intro)

    commands = _lines(COMMANDS, found)
    if commands:
        blocks.append("Команды:\n" + "\n".join(commands))

    buttons = _lines(BUTTONS, found)
    if buttons:
        header = "Кнопки в заявке, по порядку:" if MASTER in found and main == MASTER else "Кнопки:"
        blocks.append(header + "\n" + "\n".join(buttons))

    for role in (OWNER, DIRECTOR, MASTER):
        if role in found and role in TAIL:
            blocks.append(TAIL[role])

    return "\n\n".join(blocks)
