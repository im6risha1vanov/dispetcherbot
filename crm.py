import logging
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

import httpx
from selectolax.parser import HTMLParser, Node

import closing
import config
import db

log = logging.getLogger(__name__)

LOGIN_PATH = "/admin/login"
GRID_PATH = "/admin/domain/customer-request/index"
CARD_PATH = "/admin/domain/customer-request/update"
IMAGE_UPLOAD_PATH = "/admin/domain/customer-request/image-upload"

CSRF_PARAM_FALLBACK = "_csrf-frontend"
GRID_CELLS = 13
USER_AGENT = "bt-dispatch-bot/1.0"

FIELD_EMPLOYEE = "CustomerRequest[employee_id]"
FIELD_STATUS = "CustomerRequest[status]"
# Кнопка «Сохранить и закрыть»: без неё Yii только сохраняет черновик
# (суммы могут лечь, статус останется «В работе», 0 и отзыв — нет).
FIELD_SAVE_CLOSE = "save_close"

# CRM сама подтягивает эти поля следом за основным — их изменение ожидаемо
# и тревогой не является.
MIRRORED_FIELDS = {FIELD_EMPLOYEE: "_employee_id"}

# Этих ключей нет в HTML-форме (чекбокс отзыва рисуется JS, save_close
# вставляется onclick saveAndClose), но Yii их принимает в POST.
EXTRA_POST_FIELDS = {FIELD_SAVE_CLOSE, closing.FIELD_REQ_FBACK}

# Подписи option на живой карточке. Пустой «Выберите...» после POST — поля не легли.
SELECT_LABELS = {
    closing.FIELD_BSO: {"0": "Нет", "1": "Есть"},
    closing.FIELD_ZIP: {"0": "Нет", "1": "Есть"},
    closing.FIELD_FEEDBACK: {"1": "Да", "2": "Нет", "3": "Нет возможности"},
}

_IMAGE_UPLOAD_RE = re.compile(
    r"image-upload\?id=(?P<token>[^\"'\\&]+)(?:\\u0026|&amp;|&)target=(?P<target>images_\w+)"
)
_IMAGE_TARGET_RE = re.compile(r"\[(images_\w+)\]")

# Коды из раздела 4 контракта. Остальные статусы в карточке недоступны.
STATUS_WAITING = 1
STATUS_ENROUTE = 4
STATUS_IN_WORK = 8
STATUS_IN_WORK_SD = 9  # техника у мастера на сложной диагностике
STATUS_REFUSED = 64  # «Отказ»: кнопок сохранения нет, пока не вернуть в работу

_RECALL_RE = re.compile(r"\s*\(Отз\)\s*$")
_WS_RE = re.compile(r"\s+")
_DT_FORMATS = ("%d.%m.%y %H:%M", "%d.%m.%Y %H:%M", "%d.%m.%y", "%d.%m.%Y")

class CrmError(Exception):
    pass

class CrmAuthError(CrmError):
    pass

class CrmParseError(CrmError):
    pass

@dataclass(slots=True)
class RequestRow:
    crm_id: int
    opened_at: datetime | None
    req_type: str
    status_text: str
    is_recall: bool
    customer_name: str
    address: str
    master_name: str
    created_at_local: datetime | None
    closed_at_local: datetime | None
    total_cost: Decimal | None
    raw_cells: list[str]

@dataclass(slots=True, frozen=True)
class PriorOrder:
    """Строка из блока «История заказов клиента» в карточке заявки."""

    crm_id: int
    req_type: str
    status: str
    master_name: str

