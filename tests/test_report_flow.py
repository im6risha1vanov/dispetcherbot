"""Отчёт мастера целиком: от первого фото до проведения заявки в CRM.

Отчёт — это строка в таблице, а не состояние в памяти процесса. Поэтому
«перезапуск службы посреди анкеты» здесь моделируется честно: хранилище
переживает подмену всех обработчиков, и диалог продолжается с того же шага.
"""

import asyncio
import io
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import closing
import config
import messages
import photos
from crm import CrmError

KIM = 10679
KIM_CHAT = -4935665842
CRM_ID = 784223

ACTIVE = ("collecting", "pending_admin", "approved")


class Store:
    """Таблица closures в памяти: те же поля и та же уникальность активного отчёта."""

    def __init__(self):
        self.rows: dict[int, dict] = {}
        self.next_id = 1
        self.written: list[int] = []

    def open(self, crm_id, employee_id, chat_id, kind):
        for row in self.rows.values():
            if (
                row["crm_id"] == crm_id
                and row["employee_id"] == employee_id
                and row["state"] in ACTIVE
            ):
                return None
        row = {
            "id": self.next_id,
            "crm_id": crm_id,
            "employee_id": employee_id,
            "chat_id": chat_id,
            "kind": kind,
            "state": "collecting",
            "step": closing.FIRST_STEP_BY_KIND.get(kind, closing.FIRST_CLOSE_STEP),
            "payed_by_customer": None,
            "prepayment_sum": None,
            "spares_cost": None,
            "with_bso": None,
            "with_zip": None,
            "receipt_mode": None,
            "fback_mode": None,
            "branch_comment": None,
            "photos": {},
            "chat_messages": [],
            "question_message_id": None,
            "decider_messages": [],
            "conduct_attempts": 0,
            "conduct_error": None,
            "written_at": None,
            "reject_reason": None,
        }
        self.rows[row["id"]] = row
        self.next_id += 1
        return dict(row)

    def active(self, employee_id):
        live = [
            r for r in self.rows.values()
            if r["employee_id"] == employee_id and r["state"] in ("collecting", "pending_admin")
        ]
        return dict(live[-1]) if live else None

    def install(self, bot_module, stack):
        """Подменяет доступ к базе. Возвращает контекст, живущий на весь сценарий."""
        db = bot_module.db

        async def closure_by_id(cid):
            row = self.rows.get(cid)
            return dict(row) if row else None

        async def set_closure_step(cid, step):
            self.rows[cid]["step"] = step

        async def remember(cid, message_id, *, question=False):
            self.rows[cid]["chat_messages"].append(message_id)
            if question:
                self.rows[cid]["question_message_id"] = message_id

        async def add_photo(cid, kind, file_id):
            self.rows[cid]["photos"].setdefault(kind, []).append(file_id)
            return len(self.rows[cid]["photos"][kind])

        async def save_answer(cid, column, value, next_step):
            self.rows[cid][column] = value
            if next_step:
                self.rows[cid]["step"] = next_step

        async def submit(cid, copies):
            self.rows[cid]["state"] = "pending_admin"
            self.rows[cid]["decider_messages"] = [(str(c), int(m)) for c, m in copies]

        async def decider_messages(cid):
            return list(self.rows[cid]["decider_messages"])

        async def decide(cid, approved, reason="", decided_by=""):
            self.rows[cid]["state"] = "approved" if approved else "rejected"

        async def discard(cid, reason=""):
            self.rows[cid]["state"] = "discarded"
            self.rows[cid]["reject_reason"] = reason

        async def awaiting_conduct(cid):
            row = self.rows.get(cid)
            if row and row["state"] == "approved" and row["written_at"] is None:
                return dict(row)
            return None

        async def note_attempt(cid, error=""):
            self.rows[cid]["conduct_attempts"] += 1
            self.rows[cid]["conduct_error"] = error
            return self.rows[cid]["conduct_attempts"]

        async def mark_written(cid):
            self.rows[cid]["state"] = "written"
            self.rows[cid]["written_at"] = "сейчас"
            self.written.append(cid)

        patches = {
            "closure_by_id": closure_by_id,
            "set_closure_step": set_closure_step,
            "remember_closure_message": remember,
            "add_closure_photo": add_photo,
            "save_closure_answer": save_answer,
            "submit_closure": submit,
            "closure_decider_messages": decider_messages,
            "decide_closure": decide,
            "discard_closure": discard,
            "closure_awaiting_conduct": awaiting_conduct,
            "note_conduct_attempt": note_attempt,
            "mark_closure_written": mark_written,
            "open_closure": lambda *a: _ready(self.open(*a)),
            "active_closure": lambda eid: _ready(self.active(eid)),
            "closure_messages": lambda cid: _ready(list(self.rows[cid]["chat_messages"])),
            "clear_closure_messages": lambda cid: _ready(None),
            "collecting_closure_by_message": lambda mid: _ready(None),
            "collecting_closure_by_crm": lambda crm_id: _ready(None),
            "collecting_closures_in_chat": lambda chat: _ready([]),
            "master_by_employee": lambda eid: _ready({"full_name": "Габидуллин Ким"}),
            "master_by_telegram": lambda tid: _ready(
                {"employee_id": KIM, "full_name": "Габидуллин Ким",
                 "telegram_id": 111, "is_active": True}
            ),
            "claim_master_by_username": lambda name, tid: _ready(None),
            "get_state": lambda key: _ready(None),
        }
        for name, func in patches.items():
            stack.append(patch.object(db, name, func))
        for entered in stack:
            entered.__enter__()


