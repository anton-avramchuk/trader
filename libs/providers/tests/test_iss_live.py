"""Проверки на настоящем ISS: фиксируют факты об API, найденные вручную.

Выключены по умолчанию (CI не должен зависеть от внешней сети). Запуск:
``TRADER_LIVE_TESTS=1 uv run pytest tests/test_iss_live.py``.
"""

import os
from datetime import UTC, date, datetime

import pytest
from trader_engine.ingest import RawRow

from trader_providers import IssClient

pytestmark = pytest.mark.skipif(
    not os.environ.get("TRADER_LIVE_TESTS"), reason="TRADER_LIVE_TESTS не задан"
)


@pytest.fixture
def client() -> IssClient:
    return IssClient()


def test_gold_prefix_comes_from_active_series(client: IssClient) -> None:
    assert client.discover_prefix("GOLD") == "GD"
    assert client.discover_prefix("BR") == "BR"


def test_expired_contract_description(client: IssClient) -> None:
    contract = client.describe("BRZ0")

    assert contract is not None
    assert (contract.asset_code, contract.expiration_date) == ("BR", date(2020, 12, 1))
    assert contract.history_from == date(2019, 11, 26)


def test_reused_secid_of_a_decade_ago_is_renamed_by_iss(client: IssClient) -> None:
    contracts = client.list_contracts(
        "BR", from_year=2010, to_year=2010, secid_prefix="BR"
    )

    assert [c.provider_id for c in contracts if c.expiration_date.month == 12] == [
        "BRZ0_2010"
    ]


def test_contracts_of_2020_for_natural_gas(client: IssClient) -> None:
    contracts = client.list_contracts(
        "NG", from_year=2020, to_year=2020, secid_prefix="NG"
    )

    assert len(contracts) >= 6
    assert {c.expiration_date.year for c in contracts} <= {2020, 2021}


def test_candle_borders_and_a_full_day_of_minutes(client: IssClient) -> None:
    first, last = client.candle_range("BRZ0") or (None, None)

    assert first == datetime(2019, 11, 27, 13, 17, tzinfo=UTC)
    assert last == datetime(2020, 12, 1, 15, 44, tzinfo=UTC)
    rows = list(
        client.get_historical_candles("BRZ0", date(2020, 11, 30), date(2020, 11, 30))
    )
    assert len(rows) == 810  # больше 500 — проверяет пагинацию
    assert all(isinstance(row, RawRow) for row in rows)
    first_row = rows[0]
    assert isinstance(first_row, RawRow)
    assert first_row.timestamp == datetime(2020, 11, 30, 7, 0, tzinfo=UTC)  # 10:00 МСК
    assert first_row.low is not None and first_row.high is not None
    assert first_row.low <= first_row.high
