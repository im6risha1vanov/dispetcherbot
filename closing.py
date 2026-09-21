"""Сценарий закрытия заявки: бот спрашивает ровно то, что нужно карточке CRM.

Каждый вопрос соответствует полю формы, а ответ определяет, нужно ли фото и в
какое окно CRM оно ляжет. Поэтому администратору остаётся только подтвердить
полноту сведений — состав собран по самой форме, а не по памяти мастера.
"""
import json
import re
from dataclasses import dataclass
from datetime import datetime

# Поля формы CRM, которые заполняет мастер при закрытии.
FIELD_PAYED = "CustomerRequest[payed_by_customer]"
FIELD_SPARES = "CustomerRequest[spares_cost]"
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


STEPS = {
    "payed": Step("payed", "Сколько оплатил клиент? Сумма в рублях.", "amount"),
    "spares": Step("spares", "Стоимость комплектующих? Если не было — 0.", "amount"),
    "spare_photo": Step(
        "spare_photo",
        "Пришлите фото чека на запчасти. Когда всё отправите — нажмите «Готово».",
        "photo",
        photo_kind=PHOTO_SPARE_CHECK,
        multi=True,
    ),
    "bso": Step(
        "bso", "БСО есть?", "choice", (("1", "Есть"), ("0", "Нет")),
    ),
    "bso_photo": Step(
        "bso_photo",
        "Пришлите фото БСО и чека самозанятого. Когда всё отправите — нажмите «Готово».",
        "photo",
        photo_kind=PHOTO_BSO,
        multi=True,
    ),
    "feedback": Step(
        "feedback",
        "Отзыв?",
        "choice",
        (("1", "Да"), ("2", "Нет"), ("3", "Нет возможности")),
    ),
    "feedback_photo": Step(
        "feedback_photo",
        "Пришлите фото отзыва. Когда всё отправите — нажмите «Готово».",
        "photo",
        photo_kind=PHOTO_FEEDBACK,
        multi=True,
    ),
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
SD_CLOSE_STEPS = {
    "payed": Step("payed", "Сколько оплатил клиент? Итоговая сумма, включая предоплату.", "amount"),
    "spares": STEPS["spares"],
    "spare_photo": STEPS["spare_photo"],
    "safety_photo": Step(
        "safety_photo",
        "Пришлите фото договора сложной диагностики и чека самозанятого. "
        "Когда всё отправите — нажмите «Готово».",
        "photo",
        photo_kind=PHOTO_SAFETY,
        multi=True,
    ),
    "feedback": STEPS["feedback"],
    "feedback_photo": STEPS["feedback_photo"],
}

KIND_CLOSE = "close"
KIND_SD_OPEN = "sd_open"
KIND_SD_CLOSE = "sd_close"
KIND_REMOTE = "remote"  # гарантия решена звонком: вопросов нет, всё по нулям

STEPS_BY_KIND = {
    KIND_CLOSE: STEPS,
    KIND_SD_OPEN: SD_STEPS,
    KIND_SD_CLOSE: SD_CLOSE_STEPS,
    KIND_REMOTE: {},
}

FIRST_STEP_BY_KIND = {
    KIND_CLOSE: "payed",
    KIND_SD_OPEN: "safety_photo",
    KIND_SD_CLOSE: "payed",
}

FIRST_STEP = "payed"


SD_ORDER = ["safety_photo", "comment"]
# Старые вопросы срока/суммы/неисправности: незавершённый отчёт после выкладки
# сразу переводим на один комментарий, а не продолжаем цепочку.
LEGACY_SD_OPEN_STEPS = frozenset({"prepayment", "agreed_sum", "deadline", "malfunction"})


def canonical_step(kind: str, step: str) -> str:
    if kind == KIND_SD_OPEN and step in LEGACY_SD_OPEN_STEPS:
        return "comment"
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
    """Следующий вопрос. Фото просим только там, где ответ этого требует."""
    if step == "payed":
        return "spares"
    if step == "spares":
        # Купил запчасти — чек обязателен, иначе трата не подтверждена.
        return "spare_photo" if _positive(answers.get("spares_cost")) else "bso"
    if step == "spare_photo":
        return "bso"
    if step == "bso":
        return "bso_photo" if answers.get("with_bso") == "1" else "feedback"
    if step == "bso_photo":
        return "feedback"
    if step == "feedback":
        return "feedback_photo" if answers.get("fback_mode") == "1" else None
    return None  # feedback_photo — последний


def _positive(value) -> bool:
    try:
        return float(value or 0) > 0
    except (TypeError, ValueError):
        return False


def answer_field(step: str) -> str:
    return {
        "payed": "payed_by_customer",
        "spares": "spares_cost",
        "bso": "with_bso",
        "receipt": "receipt_mode",
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
    for field, column in (
        (FIELD_BSO, "with_bso"),
        (FIELD_FEEDBACK, "fback_mode"),
    ):
        if row[column] is not None:
            payload[field] = str(row[column])

    if payload.get(FIELD_FEEDBACK) in {"1", "2", "3"}:
        payload[FIELD_REQ_FBACK] = "1"

    # Эти поля мастеру не показываем: использование комплектующих следует из
    # суммы, а режим чека в филиале всегда «Без чека».
    payload[FIELD_ZIP] = "1" if _positive(row["spares_cost"]) else "0"
    payload[FIELD_RECEIPT] = RECEIPT_NONE
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


def _closing_summary(row, master_name: str, title: str, photo_note: str) -> str:
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
