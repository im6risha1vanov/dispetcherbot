"""Сценарий закрытия: какие вопросы задаются и какие фото обязательны."""

from decimal import Decimal

import closing


def walk(answers: dict) -> list[str]:
    """Проходит диалог до конца, отвечая заранее заданными значениями."""
    step = closing.FIRST_STEP
    path = [step]
    while (step := closing.next_step(step, answers)) is not None:
        path.append(step)
    return path


def test_без_зпч_про_них_больше_не_спрашивают():
    path = walk({"with_zip": "0", "fback_mode": "3"})

    assert "zip_photo" not in path
    assert "zip_sum" not in path
    assert path == ["docs_photo", "zip", "receipt", "prepay", "total", "feedback"]


def test_с_зпч_спрашивают_и_фото_и_сумму():
    """Трата без чека не подтверждена, поэтому фото обязательно."""
    path = walk({"with_zip": "1", "fback_mode": "3"})

    assert path == [
        "docs_photo", "zip", "zip_photo", "receipt", "prepay", "total",
        "zip_sum", "feedback",
    ]
    assert closing.CLOSE_STEPS["zip_photo"].required is True


def test_анкета_начинается_с_документов():
    """Документы просим, пока мастер у клиента и может переснять."""
    assert closing.FIRST_STEP_BY_KIND[closing.KIND_CLOSE] == "docs_photo"
    assert walk({"with_zip": "0", "fback_mode": "3"})[0] == "docs_photo"


def test_отдельного_вопроса_про_бсо_нет():
    """Ответ виден по тому, прислал мастер снимки или нажал «Готово» пустым."""
    path = walk({"with_zip": "0", "fback_mode": "3"})

    assert "bso" not in path
    assert closing.has_bso({"photos": {closing.PHOTO_BSO: ["f1"]}}) is True
    assert closing.has_bso({"photos": {}}) is False


def test_прерванный_старый_отчёт_начинается_заново():
    """Порядок вопросов изменился: продолжать с прежнего шага нельзя."""
    assert closing.canonical_step(closing.KIND_CLOSE, "bso") == "docs_photo"
    assert closing.next_step("payed", {}) == "docs_photo"




def test_отзыв_да_значит_нужно_фото_отзыва():
    path = walk({"with_zip": "0", "fback_mode": "1"})

    assert "feedback_photo" in path


def test_нет_возможности_отзыва_фото_не_просят():
    path = walk({"with_zip": "0", "fback_mode": "3"})

    assert "feedback_photo" not in path



def test_отзыв_предлагает_все_три_варианта_как_в_crm():
    assert [value for value, _ in closing.CLOSE_STEPS["feedback"].choices] == ["1", "2", "3"]


def test_отзыв_нет_фото_не_просит():
    """Фото отзыва есть только тогда, когда отзыв написан."""
    path = walk({"with_zip": "0", "fback_mode": "2"})

    assert "feedback_photo" not in path


def test_каждое_фото_идёт_в_своё_окно_crm():
    """Подписи окон в CRM не равны именам полей — раскладка задана явно."""
    windows = {step.photo_kind for step in closing.CLOSE_STEPS.values() if step.kind == "photo"}

    assert windows & set(closing.PHOTO_FIELDS) == windows


def _close_row(**extra):
    row = {
        "crm_id": 783175,
        "kind": closing.KIND_CLOSE,
        "payed_by_customer": 3000,
        "prepayment_sum": 0,
        "spares_cost": 0,
        "with_bso": None,
        "with_zip": "0",
        "receipt_mode": "0",
        "fback_mode": "3",
        "photos": {},
    }
    row.update(extra)
    return row


def test_ответы_превращаются_в_поля_формы():
    row = _close_row(
        spares_cost=500.5,
        with_zip="1",
        prepayment_sum=1000,
        receipt_mode="10",
        fback_mode="1",
        photos={closing.PHOTO_BSO: ["f1"]},
    )

    payload = closing.crm_payload(row)

    assert payload[closing.FIELD_PAYED] == "3000"
    assert payload[closing.FIELD_PREPAY] == "1000"
    assert payload[closing.FIELD_SPARES] == "500.5"
    assert payload[closing.FIELD_ZIP] == "1"
    assert payload[closing.FIELD_RECEIPT] == "10"
    assert payload[closing.FIELD_BSO] == "1"
    assert payload[closing.FIELD_FEEDBACK] == "1"
    assert payload[closing.FIELD_REQ_FBACK] == "1"
    assert closing.FIELD_SD_READY_AT not in payload


