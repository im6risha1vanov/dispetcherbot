"""Снимки отчёта лежат файлами на сервере, а не ссылками в Telegram.

Мастер присылает фото, бот скачивает его сразу и кладёт на диск. Раньше
хранилась только ссылка Telegram, а сам файл забирался в момент записи в CRM:
между этими моментами проходит подтверждение администратора, и всё это время
документ существовал только у Telegram.

В базе остаётся имя файла. Отчёты, начатые до перехода, помнят ссылку —
читатель различает их сам: нет файла с таким именем, значит это ссылка.

Файлы старше PHOTO_KEEP_DAYS удаляются: к этому сроку отчёт давно в CRM,
а диск не должен расти вечно.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from pathlib import Path

import config

log = logging.getLogger(__name__)

# Имя, которое мы выдаём сами: ничего, кроме букв, цифр, дефиса и точки.
# Всё остальное в базе — ссылка Telegram из старых отчётов.
_OURS = re.compile(r"^[0-9a-f]{32}\.jpg$")


def directory() -> Path:
    return Path(config.PHOTO_DIR)


def save(content: bytes) -> str:
    """Кладёт снимок на диск и возвращает имя, которое уйдёт в базу."""
    folder = directory()
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.jpg"
    (folder / name).write_bytes(content)
    return name


def read(name: str) -> bytes | None:
    """Содержимое снимка. None — если это не наш файл или он уже убран."""
    if not name or not _OURS.match(name):
        return None
    path = directory() / name
    try:
        return path.read_bytes()
    except OSError:
        log.warning("снимок %s не читается с диска", name)
        return None


def forget_old(keep_days: int) -> int:
    """Убирает снимки старше срока. Возвращает, сколько файлов удалено."""
    folder = directory()
    if not folder.is_dir():
        return 0

    deadline = time.time() - max(int(keep_days), 1) * 86400
    removed = 0
    for path in folder.iterdir():
        if not path.is_file() or not _OURS.match(path.name):
            continue
        try:
            if path.stat().st_mtime < deadline:
                path.unlink()
                removed += 1
        except OSError:
            log.warning("снимок %s не удалился", path.name)
    return removed
