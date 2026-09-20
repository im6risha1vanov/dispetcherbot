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
