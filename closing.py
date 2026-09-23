"""Сценарий закрытия заявки: бот спрашивает ровно то, что нужно карточке CRM.

Каждый вопрос соответствует полю формы, а ответ определяет, нужно ли фото и в
какое окно CRM оно ляжет. Поэтому администратору остаётся только подтвердить
полноту сведений — состав собран по самой форме, а не по памяти мастера.
"""
import json
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

import config

# Поля формы CRM, которые заполняет мастер при закрытии.
FIELD_PAYED = "CustomerRequest[payed_by_customer]"
FIELD_SPARES = "CustomerRequest[spares_cost]"
FIELD_PREPAY = "CustomerRequest[prepayment]"
FIELD_BSO = "CustomerRequest[with_bso]"
FIELD_ZIP = "CustomerRequest[with_zip]"
FIELD_RECEIPT = "CustomerRequest[receipt_mode]"
RECEIPT_NONE = "0"  # филиал работает без чека, мастера об этом не спрашиваем
FIELD_FEEDBACK = "CustomerRequest[fback_mode]"
# Чекбокс «нужен отзыв»: без него Yii прячет fback_mode (display:none) и
# на сохранении ставит 0, а в селекте нет option 0 — снова «Выберите...».
FIELD_REQ_FBACK = "CustomerRequest[is_req_fback]"

# Виды снимков и окна карточки, куда они ложатся. Подписи окон в CRM не
# совпадают с именами полей, поэтому раскладка задана явно:
#   images_main           — Договор / Чеки на услуги
#   images_check          — Чеки на комплектующие / расходы
#   images_spare          — Запчасть на фоне чека
#   images_safetyreceipt  — Сохранная расписка (только для СД)
#   images_feedback       — Отзыв
PHOTO_BSO = "bso"            # БСО и чек самозанятого
PHOTO_SPARE_CHECK = "spare"  # чек за запчасти
PHOTO_FEEDBACK = "feedback"
PHOTO_SAFETY = "safety"      # сохранная расписка и договор по СД

# Чек за запчасти CRM ждёт в двух окнах сразу — одинаковые снимки.
PHOTO_FIELDS = {
    PHOTO_BSO: ["CustomerRequest[images_main][]"],
    PHOTO_SPARE_CHECK: [
        "CustomerRequest[images_check][]",
        "CustomerRequest[images_spare][]",
    ],
    PHOTO_FEEDBACK: ["CustomerRequest[images_feedback][]"],
    PHOTO_SAFETY: ["CustomerRequest[images_safetyreceipt][]"],
}


@dataclass(frozen=True)
class Step:
    key: str
    question: str
    kind: str  # amount | choice | photo
    choices: tuple[tuple[str, str], ...] = ()
    photo_kind: str = ""
    required: bool = True
    multi: bool = False  # ждём несколько снимков и кнопку «Готово»


# Отзыв спрашивают и обычное закрытие, и закрытие СД — шаг один на двоих.
FEEDBACK_STEP = Step(
    "feedback",
    "Отзыв?",
    "choice",
    (("1", "Да"), ("2", "Нет"), ("3", "Нет возможности")),
)
FEEDBACK_PHOTO_STEP = Step(
    "feedback_photo",
    "Пришлите фото отзыва. Когда всё отправите — нажмите «Готово».",
    "photo",
    photo_kind=PHOTO_FEEDBACK,
    multi=True,
)

# Анкета закрытия заявки. Порядок задан мастером: сначала документы, пока он
# ещё у клиента и может переснять, суммы — в конце, когда всё посчитано.
#
# Отдельного вопроса «БСО есть?» нет: ответ виден по тому, прислал мастер фото
# или нажал «Готово» пустым. Лишний вопрос там, где ответ уже дан делом.
CLOSE_STEPS = {
    "docs_photo": Step(
        "docs_photo",
        "Загрузите фото БСО и чека СМЗ. Когда всё отправите — нажмите «Готово».",
        "photo",
        photo_kind=PHOTO_BSO,
        multi=True,
    ),
    "zip": Step("zip", "Есть ли ЗПЧ?", "choice", (("1", "Да"), ("0", "Нет"))),
    "zip_photo": Step(
        "zip_photo",
        "Загрузите фото ЗПЧ. Когда всё отправите — нажмите «Готово».",
        "photo",
        photo_kind=PHOTO_SPARE_CHECK,
        multi=True,
    ),
    "receipt": Step(
        "receipt",
        "Режим для чека",
        "choice",
        (("0", "Без чека"), ("5", "Чек взято всего"), ("10", "Чек чистыми")),
    ),
    "prepay": Step("prepay", "Предоплата? Сумма в рублях, если её не было — 0.", "amount"),
    "total": Step("total", "Сумма заявки? Число в рублях.", "amount"),
    "zip_sum": Step("zip_sum", "Сумма ЗПЧ? Число в рублях.", "amount"),
    "feedback": FEEDBACK_STEP,
    "feedback_photo": FEEDBACK_PHOTO_STEP,
}