def _ready(value):
    async def done():
        return value
    return done()


def _bot(monkeypatch):
    import bot as bot_module
    import roles

    monkeypatch.setattr(bot_module.config, "DIRECTOR_CHAT_ID", "dir")
    monkeypatch.setattr(bot_module.config, "ADMIN_CHAT_ID", "adm")
    monkeypatch.setattr(bot_module.config, "OWNER_CHAT_ID", "own")
    monkeypatch.setattr(bot_module.config, "TELEGRAM_TEST_CHAT_ID", "")
    monkeypatch.setattr(bot_module.config, "CRM_ALLOW_CLOSING", True)
    monkeypatch.setattr(config, "CLOSING_MAX_SUM", 1_000_000)
    roles.forget_admin_chat()
    return bot_module


def _tg():
    tg = AsyncMock()
    counter = {"n": 100}

    def sent(chat, *a, **kw):
        counter["n"] += 1
        return MagicMock(chat=MagicMock(id=chat), message_id=counter["n"])

    tg.send_message = AsyncMock(side_effect=sent)
    return tg


def _question_of(store, closure_id):
    """Сообщение бота с текущим вопросом — якорь для реплая."""
    replied = MagicMock(
        message_id=store.rows[closure_id]["question_message_id"],
        text="Закрытие заказа",
        caption=None,
    )
    replied.from_user = MagicMock(is_bot=True)
    replied.reply_to_message = None
    return replied


def _reply_message(bot_module, store, closure_id, text):
    """Сообщение мастера реплаем на текущий вопрос бота."""
    replied = _question_of(store, closure_id)

    message = AsyncMock()
    message.from_user = MagicMock(id=111, username="trentere")
    message.chat.id = KIM_CHAT
    message.text = text
    message.message_id = 900 + len(store.rows[closure_id]["chat_messages"])
    message.reply_to_message = replied
    message.bot = _tg()
    message.reply = AsyncMock(side_effect=lambda *a, **kw: MagicMock(message_id=777))
    return message


def _photo_message(store, closure_id, file_id, *, as_reply=True):
    message = AsyncMock()
    message.from_user = MagicMock(id=111, username="trentere")
    message.chat.id = KIM_CHAT
    message.photo = [MagicMock(file_id=file_id)]
    message.message_id = 800 + len(store.rows[closure_id]["photos"].get("bso", []))
    # Снимок засчитывается только реплаем на вопрос бота — как и текст.
    message.reply_to_message = _question_of(store, closure_id) if as_reply else None
    message.reply = AsyncMock(side_effect=lambda *a, **kw: MagicMock(message_id=778))
    message.bot = _tg()
    # Снимок бот забирает из Telegram сразу же, а не перед записью в CRM.
    message.bot.get_file = AsyncMock(return_value=MagicMock(file_path="tg/path.jpg"))
    message.bot.download_file = AsyncMock(
        return_value=io.BytesIO(f"снимок {file_id}".encode())
    )
    return message


