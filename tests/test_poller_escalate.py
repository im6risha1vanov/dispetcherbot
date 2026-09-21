"""Куда уходит «нет свободных мастеров» — без боевых chat id."""

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
