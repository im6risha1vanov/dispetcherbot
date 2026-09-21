import asyncio

import httpx
import pytest

import closing
import config
from crm import STATUS_ENROUTE, STATUS_IN_WORK, FIELD_STATUS, CrmClient, CrmError, _unexpected_changes

CSRF = '<meta name="csrf-param" content="_csrf-frontend"><meta name="csrf-token" content="tok123">'

CARD = f"""<html><head>{CSRF}</head><body><nav class="navbar"></nav>
<form id="customerRequestForm">
  <input type="hidden" name="_csrf-frontend" value="tok123">
  <input type="hidden" name="CustomerRequest[origin]" value="web">
  <input type="hidden" name="_employee_id" value="">
  <input name="CustomerRequest[payed_by_customer]" value="0">
  <select name="CustomerRequest[employee_id]">
    <option value="" selected>Выберите</option>
    <option value="10679">Габидуллин Ким (Сык) (650)</option>
  </select>
  <select name="CustomerRequest[status]">
    <option value="1" selected>Ожидает</option>
    <option value="4">В пути</option>
  </select>
  <input id="customerrequest-technique" value="Стиральная машина" readonly>
</form></body></html>"""

CLOSED_CARD = CARD.replace(
    '<select name="CustomerRequest[employee_id]">',
    '<select name="CustomerRequest[employee_id]" disabled>',
)

ASSIGNED_CARD = CARD.replace(
    '<option value="" selected>Выберите</option>',
    '<option value="">Выберите</option>',
).replace(
    '<option value="10679">Габидуллин Ким (Сык) (650)</option>',
    '<option value="10679" selected>Габидуллин Ким (Сык) (650)</option>',
).replace(
    '<input type="hidden" name="_employee_id" value="">',
    '<input type="hidden" name="_employee_id" value="10679">',
)

ENROUTE_CARD = CARD.replace(
    '<option value="1" selected>Ожидает</option>',
    '<option value="4" selected>В пути</option>',
)

# Живая незаполненная карточка: number value="", селекты без selected
# (на экране «Выберите...»), чекбокса is_req_fback в HTML нет.
CLOSE_CARD = CARD.replace(
    '<input name="CustomerRequest[payed_by_customer]" value="0">',
    """<input name="CustomerRequest[payed_by_customer]" value="">
  <input name="CustomerRequest[spares_cost]" value="">
  <select name="CustomerRequest[fback_mode]">
    <option value="">Выберите...</option>
    <option value="1">Да</option>
    <option value="2">Нет</option>
    <option value="3">Нет возможности</option>
  </select>
  <select name="CustomerRequest[with_bso]">
    <option value="">Выберите</option>
    <option value="0">Нет</option>
    <option value="1">Есть</option>
  </select>
  <select name="CustomerRequest[with_zip]">
    <option value="">Выберите</option>
    <option value="0">Нет</option>
    <option value="1">Есть</option>
  </select>
  <select name="CustomerRequest[receipt_mode]">
    <option value="0" selected>Без чека</option>
  </select>
  <script>window.fileinput_x={"uploadUrl":"/admin/domain/customer-request/image-upload?id=781594_abc&target=images_main"};</script>""",
)


@pytest.fixture(autouse=True)
def _credentials(monkeypatch):
    monkeypatch.setattr(config, "CRM_USERNAME", "service")
    monkeypatch.setattr(config, "CRM_PASSWORD", "secret")
    monkeypatch.setattr(config, "CRM_LOGIN_FIELD_USERNAME", "LoginForm[email]")
    monkeypatch.setattr(config, "CRM_LOGIN_FIELD_PASSWORD", "LoginForm[password]")


