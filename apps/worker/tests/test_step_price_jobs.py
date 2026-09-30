"""Задачи стоимости шага цены на поддельном провайдере (нужен TRADER_DATABASE_URL)."""

from datetime import date
from decimal import Decimal
from threading import Event

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from trader_db import list_step_prices, upsert_contract
from trader_db.models import Job
from trader_providers import DailyBar, IssError

from tests.fake_provider import TODAY, FakeProvider
from tests.test_iss_jobs import contract_id_of, run_job
from trader_worker.handlers import JobContext
from trader_worker.runner import Worker
from trader_worker.step_price_jobs import make_step_prices_all_handler

D = Decimal
# 1000 ₽ оборота на 10 контрактов по цене 5 → 20 ₽ за единицу цены; шаг 0,01 → 0,2 ₽.
GOOD = DailyBar(date(2026, 9, 28), 10, 1, D("1000"), D("5"))
GOOD_2 = DailyBar(date(2026, 9, 29), 20, 2, D("4000"), D("5"))
NO_VALUE = DailyBar(date(2026, 9, 30), 5, 1)


def prepared(iss_worker: Worker, factory: sessionmaker[Session], root_id: int) -> int:
    run_job(
        iss_worker,
        factory,
        "iss.sync_root",
        root_id=root_id,
        enqueue_imports=False,
    )
    return contract_id_of(factory, "BRZ6")


def stored(factory: sessionmaker[Session], contract_id: int) -> list[tuple[date, D]]:
    with factory() as session:
        return [
            (row.date, row.step_price) for row in list_step_prices(session, contract_id)
        ]


class TestStepPrices:
    def test_prices_are_derived_and_days_without_an_estimate_skipped(
        self,
        iss_worker: Worker,
        session_factory: sessionmaker[Session],
        provider: FakeProvider,
        root_id: int,
    ) -> None:
        contract_id = prepared(iss_worker, session_factory, root_id)
        provider.daily["BRZ6"] = [GOOD, GOOD_2, NO_VALUE]

        job = run_job(
            iss_worker, session_factory, "iss.step_prices", contract_id=contract_id
        )

        assert job.status == "succeeded", job.error
        assert (job.result["days"], job.result["skipped_days"]) == (2, 1)
        assert stored(session_factory, contract_id) == [
            (date(2026, 9, 28), D("0.20000000")),
            (date(2026, 9, 29), D("0.40000000")),
        ]

    def test_rerun_reloads_only_from_the_last_saved_day(
        self,
        iss_worker: Worker,
        session_factory: sessionmaker[Session],
        provider: FakeProvider,
        root_id: int,
    ) -> None:
        contract_id = prepared(iss_worker, session_factory, root_id)
        provider.daily["BRZ6"] = [GOOD, GOOD_2]
        run_job(iss_worker, session_factory, "iss.step_prices", contract_id=contract_id)
        provider.daily_calls.clear()

        run_job(iss_worker, session_factory, "iss.step_prices", contract_id=contract_id)

        assert provider.daily_calls == [("BRZ6", date(2026, 9, 29), date(2026, 10, 4))]
        assert len(stored(session_factory, contract_id)) == 2

    def test_today_is_never_requested(
        self,
        iss_worker: Worker,
        session_factory: sessionmaker[Session],
        provider: FakeProvider,
        root_id: int,
    ) -> None:
        contract_id = prepared(iss_worker, session_factory, root_id)
        provider.daily["BRZ6"] = [GOOD]

        run_job(iss_worker, session_factory, "iss.step_prices", contract_id=contract_id)

        assert all(call[2] < TODAY for call in provider.daily_calls)

    def test_explicit_window(
        self,
        iss_worker: Worker,
        session_factory: sessionmaker[Session],
        provider: FakeProvider,
        root_id: int,
    ) -> None:
        contract_id = prepared(iss_worker, session_factory, root_id)
        provider.daily["BRZ6"] = [GOOD, GOOD_2]

        job = run_job(
            iss_worker,
            session_factory,
            "iss.step_prices",
            contract_id=contract_id,
            **{"from": "2026-09-29", "till": "2026-09-29"},
        )

        assert job.result["window"] == ["2026-09-29", "2026-09-29"]
        assert [d for d, _ in stored(session_factory, contract_id)] == [
            date(2026, 9, 29)
        ]

    def test_iss_failure_and_bad_input_are_readable_errors(
        self,
        iss_worker: Worker,
        session_factory: sessionmaker[Session],
        provider: FakeProvider,
        root_id: int,
    ) -> None:
        contract_id = prepared(iss_worker, session_factory, root_id)
        provider.fail_on = lambda *_args: IssError("ISS недоступен: 503")

        failed = run_job(
            iss_worker, session_factory, "iss.step_prices", contract_id=contract_id
        )
        no_id = run_job(iss_worker, session_factory, "iss.step_prices")
        unknown = run_job(
            iss_worker, session_factory, "iss.step_prices", contract_id=999_999
        )
        bad_date = run_job(
            iss_worker,
            session_factory,
            "iss.step_prices",
            contract_id=contract_id,
            **{"from": "вчера"},
        )

        assert failed.status == "failed" and "503" in (failed.error or "")
        assert "contract_id" in (no_id.error or "")
        assert "не найден" in (unknown.error or "")
        assert "ГГГГ-ММ-ДД" in (bad_date.error or "")


def context() -> JobContext:
    return JobContext(
        job_id=1,
        job_type="iss.step_prices_all",
        params={},
        attempt=1,
        _sink=lambda _fraction, _message: False,
        _stop=Event(),
    )


class TestStepPricesAll:
    def test_enqueues_one_job_per_live_iss_contract_without_duplicates(
        self,
        iss_worker: Worker,
        session_factory: sessionmaker[Session],
        root_id: int,
    ) -> None:
        run_job(
            iss_worker,
            session_factory,
            "iss.sync_root",
            root_id=root_id,
            enqueue_imports=False,
        )
        with session_factory() as session:
            # Давно истёкший контракт с кодом ISS обновлять незачем.
            upsert_contract(
                session,
                root_id=root_id,
                expiration_date=date(2026, 1, 28),
                last_trade_date=None,
                secid="BRF6",
                provider_code="moex_iss",
                external_id="BRF6",
            )
            session.commit()
        handler = make_step_prices_all_handler(session_factory, today=lambda: TODAY)

        first = handler(context())
        second = handler(context())

        assert first == {"contracts": 2, "enqueued": 2}
        assert second == {"contracts": 2, "enqueued": 0}
        with session_factory() as session:
            jobs = list(
                session.scalars(select(Job).where(Job.type == "iss.step_prices"))
            )
        assert len(jobs) == 2
