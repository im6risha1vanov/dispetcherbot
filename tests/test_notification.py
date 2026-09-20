from datetime import datetime

import config
from poller import format_notification

BASE = {
    "crm_id": 781594,
    "req_type": "Впервые",
    "opened_at": datetime(2026, 9, 15, 14, 0, tzinfo=config.TIMEZONE),
    "status_text": "Ожидает",
    "is_recall": False,
    "customer_name": "Иванов Иван Иванович",
    "address": "ул. Ленина, 10",
}


def test_формат_согласованный_с_заказчиком():
    assert format_notification(BASE) == (
        "🆕 Заявка #781594 (Впервые)\n"
        "15.09.26 14:00 · Ожидает\n"
        "Иванов Иван Иванович\n"
        "ул. Ленина, 10"
    )


def test_отзывная_помечается_в_статусе():
    assert "Ожидает · отзывная" in format_notification(BASE | {"is_recall": True})


def test_пустые_поля_не_дают_пустых_строк():
    text = format_notification(BASE | {"customer_name": "", "address": "", "req_type": ""})

    assert text == "🆕 Заявка #781594\n15.09.26 14:00 · Ожидает"


def test_заявка_без_времени_не_падает():
    assert "время не указано" in format_notification(BASE | {"opened_at": None})


def test_заявка_и_напоминание_тегают_мастера():
    import messages

    rec = BASE | {"info_line": "Заказ 781594, Впервые", "telegram_username": "lpnrtm"}

    assert messages.assignment_text(rec, "assigned").startswith("@lpnrtm\n")
    assert messages.reminder_text(rec, 10).startswith("@lpnrtm\n")


def test_без_username_тега_нет_и_текст_цел():
    import messages

    rec = BASE | {"info_line": "Заказ 781594, Впервые", "telegram_username": None}

    assert messages.assignment_text(rec, "assigned").startswith("Заказ 781594")