class FakeCrm:
    def __init__(self, card: str = CARD):
        self.card = card
        self.card_after = None  # карточка после save_close
        self.card_after_second = None  # после второго POST (срок СД, если виджет появился только на статусе 9)
        self.card_reopen = None  # карточка после возврата из «Отказ» в работу
        self.card_finish = None  # карточка после finish=1
        self.writes = []
        self.write_urls = []
        self.uploads = []
        self.form_had_files = False
        self._finished = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/admin/login":
            if request.method == "POST":
                return httpx.Response(302, headers={"Location": "/"})
            return httpx.Response(200, text=f"<html><head>{CSRF}</head><body></body></html>")
        if path.endswith("/customer-request/image-upload"):
            self.uploads.append({
                "url": str(request.url),
                "xhr": request.headers.get("x-requested-with"),
            })
            return httpx.Response(200, json={"error": ""})
        if path.endswith("/customer-request/update"):
            if request.method == "POST":
                self.write_urls.append(str(request.url))
                if "finish=1" in str(request.url):
                    self._finished = True
                ctype = request.headers.get("content-type", "")
                if "multipart" in ctype:
                    self.form_had_files = True
                    self.writes.append({"_multipart": True})
                else:
                    self.writes.append(dict(httpx.QueryParams(request.content.decode())))
                return httpx.Response(302, headers={"Location": str(request.url)})
            if self._finished and self.card_finish:
                return httpx.Response(200, text=self.card_finish)
            if self.card_reopen is not None:
                if len(self.writes) == 1:
                    return httpx.Response(200, text=self.card_reopen)
                if self.writes and self.card_after:
                    return httpx.Response(200, text=self.card_after)
            elif len(self.writes) >= 2 and self.card_after_second:
                return httpx.Response(200, text=self.card_after_second)
            elif self.writes and self.card_after:
                return httpx.Response(200, text=self.card_after)
            return httpx.Response(200, text=self.card)
        return httpx.Response(200, text=f'<html><head>{CSRF}</head><body><nav class="navbar"></nav></body></html>')

    def client(self) -> CrmClient:
        return CrmClient(transport=httpx.MockTransport(self.handler))


def run_write(crm: FakeCrm, action):
    """action получает клиент; может быть корутиной или лямбдой."""
    async def scenario():
        client = crm.client()
        try:
            return await action(client)
        finally:
            await client.close()

    return asyncio.run(scenario())


def test_read_only_не_отправляет_ни_одного_post(monkeypatch):
    monkeypatch.setattr(config, "CRM_READ_ONLY", True)
    crm = FakeCrm()

    run_write(crm, lambda c: c.assign_master(781594, 10679))
    run_write(crm, lambda c: c.set_status(781594, STATUS_ENROUTE))

    assert crm.writes == [], "при CRM_READ_ONLY=true запись в CRM недопустима"


def test_назначение_отправляет_форму_целиком(monkeypatch):
    monkeypatch.setattr(config, "CRM_READ_ONLY", False)
    crm = FakeCrm()
    crm.card_after = ASSIGNED_CARD

    run_write(crm, lambda c: c.assign_master(781594, 10679))

    assert len(crm.writes) == 1
    payload = crm.writes[0]
    assert payload["CustomerRequest[employee_id]"] == "10679"
    # Остальные поля обязаны уйти целиком, иначе CRM затрёт их пустыми.
    assert payload["CustomerRequest[origin]"] == "web"
    assert payload["CustomerRequest[payed_by_customer]"] == "0"
    assert payload["CustomerRequest[status]"] == "1"
    assert payload["_csrf-frontend"] == "tok123"
    assert "save_close" not in payload


def test_readonly_поля_в_запись_не_уходят(monkeypatch):
    monkeypatch.setattr(config, "CRM_READ_ONLY", False)
    crm = FakeCrm()
    crm.card_after = ENROUTE_CARD

    run_write(crm, lambda c: c.set_status(781594, STATUS_ENROUTE))

    payload = crm.writes[0]
    assert payload["CustomerRequest[status]"] == str(STATUS_ENROUTE)
    assert "customerrequest-technique" not in payload
    assert "save_close" not in payload
    assert "finish=1" not in crm.write_urls[0]


def test_статус_4_должен_выбраться_как_в_пути(monkeypatch):
    """1 = Ожидает, 4 = В пути. GET после POST обязан показать option 4."""
    monkeypatch.setattr(config, "CRM_READ_ONLY", False)
    monkeypatch.setattr(config, "CRM_WRITE_ONLY_FOR", frozenset())
    crm = FakeCrm()
    crm.card_after = CARD.replace(
        '<option value="1" selected>Ожидает</option>',
        '<option value="4" selected>В пути</option>',
    )

    card = None

    async def scenario(c):
        nonlocal card
        damage = await c.set_status(781594, STATUS_ENROUTE)
        card = await c.fetch_request_card(781594)
        return damage

    damage = run_write(crm, scenario)
    assert damage == []
    assert crm.writes[0]["CustomerRequest[status]"] == "4"
    assert card.status_code == "4"
    assert card.labels[FIELD_STATUS] == "В пути"