def test_без_фото_бсо_в_crm_уходит_нет():
    """Мастер нажал «Готово» пустым — выдумывать за него «есть» нельзя."""
    payload = closing.crm_payload(_close_row(photos={}))

    assert payload[closing.FIELD_BSO] == "0"


def test_нулевая_предоплата_уходит_пустой():
    """Yii рисует ноль в number input пустым — сверка иначе не сойдётся."""
    assert closing.crm_payload(_close_row())[closing.FIELD_PREPAY] == ""


def test_сводка_показывает_ответы_словами():
    row = _close_row(
        prepayment_sum=500,
        receipt_mode="5",
        photos={closing.PHOTO_BSO: ["f1"]},
    )

    text = closing.summary(row, "Мишарин Олег")

    assert "Сумма заявки: 3000 р." in text
    assert "Предоплата: 500 р." in text
    assert "БСО: есть" in text
    assert "чек взято всего" in text
    assert "нет возможности" in text


def test_сводка_предупреждает_но_не_запрещает():
    """ЗПЧ дороже заявки бывает. Решает человек, бот только показывает."""
    row = _close_row(
        with_zip="1", spares_cost=5000, prepayment_sum=4000,
        photos={closing.PHOTO_BSO: ["f1"]},
    )

    alarms = closing.report_warnings(row)
    text = closing.summary(row, "Мишарин Олег")

    assert any("ЗПЧ дороже" in line for line in alarms)
    assert any("Предоплата больше" in line for line in alarms)
    assert "ЗПЧ дороже" in text


def test_отсутствие_бсо_отмечено_в_сводке():
    alarms = closing.report_warnings(_close_row(photos={}))

    assert any("БСО" in line for line in alarms)


def test_суммы_проверяются_на_мусор_и_потолок():
    assert closing.parse_amount("3500")[0] == 3500
    assert closing.parse_amount("3 500,50")[0] == Decimal("3500.50")
    assert closing.parse_amount("0")[0] == 0

    for junk in ("где-то три тыщи", "-100", "", "1500р", "1.2.3"):
        value, problem = closing.parse_amount(junk)
        assert value is None and problem, junk

    value, problem = closing.parse_amount("99999999")
    assert value is None
    assert "опечатку" in problem


def test_сводка_принимает_фото_строкой_json():
    """asyncpg без кодека отдаёт jsonb текстом — сводка не должна падать."""
    row = {
        "crm_id": 782988,
        "kind": closing.KIND_SD_OPEN,
        "branch_comment": "не греет",
        "photos": '{"safety": ["aaa", "bbb"]}',
    }

    text = closing.summary(row, "Габидуллин Ким")

    assert "safety: 2" in text
    assert "не греет" in text


def test_as_dict_принимает_dict_и_json_строку():
    assert closing.as_dict({"safety": ["a"]}) == {"safety": ["a"]}
    assert closing.as_dict('{"safety": ["a"]}') == {"safety": ["a"]}
    assert closing.as_dict(None) == {}
    assert closing.as_dict("") == {}
    assert closing.as_dict([]) == {}
    assert closing.as_dict("не json") == {}


def test_выгрузка_фото_принимает_json_строку():
    """Тот же jsonb-строковый сюрприз на пути в CRM."""
    import asyncio
    from unittest.mock import AsyncMock

    from crm import CrmClient

    async def scenario():
        client = CrmClient()
        client._download_file = AsyncMock(return_value=b"jpeg")
        try:
            return await client._download_photos('{"safety": ["file1"]}')
        finally:
            await client.close()

    files = asyncio.run(scenario())
    assert files
    assert files[0][0] in closing.PHOTO_FIELDS[closing.PHOTO_SAFETY]




def test_непрошенное_поле_заполняется_по_сумме():
    """Вопрос пропущен — значение всё равно должно уйти в CRM."""
    row = {
        "kind": closing.KIND_CLOSE,
        "payed_by_customer": 2000, "spares_cost": 0, "with_bso": "0",
        "with_zip": None, "fback_mode": "3",
    }

    assert closing.crm_payload(row)[closing.FIELD_ZIP] == "0"


