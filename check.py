"""Диагностика перед первым запуском. Только чтение: ни базу, ни Telegram не трогает.

    python check.py                 # конфиг, база, вход в CRM, разбор грида
    python check.py --card 781594   # плюс карту полей карточки заявки
"""

import argparse
import asyncio
import logging
import sys

import config
import db
from crm import CrmClient, CrmError
from poller import format_notification

PREVIEW_LIMIT = 3


def _as_dict(row) -> dict:
    return {
        "crm_id": row.crm_id,
        "req_type": row.req_type,
        "opened_at": row.opened_at,
        "status_text": row.status_text,
        "is_recall": row.is_recall,
        "customer_name": row.customer_name,
        "address": row.address,
    }


async def check_config() -> None:
    config.validate(config.POLLER_REQUIRED)
    print(f"конфиг       OK  город {config.CITY_ID}, опрос {config.POLL_INTERVAL_SEC} c, "
          f"read-only {config.CRM_READ_ONLY}")


async def check_db() -> None:
    await db.connect()
    try:
        known = await db.get_state("baseline_seeded_at")
        state = f"стартовый снимок сделан {known}" if known else "стартового снимка ещё не было"
        print(f"база         OK  {state}")
    finally:
        await db.close()


async def check_crm(card_id: int | None) -> None:
    crm = CrmClient()
    try:
        await crm.login()
        print("вход в CRM   OK")

        rows = await crm.fetch_open_requests()
        print(f"грид         OK  открытых заявок: {len(rows)}")
        if not rows:
            print("             ВНИМАНИЕ: грид пуст — проверьте, та ли это учётка филиала")

        for row in rows[:PREVIEW_LIMIT]:
            print("\n" + format_notification(_as_dict(row)))

        if card_id:
            card = await crm.fetch_request_card(card_id)
            print(f"\nкарточка {card_id}: полей {len(card.fields)}, "
                  f"статус {card.status_code or '—'}, мастер {card.employee_id or '—'}")
            for key, value in sorted(card.fields.items()):
                label = card.labels.get(key)
                print(f"  {key} = {value!r}" + (f"  [{label}]" if label else ""))
    finally:
        await crm.close()


async def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка связности перед запуском")
    parser.add_argument("--card", type=int, help="ID заявки: выгрузить карту полей карточки")
    args = parser.parse_args()

    logging.basicConfig(level=config.LOG_LEVEL, format="%(levelname)s %(name)s %(message)s")

    try:
        await check_config()
        await check_db()
        await check_crm(args.card)
    except config.ConfigError as exc:
        print(f"\nконфиг       ОШИБКА\n{exc}", file=sys.stderr)
        return 1
    except CrmError as exc:
        print(f"\nCRM          ОШИБКА: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"\nОШИБКА: {exc!r}", file=sys.stderr)
        return 1

    print("\nвсё сходится, можно запускать службы")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
