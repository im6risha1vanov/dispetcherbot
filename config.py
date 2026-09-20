import os
from datetime import time
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


class ConfigError(RuntimeError):
    pass


def _get_time(name: str, default: str) -> time:
    raw = os.getenv(name, "").strip() or default
    try:
        hours, minutes = raw.split(":")
        return time(int(hours), int(minutes))
    except ValueError as exc:
        raise ConfigError(f"{name}={raw!r}: ожидается время в формате ЧЧ:ММ") from exc


def _get(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _get_int(name: str, default: int) -> int:
    raw = _get(name)
    return int(raw) if raw else default


def _get_bool(name: str, default: bool) -> bool:
    raw = _get(name).lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


CRM_BASE_URL = _get("CRM_BASE_URL", "https://bt-lead-centre.ru")
CRM_USERNAME = _get("CRM_USERNAME")
CRM_PASSWORD = _get("CRM_PASSWORD")
CRM_LOGIN_FIELD_USERNAME = _get("CRM_LOGIN_FIELD_USERNAME")
CRM_LOGIN_FIELD_PASSWORD = _get("CRM_LOGIN_FIELD_PASSWORD")
CRM_READ_ONLY = _get_bool("CRM_READ_ONLY", True)
# Предохранитель на первое включение записи: пока список не пуст, бот пишет
# только в перечисленные заявки, остальные обходит как при read-only.
CRM_WRITE_ONLY_FOR = frozenset(
    part.strip() for part in _get("CRM_WRITE_ONLY_FOR").split(",") if part.strip()
)
CRM_TIMEOUT_SEC = _get_int("CRM_TIMEOUT_SEC", 30)
# CRM отвечает не всегда мгновенно. Тревожим человека, только если она молчит
# несколько опросов подряд, а не при каждой заминке.
CRM_FAILURES_BEFORE_ALERT = _get_int("CRM_FAILURES_BEFORE_ALERT", 5)
# Закрытие заявки пишет суммы и проводит работу — включается отдельно от
# остальной записи, после проверки на тестовых заявках.
CRM_ALLOW_CLOSING = _get_bool("CRM_ALLOW_CLOSING", False)

CITY_ID = _get_int("CITY_ID", 206)
POLL_INTERVAL_SEC = _get_int("POLL_INTERVAL_SEC", 60)

TELEGRAM_BOT_TOKEN = _get("TELEGRAM_BOT_TOKEN")
TELEGRAM_TEST_CHAT_ID = _get("TELEGRAM_TEST_CHAT_ID")
MASTERS_CHAT_ID = _get("MASTERS_CHAT_ID")
DISPATCHERS_CHAT_ID = _get("DISPATCHERS_CHAT_ID")
ADMIN_CHAT_ID = _get("ADMIN_CHAT_ID")
DIRECTOR_CHAT_ID = _get("DIRECTOR_CHAT_ID")

DATABASE_URL = _get("DATABASE_URL")

TIMEZONE = ZoneInfo(_get("TIMEZONE", "Europe/Moscow"))
LOG_LEVEL = _get("LOG_LEVEL", "INFO")

def _get_set(name: str, default: str) -> frozenset[str]:
    raw = _get(name) or default
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


# Вопреки контракту грид отдаёт и закрытые заявки (проверено 16.09.2026: среди 35
# строк были «Готов», «Отказ», «Отмена Филиала»). Поэтому активность заявки
# определяется статусом, а не фактом присутствия в гриде. Список
# незакрытых статусов безопаснее перечня закрытых: незнакомый статус
# считается закрытым, и бот по нему ничего не делает.
ACTIVE_STATUSES = _get_set("ACTIVE_STATUSES", "Ожидает,В пути,В работе,В работе СД")
ASSIGNABLE_STATUSES = _get_set("ASSIGNABLE_STATUSES", "Ожидает")

# Техника уехала к мастеру на сложную диагностику: заявка живёт днями, торопить
# некого. Бот по таким молчит — ни таймеров, ни ленты, — но заявку не закрывает.
SILENT_STATUSES = _get_set("SILENT_STATUSES", "В работе СД")

# 0 — раздавать в том же опросе, что и уведомление директору, если есть свободный.
# >0 — придержать до N минут перед визитом (opened_at из грида).
ASSIGN_LEAD_MIN = _get_int("ASSIGN_LEAD_MIN", 0)
# Тревога «нет свободных» — за столько минут до визита, не в момент появления заявки.
ESCALATE_LEAD_MIN = _get_int("ESCALATE_LEAD_MIN", 60)

ASSIGN_WINDOW_START = _get_time("ASSIGN_WINDOW_START", "09:00")
ASSIGN_WINDOW_END = _get_time("ASSIGN_WINDOW_END", "22:00")
# Принятие заявки в две ступени: сначала напоминание мастеру и сигнал директору,
# и только потом заявка уходит следующему по кругу.
ACCEPT_REMINDER_MIN = _get_int("ACCEPT_REMINDER_MIN", 5)
ACCEPT_TIMEOUT_MIN = _get_int("ACCEPT_TIMEOUT_MIN", 15)
ONSITE_TIMEOUT_MIN = _get_int("ONSITE_TIMEOUT_MIN", 55)
SHIFT_CHECK_TIME = _get_time("SHIFT_CHECK_TIME", "09:00")
# Через сколько минут после сбора сверять список: мастерам нужно время ответить.
SHIFT_ROLL_CALL_MIN = _get_int("SHIFT_ROLL_CALL_MIN", 30)

# Итоги дня директору. Закрытыми считаем заявки в этих статусах.
DIGEST_TIME = _get_time("DIGEST_TIME", "22:00")
DONE_STATUSES = _get_set("DONE_STATUSES", "Готов,Готов ОФ")

_HINTS = {
    "CRM_LOGIN_FIELD_USERNAME": "имя поля логина на /admin/login — снять в инкогнито, п. 9.1 контракта",
    "CRM_LOGIN_FIELD_PASSWORD": "имя поля пароля на /admin/login — снять в инкогнито, п. 9.1 контракта",
    "TELEGRAM_TEST_CHAT_ID": "id тестового чата — узнать командой /chatid у запущенного bot.py",
    "TELEGRAM_BOT_TOKEN": "токен от BotFather",
    "CRM_USERNAME": "логин сервисной учётки CRM",
    "CRM_PASSWORD": "пароль сервисной учётки CRM",
    "DATABASE_URL": "строка подключения к PostgreSQL",
    "MASTERS_CHAT_ID": "id общего чата мастеров — туда уходит утренний сбор смены",
    "DISPATCHERS_CHAT_ID": "id чата диспетчеров — туда бот запрашивает номер клиента",
    "ADMIN_CHAT_ID": "id чата администратора — туда идут алерты",
    "DIRECTOR_CHAT_ID": "id чата директора — туда дублируются алерты о просрочке",
}

POLLER_REQUIRED = [
    "CRM_USERNAME",
    "CRM_PASSWORD",
    "CRM_LOGIN_FIELD_USERNAME",
    "CRM_LOGIN_FIELD_PASSWORD",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_TEST_CHAT_ID",
    "DATABASE_URL",
    "MASTERS_CHAT_ID",
    "ADMIN_CHAT_ID",
    "DIRECTOR_CHAT_ID",
]
BOT_REQUIRED = ["TELEGRAM_BOT_TOKEN", "DATABASE_URL", "ADMIN_CHAT_ID"]


def validate(required: list[str]) -> None:
    missing = [name for name in required if not globals().get(name)]
    if missing:
        details = "\n".join(f"  {n} — {_HINTS.get(n, 'не заполнен')}" for n in missing)
        raise ConfigError("Не заполнены обязательные параметры в .env:\n" + details)
