"""Показательный прогон сценария в один чат.

Ничего не пишет ни в CRM, ни в базу: просто отправляет те же сообщения, что
увидели бы мастер, диспетчер, администратор и директор. Кнопки настоящие, но
нажимать их не нужно — это витрина, а не рабочий поток.

    .venv/bin/python demo.py <chat_id>
"""

import asyncio
import sys

from aiogram import Bot

import closing
import config
import messages

PAUSE = 1.2

ORDER = (
    "Заказ 783199, Впервые, Сыктывкар Петрозаводская улица, 27/1, 62, "
    "подъезд 1, этаж 9, вп электро, вирпл,, Светлана, 40+, "
    "18-09-2026 14:00, непрофильная Попросить отзыв"
)
CRM_ID = 783199
REC = {
    "crm_id": CRM_ID,
    "info_line": ORDER,
    "telegram_username": "trentere",
    "req_type": "Впервые",
    "prior_master_name": None,
}


async def act(bot: Bot, chat: int, who: str, text: str, markup=None) -> None:
    await bot.send_message(chat, f"〰️ <b>{who}</b>", parse_mode="HTML")
    await bot.send_message(chat, text, reply_markup=markup)
    await asyncio.sleep(PAUSE)


async def note(bot: Bot, chat: int, text: str) -> None:
    await bot.send_message(chat, f"⬜️ {text}")
    await asyncio.sleep(PAUSE)