def test_режим_чека_всегда_без_чека():
    """Филиал работает без чека — мастера об этом не спрашиваем."""
    row = {
        "kind": closing.KIND_CLOSE,
        "payed_by_customer": 1000, "spares_cost": 0,
        "with_bso": "1", "with_zip": None, "receipt_mode": None, "fback_mode": "2",
    }

    assert closing.crm_payload(row)[closing.FIELD_RECEIPT] == closing.RECEIPT_NONE


def test_документы_ждут_несколько_снимков():
    """БСО и чек самозанятого приходят вместе, поэтому шаг не закрывается сам."""
    assert closing.CLOSE_STEPS["docs_photo"].multi is True


def walk_kind(kind: str, answers: dict) -> list[str]:
    step = closing.FIRST_STEP_BY_KIND[kind]
    path = [step]
    while (step := closing.next_step_for(kind, step, answers)) is not None:
        path.append(step)
    return path


def test_открытие_сд_начинается_с_расписки():
    path = walk_kind(closing.KIND_SD_OPEN, {})

    assert path == ["safety_photo", "comment"]
    assert closing.SD_STEPS["safety_photo"].photo_kind == closing.PHOTO_SAFETY


def test_открытие_сд_один_вопрос_комментарий_филиала():
    """Неисправность, предоплата и срок — в одном комментарии; срок ещё в datepicker."""
    step = closing.SD_STEPS["comment"]

    assert step.kind == "text"
    assert step.question == closing.SD_COMMENT_QUESTION
    assert closing.answer_field("comment") == "branch_comment"
    assert closing.FIELD_COMMENT == "CustomerRequest[recommendation_comment]"
    assert closing.FIELD_SD_READY_AT == "CustomerRequest[work_in_sd_ready_at]"
    assert "prepayment" not in closing.SD_STEPS
    assert "deadline" not in closing.SD_STEPS
    assert "malfunction" not in closing.SD_STEPS
    assert "agreed_sum" not in closing.SD_STEPS


def test_ответы_по_сд_уходят_в_комментарий_как_есть():
    """Бот ничего не правит в словах мастера."""
    row = {
        "branch_comment": "преда 2500, соглас 6000, сроки 3 дня, не греет, нужен ТЭН",
    }

    comment = closing.sd_comment(row)

    assert comment == "преда 2500, соглас 6000, сроки 3 дня, не греет, нужен ТЭН"


def test_новый_комментарий_филиала_важнее_старой_анкеты():
    row = {
        "branch_comment": "как написал мастер",
        "prepayment": "2000", "deadline": "завтра", "malfunction": "не греет",
    }

    assert closing.sd_comment(row) == "как написал мастер"


def test_старый_отчёт_сд_ещё_склеивается():
    """Незавершённая анкета до выкладки не должна потерять уже собранные ответы."""
    row = {
        "prepayment": "2000", "agreed_sum": "8000",
        "deadline": "до 20 октября", "malfunction": "не греет, нужен ТЭН",
    }

    comment = closing.sd_comment(row)

    assert "Предоплата: 2000" in comment
    assert "Сроки сдачи: до 20 октября" in comment


def test_незавершённый_сд_перескакивает_на_комментарий():
    assert closing.canonical_step(closing.KIND_SD_OPEN, "deadline") == "comment"
    assert closing.next_step_for(closing.KIND_SD_OPEN, "prepayment", {}) == "comment"


def test_закрытие_сд_не_спрашивает_про_бсо():
    """Вместо БСО оформляется расписка, поэтому вопрос лишний."""
    path = walk_kind(closing.KIND_SD_CLOSE, {"spares_cost": 0, "fback_mode": "2"})

    assert "bso" not in path
    assert path == ["payed", "spares", "safety_photo", "feedback"]
    assert "comment" not in path
    assert "deadline" not in path
    assert "malfunction" not in path


def test_закрытие_сд_не_шлёт_фиктивные_срок_сумму_неисправность():
    """В форме нет этих полей, а предоплату не затираем нулями."""
    row = {
        "kind": closing.KIND_SD_CLOSE,
        "payed_by_customer": 5000,
        "spares_cost": 0,
        "with_bso": None,
        "fback_mode": "2",
    }

    payload = closing.crm_payload(row)

    assert closing.FIELD_COMMENT not in payload
    assert closing.FIELD_SD_READY_AT not in payload
    assert "CustomerRequest[prepayment]" not in payload
    assert not any("deadline" in name or "malfunction" in name for name in payload)
    assert payload[closing.FIELD_PAYED] == "5000"


