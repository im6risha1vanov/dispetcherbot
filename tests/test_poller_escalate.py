"""Куда уходит «нет свободных мастеров» — без боевых chat id."""

import asyncio
from datetime import datetime

import config
import poller


def test_эскалация_идёт_директору_и_администратору(monkeypatch):
    monkeypatch.setattr(poller.config, "DIRECTOR_CHAT_ID", "dir")
    monkeypatch.setattr(poller.config, "ADMIN_CHAT_ID", "adm")

    assert poller._escalation_chats() == ["dir", "adm"]


def test_без_админа_остаётся_директор(monkeypatch):
    monkeypatch.setattr(poller.config, "DIRECTOR_CHAT_ID", "dir")
    monkeypatch.setattr(poller.config, "ADMIN_CHAT_ID", "")

    assert poller._escalation_chats() == ["dir"]


def test_одинаковые_чаты_не_дублируются(monkeypatch):
    monkeypatch.setattr(poller.config, "DIRECTOR_CHAT_ID", "same")
    monkeypatch.setattr(poller.config, "ADMIN_CHAT_ID", "same")

    assert poller._escalation_chats() == ["same"]


def test_тревога_не_сейчас_если_визит_завтра(monkeypatch):
    monkeypatch.setattr(poller.config, "ESCALATE_LEAD_MIN", 60)
    now = datetime(2026, 9, 18, 19, 45, tzinfo=config.TIMEZONE)
    visit = datetime(2026, 9, 19, 10, 0, tzinfo=config.TIMEZONE)
    assert poller._alarm_is_due(visit, now=now) is False


def test_тревога_за_час_до_визита(monkeypatch):
    monkeypatch.setattr(poller.config, "ESCALATE_LEAD_MIN", 60)
    now = datetime(2026, 9, 19, 9, 0, tzinfo=config.TIMEZONE)
    visit = datetime(2026, 9, 19, 10, 0, tzinfo=config.TIMEZONE)
    assert poller._alarm_is_due(visit, now=now) is True


def test_тревога_без_времени_сразу(monkeypatch):
    monkeypatch.setattr(poller.config, "ESCALATE_LEAD_MIN", 60)
    assert poller._alarm_is_due(None) is True


def test_тревога_при_нуле_не_вечером_накануне():
    """ESCALATE_LEAD_MIN=0 — в момент визита, не «сразу как заявка появилась»."""
    now = datetime(2026, 9, 21, 19, 0, tzinfo=config.TIMEZONE)
    visit = datetime(2026, 9, 22, 10, 0, tzinfo=config.TIMEZONE)
    assert poller._alarm_is_due(visit, now=now, lead_minutes=0) is False


def test_раздача_не_вечером_накануне_на_утро():
    now = datetime(2026, 9, 21, 19, 0, tzinfo=config.TIMEZONE)
    visit = datetime(2026, 9, 22, 10, 0, tzinfo=config.TIMEZONE)
    assert poller._visit_lead_due(visit, now=now, lead_minutes=60) is False


def test_раздача_за_час_до_визита():
    now = datetime(2026, 9, 22, 9, 0, tzinfo=config.TIMEZONE)
    visit = datetime(2026, 9, 22, 10, 0, tzinfo=config.TIMEZONE)
    assert poller._visit_lead_due(visit, now=now, lead_minutes=60) is True


def test_раздача_при_нуле_в_момент_визита_не_раньше():
    now = datetime(2026, 9, 21, 19, 0, tzinfo=config.TIMEZONE)
    visit = datetime(2026, 9, 22, 10, 0, tzinfo=config.TIMEZONE)
    assert poller._visit_lead_due(visit, now=now, lead_minutes=0) is False
    assert poller._visit_lead_due(visit, now=visit, lead_minutes=0) is True


def test_раздача_без_времени_визита_сразу():
    assert poller._visit_lead_due(None, lead_minutes=60) is True


def test_расчёт_читает_карточку_только_у_проведённой(monkeypatch):
    """Отказ и отмена не дают блока расчёта — их карточки каждый опрос не читаем.

    «Готов» по-прежнему читаем: пустой расчёт ждём, непустой отправляем.
    """
    fetched: list[int] = []
    sent: list[str] = []
    marked: list[int] = []

    class Card:
        def __init__(self, payout: str) -> None:
            self.payout = payout

    class Crm:
        async def fetch_request_card(self, crm_id: int) -> Card:
            fetched.append(crm_id)
            if crm_id == 100:
                return Card("Проведенная сумма по заявке: 1500")
            return Card("")

    class Bot:
        async def send_message(self, chat_id: int, text: str) -> None:
            sent.append(text)

    async def awaiting(city_id: int):
        assert city_id == 206
        return [
            {"id": 1, "crm_id": 782923, "status_text": "Отказ",
             "delivery_chat_id": 10, "employee_id": 1, "full_name": "А"},
            {"id": 2, "crm_id": 782552, "status_text": "Отмена Филиала",
             "delivery_chat_id": 10, "employee_id": 1, "full_name": "А"},
            {"id": 3, "crm_id": 100, "status_text": "Готов",
             "delivery_chat_id": 10, "employee_id": 1, "full_name": "А"},
            {"id": 4, "crm_id": 101, "status_text": "Готов",
             "delivery_chat_id": 10, "employee_id": 1, "full_name": "А"},
        ]

    async def mark(assignment_id: int) -> None:
        marked.append(assignment_id)

    async def noop(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(poller.db, "assignments_awaiting_payout", awaiting)
    monkeypatch.setattr(poller.db, "mark_payout_sent", mark)
    monkeypatch.setattr(poller.pinning, "unpin_assignment", noop)
    monkeypatch.setattr(poller, "notify_feed", noop)
    monkeypatch.setattr(poller.config, "CITY_ID", 206)
    monkeypatch.setattr(poller.config, "DONE_STATUSES", frozenset({"Готов", "Готов ОФ"}))

    asyncio.run(poller.send_payouts(Crm(), Bot()))

    assert fetched == [100, 101]
    assert marked == [3]
    assert len(sent) == 1 and "1500" in sent[0]
