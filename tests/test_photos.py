"""Снимки отчёта лежат файлами на сервере, а не ссылками в Telegram."""

import os
import time

import config
import photos


def test_снимок_ложится_файлом_и_читается_обратно(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PHOTO_DIR", str(tmp_path / "photos"))

    name = photos.save(b"jpeg-bytes")

    assert (tmp_path / "photos" / name).is_file()
    assert photos.read(name) == b"jpeg-bytes"


def test_ссылку_telegram_за_файл_не_принимаем(monkeypatch, tmp_path):
    """Отчёты, начатые до перехода, помнят ссылку — её читает Telegram, не диск."""
    monkeypatch.setattr(config, "PHOTO_DIR", str(tmp_path / "photos"))

    assert photos.read("AgACAgIAAxkBAAIB") is None
    assert photos.read("") is None
    assert photos.read("../../etc/passwd") is None


def test_пропавший_файл_не_роняет_бот(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PHOTO_DIR", str(tmp_path / "photos"))
    name = photos.save(b"jpeg")
    (tmp_path / "photos" / name).unlink()

    assert photos.read(name) is None


def test_старые_снимки_убираются(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PHOTO_DIR", str(tmp_path / "photos"))
    old = photos.save("старый".encode())
    fresh = photos.save("свежий".encode())

    long_ago = time.time() - 30 * 86400
    os.utime(tmp_path / "photos" / old, (long_ago, long_ago))

    removed = photos.forget_old(14)

    assert removed == 1
    assert photos.read(old) is None
    assert photos.read(fresh) == "свежий".encode()


def test_уборка_без_каталога_не_падает(monkeypatch, tmp_path):
    """Первый запуск: снимков ещё не было, каталога тоже."""
    monkeypatch.setattr(config, "PHOTO_DIR", str(tmp_path / "нет-такого"))

    assert photos.forget_old(14) == 0