# Живая карточка: label «Комментарий филиала», textarea name=...[recommendation_comment].
# Кнопка «Добавить комментарий» — JS-модалка в то же поле, отдельного input нет.
FIELD_COMMENT = "CustomerRequest[recommendation_comment]"
# Krajee datepicker с подписью «Рекламная кампания» (не disabled название кампании).
FIELD_SD_READY_AT = "CustomerRequest[work_in_sd_ready_at]"

SD_COMMENT_QUESTION = (
    "Комментарий филиала\n\n"
    "Заполните комментарий по следующему скрипту:\n"
    "- неисправность\n"
    "- *сумма предоплаты*\n"
    "- срок сдачи *строго по формату 00.00.2026 "
    '(без "до", "в конце недели", "завтра" и т.д.)*'
)

SD_COMMENT_REJECT = (
    "Третья строка — только дата сдачи, например 19.09.2026.\n"
    "Без «до», «завтра», «в конце недели»."
)

_SD_DATE = re.compile(r"(?<!\d)(\d{2})\.(\d{2})\.(\d{4})(?!\d)")
_SD_FORBIDDEN = re.compile(
    r"(?i)(?<!\w)до(?!\w)|завтра|в\s+конце\s+недели|на\s+неделе|сегодня|потом"
)


def parse_sd_ready_at(text: str) -> str | None:
    """Третья строка комментария → dd-mm-yyyy для work_in_sd_ready_at, иначе None."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if len(lines) < 3:
        return None
    line = lines[2]
    if _SD_FORBIDDEN.search(line):
        return None
    found = _SD_DATE.findall(line)
    if len(found) != 1:
        return None
    day, month, year = found[0]
    try:
        parsed = datetime.strptime(f"{day}.{month}.{year}", "%d.%m.%Y")
    except ValueError:
        return None
    if parsed.year < 2026 or parsed.year > 2035:
        return None
    return f"{day}-{month}-{year}"


# Открытие сложной диагностики: расписка и комментарий филиала (третья строка — срок).
# Предоплату в отдельное поле не пишем.
SD_STEPS = {
    "safety_photo": Step(
        "safety_photo",
        "Пришлите фото сохранной расписки. Когда всё отправите — нажмите «Готово».",
        "photo",
        photo_kind=PHOTO_SAFETY,
        multi=True,
    ),
    "comment": Step("comment", SD_COMMENT_QUESTION, "text"),
}

# Закрытие СД: документы вместо БСО, поэтому про БСО не спрашиваем.
# Анкета осталась прежней — её порядок мастера уже знают, а техника у них
# на руках неделями: менять вопросы на полпути незачем.
SD_CLOSE_STEPS = {
    "payed": Step("payed", "Сколько оплатил клиент? Итоговая сумма, включая предоплату.", "amount"),
    "spares": Step("spares", "Стоимость комплектующих? Если не было — 0.", "amount"),
    "spare_photo": Step(
        "spare_photo",
        "Пришлите фото чека на запчасти. Когда всё отправите — нажмите «Готово».",
        "photo",
        photo_kind=PHOTO_SPARE_CHECK,
        multi=True,
    ),
    "safety_photo": Step(
        "safety_photo",
        "Пришлите фото договора сложной диагностики и чека самозанятого. "
        "Когда всё отправите — нажмите «Готово».",
        "photo",
        photo_kind=PHOTO_SAFETY,
        multi=True,
    ),
    "feedback": FEEDBACK_STEP,
    "feedback_photo": FEEDBACK_PHOTO_STEP,
}

KIND_CLOSE = "close"
KIND_SD_OPEN = "sd_open"
KIND_SD_CLOSE = "sd_close"
KIND_REMOTE = "remote"  # гарантия решена звонком: вопросов нет, всё по нулям

STEPS_BY_KIND = {
    KIND_CLOSE: CLOSE_STEPS,
    KIND_SD_OPEN: SD_STEPS,
    KIND_SD_CLOSE: SD_CLOSE_STEPS,
    KIND_REMOTE: {},
}

FIRST_CLOSE_STEP = "docs_photo"

FIRST_STEP_BY_KIND = {
    KIND_CLOSE: FIRST_CLOSE_STEP,
    KIND_SD_OPEN: "safety_photo",
    KIND_SD_CLOSE: "payed",
}

FIRST_STEP = FIRST_CLOSE_STEP


SD_ORDER = ["safety_photo", "comment"]
# Старые вопросы срока/суммы/неисправности: незавершённый отчёт после выкладки
# сразу переводим на один комментарий, а не продолжаем цепочку.
LEGACY_SD_OPEN_STEPS = frozenset({"prepayment", "agreed_sum", "deadline", "malfunction"})
# Прежняя анкета закрытия (суммы первыми). Отчёт, застигнутый выкладкой на
# середине, начинаем заново: порядок вопросов изменился, и продолжать с
# середины — значит спросить не то и не в том порядке.
LEGACY_CLOSE_STEPS = frozenset({"payed", "spares", "spare_photo", "bso", "bso_photo"})


def canonical_step(kind: str, step: str) -> str:
    if kind == KIND_SD_OPEN and step in LEGACY_SD_OPEN_STEPS:
        return "comment"
    if kind == KIND_CLOSE and step in LEGACY_CLOSE_STEPS:
        return FIRST_CLOSE_STEP
    return step


def next_step_for(kind: str, step: str, answers) -> str | None:
    if kind == KIND_SD_OPEN:
        if step in LEGACY_SD_OPEN_STEPS:
            return "comment"
        index = SD_ORDER.index(step)
        return SD_ORDER[index + 1] if index + 1 < len(SD_ORDER) else None
    if kind == KIND_SD_CLOSE:
        return _next_sd_close(step, answers)
    return next_step(step, answers)


def _next_sd_close(step: str, answers) -> str | None:
    """То же, что обычное закрытие, но документы вместо БСО — и без вопроса о нём."""
    if step == "payed":
        return "spares"
    if step == "spares":
        return "spare_photo" if _positive(answers.get("spares_cost")) else "safety_photo"
    if step == "spare_photo":
        return "safety_photo"
    if step == "safety_photo":
        return "feedback"
    if step == "feedback":
        return "feedback_photo" if answers.get("fback_mode") == "1" else None
    return None


def next_step(step: str, answers) -> str | None:
    """Следующий вопрос закрытия. Про ЗПЧ спрашиваем только если они были."""
    if step in LEGACY_CLOSE_STEPS:
        return FIRST_CLOSE_STEP
    if step == "docs_photo":
        return "zip"
    if step == "zip":
        # Купил запчасти — чек обязателен, иначе трата не подтверждена.
        return "zip_photo" if _has_zip(answers) else "receipt"
    if step == "zip_photo":
        return "receipt"
    if step == "receipt":
        return "prepay"
    if step == "prepay":
        return "total"
    if step == "total":
        return "zip_sum" if _has_zip(answers) else "feedback"
    if step == "zip_sum":
        return "feedback"
    if step == "feedback":
        return "feedback_photo" if answers.get("fback_mode") == "1" else None
    return None  # feedback_photo — последний


def _has_zip(answers) -> bool:
    return str(_row_get(answers, "with_zip") or "") == "1"


def _positive(value) -> bool:
    try:
        return float(value or 0) > 0
    except (TypeError, ValueError):
        return False


# Сумма: цифры, пробелы как разделители тысяч, не больше двух знаков после
# запятой. Минус не проходит регуляркой — отрицательной суммы не бывает.
_AMOUNT = re.compile(r"^\s*(\d[\d \u00a0]*?)(?:[.,](\d{1,2}))?\s*$")

AMOUNT_HINT = "Нужно число в рублях, без слов. Например: 3500 или 3500,50"


def parse_amount(text: str) -> tuple[Decimal | None, str]:
    """Сумма из ответа мастера либо причина отказа простыми словами.

    Молчать на «где-то три тыщи» нельзя: мастер решит, что ответ принят,
    и уйдёт с объекта. Поэтому на всё непонятное отвечаем и просим ещё раз.
    """
    match = _AMOUNT.match(text or "")
    if match is None:
        return None, AMOUNT_HINT

    digits = re.sub(r"[ \u00a0]", "", match.group(1))
    kopecks = match.group(2)
    try:
        value = Decimal(digits + ("." + kopecks if kopecks else ""))
    except InvalidOperation:
        return None, AMOUNT_HINT

    if value > config.CLOSING_MAX_SUM:
        return None, (
            f"Сумма больше {int(config.CLOSING_MAX_SUM)} р. — похоже на опечатку. "
            "Проверьте и пришлите ещё раз."
        )
    return value, ""


def answer_field(step: str) -> str:
    return {
        # анкета закрытия
        "zip": "with_zip",
        "receipt": "receipt_mode",
        "prepay": "prepayment_sum",
        "total": "payed_by_customer",
        "zip_sum": "spares_cost",
        # закрытие СД и старые отчёты
        "payed": "payed_by_customer",
        "spares": "spares_cost",
        "bso": "with_bso",
        "feedback": "fback_mode",
        "comment": "branch_comment",
        "prepayment": "prepayment",
        "agreed_sum": "agreed_sum",
        "deadline": "deadline",
        "malfunction": "malfunction",
    }[step]


def _row_get(row, key):
    try:
        return row[key]
    except (KeyError, TypeError, IndexError):
        return None


def sd_comment(row) -> str:
    """Текст мастера в «Комментарий филиала» как есть, без склейки полей."""
    text = _row_get(row, "branch_comment")
    if text:
        return str(text)
    # Незавершённые отчёты со старой анкетой — покажем то, что уже собрали.
    if any(_row_get(row, key) for key in ("prepayment", "agreed_sum", "deadline", "malfunction")):
        return (
            f"Предоплата: {_row_get(row, 'prepayment') or '—'}\n"
            f"Согласованная сумма: {_row_get(row, 'agreed_sum') or '—'}\n"
            f"Сроки сдачи: {_row_get(row, 'deadline') or '—'}\n"
            f"Неисправность: {_row_get(row, 'malfunction') or '—'}"
        )
    return ""


REMOTE_PAYLOAD = {
    FIELD_PAYED: "0",
    FIELD_SPARES: "",
    FIELD_BSO: "0",
    FIELD_ZIP: "0",
    FIELD_RECEIPT: RECEIPT_NONE,
}


def has_bso(row) -> bool:
    """БСО выписан, если мастер прислал хоть один снимок документов.

    Отдельного вопроса нет намеренно: ответ уже дан делом, а лишний шаг в
    анкете — лишний повод ошибиться.
    """
    return bool(as_dict(_row_get(row, "photos")).get(PHOTO_BSO))


def crm_payload(row) -> dict[str, str]:
    """Ответы мастера в виде полей формы CRM."""
    if row["kind"] == KIND_REMOTE:
        # Выезда не было: ни денег, ни БСО, ни комплектующих.
        return dict(REMOTE_PAYLOAD)

    payload = {
        # Ноль шлём как "0": без суммы Yii не проводит заявку. HTML GET при этом
        # часто value="" — сверка в crm._same_value считает пустое и 0 одним.
        FIELD_PAYED: _num(row["payed_by_customer"]),
        # Yii2 number input с 0 рисует value="" (как на закрытых карточках
        # без комплектующих). Шлём пустую строку, иначе сверка ждёт «0».
        FIELD_SPARES: _num(row["spares_cost"]) if _positive(row["spares_cost"]) else "",
    }

    if row["kind"] == KIND_CLOSE:
        payload[FIELD_BSO] = "1" if has_bso(row) else "0"
        prepay = _row_get(row, "prepayment_sum")
        payload[FIELD_PREPAY] = _num(prepay) if _positive(prepay) else ""
        # Наличие ЗПЧ спрашиваем прямо: мастер мог поставить свою запчасть
        # и не потратить ни рубля, из суммы это не выводится.
        payload[FIELD_ZIP] = "1" if _has_zip(row) else "0"
        payload[FIELD_RECEIPT] = str(_row_get(row, "receipt_mode") or RECEIPT_NONE)
    else:
        # Закрытие СД: про БСО не спрашивали, режим чека там всегда без чека.
        if row["with_bso"] is not None:
            payload[FIELD_BSO] = str(row["with_bso"])
        payload[FIELD_ZIP] = "1" if _positive(row["spares_cost"]) else "0"
        payload[FIELD_RECEIPT] = RECEIPT_NONE

    if row["fback_mode"] is not None:
        payload[FIELD_FEEDBACK] = str(row["fback_mode"])
    if payload.get(FIELD_FEEDBACK) in {"1", "2", "3"}:
        payload[FIELD_REQ_FBACK] = "1"
    return payload


def _num(value) -> str:
    if value is None:
        return "0"
    number = float(value)
    return str(int(number)) if number.is_integer() else str(number)


def as_dict(value) -> dict:
    """asyncpg без кодека отдаёт jsonb строкой — сводка и выгрузка ждут dict."""
    if not value:
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def summary(row, master_name: str) -> str:
    """Сводка для администратора: он подтверждает полноту сведений."""
    photos = as_dict(row["photos"])
    photo_note = ", ".join(
        f"{kind}: {len(items)}" for kind, items in photos.items() if items
    ) or "нет"

    if row["kind"] == KIND_REMOTE:
        return (
            f"📞 Гарантия решена дистанционно, заказ {row['crm_id']}\n"
            f"Мастер: {master_name}\n\n"
            "Выезда не было. Сумма 0, БСО нет, комплектующих нет."
        )

    if row["kind"] == KIND_SD_OPEN:
        return (
            f"📦 Сложная диагностика по заказу {row['crm_id']}\n"
            f"Мастер: {master_name}\n\n"
            f"{sd_comment(row)}\n\n"
            f"Фото: {photo_note}"
        )

    title = "📋 Отчёт" if row["kind"] == KIND_CLOSE else "📋 Отчёт по СД"
    return _closing_summary(row, master_name, title, photo_note)


def report_warnings(row) -> list[str]:
    """Странности, о которых администратор должен знать до нажатия «Провести».

    Ни одна из них не блокирует проведение: так бывает, и решать человеку.
    """
    found = []
    if row["kind"] != KIND_CLOSE:
        return found

    total = _amount(row["payed_by_customer"])
    spares = _amount(row["spares_cost"])
    prepay = _amount(_row_get(row, "prepayment_sum"))

    if spares > total:
        found.append(f"ЗПЧ дороже заявки: {_num(spares)} р. против {_num(total)} р.")
    if prepay > total:
        found.append(f"Предоплата больше суммы заявки: {_num(prepay)} р. против {_num(total)} р.")
    if _has_zip(row) and not spares:
        found.append("ЗПЧ отмечены, а сумма по ним нулевая")
    if not has_bso(row):
        found.append("Фото БСО и чека СМЗ не приложены — в CRM уйдёт «БСО: нет»")
    return found


def _amount(value) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except InvalidOperation:
        return Decimal(0)


def _closing_summary(row, master_name: str, title: str, photo_note: str) -> str:
    if row["kind"] != KIND_CLOSE:
        return _sd_close_summary(row, master_name, title, photo_note)

    receipt = {"0": "без чека", "5": "чек взято всего", "10": "чек чистыми"}
    feedback = {"1": "да", "2": "нет", "3": "нет возможности"}
    zip_line = "есть" if _has_zip(row) else "нет"
    if _has_zip(row):
        zip_line += f", {_num(row['spares_cost'])} р."

    lines = [
        f"{title} по заказу {row['crm_id']}",
        f"Мастер: {master_name}",
        "",
        f"Сумма заявки: {_num(row['payed_by_customer'])} р.",
        f"Предоплата: {_num(_row_get(row, 'prepayment_sum'))} р.",
        f"ЗПЧ: {zip_line}",
        f"Режим чека: {receipt.get(str(_row_get(row, 'receipt_mode') or '0'), '—')}",
        f"БСО: {'есть' if has_bso(row) else 'нет'}",
        f"Отзыв: {feedback.get(row['fback_mode'], '—')}",
        f"Фото: {photo_note}",
    ]
    alarms = report_warnings(row)
    if alarms:
        lines.append("")
        lines.extend(f"⚠️ {text}" for text in alarms)
    return "\n".join(lines)


def _sd_close_summary(row, master_name: str, title: str, photo_note: str) -> str:
    choice = {
        "with_bso": {"1": "есть", "0": "нет"},
        "fback_mode": {"1": "да", "2": "нет", "3": "нет возможности"},
    }

    return (
        f"{title} по заказу {row['crm_id']}\n"
        f"Мастер: {master_name}\n\n"
        f"Оплачено клиентом: {_num(row['payed_by_customer'])} р.\n"
        f"Комплектующие: {_num(row['spares_cost'])} р.\n"
        f"БСО: {choice['with_bso'].get(row['with_bso'], '—')}\n"
        f"Отзыв: {choice['fback_mode'].get(row['fback_mode'], '—')}\n"
        f"Фото: {photo_note}"
    )