def test_статус_остался_ожидает_это_сбой(monkeypatch):
    """Как 784311: POST 4, Yii оставила 1 — не «чужое поле», статус не лёг."""
    monkeypatch.setattr(config, "CRM_READ_ONLY", False)
    monkeypatch.setattr(config, "CRM_WRITE_ONLY_FOR", frozenset())
    crm = FakeCrm()
    crm.card_after = CARD  # selected всё ещё 1 Ожидает

    with pytest.raises(CrmError, match="status"):
        run_write(crm, lambda c: c.set_status(781594, STATUS_ENROUTE))


def test_отказ_смены_статуса_из_флеша_yii(monkeypatch):
    """Живая CRM: один мастер — одна заявка «В пути»; флеш должен попасть в ошибку."""
    monkeypatch.setattr(config, "CRM_READ_ONLY", False)
    monkeypatch.setattr(config, "CRM_WRITE_ONLY_FOR", frozenset())
    flash = (
        '<div class="alert-container">'
        '<div class="alert alert-danger">'
        "Отказ в смене статуса.<br>У выбранного мастера уже есть заявка \"В Пути\"."
        "</div></div>"
    )
    crm = FakeCrm()
    crm.card_after = CARD.replace("</nav>", "</nav>" + flash)

    with pytest.raises(CrmError, match="В Пути"):
        run_write(crm, lambda c: c.set_status(781594, STATUS_ENROUTE))


def test_порча_соседних_полей_замечается(monkeypatch):
    """CRM принимает форму целиком — проверяем, что после записи не съехало чужое."""
    monkeypatch.setattr(config, "CRM_READ_ONLY", False)
    monkeypatch.setattr(config, "CRM_WRITE_ONLY_FOR", frozenset())
    crm = FakeCrm()
    crm.card_after = ASSIGNED_CARD.replace(
        '<input name="CustomerRequest[payed_by_customer]" value="0">',
        '<input name="CustomerRequest[payed_by_customer]" value="1500">',
    )

    damage = []

    async def scenario(c):
        damage.extend(await c.assign_master(781594, 10679))

    run_write(crm, scenario)

    assert any("payed_by_customer" in d for d in damage), "порча соседнего поля должна быть замечена"


def test_чистая_запись_не_поднимает_тревогу(monkeypatch):
    """CRM сама подтягивает _employee_id следом за мастером — это не порча."""
    monkeypatch.setattr(config, "CRM_READ_ONLY", False)
    monkeypatch.setattr(config, "CRM_WRITE_ONLY_FOR", frozenset())
    crm = FakeCrm()
    crm.card_after = CARD.replace(
        '<option value="" selected>Выберите</option>',
        '<option value="">Выберите</option>',
    ).replace(
        '<option value="10679">Габидуллин Ким (Сык) (650)</option>',
        '<option value="10679" selected>Габидуллин Ким (Сык) (650)</option>',
    ).replace(
        '<input type="hidden" name="_employee_id" value="">',
        '<input type="hidden" name="_employee_id" value="10679">',
    )

    damage = []

    async def scenario(c):
        damage.extend(await c.assign_master(781594, 10679))

    run_write(crm, scenario)

    assert damage == []


def test_список_разрешённых_заявок_отсекает_все_остальные(monkeypatch):
    """Предохранитель на первое включение записи в боевую CRM."""
    monkeypatch.setattr(config, "CRM_READ_ONLY", False)
    monkeypatch.setattr(config, "CRM_WRITE_ONLY_FOR", frozenset({"999999"}))
    crm = FakeCrm()
    crm.card_after = ASSIGNED_CARD

    run_write(crm, lambda c: c.assign_master(781594, 10679))
    assert crm.writes == [], "заявки вне списка трогать нельзя"

    run_write(crm, lambda c: c.assign_master(999999, 10679))
    assert len(crm.writes) == 1, "разрешённая заявка должна записаться"