class Run:
    """Один прогон анкеты: хранилище живёт дольше любого «процесса»."""

    def __init__(self, monkeypatch, tmp_path, kind=closing.KIND_CLOSE):
        self.bot = _bot(monkeypatch)
        monkeypatch.setattr(config, "PHOTO_DIR", str(tmp_path / "photos"))
        self.store = Store()
        self.stack = []
        self.store.install(self.bot, self.stack)
        self.tg = _tg()
        row = self.store.open(CRM_ID, KIM, KIM_CHAT, kind)
        self.id = row["id"]

    def close(self):
        for entered in reversed(self.stack):
            entered.__exit__(None, None, None)

    def step(self):
        return self.store.rows[self.id]["step"]

    async def ask_first(self):
        row = await self.bot.db.closure_by_id(self.id)
        await self.bot._ask_closing_step(
            self.tg, row, closing.FIRST_STEP_BY_KIND[row["kind"]]
        )

    async def photo(self, file_id="f1", *, as_reply=True):
        message = _photo_message(self.store, self.id, file_id, as_reply=as_reply)
        with patch.object(self.bot, "_message_closure", AsyncMock(
            return_value=await self.bot.db.closure_by_id(self.id)
        )):
            await self.bot.on_closing_photo(message)
        return message

    async def press_done(self):
        row = await self.bot.db.closure_by_id(self.id)
        await self.bot._advance_closing(self.tg, self.id)
        return row

    async def choose(self, value):
        row = await self.bot.db.closure_by_id(self.id)
        await self.bot.db.save_closure_answer(
            self.id, closing.answer_field(row["step"]), value, None
        )
        await self.bot._advance_closing(self.tg, self.id)

    async def answer(self, text):
        message = _reply_message(self.bot, self.store, self.id, text)
        with patch.object(self.bot, "_message_closure", AsyncMock(
            return_value=await self.bot.db.closure_by_id(self.id)
        )), patch.object(self.bot, "_answers_question", AsyncMock(return_value=True)):
            await self.bot.on_closing_answer(message)
        return message


def _walk_to_summary(run, *, with_zip: bool):
    """Проходит анкету до сводки так, как это делает живой мастер."""

    async def scenario():
        await run.ask_first()
        await run.photo("bso-1")
        await run.photo("bso-2")
        await run.press_done()              # docs_photo -> prepay
        await run.answer("1000")            # предоплата
        await run.answer("3500")            # сумма заявки, уже с предоплатой
        await run.answer("500" if with_zip else "0")   # стоимость ЗПЧ
        if with_zip:
            await run.photo("zip-1")
            await run.press_done()          # zip_photo -> feedback
        await run.choose("2")               # отзыв: нет

    asyncio.run(scenario())


def test_ветка_без_зпч_доходит_до_сводки(monkeypatch, tmp_path):
    run = Run(monkeypatch, tmp_path)
    try:
        _walk_to_summary(run, with_zip=False)
        row = run.store.rows[run.id]

        assert row["state"] == "pending_admin"
        assert row["payed_by_customer"] == Decimal("3500")
        assert row["prepayment_sum"] == Decimal("1000")
        assert row["spares_cost"] == Decimal("0")
        assert len(row["photos"]["bso"]) == 2
    finally:
        run.close()


def test_ветка_с_зпч_спрашивает_фото_и_сумму(monkeypatch, tmp_path):
    run = Run(monkeypatch, tmp_path)
    try:
        _walk_to_summary(run, with_zip=True)
        row = run.store.rows[run.id]

        assert row["state"] == "pending_admin"
        assert row["spares_cost"] == Decimal("500")
        assert len(row["photos"]["spare"]) == 1
        assert closing.crm_payload(row)[closing.FIELD_ZIP] == "1"
    finally:
        run.close()


def test_анкету_можно_прервать_на_любом_шаге(monkeypatch, tmp_path):
    """Перезапуск службы: обработчики новые, строка та же — шаг не теряется."""
    run = Run(monkeypatch, tmp_path)
    try:
        async def part_one():
            await run.ask_first()
            await run.photo("bso-1")
            await run.press_done()
            await run.answer("1000")

        asyncio.run(part_one())
        assert run.step() == "total"

        # «Процесс перезапустился»: всё, что знает бот, он читает из строки.
        async def part_two():
            await run.answer("4000")
            await run.answer("0")
            await run.choose("2")

        asyncio.run(part_two())
        row = run.store.rows[run.id]

        assert row["state"] == "pending_admin"
        assert row["prepayment_sum"] == Decimal("1000")
        assert row["payed_by_customer"] == Decimal("4000")
    finally:
        run.close()


