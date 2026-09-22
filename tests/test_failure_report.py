"""Вечерняя сводка сбоев владельцу: что ломалось за сутки и что с этим делать.

В чат сбой уходит сразу, но повторы схлопываются, а поток ограничен дюжиной
в час. Поэтому в 21:00 нужна честная сводка — включая то, о чём промолчали.
"""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import config
import messages
import poller
import reporting
import roles


def _row(category, summary, times, hour, *, source="боте", error=False):
    return {
        "category": category,
        "summary": summary,
        "source": source,
        "times": times,
        "last_at": datetime(2026, 9, 23, hour, 5),
        "had_error": error,
    }


def test_у_каждого_класса_сбоя_есть_совет():
    """Класс без совета оставил бы владельца с пустой строкой вместо подсказки."""
    for name, _markers in reporting.CATEGORIES:
        assert name in reporting.FIXES, name
    assert reporting.OTHER in reporting.FIXES


def test_классы_сбоев_узнаются_по_тексту():
    cases = {
        "TelegramForbiddenError: bot was kicked from the group chat": "права в чате",
        "заявка 784223: закрытие не записано в CRM": "запись в CRM",
        "CRM не отвечает 5 опросов подряд: ReadTimeout": "связь с CRM",
        "CrmAuthError: запись вернула редирект на вход": "вход в CRM",
        "TelegramNetworkError: Connection reset by peer": "связь с Telegram",
        "pool is closed": "база данных",
        "Failed to fetch updates - TelegramNetworkError": "связь с Telegram",
        "непредвиденная ошибка цикла опроса": reporting.OTHER,
    }
    for text, expected in cases.items():
        assert reporting.classify(text) == expected, text


def test_тихий_день_тоже_новость():
    now = datetime(2026, 9, 23, 21, 0)
    text = messages.failure_report_text(now, now - timedelta(days=1), [], reporting.FIXES)

    assert "сбоев не было" in text
    assert "21:00" in text


def test_сводка_показывает_счёт_и_совет():
    now = datetime(2026, 9, 23, 21, 0)
    rows = [
        _row("запись в CRM", "заявка 784223: закрытие не записано в CRM", 1, 12, error=True),
        _row("связь с Telegram", "Сбой связи с Telegram", 11, 18),
    ]
    text = messages.failure_report_text(now, now - timedelta(days=1), rows, reporting.FIXES)

    assert "Всего 12, причин 2." in text
    assert "один раз, в 12:05" in text
    assert "11 раз, последний в 18:05" in text
    assert "проведите её руками" in text
    assert "🛑" in text and "⚠️" in text


def test_незнакомый_класс_не_оставляет_пустоты():
    """Совет забыли добавить — сводка всё равно должна что-то сказать."""
    now = datetime(2026, 9, 23, 21, 0)
    text = messages.failure_report_text(
        now, now - timedelta(days=1), [_row("новый класс", "что-то сломалось", 1, 9)], {}
    )

    assert messages.UNKNOWN_FIX in text


def _clock(monkeypatch, moment):
    monkeypatch.setattr(poller, "_today", lambda: moment)
    monkeypatch.setattr(poller.config, "FAILURE_REPORT_TIME", config.time(21, 0))
    monkeypatch.setattr(poller.config, "CITY_ID", 206)
    monkeypatch.setattr(roles.config, "OWNER_CHAT_ID", "own")


def _run(monkeypatch, moment, state, rows):
    _clock(monkeypatch, moment)
    tg = AsyncMock()
    sent = []

    async def get_state(key):
        return state.get(key)

    async def set_state(key, value):
        state[key] = value

    async def between(start, end):
        sent.append((start, end))
        return rows

    with patch.object(poller.db, "get_state", get_state), \
         patch.object(poller.db, "set_state", set_state), \
         patch.object(poller.db, "failures_between", between), \
         patch.object(poller.db, "forget_old_failures", AsyncMock()):
        asyncio.run(poller.maybe_send_failure_report(tg))
    return tg, sent


def test_до_21_00_сводка_молчит(monkeypatch):
    tg, asked = _run(monkeypatch, datetime(2026, 9, 23, 20, 59), {}, [])

    tg.send_message.assert_not_awaited()
    assert asked == []


def test_в_21_00_сводка_уходит_владельцу_и_только_раз(monkeypatch):
    state: dict = {}
    moment = datetime(2026, 9, 23, 21, 0)

    tg, _ = _run(monkeypatch, moment, state, [])
    assert tg.send_message.await_args.args[0] == "own"

    # Второй опрос в ту же минуту не должен слать сводку повторно.
    tg2, _ = _run(monkeypatch, moment, state, [])
    tg2.send_message.assert_not_awaited()


def test_период_считается_от_прошлой_сводки(monkeypatch):
    """Иначе сбои между 21:00 и полуночью не попали бы ни в одну сводку."""
    yesterday = datetime(2026, 9, 22, 21, 0)
    state = {poller.FAILURE_REPORT_SINCE: yesterday.isoformat()}
    moment = datetime(2026, 9, 23, 21, 0)

    _, asked = _run(monkeypatch, moment, state, [])

    assert asked == [(yesterday, moment)]
    assert state[poller.FAILURE_REPORT_SINCE] == moment.isoformat()


def test_первая_сводка_смотрит_на_сутки_назад(monkeypatch):
    moment = datetime(2026, 9, 23, 21, 0)

    _, asked = _run(monkeypatch, moment, {}, [])

    assert asked == [(moment - timedelta(days=1), moment)]


def test_сбой_попадает_в_сводку_даже_если_в_чат_не_ушёл():
    """Повтор в чат не уходит, но в сводке он обязан быть."""
    import logging

    handler = reporting.TelegramErrorHandler.__new__(reporting.TelegramErrorHandler)
    logging.Handler.__init__(handler, level=logging.WARNING)
    handler._bot, handler._chat_id, handler._source = MagicMock(), "own", "боте"
    handler._seen, handler._sent_at, handler._loop = {}, [], MagicMock()
    handler._transient_count = 0
    handler._transient_at = 0.0

    record = logging.LogRecord("crm", logging.ERROR, "f", 1, "заявка 1: не записано в CRM", None, None)
    for _ in range(3):
        handler.emit(record)

    kinds = []
    for call in handler._loop.create_task.call_args_list:
        kinds.append(call.args[0].__name__)
        call.args[0].close()

    assert kinds.count("_send") == 1
    assert kinds.count("_remember") == 3