def test_пустой_список_разрешает_все_заявки(monkeypatch):
    monkeypatch.setattr(config, "CRM_READ_ONLY", False)
    monkeypatch.setattr(config, "CRM_WRITE_ONLY_FOR", frozenset())
    crm = FakeCrm()
    crm.card_after = ASSIGNED_CARD

    run_write(crm, lambda c: c.assign_master(781594, 10679))

    assert len(crm.writes) == 1


def test_сотрудника_вне_селекта_crm_не_пишем(monkeypatch):
    """Директор в боте — мастер, в CRM его нет: POST с выдуманным id не уходит."""
    monkeypatch.setattr(config, "CRM_READ_ONLY", False)
    monkeypatch.setattr(config, "CRM_WRITE_ONLY_FOR", frozenset())
    crm = FakeCrm()
    crm.card_after = ASSIGNED_CARD

    with pytest.raises(CrmError, match="нет в списке мастеров CRM"):
        run_write(crm, lambda c: c.assign_master(781594, 708703366))

    assert crm.writes == []


def test_незаписанные_поля_закрытия_это_сбой_а_не_чужие():
    """Пустой селект после POST 3 — закрытие не легло, это не «задели чужое»."""
    before = {
        closing.FIELD_SPARES: "",
        closing.FIELD_FEEDBACK: "",
        "CustomerRequest[payed_by_customer]": "0",
    }
    intended = {
        closing.FIELD_SPARES: "0",
        closing.FIELD_FEEDBACK: "3",
        "CustomerRequest[payed_by_customer]": "1500",
    }
    after = {
        closing.FIELD_SPARES: "",
        closing.FIELD_FEEDBACK: "",
        "CustomerRequest[payed_by_customer]": "1500",
    }
    from crm import _classify_changes
    foreign, unpersisted = _classify_changes(before, after, intended)
    assert foreign == []
    assert not any("spares_cost" in d for d in unpersisted), "пустое vs 0 у суммы не промах"
    assert any("fback_mode" in d for d in unpersisted)


def test_пустой_html_нуля_оплаты_не_промах():
    """Как 785236: POST 0, Yii number input остаётся value="" — это сохранённый ноль."""
    from crm import _classify_changes
    before = {closing.FIELD_PAYED: "", closing.FIELD_SPARES: ""}
    intended = {closing.FIELD_PAYED: "0", closing.FIELD_SPARES: "0"}
    after = {closing.FIELD_PAYED: "", closing.FIELD_SPARES: ""}
    foreign, unpersisted = _classify_changes(before, after, intended)
    assert foreign == []
    assert unpersisted == []


def test_ожидали_сумму_а_html_пустой_это_сбой():
    """Реальный промах: ждали 1500, карточка так и пустая."""
    from crm import _classify_changes
    before = {closing.FIELD_PAYED: ""}
    intended = {closing.FIELD_PAYED: "1500"}
    after = {closing.FIELD_PAYED: ""}
    foreign, unpersisted = _classify_changes(before, after, intended)
    assert foreign == []
    assert any("payed_by_customer" in d for d in unpersisted)


def test_затирание_соседней_суммы_по_прежнему_замечается():
    before = {"CustomerRequest[payed_by_customer]": "1500", closing.FIELD_SPARES: ""}
    intended = {closing.FIELD_FEEDBACK: "3"}
    after = {"CustomerRequest[payed_by_customer]": "", closing.FIELD_SPARES: ""}
    damage = _unexpected_changes(before, after, intended)
    assert any("payed_by_customer" in d for d in damage)


def test_проведённая_карточка_читает_статус_готов(monkeypatch):
    """Живая CRM после проведения рисует disabled input value=Готов без name."""
    monkeypatch.setattr(config, "CRM_READ_ONLY", False)
    html = CARD.replace(
        """<select name="CustomerRequest[status]">
    <option value="1" selected>Ожидает</option>
    <option value="4">В пути</option>
  </select>""",
        '<input type="text" class="form-control" disabled value="Готов">',
    )
    crm = FakeCrm(card=html)

    async def scenario(c):
        return await c.fetch_request_card(781594)

    from crm import _is_conducted
    card = run_write(crm, scenario)
    assert card.fields.get("__status_text") == "Готов"
    assert _is_conducted(card)
    assert "CustomerRequest[status]" not in card.postable


