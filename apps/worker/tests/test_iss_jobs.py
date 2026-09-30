"""Задачи MOEX ISS на поддельном провайдере (нужен TRADER_DATABASE_URL)."""

from datetime import date
from threading import Event
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from trader_db import provider_contract_id
from trader_db.models import (
    Contract,
    DataImport,
    DatasetVersion,
    Job,
    RawCandle1m,
)
from trader_engine.ingest import RowError
from trader_providers import IssError

from tests.fake_provider import MON, TODAY, TUE, WED, FakeProvider, day_rows
from tests.helpers import enqueue, load
from trader_worker.runner import Worker


def run_job(
    worker: Worker, factory: sessionmaker[Session], job_type: str, **params: Any
) -> Any:
    job_id = enqueue(factory, job_type, params)
    # Импорты сами ставят сборку баров: задачи, стоящие в очереди раньше нужной,
    # выполняются по пути (FIFO).
    for _ in range(20):
        worker.run_once(Event())
        job = load(factory, job_id)
        if job.status not in ("queued", "running"):
            return job
    raise AssertionError(f"задача {job_type} не выполнена")


def count(factory: sessionmaker[Session], model: type) -> int:
    with factory() as session:
        return session.scalar(select(func.count()).select_from(model)) or 0


def contract_id_of(factory: sessionmaker[Session], secid: str) -> int:
    with factory() as session:
        return session.scalars(select(Contract.id).where(Contract.secid == secid)).one()