@dataclass(slots=True)
class RequestCard:
    crm_id: int
    fields: dict[str, str]
    postable: dict[str, str]  # только поля с name: readonly-поля в POST не уходят
    labels: dict[str, str]
    history: list[PriorOrder]
    image_uploads: dict[str, str]  # target images_* → URL krajee ajax-загрузки
    flashes: list[str]  # Yii alert после POST: отказ в смене статуса и т.п.
    employee_ids: frozenset[str] = frozenset()  # option value из селекта мастера

    @property
    def previous_master(self) -> str:
        """Кто делал прошлую работу у этого клиента — для гарантий и повторов.

        Берём ближайшую предыдущую заявку: история отсортирована по убыванию id,
        поэтому это первая строка старше текущей, у которой есть мастер.
        """
        for order in self.history:
            if order.crm_id < self.crm_id and order.master_name:
                return order.master_name
        return ""

    @property
    def payout(self) -> str:
        """Расчёт мастеру — как его показывает CRM. Бот ничего не считает сам."""
        return self.fields.get("__payout", "")

    @property
    def info_line(self) -> str:
        """Строка заявки ровно в том виде, в каком её копирует администратор.

        CRM держит основу в скрытом поле reqInfo, а хвосты дописывает скриптом
        при нажатии «Копировать информацию» — повторяем те же правила.
        """
        line = self.fields.get("reqInfo", "").strip()
        if not line:
            return ""
        if self.fields.get("__tIsColdCall") == "1":
            line += " (без прозвона)"
        if self.fields.get("__tIsNeedCallCheck") == "1":
            line += " (Прозвон перед выездом)"
        return line + " Попросить отзыв"

    @property
    def status_code(self) -> str:
        return self.fields.get("CustomerRequest[status]", "")

    @property
    def employee_id(self) -> str:
        return self.fields.get("CustomerRequest[employee_id]", "")

def _text(node: Node | None) -> str:
    if node is None:
        return ""
    return _WS_RE.sub(" ", node.text(deep=True)).strip()

def _parse_dt(raw: str) -> datetime | None:
    raw = raw.strip()
    if not raw:
        return None
    for fmt in _DT_FORMATS:
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=config.TIMEZONE)
        except ValueError:
            continue
    log.warning("не разобрал дату %r", raw)
    return None

def _parse_money(raw: str) -> Decimal | None:
    cleaned = re.sub(r"[^\d,.\-]", "", raw).replace(",", ".")
    if not cleaned or not any(ch.isdigit() for ch in cleaned):
        return None  # пустая ячейка или прочерк
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        log.warning("не разобрал сумму %r", raw)
        return None

def _parse_grid_row(tr: Node) -> RequestRow | None:
    key = tr.attributes.get("data-key") or ""
    if not key.isdigit():
        return None
    cells = [_text(td) for td in tr.css("td")]
    if len(cells) != GRID_CELLS:
        log.error(
            "заявка %s: ожидал %d ячеек, получил %d — разметка грида изменилась, строка пропущена",
            key, GRID_CELLS, len(cells),
        )
        return None

    status_raw = cells[6]
    return RequestRow(
        crm_id=int(key),
        opened_at=_parse_dt(cells[4]),
        req_type=cells[5],
        status_text=_RECALL_RE.sub("", status_raw).strip(),
        is_recall=bool(_RECALL_RE.search(status_raw)),
        customer_name=cells[7],
        address=cells[8],
        master_name=cells[9],
        created_at_local=_parse_dt(cells[10]),
        closed_at_local=_parse_dt(cells[11]),
        total_cost=_parse_money(cells[12]),
        raw_cells=cells,
    )

PAYOUT_ANCHOR = "Проведенная сумма по заявке"
_PAYOUT_RE = re.compile(
    r"Проведенная сумма по заявке:\s*(?P<total>.+?)\s+"
    r"Сумма к сдаче:\s*(?P<hand_over>.+?)\s+"
    r"Группа расчета:\s*(?P<group>.+?)\s*$"
)

def _parse_payout(html: str) -> str:
    """Блок расчёта появляется только на проведённой заявке."""
    start = html.find(PAYOUT_ANCHOR)
    if start < 0:
        return ""
    end = html.find("<hr>", start)
    fragment = html[start : end if end > 0 else start + 1000]

    text = HTMLParser(fragment).text(deep=True).replace("\xa0", " ")
    text = _WS_RE.sub(" ", text).strip()
    match = _PAYOUT_RE.search(text)
    if not match:
        log.warning("не разобрал блок расчёта: %r", text[:200])
        return ""
    return (
        f"Проведенная сумма по заявке: {match['total']}\n"
        f"Сумма к сдаче: {match['hand_over']}\n"
        f"Группа расчета: {match['group']}"
    )

HISTORY_GRID = "#customer-requests-grid"
HISTORY_CELLS = {"type": 1, "status": 5, "master": 6}