async def main(chat: int) -> None:
    bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
    try:
        await bot.send_message(
            chat,
            "🎬 Показательный прогон. Ничего не пишется ни в CRM, ни в базу.\n"
            "Подписи сверху показывают, в чей чат ушло бы сообщение.",
        )
        await asyncio.sleep(PAUSE)

        await act(bot, chat, "ОБЩИЙ ЧАТ МАСТЕРОВ · 09:00",
                  messages.shift_prompt_text(["trentere", "NoLi_z", "lpnrtm"]),
                  messages.shift_keyboard())
        await note(bot, chat, "Порядок нажатий задаёт очередь на день.")

        await act(bot, chat, "ДИРЕКТОР · через 30 минут",
                  "📋 Не отметились на смену (1):\n· Кузиванов Илья")

        await note(bot, chat, "Появилась заявка. До визита остался час — пора раздавать.")
        await act(bot, chat, "ЧАТ МАСТЕРА · заявка",
                  messages.assignment_text(REC, "assigned"),
                  messages.master_keyboard(CRM_ID, "assigned"))
        await note(bot, chat, "Сообщение закрепляется в чате мастера.")

        await act(bot, chat, "ВЫ И ДИРЕКТОР · лента",
                  f"📤 Заказ {CRM_ID} → Габидуллин Ким (1-й в очереди)",
                  messages.move_keyboard(CRM_ID))

        await act(bot, chat, "ЧАТ МАСТЕРА · через 5 минут молчания",
                  messages.reminder_text(REC, 10),
                  messages.master_keyboard(CRM_ID, "assigned"))
        await act(bot, chat, "ДИРЕКТОР",
                  f"🔔 Заявка #{CRM_ID}: Габидуллин Ким не принял её за 5 мин.\n"
                  f"Петрозаводская улица, 27/1\nЧерез 10 мин заявка уйдёт следующему мастеру.")

        await note(bot, chat, "Мастер нажал «В пути» — в CRM статус «В пути».")
        await act(bot, chat, "ЧАТ МАСТЕРА",
                  messages.assignment_text(REC, "enroute"),
                  messages.master_keyboard(CRM_ID, "enroute"))

        await note(bot, chat, "Мастер нажал «Номер для дозвона».")
        await act(bot, chat, "ЧАТ ДИСПЕТЧЕРОВ",
                  messages.info_request_text(messages.KIND_PHONE, REC, "Габидуллин Ким"))
        await act(bot, chat, "ЧАТ МАСТЕРА · диспетчер ответил реплаем",
                  messages.info_answer_text(messages.KIND_PHONE, CRM_ID, "+7 912 345-67-89"))

        await note(bot, chat, "Мастер нажал «На месте» — статус в CRM не меняется.")
        await act(bot, chat, "ЧАТ МАСТЕРА",
                  messages.assignment_text(REC, "onsite"),
                  messages.master_keyboard(CRM_ID, "onsite"))

        await note(bot, chat, "Мастер нажал «В работе» — в CRM статус «В работе».")
        await act(bot, chat, "ЧАТ МАСТЕРА",
                  messages.assignment_text(REC, "inwork"),
                  messages.master_keyboard(CRM_ID, "inwork"))

        await note(bot, chat, "━━━ Ветка 1: мастер закрывает заявку ━━━")
        for key in ("payed", "spares", "spare_photo", "bso", "bso_photo", "feedback"):
            step = closing.STEPS[key]
            await act(bot, chat, "ЧАТ МАСТЕРА · вопрос",
                      messages.closing_question(step, CRM_ID),
                      messages.closing_keyboard(step))

        report = {
            "crm_id": CRM_ID, "kind": closing.KIND_CLOSE,
            "payed_by_customer": 3000, "spares_cost": 500,
            "with_bso": "1", "fback_mode": "2",
            "photos": {"spare": ["a"], "bso": ["b", "c"]},
        }
        await act(bot, chat, "ВЫ · отчёт на проверку",
                  closing.summary(report, "Габидуллин Ким"),
                  messages.admin_decision_keyboard(1))
        await note(bot, chat, "Фото уходят следом отдельными сообщениями.")
        await act(bot, chat, "ЧАТ МАСТЕРА · после подтверждения",
                  f"✅ Отчёт по заказу {CRM_ID} принят.")
        await act(bot, chat, "ЧАТ МАСТЕРА · когда заявка проведена в CRM",
                  messages.payout_text(CRM_ID,
                                       "Проведенная сумма по заявке: 3 000 р.\n"
                                       "Сумма к сдаче: 2 100 р.\n"
                                       'Группа расчета: "A" 18П'))

        await note(bot, chat, "━━━ Ветка 2: мастер забирает технику на СД ━━━")
        for key in closing.SD_ORDER:
            step = closing.SD_STEPS[key]
            await act(bot, chat, "ЧАТ МАСТЕРА · вопрос",
                      messages.closing_question(step, CRM_ID),
                      messages.closing_keyboard(step))

        sd = {
            "crm_id": CRM_ID, "kind": closing.KIND_SD_OPEN,
            "branch_comment": "преда 2500, соглас 6000, сроки 3 дня, не греет",
            "photos": {"safety": ["a"]},
        }
        await act(bot, chat, "ВЫ · перевод в СД на проверку",
                  closing.summary(sd, "Габидуллин Ким"),
                  messages.admin_decision_keyboard(2))
        await note(bot, chat,
                   "После подтверждения в CRM: статус «В работе СД», расписка в своё окно, "
                   "а ответы мастера — в «Комментарий филиала» дословно.")

        await note(bot, chat, "━━━ Через месяц: закрытие СД ━━━")
        await act(bot, chat, "ЧАТ МАСТЕРА · мастер пишет",
                  f"@level_shadow_helper_bot {CRM_ID}")
        await act(bot, chat, "ЧАТ МАСТЕРА · бот нашёл заявку",
                  messages.sd_close_offer(REC), messages.sd_close_keyboard(CRM_ID))
        for key in ("payed", "spares", "safety_photo", "feedback"):
            step = closing.SD_CLOSE_STEPS[key]
            await act(bot, chat, "ЧАТ МАСТЕРА · вопрос",
                      messages.closing_question(step, CRM_ID),
                      messages.closing_keyboard(step))

        await note(bot, chat, "━━━ Прочее ━━━")
        await act(bot, chat, "ВЫ И ДИРЕКТОР",
                  f"🚨 Некому отдать заявку — все мастера заняты\n\n{ORDER}\n\n"
                  "Заняты: Габидуллин Ким, Липин Артем\n"
                  "Назначьте вручную или дождитесь освобождения.",
                  messages.move_keyboard(CRM_ID))
        await act(bot, chat, "ДИРЕКТОР",
                  "⏳ Гарантия ждёт своего мастера: Кузиванов Илья\n\n"
                  f"{ORDER}\n\nЗаявка закреплена за ним и будет ждать, пока он не освободится.")
        await act(bot, chat, "ВЫ И ДИРЕКТОР",
                  f"🔔 Заявка #{CRM_ID}: Габидуллин Ким в пути больше 40 мин "
                  "и не отметился на месте.\nПетрозаводская улица, 27/1")

        await bot.send_message(chat, "🏁 Прогон закончен. Записи в CRM не было.")
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1])))