def test_обычное_закрытие_без_комментария_филиала():
    path = walk({"with_zip": "0", "fback_mode": "3"})

    assert "comment" not in path
    assert path == ["docs_photo", "zip", "receipt", "prepay", "total", "feedback"]


def test_комментарий_филиала_только_ответом_на_вопрос():
    import messages

    text = messages.closing_question(closing.SD_STEPS["comment"], 782988)

    assert "Комментарий филиала" in text
    assert "Заполните комментарий" in text
    assert "00.00.2026" in text
    assert "Ответьте на это сообщение" in text


def test_срок_сдачи_из_третьей_строки_комментария():
    assert closing.parse_sd_ready_at("не греет\n4000\n19.09.2026") == "19-09-2026"
    assert closing.parse_sd_ready_at("не греет\n4000\nсрок сдачи 22.09.2026") == "22-09-2026"
    assert closing.parse_sd_ready_at("не греет\n4000") is None
    assert closing.parse_sd_ready_at("не греет\n4000\nдо 19.09.2026") is None
    assert closing.parse_sd_ready_at("не греет\n4000\nзавтра") is None
    assert closing.parse_sd_ready_at("не греет\n4000\nв конце недели") is None
    assert closing.parse_sd_ready_at("не греет\n4000\n19-09-2026") is None
    assert closing.parse_sd_ready_at("не греет\n4000\n32.13.2026") is None


def test_закрытие_сд_кладёт_документы_в_окно_расписки():
    step = closing.SD_CLOSE_STEPS["safety_photo"]

    assert step.photo_kind == closing.PHOTO_SAFETY
    assert closing.PHOTO_FIELDS[closing.PHOTO_SAFETY] == [
        "CustomerRequest[images_safetyreceipt][]"
    ]


def test_при_закрытии_сд_спрашивают_итоговую_сумму():
    assert "включая предоплату" in closing.SD_CLOSE_STEPS["payed"].question


def test_каждый_шаг_с_фото_принимает_несколько_снимков():
    """Чек может быть на двух листах, отзыв — со скриншотами."""
    for steps in closing.STEPS_BY_KIND.values():
        for step in steps.values():
            if step.kind == "photo":
                assert step.multi is True, f"{step.key} принимает только одно фото"


def test_при_закрытии_и_сд_лишних_кнопок_нет():
    import messages

    kb = messages.master_keyboard(1, "inwork")
    titles = [b.text for row in kb.inline_keyboard for b in row]

    assert any("Отчёт" in t for t in titles)
    assert any("СД" in t for t in titles)
    assert not any("Номер" in t or "квартир" in t for t in titles)


def test_переписка_по_отчёту_копится_для_уборки():
    """Список сообщений нужен, чтобы потом вычистить чат мастера."""
    import db

    assert hasattr(db, "remember_closure_message")
    assert hasattr(db, "closure_messages")
    assert hasattr(db, "clear_closure_messages")


def test_дистанционное_решение_закрывает_нулями():
    """Выезда не было: ни денег, ни БСО, ни комплектующих."""
    row = {"kind": closing.KIND_REMOTE, "crm_id": 1}

    payload = closing.crm_payload(row)

    assert payload[closing.FIELD_PAYED] == "0"
    assert payload[closing.FIELD_SPARES] == ""
    assert payload[closing.FIELD_BSO] == "0"
    assert payload[closing.FIELD_ZIP] == "0"
    assert closing.FIELD_REQ_FBACK not in payload


def test_дистанционное_решение_не_задаёт_вопросов():
    assert closing.STEPS_BY_KIND[closing.KIND_REMOTE] == {}


def test_кнопка_дистанционного_решения_только_у_гарантии():
    import messages

    usual = messages.master_keyboard(1, "assigned")
    warranty = messages.master_keyboard(1, "assigned", warranty="ask")
    after = messages.master_keyboard(1, "assigned", warranty="done")

    def titles(kb):
        return [b.text for row in kb.inline_keyboard for b in row]

    assert not any("истанционн" in t for t in titles(usual))
    assert any("Дистанционное решение" in t for t in titles(warranty))
    assert any("Решено дистанционно" in t for t in titles(after))


def test_диспетчерам_уходит_привычный_скрипт():
    import messages

    text = messages.info_request_text(messages.KIND_REMOTE, {"crm_id": 783199}, "Ким")

    assert text == "783199 номер для дист решения"