def _parse_history(tree: HTMLParser) -> list[PriorOrder]:
    """История заказов клиента: по ней гарантия возвращается прежнему мастеру."""
    grid = tree.css_first(HISTORY_GRID)
    if grid is None:
        return []

    orders = []
    for tr in grid.css("tbody tr[data-key]"):
        key = tr.attributes.get("data-key") or ""
        if not key.isdigit():
            continue
        cells = [_text(td) for td in tr.css("td")]
        if len(cells) <= HISTORY_CELLS["master"]:
            continue
        orders.append(
            PriorOrder(
                crm_id=int(key),
                req_type=cells[HISTORY_CELLS["type"]],
                status=cells[HISTORY_CELLS["status"]],
                master_name=cells[HISTORY_CELLS["master"]],
            )
        )
    return sorted(orders, key=lambda o: o.crm_id, reverse=True)

def _closed_status_text(form: Node) -> str:
    """На проведённой заявке статус — disabled input «Готов», часто без name и без обёртки."""
    wrap = form.css_first(".field-customerrequest-status")
    if wrap is not None and wrap.css_first("select[name='CustomerRequest[status]']"):
        return ""
    nodes = wrap.css("input") if wrap is not None else form.css("input[disabled]")
    for node in nodes:
        val = (node.attributes.get("value") or "").strip()
        if val.startswith("Готов"):
            return val
    return ""


def _parse_flashes(tree: HTMLParser) -> list[str]:
    """Сообщения Yii после POST: живут в сессии и всплывают на следующем GET."""
    found: list[str] = []
    seen: set[str] = set()
    for node in tree.css(".alert-danger, .alert-container .alert"):
        text = _WS_RE.sub(" ", node.text(deep=True)).strip()
        text = text.lstrip("×x ").strip()
        if text and text not in seen:
            seen.add(text)
            found.append(text)
    return found


def _is_conducted(card: RequestCard) -> bool:
    text = card.fields.get("__status_text") or card.labels.get(FIELD_STATUS) or ""
    if text.startswith("Готов"):
        return True
    return bool(card.payout)


def _parse_image_uploads(html: str) -> dict[str, str]:
    """Krajee FileInput грузит снимки отдельным AJAX, не через #customerRequestForm."""
    found: dict[str, str] = {}
    for match in _IMAGE_UPLOAD_RE.finditer(html):
        target = match["target"]
        found[target] = f"{IMAGE_UPLOAD_PATH}?id={match['token']}&target={target}"
    return found


def _image_target(field: str) -> str:
    match = _IMAGE_TARGET_RE.search(field)
    return match.group(1) if match else ""


def _classify_changes(
    before: dict[str, str], after: dict[str, str], intended: dict[str, str]
) -> tuple[list[str], list[str]]:
    """Сверка после POST: (чужие поля, задуманные поля которые не легли)."""
    foreign: list[str] = []
    unpersisted: list[str] = []
    for name, old in before.items():
        expected = intended.get(name, old)
        new = after.get(name)
        if new is None:
            continue  # поле исчезло из формы — так бывает при смене статуса
        if new == expected:
            continue
        msg = f"{name}: было {old!r}, ожидали {expected!r}, стало {new!r}"
        if name in intended:
            unpersisted.append(msg)
        else:
            foreign.append(msg)
    for name, expected in intended.items():
        if name in EXTRA_POST_FIELDS or name in before:
            continue
        new = after.get(name)
        if new != expected:
            unpersisted.append(
                f"{name}: не было в форме, ожидали {expected!r}, стало {new!r}"
            )
    return foreign, unpersisted


def _unpersisted_select_labels(after: RequestCard, intended: dict[str, str]) -> list[str]:
    """«Выберите...» после записи 0/2 — Yii не сохранила option, это не то же самое что 0."""
    missed: list[str] = []
    for name, expected in intended.items():
        wanted = SELECT_LABELS.get(name, {}).get(expected)
        if not wanted:
            continue
        got = after.labels.get(name) or ""
        if got != wanted:
            missed.append(f"{name}: подпись {got!r}, ожидали {wanted!r}")
    return missed


def _unexpected_changes(
    before: dict[str, str], after: dict[str, str], intended: dict[str, str]
) -> list[str]:
    """Совместимость для тестов: любые расхождения после записи."""
    foreign, unpersisted = _classify_changes(before, after, intended)
    return foreign + unpersisted

