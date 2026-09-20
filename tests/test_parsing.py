from decimal import Decimal

import pytest
from selectolax.parser import HTMLParser

from crm import _parse_dt, _parse_grid_row, _parse_money


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
    tr = HTMLParser(
        '<table><tbody><tr data-key="781594"><td>781594</td><td>лишь две</td></tr></tbody></table>'
    ).css_first("tr")

    assert _parse_grid_row(tr) is None


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