class TestSyncRoot:
    def test_dry_run_only_lists_contracts(
        self,
        iss_worker: Worker,
        session_factory: sessionmaker[Session],
        root_id: int,
    ) -> None:
        job = run_job(
            iss_worker, session_factory, "iss.sync_root", root_id=root_id, dry_run=True
        )

        assert job.status == "succeeded", job.error
        assert job.result["dry_run"] is True
        assert [
            (c["secid"], c["expiration_date"]) for c in job.result["contracts"]
        ] == [
            ("BRX6", "2026-11-02"),
            ("BRZ6", "2026-12-01"),
        ]
        assert count(session_factory, Contract) == 0
        assert count(session_factory, Job) == 1  # только сама задача

    def test_contracts_are_created_with_iss_codes_and_imports_enqueued(
        self,
        iss_worker: Worker,
        session_factory: sessionmaker[Session],
        root_id: int,
    ) -> None:
        job = run_job(iss_worker, session_factory, "iss.sync_root", root_id=root_id)

        assert job.status == "succeeded", job.error
        assert job.result["contracts_found"] == 2  # NGZ6 — другой актив
        assert (job.result["contracts_created"], job.result["imports_enqueued"]) == (
            2,
            2,
        )
        with session_factory() as session:
            contracts = session.scalars(
                select(Contract).order_by(Contract.expiration_date)
            ).all()
            assert [(c.secid, c.expiration_date) for c in contracts] == [
                ("BRX6", date(2026, 11, 2)),
                ("BRZ6", date(2026, 12, 1)),
            ]
            assert provider_contract_id(session, contracts[1].id, "moex_iss") == "BRZ6"
            queued = session.scalars(select(Job).where(Job.type == "import.iss")).all()
            assert sorted(j.params["contract_id"] for j in queued) == sorted(
                c.id for c in contracts
            )

    def test_repeated_sync_does_not_duplicate_contracts_or_pending_imports(
        self,
        iss_worker: Worker,
        session_factory: sessionmaker[Session],
        root_id: int,
    ) -> None:
        # Обе синхронизации в очереди раньше импортов, которые породит первая.
        first_id = enqueue(session_factory, "iss.sync_root", {"root_id": root_id})
        second_id = enqueue(session_factory, "iss.sync_root", {"root_id": root_id})

        iss_worker.run_once(Event())
        iss_worker.run_once(Event())

        first, second = (
            load(session_factory, first_id),
            load(session_factory, second_id),
        )
        assert first.result is not None
        assert second.result is not None
        assert (
            first.result["contracts_created"],
            first.result["imports_enqueued"],
        ) == (2, 2)
        assert (
            second.result["contracts_created"],
            second.result["imports_enqueued"],
        ) == (0, 0)
        assert count(session_factory, Contract) == 2
        assert count(session_factory, Job) == 4  # 2 синхронизации + 2 загрузки

    def test_imports_can_be_left_out(
        self,
        iss_worker: Worker,
        session_factory: sessionmaker[Session],
        root_id: int,
    ) -> None:
        job = run_job(
            iss_worker,
            session_factory,
            "iss.sync_root",
            root_id=root_id,
            enqueue_imports=False,
        )

        assert job.result["imports_enqueued"] == 0
        assert count(session_factory, Job) == 1

    def test_year_range_and_prefix_are_passed_to_the_provider(
        self,
        iss_worker: Worker,
        session_factory: sessionmaker[Session],
        provider: FakeProvider,
        root_id: int,
    ) -> None:
        run_job(
            iss_worker,
            session_factory,
            "iss.sync_root",
            root_id=root_id,
            from_year=2022,
            to_year=2024,
            secid_prefix="BR",
        )

        assert provider.list_calls == [
            {"asset": "BR", "from": 2022, "to": 2024, "prefix": "BR"}
        ]

    def test_default_years_start_in_2020_and_end_next_year(
        self,
        iss_worker: Worker,
        session_factory: sessionmaker[Session],
        provider: FakeProvider,
        root_id: int,
    ) -> None:
        run_job(iss_worker, session_factory, "iss.sync_root", root_id=root_id)

        assert provider.list_calls[0]["from"] == 2020
        assert provider.list_calls[0]["to"] == TODAY.year + 1

    def test_unknown_root_fails_with_a_clear_message(
        self, iss_worker: Worker, session_factory: sessionmaker[Session]
    ) -> None:
        job = run_job(iss_worker, session_factory, "iss.sync_root", root_id=999)

        assert job.status == "failed"
        assert "Root 999" in job.error

    def test_provider_error_is_reported_without_traceback_and_provider_is_closed(
        self,
        iss_worker: Worker,
        session_factory: sessionmaker[Session],
        provider: FakeProvider,
        root_id: int,
    ) -> None:
        provider.list_error = IssError("ISS недоступен: таймаут")

        job = run_job(iss_worker, session_factory, "iss.sync_root", root_id=root_id)

        assert job.status == "failed"
        assert job.error == "ISS недоступен: таймаут"
        assert provider.closed == 1
        assert count(session_factory, Contract) == 0

    def test_missing_root_id_parameter(
        self, iss_worker: Worker, session_factory: sessionmaker[Session]
    ) -> None:
        job = run_job(iss_worker, session_factory, "iss.sync_root")

        assert job.status == "failed"
        assert "root_id" in job.error


