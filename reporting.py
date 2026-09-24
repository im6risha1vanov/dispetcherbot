"""Сбои из журнала уходят администратору в Telegram.

Журнал на сервере никто не читает, поэтому любая неудача — не закрепилось,
не отправилось, CRM не ответила — должна доходить до человека. Но одна и та же
ошибка повторяется каждую минуту опроса, поэтому одинаковые сообщения
схлопываются, а поток ограничен: заваленный чат читают так же, как журнал.

В чат — одна русская строка. Имена исключений и traceback остаются в journalctl.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time

import config
import db

log = logging.getLogger(__name__)

REPEAT_SILENCE_SEC = 900  # одну и ту же ошибку повторяем не чаще, чем раз в 15 минут
MAX_PER_HOUR = 12
# Как CRM ReadTimeout: одна заминка getUpdates — не повод будить человека.
TRANSIENT_FAILURES_BEFORE_ALERT = config.CRM_FAILURES_BEFORE_ALERT
# Между одиночными RST часы; ретраи aiogram укладываются в секунды (backoff ≤ 5 с).
TRANSIENT_STREAK_GAP_SEC = 60

# Штатные события, которые библиотеки пишут как предупреждения. Это не сбои:
# остановка службы при выкладке — обычное дело, а ложные тревоги быстро
# приучают не читать настоящие.
ROUTINE = (
    "Received SIGTERM signal",
    "Received SIGINT signal",
    "Polling stopped",
    "Updates were skipped",
    "Sleep for ",
    "Connection established",
)

TELEGRAM_RST = (
    "TelegramNetworkError",
    "ClientOSError",
    "Errno 104",
    "Connection reset by peer",
    "Request timeout error",
    "ServerDisconnectedError",
    "ClientConnectorError",
)
TELEGRAM_FLOOD = ("TelegramRetryAfter", "Flood control")
TELEGRAM_DOWN = ("TelegramServerError", "Bad Gateway", "Service Unavailable")
CRM_NETWORK = (
    "ReadTimeout",
    "ConnectTimeout",
    "ConnectError",
    "HTTPStatusError",
    "RemoteProtocolError",
)

TELEGRAM_RIGHTS = (
    "TelegramForbiddenError",
    "bot was blocked",
    "bot was kicked",
    "not enough rights",
    "have no rights",
    "chat not found",
    "CHAT_WRITE_FORBIDDEN",
)
CRM_AUTH = ("CrmAuthError", "редирект на вход", "нет csrf")
# Сдвинувшаяся колонка грида: чинится только правкой кода.
CRM_LAYOUT = (
    "структура грида",
    "Структура грида",
    "разметка грида",
    "разметка CRM изменилась",
    "колонок всего",
)
# Запись не прошла: заявку придётся провести руками, сама она не дозапишется.
CRM_WRITE = (
    "не записан",
    "не записано",
    "не приняла",
    "не провела",
    "Проведите вручную",
    "проведите вручную",
    "задела чужие поля",
)
# Только технические признаки: слово «база» встречается в обычных сообщениях
# вроде «фото уже в базе» и утащило бы в этот класс что попало.
DATABASE = (
    "asyncpg",
    "Postgres",
    "pool is closed",
    "InterfaceError",
    "ConnectionDoesNotExistError",
    "connection was closed",
)

CATEGORIES = (
    ("права в чате", TELEGRAM_RIGHTS),
    ("вход в CRM", CRM_AUTH),
    ("разметка CRM", CRM_LAYOUT),
    ("запись в CRM", CRM_WRITE),
    ("база данных", DATABASE),
    ("Telegram просит подождать", TELEGRAM_FLOOD),
    ("Telegram недоступен", TELEGRAM_DOWN),
    ("связь с Telegram", TELEGRAM_RST),
    ("связь с CRM", CRM_NETWORK),
)
OTHER = "прочее"

# Что делать человеку, который не разработчик. Без этого сводка бесполезна.
FIXES = {
    "права в чате": (
        "Бота выгнали из чата или сняли права. Верните его в чат и дайте "
        "право удалять и закреплять сообщения — без них заявки не убираются."
    ),
    "разметка CRM": (
        "CRM переставила или добавила колонку в списке заявок. Бот перестал "
        "их читать и не раздаёт: раздавайте вручную и скажите мне — нужно "
        "переписать разбор под новую разметку."
    ),
    "вход в CRM": (
        "CRM не пустила бота. Проверьте, не сменили ли пароль служебной "
        "учётки, и скажите мне — пропишу новый."
    ),
    "запись в CRM": (
        "Откройте заявку в CRM и проведите её руками: бот записать не смог, "
        "сам он второй раз не попробует."
    ),
    "база данных": (
        "База не ответила. Скажите мне — нужно посмотреть сервер. Пока это "
        "единичные строки, заявки не теряются."
    ),
    "Telegram просит подождать": (
        "Ничего делать не нужно: Telegram попросил сбавить темп, бот подождал "
        "и продолжил сам."
    ),
    "Telegram недоступен": (
        "Это на стороне Telegram, не у нас. Само проходит; если к утру не "
        "прошло — скажите мне."
    ),
    "связь с Telegram": (
        "Ничего делать не нужно: бот переподключается сам. Если таких строк "
        "больше сотни за сутки — скажите мне, посмотрю сеть сервера."
    ),
    "связь с CRM": (
        "Проверьте, открывается ли bt-lead-centre.ru в браузере. Открывается — "
        "ничего не делайте, бот повторит сам. Нет — CRM лежит, ждём её."
    ),
    OTHER: (
        "Причина непонятная. Перешлите мне эту строку — разберу по журналу "
        "сервера."
    ),
}


def classify(text: str) -> str:
    """Класс сбоя: по нему в вечерней сводке подбирается совет.

    Порядок важен. Отказ в правах приходит тем же исключением, что и обрыв
    связи, а «не записано в CRM» — без имени исключения вовсе, одной русской
    строкой. Поэтому сначала проверяем частное, потом общее.
    """
    for name, markers in CATEGORIES:
        if any(marker in text for marker in markers):
            return name
    if "Failed to fetch updates" in text:
        return "связь с Telegram"
    return OTHER


_CYRILLIC = re.compile(r"[А-Яа-яЁё]")
_EXCEPTION_NAME = re.compile(
    r"\b(?:"
    r"Telegram(?:NetworkError|RetryAfter|ServerError|APIError|BadRequest|Forbidden|NotFound|ConflictError|UnauthorizedError)|"
    r"ClientOSError|ClientConnectorError|ServerDisconnectedError|"
    r"ReadTimeout|ConnectTimeout|ConnectError|HTTPStatusError|TimeoutException|"
    r"RemoteProtocolError|ConnectionError|HTTPError|OSError|"
    r"httpx(?:\.\w+)?|aiogram(?:\.\w+)+"
    r")\b"
)
_TECH_NOISE = re.compile(
    r"HTTP Client says\s*-?\s*"
    r"|Telegram server says\s*-?\s*"
    r"|\[Errno \d+\]"
    r"|Connection reset by peer"
    r"|Request timeout error"
    r"|timed out"
    r"|Failed to fetch updates\s*-?\s*"
)


def humanize_for_chat(text: str) -> str:
    """Одна русская строка для чата. Технические детали отбрасываются."""
    line = text.strip().split("\n")[0]
    if any(marker in line for marker in TELEGRAM_RST):
        return "Сбой связи с Telegram, бот сам переподключится"
    if any(marker in line for marker in TELEGRAM_FLOOD):
        return "Telegram просит подождать, бот продолжит сам"
    if any(marker in line for marker in TELEGRAM_DOWN):
        return "Telegram временно недоступен, бот сам переподключится"
    if "Failed to fetch updates" in line:
        return "Сбой связи с Telegram, бот сам переподключится"
    if any(marker in line for marker in CRM_NETWORK):
        head = _russian_head(line)
        return head or "Сбой связи с CRM"

    cleaned = _russian_head(line)
    if cleaned:
        return cleaned
    return "Непредвиденный сбой, подробности в журнале"


def is_transient_telegram(text: str) -> bool:
    """getUpdates RST/таймаут/обрыв: aiogram сам ретраит, как CRM ReadTimeout."""
    return any(marker in text for marker in TELEGRAM_RST) or "Failed to fetch updates" in text


def _russian_head(line: str) -> str:
    cleaned = _EXCEPTION_NAME.sub("", line)
    cleaned = _TECH_NOISE.sub("", cleaned)
    cleaned = re.sub(r"\([^)]*\)", "", cleaned)
    cleaned = re.sub(r"['\"]{2,}", "", cleaned)
    cleaned = re.sub(r"[:\-/,.]+\s*$", "", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" :,-")
    if not cleaned or not _CYRILLIC.search(cleaned):
        return ""
    return cleaned[:400]


class TelegramErrorHandler(logging.Handler):
    def __init__(self, bot, chat_id: str, source: str):
        super().__init__(level=logging.WARNING)
        self._bot = bot
        self._chat_id = chat_id
        self._source = source
        self._seen: dict[str, float] = {}
        self._sent_at: list[float] = []
        self._transient_count = 0
        self._transient_at = 0.0
        self._loop = asyncio.get_event_loop()

    def emit(self, record: logging.LogRecord) -> None:
        if self._routine(record):
            return

        # В чат уходит не всё: повторы схлопываются, поток ограничен. В сводку
        # пишем каждый случай — иначе вечером не видно, что сбоило весь день.
        text = record.getMessage()
        self._loop.create_task(
            self._remember(
                classify(text),
                humanize_for_chat(text),
                "error" if record.levelno >= logging.ERROR else "warning",
            )
        )

        message = self._build_alert(record)
        if message is None:
            return
        self._loop.create_task(self._send(message))

    @staticmethod
    def _routine(record: logging.LogRecord) -> bool:
        """Себя не докладываем, штатные предупреждения библиотек — тоже."""
        if record.name == __name__:
            return True
        return any(phrase in record.getMessage() for phrase in ROUTINE)

    async def _remember(self, category: str, summary: str, level: str) -> None:
        try:
            await db.record_failure(self._source, category, summary, level)
        except Exception:
            # debug, а не warning: иначе жалоба на базу пойдёт через тот же
            # обработчик и сама попробует записаться в базу.
            log.debug("сбой не записан для сводки", exc_info=True)

    def _build_alert(self, record: logging.LogRecord) -> str | None:
        if self._routine(record):
            return None

        text = record.getMessage()

        now = time.monotonic()
        if is_transient_telegram(text):
            if now - self._transient_at > TRANSIENT_STREAK_GAP_SEC:
                self._transient_count = 0
            self._transient_count += 1
            self._transient_at = now
            if self._transient_count < TRANSIENT_FAILURES_BEFORE_ALERT:
                return None
        else:
            self._transient_count = 0

        summary = humanize_for_chat(text)
        key = f"{record.name}:{summary}"
        if now - self._seen.get(key, -REPEAT_SILENCE_SEC) < REPEAT_SILENCE_SEC:
            return None

        self._sent_at = [t for t in self._sent_at if now - t < 3600]
        if len(self._sent_at) >= MAX_PER_HOUR:
            return None

        self._seen[key] = now
        self._sent_at.append(now)

        icon = "🛑" if record.levelno >= logging.ERROR else "⚠️"
        return f"{icon} Сбой в {self._source}\n\n{summary}"

    async def _send(self, text: str) -> None:
        try:
            await self._bot.send_message(self._chat_id, text)
        except Exception:
            pass  # молча: иначе получим бесконечную цепочку жалоб на жалобы


def quiet_http_client_logs() -> None:
    """httpx на INFO пишет каждый GET. В журнале службы нужны ошибки, не трасса.

    DEBUG оставляем как есть: руками разобрать сессию CRM иначе нечем.
    """
    if config.LOG_LEVEL.upper() == "DEBUG":
        return
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


def attach(bot, chat_id: str, source: str) -> None:
    if not chat_id:
        log.warning("чат для сообщений о сбоях не задан — ошибки останутся в журнале")
        return

    handler = TelegramErrorHandler(bot, chat_id, source)
    handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
