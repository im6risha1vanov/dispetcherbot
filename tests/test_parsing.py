from decimal import Decimal

import pytest
from selectolax.parser import HTMLParser

from crm import _parse_dt, _parse_grid_row, _parse_money, CrmLayoutError, _check_grid_columns


def grid_row(
    crm_id: str = "781594",
    opened: str = "15.09.26 14:00",
    req_type: str = "Впервые",
    status: str = "Ожидает",
    name: str = "Иванов Иван Иванович",
    address: str = "ул. Ленина, 10",
    master: str = "Кузиванов И А",
    created: str = "15.09.26 13:45",
    closed: str = "",
    cost: str = "1 500,00",
):
    html = f"""
    <table><tbody>
      <tr data-key="{crm_id}">
        <td class="col__id">{crm_id}</td>
        <td class="col__check"></td>
        <td class="col__check"></td>
        <td class="col__check"></td>
        <td class="col__date col__openedAt">{opened}</td>
        <td class="col__req_type">{req_type}</td>
        <td class="col__req_status">{status}</td>
        <td class="--fullwidth">{name}</td>
        <td>{address}</td>
        <td class="col__req_master">{master}</td>
        <td class="col__date">{created}</td>
        <td class="col__date">{closed}</td>
        <td>{cost}</td>
      </tr>
    </tbody></table>
    """
    return HTMLParser(html).css_first("tr")


def test_разбирает_строку_грида():
    row = _parse_grid_row(grid_row())

    assert row.crm_id == 781594
    assert row.req_type == "Впервые"
    assert row.status_text == "Ожидает"
    assert row.is_recall is False
    assert row.customer_name == "Иванов Иван Иванович"
    assert row.address == "ул. Ленина, 10"
    assert row.master_name == "Кузиванов И А"
    assert row.total_cost == Decimal("1500.00")
    assert row.opened_at.hour == 14
    assert row.closed_at_local is None
    assert len(row.raw_cells) == 13


def test_отзывная_заявка_снимает_суффикс():
    row = _parse_grid_row(grid_row(status="В работе (Отз)"))

    assert row.is_recall is True
    assert row.status_text == "В работе"


def test_строка_без_data_key_пропускается():
    tr = HTMLParser("<table><tbody><tr><td>шапка</td></tr></tbody></table>").css_first("tr")

    assert _parse_grid_row(tr) is None


def test_изменившаяся_вёрстка_не_даёт_кривых_данных():
    """Раньше строку молча пропускали, и заявка исчезала из поля зрения бота."""
    tr = HTMLParser(
        '<table><tbody><tr data-key="781594"><td>781594</td><td>лишь две</td></tr></tbody></table>'
    ).css_first("tr")

    with pytest.raises(CrmLayoutError) as failure:
        _parse_grid_row(tr)

    assert "13" in str(failure.value) and "2" in str(failure.value)


GRID_HEAD = (
    "<thead><tr>"
    "<th>ID</th><th></th><th>П</th><th>Н</th><th>Время заявки</th><th>Тип</th>"
    "<th>Статус</th><th>Имя клиента</th><th>Адрес</th><th>Мастер</th>"
    "<th>Создано (лок)</th><th>Закрыто (лок)</th><th>Сумма</th>"
    "</tr></thead>"
)


def _table(head: str, body: str = "") -> object:
    html = f'<table class="table__tr-link">{head}<tbody>{body}</tbody></table>'
    return HTMLParser(html).css_first("table")


def test_живая_шапка_грида_проходит_сверку():
    """Контроль на текущей разметке CRM, снятой 24.09.2026."""
    _check_grid_columns(_table(GRID_HEAD))


def test_переставленные_колонки_ловятся():
    """Опаснее новой колонки: число то же, а адрес ляжет в поле мастера."""
    swapped = GRID_HEAD.replace(
        "<th>Адрес</th><th>Мастер</th>", "<th>Мастер</th><th>Адрес</th>"
    )

    with pytest.raises(CrmLayoutError) as failure:
        _check_grid_columns(_table(swapped))

    text = str(failure.value)
    assert "колонка 8" in text and "Адрес" in text and "Мастер" in text


def test_новая_колонка_ловится():
    added = GRID_HEAD.replace("<th>Сумма</th>", "<th>Сумма</th><th>Скидка</th>")

    with pytest.raises(CrmLayoutError) as failure:
        _check_grid_columns(_table(added))

    assert "ожидал 13, пришло 14" in str(failure.value)


def test_грид_без_шапки_ловится():
    with pytest.raises(CrmLayoutError):
        _check_grid_columns(_table(""))


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1 500,00", Decimal("1500.00")),
        ("2500", Decimal("2500")),
        ("", None),
        ("—", None),
        ("-", None),
    ],
)
def test_разбор_суммы(raw, expected):
    assert _parse_money(raw) == expected


@pytest.mark.parametrize("raw", ["15.09.26 14:00", "15.09.2026 14:00", "15.09.26"])
def test_разбор_даты(raw):
    parsed = _parse_dt(raw)

    assert parsed is not None
    assert (parsed.day, parsed.month) == (15, 9)
    assert parsed.tzinfo is not None


def test_пустая_дата_это_none():
    assert _parse_dt("") is None