def _pick_options(options: list[tuple[str, str]], selected: str) -> str:
    lines = []
    for value, label in options:
        mark = " selected" if value == selected else ""
        lines.append(f'    <option value="{value}"{mark}>{label}</option>')
    return "\n".join(lines)


def _saved_close_card(**repl) -> str:
    """Как живая карточка после save_close: выбранные option, не «Выберите...»."""
    payed = repl.get("payed", "1500")
    fback = repl.get("fback", "3")
    bso = repl.get("bso", "1")
    zip_val = repl.get("zip", "0")
    html = CLOSE_CARD
    html = html.replace(
        '<input name="CustomerRequest[payed_by_customer]" value="">',
        f'<input name="CustomerRequest[payed_by_customer]" value="{payed}">',
    )
    html = html.replace(
        """<select name="CustomerRequest[fback_mode]">
    <option value="">Выберите...</option>
    <option value="1">Да</option>
    <option value="2">Нет</option>
    <option value="3">Нет возможности</option>
  </select>""",
        "<select name=\"CustomerRequest[fback_mode]\">\n"
        + _pick_options(
            [("", "Выберите..."), ("1", "Да"), ("2", "Нет"), ("3", "Нет возможности")],
            fback,
        )
        + "\n  </select>",
    )
    html = html.replace(
        """<select name="CustomerRequest[with_bso]">
    <option value="">Выберите</option>
    <option value="0">Нет</option>
    <option value="1">Есть</option>
  </select>""",
        "<select name=\"CustomerRequest[with_bso]\">\n"
        + _pick_options([("", "Выберите"), ("0", "Нет"), ("1", "Есть")], bso)
        + "\n  </select>",
    )
    html = html.replace(
        """<select name="CustomerRequest[with_zip]">
    <option value="">Выберите</option>
    <option value="0">Нет</option>
    <option value="1">Есть</option>
  </select>""",
        "<select name=\"CustomerRequest[with_zip]\">\n"
        + _pick_options([("", "Выберите"), ("0", "Нет"), ("1", "Есть")], zip_val)
        + "\n  </select>",
    )
    return html


CONDUCTED_FOOTER = """<div class="form-group field-customerrequest-status">
    <input type="text" class="form-control" disabled value="Готов">
  </div>"""


def test_закрытие_сначала_save_close_потом_finish(monkeypatch):
    monkeypatch.setattr(config, "CRM_READ_ONLY", False)
    monkeypatch.setattr(config, "CRM_WRITE_ONLY_FOR", frozenset())
    crm = FakeCrm(card=CLOSE_CARD)
    saved = _saved_close_card()
    crm.card_after = saved
    crm.card_finish = saved.replace(
        """<select name="CustomerRequest[status]">
    <option value="1" selected>Ожидает</option>
    <option value="4">В пути</option>
  </select>""",
        CONDUCTED_FOOTER,
    )

    async def scenario(c):
        async def download(_fid):
            return b"jpeg"
        c._download_file = download
        return await c.close_request(
            781594,
            {
                closing.FIELD_PAYED: "1500",
                closing.FIELD_SPARES: "",
                closing.FIELD_BSO: "1",
                closing.FIELD_FEEDBACK: "3",
                closing.FIELD_REQ_FBACK: "1",
                closing.FIELD_ZIP: "0",
                closing.FIELD_RECEIPT: "0",
            },
            {"bso": ["tgfile1"]},
        )

    damage = run_write(crm, scenario)
    assert damage == []
    assert len(crm.writes) == 2
    assert "save_close" in crm.writes[0]
    assert crm.writes[0]["save_close"] == "1"
    assert "finish=1" not in crm.write_urls[0]
    assert "finish=1" in crm.write_urls[1]
    assert "save_close" not in crm.writes[1]
    assert crm.writes[0][closing.FIELD_FEEDBACK] == "3"
    assert crm.writes[0][closing.FIELD_REQ_FBACK] == "1"
    assert crm.form_had_files is False
    assert crm.uploads, "фото должны уйти на image-upload, не в форму карточки"
    assert crm.uploads[0]["xhr"] == "XMLHttpRequest"


