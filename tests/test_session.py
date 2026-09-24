import asyncio

import httpx
import pytest

import config
from crm import CrmAuthError, CrmClient

CSRF = '<meta name="csrf-param" content="_csrf-frontend"><meta name="csrf-token" content="tok123">'
LOGIN_PAGE = f"""<html><head>{CSRF}</head><body>
<form action="/admin/login" method="post">
  <input type="hidden" name="_csrf-frontend" value="tok123">
  <input type="text" name="LoginForm[email]">
  <input type="password" name="LoginForm[password]">
  <input type="hidden" name="LoginForm[rememberMe]" value="0">
  <input type="checkbox" name="LoginForm[rememberMe]" value="1">
</form></body></html>"""
AUTHED_PAGE = f'<html><head>{CSRF}</head><body><nav class="navbar"></nav></body></html>'

GRID_PAGE = f"""<html><head>{CSRF}</head><body><nav class="navbar"></nav>
<div class="grid-view"><table class="table__tr-link"><thead><tr><th>ID</th><th></th><th>П</th><th>Н</th><th>Время заявки</th><th>Тип</th><th>Статус</th><th>Имя клиента</th><th>Адрес</th><th>Мастер</th><th>Создано (лок)</th><th>Закрыто (лок)</th><th>Сумма</th></tr></thead><tbody>
  <tr data-key="781594">
    <td class="col__id">781594</td><td class="col__check"></td><td class="col__check"></td>
    <td class="col__check"></td><td class="col__date col__openedAt">15.09.26 14:00</td>
    <td class="col__req_type">Впервые</td><td class="col__req_status">Ожидает</td>
    <td class="--fullwidth">Иванов И И</td><td>ул. Ленина, 10</td>
    <td class="col__req_master"></td><td class="col__date">15.09.26 13:45</td>
    <td class="col__date"></td><td>0</td>
  </tr>
</tbody></table></div></body></html>"""


@pytest.fixture(autouse=True)
def _credentials(monkeypatch):
    monkeypatch.setattr(config, "CRM_USERNAME", "service")
    monkeypatch.setattr(config, "CRM_PASSWORD", "secret")
    monkeypatch.setattr(config, "CRM_LOGIN_FIELD_USERNAME", "LoginForm[email]")
    monkeypatch.setattr(config, "CRM_LOGIN_FIELD_PASSWORD", "LoginForm[password]")


class FakeCrm:
    """Мини-CRM на MockTransport: считает логины и умеет разлогинивать."""

    def __init__(self, grid_responses):
        self.grid_responses = list(grid_responses)
        self.logins = 0
        self.login_payloads = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path

        if path == "/admin/login":
            if request.method == "POST":
                self.logins += 1
                self.login_payloads.append(request.content.decode())
                return httpx.Response(302, headers={"Location": "/"})
            return httpx.Response(200, text=LOGIN_PAGE)

        if path == "/":
            return httpx.Response(200, text=AUTHED_PAGE)

        if path.endswith("/customer-request/index"):
            behaviour = self.grid_responses.pop(0)
            if behaviour == "logged_out":
                return httpx.Response(302, headers={"Location": "/"})
            return httpx.Response(200, text=GRID_PAGE)

        return httpx.Response(404)

    def client(self) -> CrmClient:
        return CrmClient(transport=httpx.MockTransport(self.handler))


def run(coro):
    return asyncio.run(coro)


def test_логин_отправляет_csrf_и_учётные_данные():
    crm = FakeCrm(["ok"])

    async def scenario():
        client = crm.client()
        try:
            await client.login()
        finally:
            await client.close()

    run(scenario())

    assert crm.logins == 1
    payload = dict(httpx.QueryParams(crm.login_payloads[0]))
    assert payload["_csrf-frontend"] == "tok123"
    assert payload["LoginForm[email]"] == "service"
    assert payload["LoginForm[password]"] == "secret"


def test_вход_просит_запомнить_сессию():
    """Длинная сессия — меньше релогинов в сутки."""
    crm = FakeCrm(["ok"])

    async def scenario():
        client = crm.client()
        try:
            await client.login()
        finally:
            await client.close()

    run(scenario())

    payload = dict(httpx.QueryParams(crm.login_payloads[0]))
    assert payload["LoginForm[rememberMe]"] == "1"


def test_первый_запрос_логинится_сам():
    crm = FakeCrm(["ok"])

    async def scenario():
        client = crm.client()
        try:
            return await client.fetch_open_requests()
        finally:
            await client.close()

    rows = run(scenario())

    assert crm.logins == 1
    assert [row.crm_id for row in rows] == [781594]


def test_разрыв_сессии_чинится_релогином_и_запрос_повторяется():
    crm = FakeCrm(["ok", "logged_out", "ok"])

    async def scenario():
        client = crm.client()
        try:
            await client.fetch_open_requests()
            return await client.fetch_open_requests()
        finally:
            await client.close()

    rows = run(scenario())

    assert crm.logins == 2, "на разлогин должен быть ровно один повторный вход"
    assert [row.crm_id for row in rows] == [781594]


def test_не_зацикливается_если_релогин_не_помог():
    crm = FakeCrm(["logged_out", "logged_out"])

    async def scenario():
        client = crm.client()
        try:
            await client.fetch_open_requests()
        finally:
            await client.close()

    with pytest.raises(CrmAuthError):
        run(scenario())

    assert crm.logins == 2


def test_пустые_имена_полей_входа_не_дают_стартовать(monkeypatch):
    monkeypatch.setattr(config, "CRM_LOGIN_FIELD_USERNAME", "")
    crm = FakeCrm(["ok"])

    async def scenario():
        client = crm.client()
        try:
            await client.login()
        finally:
            await client.close()

    with pytest.raises(config.ConfigError):
        run(scenario())

    assert crm.logins == 0