def _login_form_defaults(tree: HTMLParser) -> dict[str, str]:
    """Скрытые поля формы входа: у Yii их состав может меняться между версиями."""
    form = tree.css_first('form[action="/admin/login"]') or tree.css_first("form")
    if form is None:
        return {}
    return {
        name: node.attributes.get("value") or ""
        for node in form.css("input")
        if (name := node.attributes.get("name"))
    }

class CrmClient:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._client = httpx.AsyncClient(
            base_url=config.CRM_BASE_URL,
            timeout=config.CRM_TIMEOUT_SEC,
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT},
            transport=transport,
        )
        self._download_file = None  # ставит bot.py: фото живут в Telegram
        self._csrf_param = CSRF_PARAM_FALLBACK
        self._csrf_token = ""
        self._logged_in = False

    async def close(self) -> None:
        await self._client.aclose()

    async def login(self) -> None:
        config.validate([
            "CRM_USERNAME", "CRM_PASSWORD",
            "CRM_LOGIN_FIELD_USERNAME", "CRM_LOGIN_FIELD_PASSWORD",
        ])
        self._logged_in = False
        self._client.cookies.clear()

        page = await self._client.get(LOGIN_PATH)
        page.raise_for_status()
        tree = HTMLParser(page.text)
        self._read_csrf(tree)
        if not self._csrf_token:
            raise CrmAuthError("на /admin/login нет meta csrf-token — разметка входа изменилась")

        payload = _login_form_defaults(tree) | {
            self._csrf_param: self._csrf_token,
            config.CRM_LOGIN_FIELD_USERNAME: config.CRM_USERNAME,
            config.CRM_LOGIN_FIELD_PASSWORD: config.CRM_PASSWORD,
        }
        # «Запомнить меня» продлевает сессию: чем реже релогин, тем меньше шансов
        # разойтись с CRM на ровном месте.
        for name in payload:
            if "rememberme" in name.lower():
                payload[name] = "1"

        # Аутентификация, а не запись данных, поэтому не подчиняется CRM_READ_ONLY.
        resp = await self._client.post(LOGIN_PATH, data=payload)
        if resp.status_code >= 400:
            raise CrmAuthError(f"вход вернул HTTP {resp.status_code}")

        check = await self._client.get("/")
        check.raise_for_status()
        tree = HTMLParser(check.text)
        if tree.css_first(".navbar") is None:
            raise CrmAuthError(
                "вход не удался: после POST на /admin/login главная без .navbar. "
                "Проверьте CRM_LOGIN_FIELD_USERNAME / CRM_LOGIN_FIELD_PASSWORD и учётные данные"
            )
        self._read_csrf(tree)
        self._logged_in = True
        log.info("вход в CRM выполнен")

    async def fetch_open_requests(self) -> list[RequestRow]:
        """Грид без фильтра отдаёт только открытые заявки — это и есть рабочая выборка."""
        tree = await self._get_html(GRID_PATH, {"sort": "-id"})
        table = tree.css_first(".grid-view table.table__tr-link")
        if table is None:
            raise CrmParseError("не нашёл таблицу грида заявок — разметка CRM изменилась")
        rows = [row for tr in table.css("tbody tr[data-key]") if (row := _parse_grid_row(tr))]
        log.debug("грид отдал %d заявок", len(rows))
        return rows

    async def fetch_request_card(self, crm_id: int) -> RequestCard:
        tree = await self._get_html(CARD_PATH, {"id": crm_id})
        form = tree.css_first("#customerRequestForm")
        if form is None:
            raise CrmParseError(f"карточка {crm_id}: не нашёл #customerRequestForm")

        fields: dict[str, str] = {}
        postable: dict[str, str] = {}
        labels: dict[str, str] = {}
        employee_ids: set[str] = set()
        for node in form.css("input, select, textarea"):
            attrs = node.attributes
            input_type = (attrs.get("type") or "").lower()
            if input_type in ("submit", "button", "reset", "file"):
                continue
            # readonly-поля карточки идут без name, их ключ — id
            name = attrs.get("name")
            key = name or attrs.get("id")
            if not key or "csrf" in key.lower():
                continue

            if node.tag == "select":
                option = node.css_first("option[selected]")
                value = (option.attributes.get("value") or "") if option else ""
                labels[key] = _text(option)
                if name == FIELD_EMPLOYEE:
                    employee_ids = {
                        (opt.attributes.get("value") or "").strip()
                        for opt in node.css("option")
                        if (opt.attributes.get("value") or "").strip()
                    }
            elif node.tag == "textarea":
                value = (node.text(deep=True) or "").strip()
            elif input_type == "checkbox":
                value = "1" if "checked" in attrs else "0"
            else:
                value = attrs.get("value") or ""

            fields[key] = value
            if name and "disabled" not in attrs:
                postable[name] = value

        payout = _parse_payout(tree.html)
        if payout:
            fields["__payout"] = payout
        status_text = _closed_status_text(form)
        if status_text:
            fields["__status_text"] = status_text

        return RequestCard(
            crm_id=crm_id,
            fields=fields,
            postable=postable,
            labels=labels,
            history=_parse_history(tree),
            image_uploads=_parse_image_uploads(tree.html or ""),
            flashes=_parse_flashes(tree),
            employee_ids=frozenset(employee_ids),
        )

    async def assign_master(self, crm_id: int, employee_id: int) -> list[str]:
        return await self._update_request(crm_id, {FIELD_EMPLOYEE: str(employee_id)})

    async def set_status(self, crm_id: int, status_code: int) -> list[str]:
        return await self._update_request(crm_id, {FIELD_STATUS: str(status_code)})

    async def open_sd(
        self, crm_id: int, comment: str, photos: dict[str, list[str]] | None = None
    ) -> list[str]:
        """Перевод в СД: статус 9, расписка, комментарий филиала и срок сдачи."""
        ready_at = closing.parse_sd_ready_at(comment)
        if not ready_at:
            raise CrmError(
                f"заявка {crm_id}: в комментарии филиала нет срока сдачи ДД.ММ.ГГГГ"
            )
        files = await self._download_photos(photos or {})
        changes = {
            FIELD_STATUS: str(STATUS_IN_WORK_SD),
            closing.FIELD_COMMENT: comment,
        }
        async with db.request_lock(crm_id):
            card = await self.fetch_request_card(crm_id)
            if closing.FIELD_SD_READY_AT in card.postable:
                changes[closing.FIELD_SD_READY_AT] = ready_at
            damage = await self._update_locked(crm_id, changes, files=files)
            after = await self.fetch_request_card(crm_id)
            stored = after.postable.get(closing.FIELD_SD_READY_AT) or after.fields.get(
                closing.FIELD_SD_READY_AT
            )
            if stored != ready_at:
                if closing.FIELD_SD_READY_AT not in after.postable:
                    raise CrmError(
                        f"заявка {crm_id}: поле срока сдачи (Рекламная кампания) "
                        "нет на карточке после перевода в СД"
                    )
                extra = await self._update_locked(
                    crm_id, {closing.FIELD_SD_READY_AT: ready_at}
                )
                damage = list(damage or []) + list(extra or [])
                after = await self.fetch_request_card(crm_id)
                stored = after.postable.get(closing.FIELD_SD_READY_AT) or after.fields.get(
                    closing.FIELD_SD_READY_AT
                )
            if stored != ready_at:
                raise CrmError(
                    f"заявка {crm_id}: срок сдачи не записался: ждали {ready_at!r}, стало {stored!r}"
                )
            return damage

    async def close_remote(self, crm_id: int, fields: dict[str, str]) -> list[str]:
        """Гарантия решена звонком: заявка переводится в работу и проводится."""
        async with db.request_lock(crm_id):
            await self._update_locked(crm_id, {FIELD_STATUS: str(STATUS_IN_WORK)})
            return await self._close_locked(crm_id, fields)

    async def close_request(
        self, crm_id: int, fields: dict[str, str], photos: dict[str, list[str]] | None = None
    ) -> list[str]:
        """Закрытие: сначала «Сохранить и закрыть», потом проведение.

        На живой карточке «Провести заявку» не рисуется, пока не задана сумма
        («Проведение заявки не доступно, не задана сумма оплаченная клиентом»).
        Кнопка «Сохранить и закрыть» добавляет hidden save_close=1 — без него
        Yii не записывает 0/Нет/отзыв. finish=1 одним запросом поля не кладёт.
        Снимки — отдельный krajee image-upload, не #customerRequestForm.
        """
        files = await self._download_photos(photos or {})
        async with db.request_lock(crm_id):
            return await self._close_locked(crm_id, fields, files=files)

    async def _close_locked(
        self, crm_id: int, fields: dict[str, str], files: list | None = None
    ) -> list[str]:
        # На «Отказ» футер пустой: нет «Сохранить и закрыть», Yii игнорирует POST.
        # Сначала возвращаем в работу; если статус не сдвинулся — поля не запишутся.
        card = await self.fetch_request_card(crm_id)
        if card.status_code == str(STATUS_REFUSED):
            await self._update_locked(crm_id, {FIELD_STATUS: str(STATUS_IN_WORK)})
            reopened = await self.fetch_request_card(crm_id)
            if reopened.status_code == str(STATUS_REFUSED):
                raise CrmError(
                    f"заявка {crm_id}: статус «Отказ», CRM не возвращает в работу "
                    "(кнопок сохранения нет, POST игнорируется). Проведите вручную."
                )
        persist = dict(fields)
        persist[FIELD_SAVE_CLOSE] = "1"
        damage = await self._update_locked(crm_id, persist, files=files)
        damage.extend(await self._update_locked(crm_id, fields, finish=True))
        return damage

    async def _download_photos(self, photos: dict[str, list[str]]) -> list[tuple[str, tuple]]:
        photos = closing.as_dict(photos)
        if not photos or not self._download_file:
            return []

        files = []
        for kind, file_ids in photos.items():
            fields = closing.PHOTO_FIELDS.get(kind)
            if not fields:
                log.warning("неизвестный вид фото %r — пропускаю", kind)
                continue
            for index, file_id in enumerate(file_ids):
                content = await self._download_file(file_id)
                if not content:
                    continue
                # Один снимок может ждаться сразу в нескольких окнах карточки.
                for field in fields:
                    files.append((field, (f"{kind}_{index}.jpg", content, "image/jpeg")))
        return files

    async def _update_request(self, crm_id: int, changes: dict[str, str]) -> list[str]:
        """Read-modify-write под блокировкой: форма отправляется целиком."""
        async with db.request_lock(crm_id):
            return await self._update_locked(crm_id, changes)

    async def _update_locked(
        self, crm_id: int, changes: dict[str, str], files: list | None = None,
        finish: bool = False,
    ) -> list[str]:
        if config.CRM_WRITE_ONLY_FOR and str(crm_id) not in config.CRM_WRITE_ONLY_FOR:
            log.warning(
                "заявка %s вне списка CRM_WRITE_ONLY_FOR — запись не отправлена: %s",
                crm_id, changes,
            )
            return []

        card = await self.fetch_request_card(crm_id)
        if FIELD_EMPLOYEE in changes:
            wanted = changes[FIELD_EMPLOYEE]
            if wanted and wanted not in card.employee_ids:
                raise CrmError(
                    f"заявка {crm_id}: сотрудника {wanted} нет в списке мастеров CRM"
                )
        missing = [
            name for name in changes
            if name not in card.postable and name not in EXTRA_POST_FIELDS
        ]
        if missing:
            raise CrmError(
                f"заявка {crm_id}: поля {missing} недоступны для записи — "
                "скорее всего заявка закрыта, на закрытой заявке они disabled"
            )

        if files:
            await self._upload_images(crm_id, card, files)

        payload = dict(card.postable) | changes
        path = f"{CARD_PATH}?id={crm_id}"
        if finish:
            path += "&finish=1"
        await self._post_write(path, payload, changes)
        if config.CRM_READ_ONLY:
            return []

        # Сверка после записи: CRM принимает форму целиком, поэтому единственный
        # способ убедиться, что не задели соседние поля, — перечитать карточку.
        after = await self.fetch_request_card(crm_id)
        expected = dict(changes)
        for field, mirror in MIRRORED_FIELDS.items():
            if field in changes:
                expected[mirror] = changes[field]
        closing_write = finish or FIELD_SAVE_CLOSE in changes
        foreign, unpersisted = _classify_changes(card.postable, after.postable, expected)
        if closing_write:
            unpersisted.extend(_unpersisted_select_labels(after, expected))
        if unpersisted:
            flash = f" Yii: {after.flashes}" if after.flashes else ""
            log.error("заявка %s: задуманные поля не легли: %s%s", crm_id, unpersisted, flash)
            raise CrmError(
                f"заявка {crm_id}: CRM не сохранила задуманные поля: {unpersisted}{flash}"
            )
        if foreign:
            log.error("заявка %s: запись задела чужие поля: %s", crm_id, foreign)
        if finish and not _is_conducted(after):
            log.error(
                "заявка %s: POST finish=1 не провёл заявку (статус %s)",
                crm_id,
                after.fields.get("__status_text") or after.fields.get(FIELD_STATUS),
            )
            raise CrmError(f"заявка {crm_id}: CRM не провела заявку в «Готов»")
        return foreign + unpersisted

    async def _upload_images(
        self, crm_id: int, card: RequestCard, files: list[tuple[str, tuple]]
    ) -> None:
        """Фото в окна карточки: POST на image-upload, как krajee FileInput."""
        if config.CRM_READ_ONLY:
            log.warning("CRM_READ_ONLY=true — фото не отправлены: %s файлов", len(files))
            return
        if not card.image_uploads:
            raise CrmError(
                f"заявка {crm_id}: на карточке нет image-upload — фото некуда отправить"
            )
        if not self._csrf_token:
            raise CrmAuthError("нет csrf-токена для загрузки фото")

        for field, file_tuple in files:
            target = _image_target(field)
            url = card.image_uploads.get(target)
            if not url:
                raise CrmError(
                    f"заявка {crm_id}: нет URL загрузки для {target or field}"
                )
            resp = await self._client.post(
                url,
                data={self._csrf_param: self._csrf_token},
                files={field: file_tuple},
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
            if resp.status_code >= 400:
                raise CrmError(
                    f"заявка {crm_id}: image-upload {target} вернул HTTP {resp.status_code}"
                )
            try:
                body = resp.json()
            except ValueError:
                raise CrmError(
                    f"заявка {crm_id}: image-upload {target} ответил не JSON"
                ) from None
            err = body.get("error") if isinstance(body, dict) else None
            if err:
                raise CrmError(f"заявка {crm_id}: image-upload {target}: {err}")
            log.info("фото в CRM: заявка %s target=%s field=%s", crm_id, target, field)

    async def _post_write(
        self, path: str, payload: dict[str, str], changes: dict[str, str], files: list | None = None
    ) -> None:
        """Единственная точка записи полей формы в CRM (без файлов)."""
        if config.CRM_READ_ONLY:
            log.warning("CRM_READ_ONLY=true — запись не отправлена: POST %s, изменения %s", path, changes)
            return

        if not self._csrf_token:
            raise CrmAuthError("нет csrf-токена для записи")

        resp = await self._client.post(
            path,
            data=payload | {self._csrf_param: self._csrf_token},
            files=files or None,
        )
        if resp.status_code >= 400:
            raise CrmError(f"запись {path} вернула HTTP {resp.status_code}")
        if resp.is_redirect:
            loc = resp.headers.get("location") or ""
            log.info("записано в CRM: %s HTTP %s → %s изменения %s", path, resp.status_code, loc, changes)
            if "login" in loc.lower():
                raise CrmAuthError(f"запись {path} вернула редирект на вход: {loc}")
        else:
            log.info("записано в CRM: %s %s", path, changes)

    def _read_csrf(self, tree: HTMLParser) -> None:
        param = tree.css_first('meta[name="csrf-param"]')
        token = tree.css_first('meta[name="csrf-token"]')
        if param and param.attributes.get("content"):
            self._csrf_param = param.attributes["content"]
        if token and token.attributes.get("content"):
            self._csrf_token = token.attributes["content"]

    async def _get_html(self, path: str, params: dict | None = None) -> HTMLParser:
        if not self._logged_in:
            await self.login()

        tree = await self._fetch(path, params)
        if tree is None:
            log.info("сессия CRM потеряна, выполняю релогин")
            await self.login()
            tree = await self._fetch(path, params)
            if tree is None:
                raise CrmAuthError(f"после релогина {path} снова отдаёт разлогиненный ответ")
        return tree

    async def _fetch(self, path: str, params: dict | None) -> HTMLParser | None:
        """None означает разлогин: редирект на / или страница без .navbar."""
        resp = await self._client.get(path, params=params)
        if resp.is_redirect or resp.status_code in (401, 403):
            return None
        resp.raise_for_status()
        tree = HTMLParser(resp.text)
        if tree.css_first(".navbar") is None:
            return None
        self._read_csrf(tree)
        return tree