def test_ноль_и_нет_должны_выбраться_на_карточке(monkeypatch):
    """Как 785236: POST оплаты 0, HTML value="" — селекты Нет, закрытие доходит до finish=1."""
    monkeypatch.setattr(config, "CRM_READ_ONLY", False)
    monkeypatch.setattr(config, "CRM_WRITE_ONLY_FOR", frozenset())
    crm = FakeCrm(card=CLOSE_CARD)
    saved = _saved_close_card(payed="", fback="2", bso="0", zip="0")
    crm.card_after = saved
    crm.card_finish = saved.replace(
        """<select name="CustomerRequest[status]">
    <option value="1" selected>Ожидает</option>
    <option value="4">В пути</option>
  </select>""",
        CONDUCTED_FOOTER,
    )

    card = None

    async def scenario(c):
        nonlocal card
        damage = await c.close_request(781594, {
            closing.FIELD_PAYED: "0",
            closing.FIELD_SPARES: "",
            closing.FIELD_BSO: "0",
            closing.FIELD_FEEDBACK: "2",
            closing.FIELD_REQ_FBACK: "1",
            closing.FIELD_ZIP: "0",
            closing.FIELD_RECEIPT: "0",
        })
        card = await c.fetch_request_card(781594)
        return damage

    damage = run_write(crm, scenario)
    assert damage == []
    assert crm.writes[0][closing.FIELD_PAYED] == "0"
    assert crm.writes[0][closing.FIELD_BSO] == "0"
    assert crm.writes[0][closing.FIELD_FEEDBACK] == "2"
    assert crm.writes[0][closing.FIELD_ZIP] == "0"
    assert "finish=1" in crm.write_urls[1]
    assert card.fields[closing.FIELD_PAYED] == ""
    assert card.labels[closing.FIELD_BSO] == "Нет"
    assert card.labels[closing.FIELD_FEEDBACK] == "Нет"
    assert card.labels[closing.FIELD_ZIP] == "Нет"
    assert "Выберите" not in card.labels[closing.FIELD_BSO]
    assert "Выберите" not in card.labels[closing.FIELD_FEEDBACK]


def test_выберите_после_поста_нуля_это_сбой(monkeypatch):
    """Живая карточка с пустым селектом не считается сохранённым 0/2."""
    monkeypatch.setattr(config, "CRM_READ_ONLY", False)
    monkeypatch.setattr(config, "CRM_WRITE_ONLY_FOR", frozenset())
    crm = FakeCrm(card=CLOSE_CARD)
    crm.card_after = CLOSE_CARD

    async def scenario(c):
        await c.close_request(781594, {
            closing.FIELD_PAYED: "0",
            closing.FIELD_SPARES: "",
            closing.FIELD_BSO: "0",
            closing.FIELD_FEEDBACK: "2",
            closing.FIELD_REQ_FBACK: "1",
            closing.FIELD_ZIP: "0",
            closing.FIELD_RECEIPT: "0",
        })

    with pytest.raises(CrmError, match="не сохранила"):
        run_write(crm, scenario)


def test_если_0_и_3_не_легли_закрытие_падает(monkeypatch):
    monkeypatch.setattr(config, "CRM_READ_ONLY", False)
    monkeypatch.setattr(config, "CRM_WRITE_ONLY_FOR", frozenset())
    crm = FakeCrm(card=CLOSE_CARD)
    crm.card_after = CLOSE_CARD  # Yii «сохранила» и оставила пустое

    async def scenario(c):
        await c.close_request(781594, {
            closing.FIELD_PAYED: "1500",
            closing.FIELD_SPARES: "0",
            closing.FIELD_FEEDBACK: "3",
            closing.FIELD_REQ_FBACK: "1",
        })

    with pytest.raises(CrmError, match="не сохранила"):
        run_write(crm, scenario)


