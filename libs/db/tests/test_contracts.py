"""Контракты и покрытие истории поставщика (нужен TRADER_DATABASE_URL)."""

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trader_db import finish_import, start_import
from trader_db.contracts import loaded_windows, provider_contract_id, upsert_contract
from trader_db.models import Contract, ContractProviderId, Root, TradingCalendar


def make_root(session: Session, code: str = "BR") -> int:
    calendar_id = session.scalars(
        select(TradingCalendar.id).where(TradingCalendar.code == "moex_forts")
    ).one()
    root = Root(
        code=code,
        name=code,
        exchange="MOEX",
        quote_currency="USD",
        tick_size=Decimal("0.01"),
        calendar_id=calendar_id,
        roll_trading_days=5,
    )
    session.add(root)
    session.flush()
    return root.id


def add(session: Session, root_id: int, **overrides: Any) -> tuple[Contract, bool]:
    values: dict[str, Any] = {
        "root_id": root_id,
        "expiration_date": date(2026, 12, 1),
        "last_trade_date": date(2026, 12, 1),
        "secid": "BRZ6",
        "provider_code": "moex_iss",
        "external_id": "BRZ6",
    }
    values.update(overrides)
    return upsert_contract(session, **values)


class TestUpsert:
    def test_creates_contract_and_provider_code(self, session: Session) -> None:
        root_id = make_root(session)

        contract, created = add(session, root_id)

        assert created is True
        assert (contract.secid, contract.last_trade_date) == ("BRZ6", date(2026, 12, 1))
        assert provider_contract_id(session, contract.id, "moex_iss") == "BRZ6"

    def test_second_call_is_idempotent(self, session: Session) -> None:
        root_id = make_root(session)
        first, _ = add(session, root_id)

        second, created = add(session, root_id)

        assert (created, second.id) == (False, first.id)
        assert session.scalar(select(func.count()).select_from(Contract)) == 1
        assert session.scalar(select(func.count()).select_from(ContractProviderId)) == 1

    def test_mutable_attributes_are_updated(self, session: Session) -> None:
        root_id = make_root(session)
        add(session, root_id, last_trade_date=None, external_id="OLD")

        contract, _ = add(session, root_id, last_trade_date=date(2026, 11, 30))

        assert contract.last_trade_date == date(2026, 11, 30)
        assert provider_contract_id(session, contract.id, "moex_iss") == "BRZ6"

    def test_same_secid_ten_years_apart_are_different_contracts(
        self, session: Session
    ) -> None:
        root_id = make_root(session)

        old, _ = add(
            session, root_id, expiration_date=date(2016, 12, 1), last_trade_date=None
        )
        new, _ = add(session, root_id, expiration_date=date(2026, 12, 1))

        assert old.id != new.id

    def test_unknown_provider(self, session: Session) -> None:
        root_id = make_root(session)

        try:
            add(session, root_id, provider_code="nope")
        except LookupError as error:
            assert "nope" in str(error)
        else:
            raise AssertionError("ожидали LookupError")

    def test_missing_provider_code_is_none(self, session: Session) -> None:
        root_id = make_root(session)
        contract, _ = add(session, root_id)

        assert provider_contract_id(session, contract.id, "tinvest") is None


class TestLoadedWindows:
    def _import(
        self,
        session: Session,
        contract_id: int,
        window: tuple[str, str] | None,
        *,
        provider: str = "moex_iss",
        status: str = "completed",
    ) -> None:
        params = (
            {} if window is None else {"window": {"from": window[0], "till": window[1]}}
        )
        import_id = start_import(
            session,
            provider_code=provider,
            contract_id=contract_id,
            source_type="api",
            params=params,
        )
        finish_import(
            session,
            import_id,
            status=status,
            error=None if status == "completed" else "сбой",
        )

    def test_windows_of_completed_imports_are_merged(self, session: Session) -> None:
        contract, _ = add(session, make_root(session))
        self._import(session, contract.id, ("2026-01-01", "2026-01-31"))
        self._import(session, contract.id, ("2026-02-01", "2026-02-28"))
        self._import(session, contract.id, ("2026-05-01", "2026-05-31"))

        assert loaded_windows(session, contract.id, "moex_iss") == [
            (date(2026, 1, 1), date(2026, 2, 28)),
            (date(2026, 5, 1), date(2026, 5, 31)),
        ]

    def test_failed_imports_do_not_count(self, session: Session) -> None:
        contract, _ = add(session, make_root(session))
        self._import(
            session, contract.id, ("2026-01-01", "2026-01-31"), status="failed"
        )

        assert loaded_windows(session, contract.id, "moex_iss") == []

    def test_other_provider_and_imports_without_window_do_not_count(
        self, session: Session
    ) -> None:
        contract, _ = add(session, make_root(session))
        self._import(session, contract.id, ("2026-01-01", "2026-01-31"), provider="csv")
        self._import(session, contract.id, None)

        assert loaded_windows(session, contract.id, "moex_iss") == []

    def test_other_contracts_do_not_count(self, session: Session) -> None:
        root_id = make_root(session)
        one, _ = add(session, root_id)
        other, _ = add(session, root_id, expiration_date=date(2027, 1, 4), secid="BRF7")
        self._import(session, other.id, ("2026-01-01", "2026-01-31"))

        assert loaded_windows(session, one.id, "moex_iss") == []
