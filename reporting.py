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
        message = self._build_alert(record)
        if message is None:
            return
        self._loop.create_task(self._send(message))

    def _build_alert(self, record: logging.LogRecord) -> str | None:
        # Себя не докладываем: иначе сбой отправки породит новый сбой отправки.
        if record.name == __name__:
            return None

        text = record.getMessage()
        if any(phrase in text for phrase in ROUTINE):
            return None

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


def attach(bot, chat_id: str, source: str) -> None:
    if not chat_id:
        log.warning("чат для сообщений о сбоях не задан — ошибки останутся в журнале")
        return

    handler = TelegramErrorHandler(bot, chat_id, source)
    handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