def test_отказ_сначала_вернуть_в_работу_потом_save_close(monkeypatch):
    """783806: статус 64, кнопок нет — сначала «В работе», потом save_close, потом finish."""
    monkeypatch.setattr(config, "CRM_READ_ONLY", False)
    monkeypatch.setattr(config, "CRM_WRITE_ONLY_FOR", frozenset())
    refused = CLOSE_CARD.replace(
        '<option value="1" selected>Ожидает</option>',
        '<option value="64" selected>Отказ</option>',
    )
    in_work = CLOSE_CARD.replace(
        '<option value="1" selected>Ожидает</option>',
        '<option value="8" selected>В работе</option>',
    )
    saved = _saved_close_card(payed="0", fback="2", bso="0", zip="0").replace(
        '<option value="1" selected>Ожидает</option>',
        '<option value="8" selected>В работе</option>',
    )
    crm = FakeCrm(card=refused)
    crm.card_reopen = in_work
    crm.card_after = saved
    crm.card_finish = saved.replace(
        """<select name="CustomerRequest[status]">
    <option value="8" selected>В работе</option>
    <option value="4">В пути</option>
  </select>""",
        CONDUCTED_FOOTER,
    )

    async def scenario(c):
        return await c.close_request(781594, {
            closing.FIELD_PAYED: "0",
            closing.FIELD_SPARES: "",
            closing.FIELD_BSO: "0",
            closing.FIELD_FEEDBACK: "2",
            closing.FIELD_REQ_FBACK: "1",
            closing.FIELD_ZIP: "0",
            closing.FIELD_RECEIPT: "0",
        })

    damage = run_write(crm, scenario)
    assert damage == []
    assert len(crm.writes) == 3
    assert crm.writes[0]["CustomerRequest[status]"] == str(STATUS_IN_WORK)
    assert "save_close" not in crm.writes[0]
    assert crm.writes[1]["save_close"] == "1"
    assert "finish=1" not in crm.write_urls[1]
    assert "finish=1" in crm.write_urls[2]


def test_отказ_без_возврата_в_работу_не_шлёт_finish(monkeypatch):
    """Живая 783806: POST статуса 8 игнорируется, save_close/finish бесполезны."""
    monkeypatch.setattr(config, "CRM_READ_ONLY", False)
    monkeypatch.setattr(config, "CRM_WRITE_ONLY_FOR", frozenset())
    refused = CLOSE_CARD.replace(
        '<option value="1" selected>Ожидает</option>',
        '<option value="64" selected>Отказ</option>',
    )
    crm = FakeCrm(card=refused)
    crm.card_reopen = refused

    async def scenario(c):
        await c.close_request(781594, {
            closing.FIELD_PAYED: "0",
            closing.FIELD_BSO: "0",
            closing.FIELD_FEEDBACK: "2",
            closing.FIELD_ZIP: "0",
        })

    with pytest.raises(CrmError, match="не сохранила"):
        run_write(crm, scenario)
    assert all("finish=1" not in u for u in crm.write_urls)
    assert all("save_close" not in w for w in crm.writes)


def test_на_закрытой_заявке_мастер_не_назначается(monkeypatch):
    monkeypatch.setattr(config, "CRM_READ_ONLY", False)
    crm = FakeCrm(card=CLOSED_CARD)

    with pytest.raises(CrmError):
        run_write(crm, lambda c: c.assign_master(781594, 10679))

    assert crm.writes == []


SD_COMMENT = "не греет\n4000\n19.09.2026"

SD_OPEN_CARD = CARD.replace(
    '<input name="CustomerRequest[payed_by_customer]" value="0">',
    """<input name="CustomerRequest[payed_by_customer]" value="0">
  <input name="CustomerRequest[prepayment]" value="">
  <input name="CustomerRequest[work_in_sd_ready_at]" value="">
  <textarea name="CustomerRequest[recommendation_comment]"></textarea>
  <script>window.fileinput_x={"uploadUrl":"/admin/domain/customer-request/image-upload?id=781594_abc&amp;target=images_safetyreceipt"};</script>""",
)