TOTALS = {
    "orders": 3, "turnover": 42100, "average": 14033,
    "biggest": 20000, "top_master": "Мишарин Олег", "top_order": 782847,
}


def leftover(crm_id: int, status: str, master: str, on_sd: bool) -> dict:
    return {"crm_id": crm_id, "status_text": status, "master_name": master, "on_sd": on_sd}


def test_дайджест_хвалит_когда_всё_закрыто():
    import messages
    from datetime import date

    text = messages.digest_text(date(2026, 9, 17), TOTALS, [])

    assert "Все заявки дня закрыты" in text
    assert "Не закрыто" not in text


def test_дайджест_перечисляет_незакрытые():
    import messages
    from datetime import date

    text = messages.digest_text(date(2026, 9, 17), TOTALS, [
        leftover(783199, "В работе", "Габидуллин Ким", False),
        leftover(783201, "Ожидает", "", False),
    ])

    assert "Не закрыто: 2" in text
    assert "783199 — Габидуллин Ким (В работе)" in text
    assert "мастер не назначен" in text


def test_дайджест_отдельно_показывает_сложную_диагностику():
    import messages
    from datetime import date

    text = messages.digest_text(date(2026, 9, 17), TOTALS, [
        leftover(783150, "В работе СД", "Липин Артем", True),
    ])

    assert "Забрали на сложную диагностику: 1" in text
    assert "Все заявки дня закрыты" in text, "СД не считается незакрытой заявкой"


def test_при_неудаче_открепления_номер_не_теряется():
    """Иначе заявка останется в закрепе навсегда: повторить будет нечем."""
    import asyncio
    from unittest.mock import AsyncMock, patch

    import pinning

    state = {"pinned": 283}

    async def get_pinned(employee_id):
        return state["pinned"]

    async def set_pinned(employee_id, message_id):
        state["pinned"] = message_id

    bot = AsyncMock()
    bot.unpin_chat_message.side_effect = RuntimeError("Too Many Requests")

    with patch.object(pinning.db, "get_pinned_message", get_pinned), \
         patch.object(pinning.db, "set_pinned_message", set_pinned):
        asyncio.run(pinning.unpin_assignment(bot, 8771, -100))

    assert state["pinned"] == 283, "номер закреплённого сообщения потерян"


def test_после_успешного_открепления_номер_забывается():
    import asyncio
    from unittest.mock import AsyncMock, patch

    import pinning

    state = {"pinned": 283}

    async def get_pinned(employee_id):
        return state["pinned"]

    async def set_pinned(employee_id, message_id):
        state["pinned"] = message_id

    with patch.object(pinning.db, "get_pinned_message", get_pinned), \
         patch.object(pinning.db, "set_pinned_message", set_pinned):
        asyncio.run(pinning.unpin_assignment(AsyncMock(), 8771, -100))

    assert state["pinned"] is None


def test_открепление_передаёт_message_id_именем():
    """Второй позиционный аргумент unpin — business_connection_id, не message_id."""
    import asyncio
    from unittest.mock import AsyncMock, patch

    import pinning

    bot = AsyncMock()

    async def get_pinned(employee_id):
        return 283

    async def set_pinned(employee_id, message_id):
        pass

    with patch.object(pinning.db, "get_pinned_message", get_pinned), \
         patch.object(pinning.db, "set_pinned_message", set_pinned):
        asyncio.run(pinning.unpin_assignment(bot, 8771, -100))

    kwargs = bot.unpin_chat_message.await_args.kwargs
    assert kwargs["message_id"] == 283
    assert "business_connection_id" not in kwargs or kwargs.get("business_connection_id") is None


def test_штатный_get_httpx_не_пишется_на_info(monkeypatch):
    import logging

    import reporting

    monkeypatch.setattr(reporting.config, "LOG_LEVEL", "INFO")
    logger = logging.getLogger("httpx")
    previous = logger.level
    logger.setLevel(logging.NOTSET)
    try:
        reporting.quiet_http_client_logs()
        assert logger.level == logging.WARNING
        assert not logger.isEnabledFor(logging.INFO)
    finally:
        logger.setLevel(previous)


def test_debug_оставляет_трассу_httpx(monkeypatch):
    import logging

    import reporting

    monkeypatch.setattr(reporting.config, "LOG_LEVEL", "DEBUG")
    logger = logging.getLogger("httpx")
    previous = logger.level
    logger.setLevel(logging.INFO)
    try:
        reporting.quiet_http_client_logs()
        assert logger.level == logging.INFO
    finally:
        logger.setLevel(previous)