def test_фото_мастера_удаляется_сразу(monkeypatch, tmp_path):
    run = Run(monkeypatch, tmp_path)
    try:
        dropped = []

        async def drop(bot, chat_id, message_id):
            dropped.append(message_id)
            return True

        with patch.object(run.bot.pinning, "drop_message", drop):
            async def scenario():
                await run.ask_first()
                message = await run.photo("bso-1")
                return message.message_id

            message_id = asyncio.run(scenario())

        assert dropped == [message_id], "снимок должен исчезать из чата мастера"

        saved = run.store.rows[run.id]["photos"]["bso"]
        assert len(saved) == 1
        assert photos.read(saved[0]) == "снимок bso-1".encode(), (
            "файл должен лежать на диске, а не только ссылкой в Telegram"
        )
    finally:
        run.close()


def test_мусор_вместо_суммы_не_проходит_молча(monkeypatch, tmp_path):
    run = Run(monkeypatch, tmp_path)
    try:
        async def scenario():
            await run.ask_first()
            await run.photo("bso-1")
            await run.press_done()
            message = await run.answer("где-то три тыщи")
            return message

        message = asyncio.run(scenario())
        row = run.store.rows[run.id]

        assert row["step"] == "prepay", "шаг не должен сдвинуться"
        assert row["prepayment_sum"] is None
        message.reply.assert_awaited()
        said = message.reply.await_args.args[0]
        assert "число в рублях" in said
    finally:
        run.close()


def test_отказ_сбрасывает_отчёт_целиком(monkeypatch, tmp_path):
    """Прежние фото не должны уехать в CRM вместе с исправленными."""
    run = Run(monkeypatch, tmp_path)
    try:
        _walk_to_summary(run, with_zip=False)
        old_id = run.id

        callback = AsyncMock()
        callback.message = AsyncMock()
        callback.message.chat.id = "adm"
        callback.message.message_id = 11
        callback.data = f"{messages.CB_CLOSE_NO}:{old_id}"
        callback.from_user = MagicMock(id=999, full_name="Админ")
        callback.bot = _tg()

        asyncio.run(run.bot._decide_closing(callback, AsyncMock(), approved=False))

        assert run.store.rows[old_id]["state"] == "discarded"
        fresh = [r for r in run.store.rows.values() if r["id"] != old_id]
        assert len(fresh) == 1
        assert fresh[0]["state"] == "collecting"
        assert fresh[0]["step"] == closing.FIRST_CLOSE_STEP
        assert fresh[0]["photos"] == {}, "фото отклонённого отчёта не переиспользуются"
    finally:
        run.close()


def _approve(run, crm):
    callback = AsyncMock()
    callback.message = AsyncMock()
    callback.message.chat.id = "adm"
    callback.message.message_id = 11
    callback.data = f"{messages.CB_CLOSE_OK}:{run.id}"
    callback.from_user = MagicMock(id=999, full_name="Админ")
    callback.bot = _tg()
    asyncio.run(run.bot._decide_closing(callback, crm, approved=True))
    return callback


def test_повторное_провести_не_проводит_дважды(monkeypatch, tmp_path):
    run = Run(monkeypatch, tmp_path)
    try:
        _walk_to_summary(run, with_zip=False)
        crm = AsyncMock()
        crm.close_request = AsyncMock(return_value=[])

        _approve(run, crm)
        assert crm.close_request.await_count == 1
        assert run.store.rows[run.id]["state"] == "written"

        # Администратор нажал ещё раз: отчёт уже не в pending_admin.
        second = _approve(run, crm)

        assert crm.close_request.await_count == 1
        second.answer.assert_awaited()
        assert "уже обработан" in str(second.answer.await_args)
    finally:
        run.close()