def test_открытие_сд_пишет_комментарий_филиала_а_не_фиктивные_поля(monkeypatch):
    monkeypatch.setattr(config, "CRM_READ_ONLY", False)
    monkeypatch.setattr(config, "CRM_WRITE_ONLY_FOR", frozenset())
    crm = FakeCrm(card=SD_OPEN_CARD)
    crm.card_after = (
        SD_OPEN_CARD
        .replace(
            '<option value="1" selected>Ожидает</option>',
            '<option value="9" selected>В работе СД</option>',
        )
        .replace(
            '<textarea name="CustomerRequest[recommendation_comment]"></textarea>',
            f'<textarea name="CustomerRequest[recommendation_comment]">{SD_COMMENT}</textarea>',
        )
        .replace(
            '<input name="CustomerRequest[work_in_sd_ready_at]" value="">',
            '<input name="CustomerRequest[work_in_sd_ready_at]" value="19-09-2026">',
        )
    )

    async def scenario(c):
        async def download(_fid):
            return b"jpeg"
        c._download_file = download
        return await c.open_sd(
            781594,
            SD_COMMENT,
            {"safety": ["tgfile1"]},
        )

    damage = run_write(crm, scenario)
    assert damage == []
    assert crm.writes, "открытие СД должно записать форму"
    payload = crm.writes[0]
    assert payload[closing.FIELD_COMMENT] == SD_COMMENT
    assert payload[closing.FIELD_SD_READY_AT] == "19-09-2026"
    assert payload["CustomerRequest[status]"] == "9"
    assert payload["CustomerRequest[prepayment]"] == ""
    assert "deadline" not in payload
    assert "malfunction" not in payload
    assert crm.uploads, "расписка должна уйти в окно фото"
    assert "images_safetyreceipt" in crm.uploads[0]["url"]


def test_открытие_сд_без_даты_в_комментарии_не_пишет_crm(monkeypatch):
    monkeypatch.setattr(config, "CRM_READ_ONLY", False)
    monkeypatch.setattr(config, "CRM_WRITE_ONLY_FOR", frozenset())
    crm = FakeCrm(card=SD_OPEN_CARD)

    async def scenario(c):
        await c.open_sd(781594, "не греет\n4000")

    with pytest.raises(CrmError, match="срока сдачи"):
        run_write(crm, scenario)
    assert crm.writes == []


def test_открытие_сд_пишет_срок_вторым_постом_если_виджета_не_было(monkeypatch):
    """На «Ожидает» datepicker может отсутствовать; после статуса 9 он появляется."""
    monkeypatch.setattr(config, "CRM_READ_ONLY", False)
    monkeypatch.setattr(config, "CRM_WRITE_ONLY_FOR", frozenset())
    before = CARD.replace(
        '<input name="CustomerRequest[payed_by_customer]" value="0">',
        """<input name="CustomerRequest[payed_by_customer]" value="0">
  <input name="CustomerRequest[prepayment]" value="">
  <textarea name="CustomerRequest[recommendation_comment]"></textarea>
  <script>window.fileinput_x={"uploadUrl":"/admin/domain/customer-request/image-upload?id=781594_abc&amp;target=images_safetyreceipt"};</script>""",
    )
    after_status = (
        before
        .replace(
            '<option value="1" selected>Ожидает</option>',
            '<option value="9" selected>В работе СД</option>',
        )
        .replace(
            '<textarea name="CustomerRequest[recommendation_comment]"></textarea>',
            f'<textarea name="CustomerRequest[recommendation_comment]">{SD_COMMENT}</textarea>',
        )
        .replace(
            '<input name="CustomerRequest[prepayment]" value="">',
            '<input name="CustomerRequest[prepayment]" value="">\n  '
            '<input name="CustomerRequest[work_in_sd_ready_at]" value="">',
        )
    )
    after_date = after_status.replace(
        '<input name="CustomerRequest[work_in_sd_ready_at]" value="">',
        '<input name="CustomerRequest[work_in_sd_ready_at]" value="19-09-2026">',
    )
    crm = FakeCrm(card=before)
    crm.card_after = after_status
    crm.card_after_second = after_date

    async def scenario(c):
        async def download(_fid):
            return b"jpeg"
        c._download_file = download
        return await c.open_sd(781594, SD_COMMENT, {"safety": ["tgfile1"]})

    damage = run_write(crm, scenario)
    assert damage == []
    assert len(crm.writes) == 2
    assert closing.FIELD_SD_READY_AT not in crm.writes[0]
    assert crm.writes[1][closing.FIELD_SD_READY_AT] == "19-09-2026"
    assert crm.writes[0][closing.FIELD_COMMENT] == SD_COMMENT