class TestIssImport:
    def synced(
        self, iss_worker: Worker, factory: sessionmaker[Session], root_id: int
    ) -> int:
        run_job(
            iss_worker, factory, "iss.sync_root", root_id=root_id, enqueue_imports=False
        )
        return contract_id_of(factory, "BRZ6")

    def test_whole_history_is_loaded_in_chunks_with_windows_recorded(
        self,
        iss_worker: Worker,
        session_factory: sessionmaker[Session],
        provider: FakeProvider,
        root_id: int,
    ) -> None:
        contract_id = self.synced(iss_worker, session_factory, root_id)

        job = run_job(
            iss_worker, session_factory, "import.iss", contract_id=contract_id
        )

        assert job.status == "succeeded", job.error
        assert job.result["window"] == ["2026-09-28", "2026-09-30"]
        assert (job.result["chunks"], job.result["inserted"]) == (2, 90)
        assert provider.candle_calls == [
            ("BRZ6", MON, TUE),
            ("BRZ6", WED, WED),
        ]
        assert count(session_factory, RawCandle1m) == 90
        assert count(session_factory, DatasetVersion) == 2
        with session_factory() as session:
            windows = sorted(
                (i.params["window"]["from"], i.params["window"]["till"])
                for i in session.scalars(select(DataImport))
            )
        assert windows == [("2026-09-28", "2026-09-29"), ("2026-09-30", "2026-09-30")]

    def test_repeat_run_loads_nothing(
        self,
        iss_worker: Worker,
        session_factory: sessionmaker[Session],
        provider: FakeProvider,
        root_id: int,
    ) -> None:
        contract_id = self.synced(iss_worker, session_factory, root_id)
        run_job(iss_worker, session_factory, "import.iss", contract_id=contract_id)
        provider.candle_calls.clear()

        second = run_job(
            iss_worker, session_factory, "import.iss", contract_id=contract_id
        )

        assert second.status == "succeeded", second.error
        assert (second.result["chunks"], second.result["inserted"]) == (0, 0)
        assert provider.candle_calls == []
        assert count(session_factory, RawCandle1m) == 90

    def test_when_iss_gets_new_days_only_they_are_loaded(
        self,
        iss_worker: Worker,
        session_factory: sessionmaker[Session],
        provider: FakeProvider,
        root_id: int,
    ) -> None:
        contract_id = self.synced(iss_worker, session_factory, root_id)
        run_job(iss_worker, session_factory, "import.iss", contract_id=contract_id)
        provider.candle_calls.clear()
        new_days = [date(2026, 10, 1), date(2026, 10, 2)]
        provider.days["BRZ6"].update({d: list(day_rows(d)) for d in new_days})

        second = run_job(
            iss_worker, session_factory, "import.iss", contract_id=contract_id
        )

        assert second.result["window"] == ["2026-09-28", "2026-10-02"]
        assert provider.candle_calls == [("BRZ6", new_days[0], new_days[1])]
        assert second.result["inserted"] == 60
        assert count(session_factory, RawCandle1m) == 150

    def test_today_is_never_loaded_because_it_is_incomplete(
        self,
        iss_worker: Worker,
        session_factory: sessionmaker[Session],
        provider: FakeProvider,
        root_id: int,
    ) -> None:
        contract_id = self.synced(iss_worker, session_factory, root_id)
        provider.days["BRZ6"][TODAY] = list(day_rows(TODAY))

        job = run_job(
            iss_worker, session_factory, "import.iss", contract_id=contract_id
        )

        assert job.result["window"][1] == "2026-10-04"
        assert all(call[2] < TODAY for call in provider.candle_calls)

    def test_interrupted_load_resumes_with_the_first_missing_chunk(
        self,
        iss_worker: Worker,
        session_factory: sessionmaker[Session],
        provider: FakeProvider,
        root_id: int,
    ) -> None:
        contract_id = self.synced(iss_worker, session_factory, root_id)
        provider.fail_on = lambda _s, start, _e: (
            IssError("ISS недоступен: 503") if start == WED else None
        )

        failed = run_job(
            iss_worker, session_factory, "import.iss", contract_id=contract_id
        )

        assert failed.status == "failed"
        assert failed.error == "ISS недоступен: 503"
        assert count(session_factory, RawCandle1m) == 60  # первый кусок сохранён
        provider.fail_on = None
        provider.candle_calls.clear()

        resumed = run_job(
            iss_worker, session_factory, "import.iss", contract_id=contract_id
        )

        assert resumed.status == "succeeded", resumed.error
        assert provider.candle_calls == [("BRZ6", WED, WED)]  # только недостающее
        assert count(session_factory, RawCandle1m) == 90

    def test_from_and_till_limit_the_window(
        self,
        iss_worker: Worker,
        session_factory: sessionmaker[Session],
        provider: FakeProvider,
        root_id: int,
    ) -> None:
        contract_id = self.synced(iss_worker, session_factory, root_id)

        job = run_job(
            iss_worker,
            session_factory,
            "import.iss",
            contract_id=contract_id,
            **{"from": "2026-09-29", "till": "2026-09-29"},
        )

        assert job.result["window"] == ["2026-09-29", "2026-09-29"]
        assert provider.candle_calls == [("BRZ6", TUE, TUE)]

    def test_invalid_date_parameter(
        self,
        iss_worker: Worker,
        session_factory: sessionmaker[Session],
        root_id: int,
    ) -> None:
        contract_id = self.synced(iss_worker, session_factory, root_id)

        job = run_job(
            iss_worker,
            session_factory,
            "import.iss",
            contract_id=contract_id,
            till="вчера",
        )

        assert job.status == "failed"
        assert "till" in job.error

    def test_rejected_rows_are_counted_and_do_not_fail_the_job(
        self,
        iss_worker: Worker,
        session_factory: sessionmaker[Session],
        provider: FakeProvider,
        root_id: int,
    ) -> None:
        contract_id = self.synced(iss_worker, session_factory, root_id)
        provider.days["BRZ6"][MON].insert(
            5, RowError(6, "{...}", "bad_datetime", "время свечи 'вчера'")
        )

        job = run_job(
            iss_worker, session_factory, "import.iss", contract_id=contract_id
        )

        assert job.status == "succeeded", job.error
        assert job.result["rows_rejected"] == 1
        assert job.result["inserted"] == 90

    def test_contract_without_iss_code_asks_to_sync_first(
        self,
        iss_worker: Worker,
        session_factory: sessionmaker[Session],
        root_id: int,
    ) -> None:
        with session_factory() as session:
            manual = Contract(root_id=root_id, expiration_date=date(2027, 1, 4))
            session.add(manual)
            session.flush()
            manual_id = manual.id
            session.commit()

        job = run_job(iss_worker, session_factory, "import.iss", contract_id=manual_id)

        assert job.status == "failed"
        assert "iss.sync_root" in job.error

    def test_contract_without_minute_candles_is_a_note_not_a_failure(
        self,
        iss_worker: Worker,
        session_factory: sessionmaker[Session],
        provider: FakeProvider,
        root_id: int,
    ) -> None:
        contract_id = self.synced(iss_worker, session_factory, root_id)
        provider.range_override["BRZ6"] = None

        job = run_job(
            iss_worker, session_factory, "import.iss", contract_id=contract_id
        )

        assert job.status == "succeeded"
        assert "нет минутных свечей" in job.result["note"]

    def test_provider_is_always_closed(
        self,
        iss_worker: Worker,
        session_factory: sessionmaker[Session],
        provider: FakeProvider,
        root_id: int,
    ) -> None:
        contract_id = self.synced(iss_worker, session_factory, root_id)
        provider.closed = 0
        provider.fail_on = lambda *_args: IssError("сбой")

        run_job(iss_worker, session_factory, "import.iss", contract_id=contract_id)

        assert provider.closed == 1


class TestWholeRoot:
    def test_sync_then_worker_loads_every_contract_and_a_rerun_only_what_is_new(
        self,
        iss_worker: Worker,
        session_factory: sessionmaker[Session],
        provider: FakeProvider,
        root_id: int,
    ) -> None:
        provider.set_days("BRX6", [MON, TUE])
        enqueue(session_factory, "iss.sync_root", {"root_id": root_id})

        processed = 0
        while iss_worker.run_once(Event()):
            processed += 1

        assert processed == 5  # sync + два import.iss + две сборки баров
        assert count(session_factory, RawCandle1m) == 90 + 60
        with session_factory() as session:
            statuses = set(session.scalars(select(Job.status)))
        assert statuses == {"succeeded"}

        # Повторная синхронизация: контракты те же, а загружать больше нечего.
        provider.candle_calls.clear()
        enqueue(session_factory, "iss.sync_root", {"root_id": root_id})
        while iss_worker.run_once(Event()):
            pass
        assert provider.candle_calls == []
        assert count(session_factory, RawCandle1m) == 150