def test_падение_crm_сохраняет_отчёт_и_даёт_повтор(monkeypatch, tmp_path):
    run = Run(monkeypatch, tmp_path)
    try:
        _walk_to_summary(run, with_zip=False)
        crm = AsyncMock()
        crm.close_request = AsyncMock(
            side_effect=CrmError("заявка 784223: CRM не сохранила задуманные поля")
        )
        tg = _tg()

        with patch.object(run.bot, "_tell_supervisors", AsyncMock()) as told:
            callback = AsyncMock()
            callback.message = AsyncMock()
            callback.message.chat.id = "adm"
            callback.message.message_id = 11
            callback.data = f"{messages.CB_CLOSE_OK}:{run.id}"
            callback.from_user = MagicMock(id=999, full_name="Админ")
            callback.bot = tg
            asyncio.run(run.bot._decide_closing(callback, crm, approved=True))

        row = run.store.rows[run.id]
        assert row["state"] == "approved", "отчёт не должен теряться"
        assert row["written_at"] is None
        assert row["conduct_attempts"] == 1
        assert "не сохранила" in row["conduct_error"]

        told.assert_awaited()
        text = told.await_args.args[1]
        assert "не сохранила" in text, "администратор должен видеть причину"
        assert told.await_args.kwargs["markup"] is not None, "нужна кнопка повтора"

        master_said = " ".join(str(c) for c in tg.send_message.await_args_list)
        assert "заново не нужно" in master_said
    finally:
        run.close()


def test_повтор_после_сбоя_проводит_заявку(monkeypatch, tmp_path):
    run = Run(monkeypatch, tmp_path)
    try:
        _walk_to_summary(run, with_zip=False)
        crm = AsyncMock()
        crm.close_request = AsyncMock(side_effect=CrmError("CRM не провела заявку"))

        with patch.object(run.bot, "_tell_supervisors", AsyncMock()):
            _approve(run, crm)
        assert run.store.rows[run.id]["state"] == "approved"

        # CRM починилась — администратор жмёт «Повторить проведение».
        crm.close_request = AsyncMock(return_value=[])
        retry = AsyncMock()
        retry.message = AsyncMock()
        retry.message.chat.id = "adm"
        retry.data = f"{messages.CB_CLOSE_RETRY}:{run.id}"
        retry.from_user = MagicMock(id=999, full_name="Админ")
        retry.bot = _tg()
        asyncio.run(run.bot.on_conduct_retry(retry, crm))

        assert crm.close_request.await_count == 1
        assert run.store.rows[run.id]["state"] == "written"
    finally:
        run.close()


def test_повтор_по_проведённой_заявке_не_срабатывает(monkeypatch, tmp_path):
    run = Run(monkeypatch, tmp_path)
    try:
        _walk_to_summary(run, with_zip=False)
        crm = AsyncMock()
        crm.close_request = AsyncMock(return_value=[])
        _approve(run, crm)

        retry = AsyncMock()
        retry.message = AsyncMock()
        retry.message.chat.id = "adm"
        retry.data = f"{messages.CB_CLOSE_RETRY}:{run.id}"
        retry.from_user = MagicMock(id=999, full_name="Админ")
        retry.bot = _tg()
        asyncio.run(run.bot.on_conduct_retry(retry, crm))

        assert crm.close_request.await_count == 1, "второй записи быть не должно"
        assert "уже проведена" in str(retry.answer.await_args)
    finally:
        run.close()


def test_владелец_не_проводит_заявку(monkeypatch, tmp_path):
    run = Run(monkeypatch, tmp_path)
    try:
        _walk_to_summary(run, with_zip=False)
        crm = AsyncMock()

        callback = AsyncMock()
        callback.message = AsyncMock()
        callback.message.chat.id = "own"
        callback.data = f"{messages.CB_CLOSE_OK}:{run.id}"
        callback.from_user = MagicMock(id=1, full_name="Владелец")
        callback.bot = _tg()
        asyncio.run(run.bot._decide_closing(callback, crm, approved=True))

        crm.close_request.assert_not_called()
        assert run.store.rows[run.id]["state"] == "pending_admin"
    finally:
        run.close()


def test_фото_без_реплая_не_попадает_в_отчёт(monkeypatch, tmp_path):
    """Снимок «просто так» уходил в последний незавершённый отчёт и пропадал.

    В чате мастера он исчезал, в CRM не попадал никогда: отчёт был брошен
    неделю назад и завершать его никто не собирался.
    """
    run = Run(monkeypatch, tmp_path)
    try:
        async def scenario():
            await run.ask_first()
            message = await run.photo("случайное фото", as_reply=False)
            return message

        message = asyncio.run(scenario())
        row = run.store.rows[run.id]

        assert row["photos"] == {}, "снимок без реплая не должен попадать в отчёт"
        message.reply.assert_awaited()
        assert "реплаем" in message.reply.await_args.args[0]
    finally:
        run.close()