def _scheduled(handler):
    """Какие корутины обработчик поставил в очередь: отправки и записи в сводку."""
    kinds = []
    for call in handler._loop.create_task.call_args_list:
        coro = call.args[0]
        kinds.append(coro.__name__)
        coro.close()  # их никто не ждёт: гасим, чтобы не сыпались предупреждения
    return kinds


def test_одинаковые_ошибки_не_спамят_админа():
    """Ошибка повторяется каждую минуту опроса — в чат она должна уйти один раз.

    Но в вечернюю сводку должны попасть все пять: иначе не видно, что CRM
    молчала весь день, а не разок.
    """
    import logging

    handler = _handler()
    record = logging.LogRecord("crm", logging.WARNING, "f", 1, "CRM не ответила", None, None)
    for _ in range(5):
        handler.emit(record)

    kinds = _scheduled(handler)
    assert kinds.count("_send") == 1
    assert kinds.count("_remember") == 5


def test_штатная_остановка_в_сводку_не_идёт():
    """SIGTERM при выкладке — не сбой: ни в чат, ни в вечернюю сводку."""
    import logging

    handler = _handler()
    handler.emit(
        logging.LogRecord("aiogram", logging.WARNING, "f", 1, "Received SIGTERM signal", None, None)
    )

    assert _scheduled(handler) == []


def test_сбой_отправки_не_порождает_новый_сбой():
    """Иначе получится бесконечная цепочка жалоб на жалобы."""
    import logging

    import reporting

    handler = _handler()
    own = logging.LogRecord(reporting.__name__, logging.ERROR, "f", 1, "не отправил", None, None)
    handler.emit(own)

    assert handler._loop.create_task.call_count == 0


def _handler():
    import logging
    from unittest.mock import MagicMock

    import reporting

    h = reporting.TelegramErrorHandler.__new__(reporting.TelegramErrorHandler)
    logging.Handler.__init__(h, level=logging.WARNING)
    h._bot, h._chat_id, h._source = MagicMock(), "1", "тесте"
    h._seen, h._sent_at, h._loop = {}, [], MagicMock()
    h._transient_count = 0
    h._transient_at = 0.0
    h.setFormatter(logging.Formatter("%(message)s"))
    return h


def test_остановка_службы_не_считается_сбоем():
    """SIGTERM прилетает при каждой выкладке — это не авария."""
    import logging

    h = _handler()
    h.emit(logging.LogRecord("aiogram.dispatcher", logging.WARNING, "f", 1,
                             "Received SIGTERM signal", None, None))

    assert h._loop.create_task.call_count == 0


def test_настоящий_сбой_по_прежнему_доходит():
    import logging

    h = _handler()
    record = logging.LogRecord("crm", logging.ERROR, "f", 1,
                               "CRM не приняла запись", None, None)
    text = h._build_alert(record)

    assert text is not None
    assert "CRM не приняла запись" in text
    assert "Traceback" not in text


RST = (
    "Failed to fetch updates - TelegramNetworkError: HTTP Client says - "
    "ClientOSError: [Errno 104] Connection reset by peer"
)


def test_одиночный_обрыв_telegram_не_будит_директора():
    """aiogram сам ретраит getUpdates; один RST — не сбой продукта."""
    import logging

    h = _handler()
    assert h._build_alert(logging.LogRecord(
        "aiogram.dispatcher", logging.ERROR, "f", 1, RST, None, None
    )) is None
    h.emit(logging.LogRecord(
        "aiogram.dispatcher", logging.WARNING, "f", 1,
        "Sleep for 1.000000 seconds and try again... (tryings = 0, bot id = 1)",
        None, None,
    ))
    assert h._loop.create_task.call_count == 0


def test_серия_обрывов_telegram_без_восстановления_доходит():
    """Как CRM ReadTimeout: тревожим только после нескольких неудач подряд."""
    import logging

    import reporting

    h = _handler()
    record = logging.LogRecord("aiogram.dispatcher", logging.ERROR, "f", 1, RST, None, None)
    for _ in range(reporting.TRANSIENT_FAILURES_BEFORE_ALERT - 1):
        assert h._build_alert(record) is None

    text = h._build_alert(record)
    assert text is not None
    assert "Сбой связи с Telegram, бот сам переподключится" in text
    assert "TelegramNetworkError" not in text
    assert "ClientOSError" not in text
    assert "Errno 104" not in text


