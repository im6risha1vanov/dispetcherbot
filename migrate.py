"""Применение миграций по порядку с отметкой в базе.

До этого миграции применяли руками через psql, и что применено было видно
только по схеме. Теперь каждая отмечается в schema_migrations, а службы при
старте отказываются работать на устаревшей базе.

    .venv/bin/python migrate.py              применить неприменённые
    .venv/bin/python migrate.py --status     показать состояние
    .venv/bin/python migrate.py --baseline 023   отметить 001-023 применёнными

Таблицу заводит сам скрипт, а не миграция: файл, отмечающий 001-023
применёнными, на чистой базе соврал бы и схема не создалась бы вовсе.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import config
import db

log = logging.getLogger("migrate")

FOLDER = Path(__file__).resolve().parent / "migrations"
_NAME = re.compile(r"^(\d{3})_([\w-]+)\.sql$")

SCHEMA_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    filename   TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


@dataclass(frozen=True)
class Migration:
    version: str  # «023»
    filename: str  # «023_report_v2.sql»
    path: Path

    @property
    def title(self) -> str:
        return self.filename.removesuffix(".sql")


def known() -> list[Migration]:
    """Все файлы миграций по возрастанию номера."""
    found = []
    for path in sorted(FOLDER.glob("*.sql")):
        match = _NAME.match(path.name)
        if match is None:
            log.warning("файл %s не похож на миграцию — пропускаю", path.name)
            continue
        found.append(Migration(version=match.group(1), filename=path.name, path=path))
    return found


def pending(applied: set[str]) -> list[Migration]:
    return [m for m in known() if m.version not in applied]


async def applied_versions(conn) -> set[str]:
    await conn.execute(SCHEMA_TABLE_DDL)
    rows = await conn.fetch("SELECT version FROM schema_migrations")
    return {row["version"] for row in rows}


async def _apply(conn, migration: Migration) -> None:
    """Миграция и отметка о ней — в одной транзакции.

    Иначе возможна база, где таблица создана, а записи о ней нет: следующий
    запуск попробует применить ещё раз и упадёт на полпути.
    """
    sql = migration.path.read_text(encoding="utf-8")
    async with conn.transaction():
        await conn.execute(sql)
        await conn.execute(
            "INSERT INTO schema_migrations (version, filename) VALUES ($1, $2)",
            migration.version,
            migration.filename,
        )


async def run_pending(conn) -> list[Migration]:
    applied = await applied_versions(conn)
    todo = pending(applied)
    for migration in todo:
        print(f"применяю {migration.title} …", flush=True)
        await _apply(conn, migration)
    return todo


async def baseline(conn, upto: str) -> list[Migration]:
    """Отмечает миграции по указанную включительно применёнными, не выполняя их."""
    applied = await applied_versions(conn)
    marked = []
    for migration in known():
        if migration.version > upto or migration.version in applied:
            continue
        await conn.execute(
            "INSERT INTO schema_migrations (version, filename) VALUES ($1, $2)",
            migration.version,
            migration.filename,
        )
        marked.append(migration)
    return marked


async def status(conn) -> None:
    applied = await applied_versions(conn)
    rows = {
        row["version"]: row["applied_at"]
        for row in await conn.fetch("SELECT version, applied_at FROM schema_migrations")
    }
    for migration in known():
        if migration.version in applied:
            print(f"  ✓ {migration.title:<34} применена {rows[migration.version]:%Y-%m-%d %H:%M}")
        else:
            print(f"  · {migration.title:<34} НЕ ПРИМЕНЕНА")
    left = len(pending(applied))
    print(f"\nвсего {len(known())}, не применено {left}")


async def main() -> int:
    parser = argparse.ArgumentParser(description="миграции базы bt-dispatch-bot")
    parser.add_argument("--status", action="store_true", help="показать состояние и выйти")
    parser.add_argument(
        "--baseline",
        metavar="NNN",
        help="отметить миграции по NNN включительно применёнными, не выполняя их",
    )
    args = parser.parse_args()

    logging.basicConfig(level=config.LOG_LEVEL, format="%(levelname)s %(message)s")
    config.validate(["DATABASE_URL"])
    await db.connect()
    try:
        async with db.acquire() as conn:
            if args.status:
                await status(conn)
                return 0

            if args.baseline:
                marked = await baseline(conn, args.baseline.zfill(3))
                if marked:
                    print("отмечены применёнными без выполнения:")
                    for migration in marked:
                        print(f"  {migration.title}")
                else:
                    print("нечего отмечать: всё уже учтено")
                return 0

            done = await run_pending(conn)
            print(f"применено миграций: {len(done)}" if done else "нечего применять")
            return 0
    finally:
        await db.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