def test_обрывы_с_паузой_это_не_серия():
    """Восстановившийся polling начинает счётчик заново."""
    import logging

    h = _handler()
    record = logging.LogRecord("aiogram.dispatcher", logging.ERROR, "f", 1, RST, None, None)
    assert h._build_alert(record) is None
    h._transient_at -= 120
    assert h._build_alert(record) is None


def test_алерт_без_английских_исключений_и_traceback():
    import logging
    import sys

    import reporting

    assert reporting.humanize_for_chat(RST) == (
        "Сбой связи с Telegram, бот сам переподключится"
    )
    assert reporting.humanize_for_chat(
        "CRM не отвечает 5 опросов подряд: ReadTimeout('timed out')"
    ) == "CRM не отвечает 5 опросов подряд"
    flash = (
        'заявка 784311: CRM не сохранила статус «В пути»: '
        'У выбранного мастера уже есть заявка "В Пути"'
    )
    assert reporting.humanize_for_chat(flash) == flash
    assert reporting.humanize_for_chat(
        "Failed to fetch updates - TelegramRetryAfter: Flood control exceeded"
    ) == "Telegram просит подождать, бот продолжит сам"

    h = _handler()
    try:
        raise RuntimeError("HTTP Client says - boom")
    except RuntimeError:
        record = logging.LogRecord(
            "bot", logging.ERROR, "f", 1,
            "заявка 1: закрытие не записано в CRM", None, sys.exc_info(),
        )
    text = h._build_alert(record)
    assert text is not None
    assert "закрытие не записано в CRM" in text
    assert "Traceback" not in text
    assert "RuntimeError" not in text
    assert "HTTP Client" not in text


def test_болтовня_в_чате_не_идёт_в_отчёт():
    """В чате мастера сидят директор и админ — их реплики не ответы бота."""
    import asyncio
    from unittest.mock import AsyncMock, patch

    import bot as bot_module

    closure = {"id": 1}
    message = AsyncMock()
    message.reply_to_message = None

    with patch.object(bot_module.db, "closure_messages", AsyncMock(return_value=[42])):
        assert asyncio.run(bot_module._answers_question(message, closure)) is False


def test_ответ_на_вопрос_бота_засчитывается():
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch

    import bot as bot_module

    message = AsyncMock()
    message.reply_to_message = MagicMock(message_id=42)

    with patch.object(bot_module.db, "closure_messages", AsyncMock(return_value=[42])):
        assert asyncio.run(bot_module._answers_question(message, {"id": 1, "crm_id": 783812})) is True


def test_реплай_на_подсказку_ответить_засчитывается():
    """Вопрос и «Ответьте на это сообщение» должны оба приниматься как якорь."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch

    import bot as bot_module

    replied = MagicMock()
    replied.message_id = 900
    replied.text = "Закрытие заказа 783812\n\nСумма предоплаты?\n\n↩️ Ответьте на это сообщение."
    replied.caption = None
    replied.from_user = MagicMock(is_bot=True)
    replied.reply_to_message = None
    message = AsyncMock()
    message.reply_to_message = replied

    with patch.object(bot_module.db, "closure_messages", AsyncMock(return_value=[897])), \
         patch.object(bot_module.db, "remember_closure_message", AsyncMock()) as remember:
        assert asyncio.run(
            bot_module._answers_question(message, {"id": 3, "crm_id": 783812})
        ) is True
        remember.assert_awaited()


def test_реплай_на_свой_прошлый_ответ_засчитывается():
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch

    import bot as bot_module

    nested = MagicMock(message_id=900)
    replied = MagicMock(message_id=910, text="4000", caption=None, reply_to_message=nested)
    replied.from_user = MagicMock(is_bot=False)
    message = AsyncMock()
    message.reply_to_message = replied

    with patch.object(bot_module.db, "closure_messages", AsyncMock(return_value=[900])):
        assert asyncio.run(
            bot_module._answers_question(message, {"id": 3, "crm_id": 783812})
        ) is True


def test_число_на_предоплате_не_глотается_как_сумма_закрытия():
    """Именно это сломало 783812: «4000» попало в amount-хендлер и было отброшено."""
    import bot as bot_module

    step = bot_module._closing_step({"kind": closing.KIND_SD_OPEN, "step": "prepayment"})

    assert step is not None
    assert step.kind == "text"
    assert step.key == "comment"
