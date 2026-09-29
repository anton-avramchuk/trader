"""Интеграционные тесты конвейера импорта 1m (нужен TRADER_DATABASE_URL)."""

import time
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from trader_engine.calendar import TradingCalendar, moex_forts_calendar
from trader_engine.ingest import RawRow, RowError

from trader_db import (
    ImportOutcome,
    extend_dataset_version,
    latest_dataset_version,
    read_candles,
    resolve_conflicts,
    run_candle_import,
    start_import,
)
from trader_db import import_pipeline as pipeline_module
from trader_db.models import (
    DataImport,
    DataImportError,
    DatasetVersion,
    ImportConflict,
    RawCandle1m,
)

CALENDAR = moex_forts_calendar()
MINUTE = timedelta(minutes=1)
# Вторник 2026-09-29, режим «с 23.03.2026»: одна непрерывная сессия 04:00–20:50 UTC.
DAY = datetime(2026, 9, 29, 4, 0, tzinfo=UTC)
DAY_MINUTES = 17 * 60 - 10


def make_row(number: int, stamp: datetime, **overrides: Any) -> RawRow:
    values: dict[str, Any] = {
        "row_number": number,
        "raw": f"row {number}",
        "timestamp": stamp,
        "open": Decimal("100"),
        "high": Decimal("110"),
        "low": Decimal("90"),
        "close": Decimal("100.5"),
        "volume": Decimal("10"),
    }
    values.update(overrides)
    return RawRow(**values)


def day_rows(
    count: int = DAY_MINUTES, start: datetime = DAY, **overrides: Any
) -> list[RawRow]:
    return [make_row(i + 1, start + i * MINUTE, **overrides) for i in range(count)]


def do_import(
    factory: sessionmaker[Session],
    contract_id: int,
    rows: list[RawRow | RowError],
    **kwargs: Any,
) -> ImportOutcome:
    return run_candle_import(
        factory,
        provider_code="csv",
        contract_id=contract_id,
        source_type="file",
        source_name="test.csv",
        rows=rows,
        calendar=kwargs.pop("calendar", CALENDAR),
        **kwargs,
    )


def count(factory: sessionmaker[Session], model: type) -> int:
    with factory() as session:
        return session.scalar(select(func.count()).select_from(model)) or 0


class TestReport:
    def test_complete_day_report(
        self, session_factory: sessionmaker[Session], committed_contract_id: int
    ) -> None:
        rows: list[RawRow | RowError] = [*day_rows()]

        outcome = do_import(session_factory, committed_contract_id, rows)

        report = outcome.report
        assert (
            report["rows_total"],
            report["rows_valid"],
            report["rows_rejected"],
        ) == (
            DAY_MINUTES,
            DAY_MINUTES,
            0,
        )
        assert (report["inserted"], report["duplicates"], report["conflicts"]) == (
            DAY_MINUTES,
            0,
            0,
        )
        assert report["range"] == {
            "start": DAY.isoformat(),
            "end": (DAY + (DAY_MINUTES - 1) * MINUTE).isoformat(),
        }
        assert (report["min_price"], report["max_price"]) == ("90", "110")
        assert report["missing"]["intervals"] == 0
        assert report["warnings"] == {"out_of_order_rows": 0, "outside_session_rows": 0}
        with session_factory() as session:
            stored = session.get(DataImport, outcome.import_id)
            assert stored is not None
            assert (stored.status, stored.report["inserted"]) == (
                "completed",
                DAY_MINUTES,
            )
            assert stored.finished_at is not None

    def test_report_creates_first_dataset_version(
        self, session_factory: sessionmaker[Session], committed_contract_id: int
    ) -> None:
        rows: list[RawRow | RowError] = [*day_rows()]

        outcome = do_import(session_factory, committed_contract_id, rows)

        with session_factory() as session:
            version = latest_dataset_version(session, committed_contract_id)
            assert version is not None
            assert (version.id, version.version_number, version.row_count) == (
                outcome.dataset_version_id,
                1,
                DAY_MINUTES,
            )
            assert outcome.report["dataset_version_id"] == version.id
            assert len(read_candles(session, version.id)) == DAY_MINUTES

    def test_missing_interval_is_reported_but_overnight_gap_is_not(
        self, session_factory: sessionmaker[Session], committed_contract_id: int
    ) -> None:
        hole = range(360, 370)  # 10:00–10:10 UTC
        tuesday = [m for i, m in enumerate(day_rows()) if i not in hole]
        wednesday = day_rows(start=DAY + timedelta(days=1))
        rows: list[RawRow | RowError] = [*tuesday, *wednesday]

        outcome = do_import(session_factory, committed_contract_id, rows)

        missing = outcome.report["missing"]
        assert (missing["intervals"], missing["minutes"]) == (1, 10)
        assert missing["sample"] == [
            {
                "start": (DAY + 360 * MINUTE).isoformat(),
                "end": (DAY + 370 * MINUTE).isoformat(),
                "minutes": 10,
            }
        ]

    def test_candles_outside_sessions_are_a_warning_not_an_error(
        self, session_factory: sessionmaker[Session], committed_contract_id: int
    ) -> None:
        late = DAY + timedelta(hours=17)  # 21:00 UTC — биржа закрыта
        rows: list[RawRow | RowError] = [*day_rows(5), make_row(99, late)]

        outcome = do_import(session_factory, committed_contract_id, rows)

        assert outcome.report["inserted"] == 6
        assert outcome.report["warnings"]["outside_session_rows"] == 1
        assert outcome.report["outside_session_sample"] == [late.isoformat()]

    def test_out_of_order_rows_are_counted_and_stored_sorted(
        self, session_factory: sessionmaker[Session], committed_contract_id: int
    ) -> None:
        rows: list[RawRow | RowError] = [*reversed(day_rows(4))]

        outcome = do_import(session_factory, committed_contract_id, rows)

        assert outcome.report["warnings"]["out_of_order_rows"] == 3
        with session_factory() as session:
            assert outcome.dataset_version_id is not None
            stamps = [
                c.timestamp for c in read_candles(session, outcome.dataset_version_id)
            ]
        assert stamps == sorted(stamps)

    def test_progress_moves_forward_from_start_to_finish(
        self, session_factory: sessionmaker[Session], committed_contract_id: int
    ) -> None:
        seen: list[float] = []

        def record(fraction: float, _message: str) -> None:
            seen.append(fraction)

        rows: list[RawRow | RowError] = [*day_rows()]

        do_import(
            session_factory,
            committed_contract_id,
            rows,
            progress=record,
        )

        assert seen == sorted(seen)
        assert seen[0] < 0.1
        assert 0.9 <= seen[-1] < 1.0


class TestErrors:
    def test_rejected_rows_are_stored_with_reason_and_do_not_block_the_rest(
        self, session_factory: sessionmaker[Session], committed_contract_id: int
    ) -> None:
        rows: list[RawRow | RowError] = [
            make_row(1, DAY),
            make_row(2, DAY + MINUTE, volume=Decimal("-1")),
            make_row(3, DAY + 2 * MINUTE, high=Decimal("80")),
            RowError(4, "мусор", "parse_error", "нечисловое значение"),
            make_row(5, DAY + 4 * MINUTE),
        ]

        outcome = do_import(session_factory, committed_contract_id, rows)

        report = outcome.report
        assert (report["inserted"], report["rows_rejected"]) == (2, 3)
        assert report["error_counts"] == {
            "negative_volume": 1,
            "high_lt_low": 1,
            "parse_error": 1,
        }
        with session_factory() as session:
            stored = session.scalars(
                select(DataImportError).order_by(DataImportError.row_number)
            ).all()
        assert [(e.row_number, e.reason_code, e.raw_row) for e in stored] == [
            (2, "negative_volume", "row 2"),
            (3, "high_lt_low", "row 3"),
            (4, "parse_error", "мусор"),
        ]

    def test_file_without_valid_rows_still_completes(
        self, session_factory: sessionmaker[Session], committed_contract_id: int
    ) -> None:
        rows: list[RawRow | RowError] = [make_row(1, DAY, volume=Decimal("-1"))]

        outcome = do_import(session_factory, committed_contract_id, rows)

        assert (outcome.report["inserted"], outcome.report["range"]) == (0, None)
        assert outcome.report["min_price"] is None
        assert outcome.dataset_version_id is None

    def test_stored_errors_are_capped_but_counted(
        self,
        session_factory: sessionmaker[Session],
        committed_contract_id: int,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(pipeline_module, "MAX_STORED_ERRORS", 3)
        rows: list[RawRow | RowError] = [
            make_row(i, DAY + i * MINUTE, volume=Decimal("-1")) for i in range(10)
        ]

        outcome = do_import(session_factory, committed_contract_id, rows)

        assert outcome.report["rows_rejected"] == 10
        assert outcome.report["error_counts"] == {"negative_volume": 10}
        assert (
            outcome.report["errors_stored"],
            outcome.report["errors_truncated"],
        ) == (3, True)
        assert count(session_factory, DataImportError) == 3

    def test_non_positive_prices_can_be_allowed(
        self, session_factory: sessionmaker[Session], committed_contract_id: int
    ) -> None:
        row = make_row(
            1,
            DAY,
            open=Decimal("-1"),
            high=Decimal("-0.5"),
            low=Decimal("-2"),
            close=Decimal("-1"),
        )

        outcome = do_import(
            session_factory,
            committed_contract_id,
            [row],
            allow_non_positive_prices=True,
        )

        assert outcome.report["inserted"] == 1


class TestDuplicatesAndConflicts:
    def test_reimport_is_all_duplicates_and_creates_no_version(
        self, session_factory: sessionmaker[Session], committed_contract_id: int
    ) -> None:
        rows: list[RawRow | RowError] = [*day_rows(50)]
        first = do_import(session_factory, committed_contract_id, rows)

        second = do_import(session_factory, committed_contract_id, rows)

        assert (second.report["inserted"], second.report["duplicates"]) == (0, 50)
        assert second.report["duplicates_in_db"] == 50
        assert second.dataset_version_id is None
        with session_factory() as session:
            latest = latest_dataset_version(session, committed_contract_id)
            assert latest is not None
            assert latest.id == first.dataset_version_id
        assert count(session_factory, DatasetVersion) == 1

    def test_duplicates_inside_the_file_are_counted_separately(
        self, session_factory: sessionmaker[Session], committed_contract_id: int
    ) -> None:
        rows: list[RawRow | RowError] = [*day_rows(3), make_row(50, DAY)]

        outcome = do_import(session_factory, committed_contract_id, rows)

        assert (outcome.report["inserted"], outcome.report["duplicates_in_file"]) == (
            3,
            1,
        )

    def test_changed_candle_becomes_a_pending_conflict_without_new_version(
        self, session_factory: sessionmaker[Session], committed_contract_id: int
    ) -> None:
        first = do_import(session_factory, committed_contract_id, [*day_rows(5)])
        changed: list[RawRow | RowError] = [
            *day_rows(5)[:2],
            make_row(3, DAY + 2 * MINUTE, close=Decimal("101")),
        ]

        second = do_import(session_factory, committed_contract_id, changed)

        assert (second.report["inserted"], second.report["duplicates"]) == (0, 2)
        assert second.report["conflicts"] == 1
        assert second.dataset_version_id is None
        with session_factory() as session:
            conflict = session.scalars(select(ImportConflict)).one()
            assert (conflict.status, conflict.new_close) == ("pending", Decimal("101"))
            assert first.dataset_version_id is not None
            closes = {c.close for c in read_candles(session, first.dataset_version_id)}
            assert closes == {Decimal("100.5")}

    def test_accepting_the_conflict_yields_a_new_version_with_new_value(
        self, session_factory: sessionmaker[Session], committed_contract_id: int
    ) -> None:
        first = do_import(session_factory, committed_contract_id, [*day_rows(5)])
        second = do_import(
            session_factory,
            committed_contract_id,
            [make_row(1, DAY, close=Decimal("101"))],
        )

        with session_factory() as session:
            resolution = resolve_conflicts(session, second.import_id, accept=True)
            assert resolution is not None
            version = extend_dataset_version(session, resolution)
            session.commit()
            new = read_candles(session, version.id)[0]
            assert first.dataset_version_id is not None
            old = read_candles(session, first.dataset_version_id)[0]

        assert (new.close, old.close) == (Decimal("101"), Decimal("100.5"))


class TestAtomicity:
    def test_cancellation_mid_import_leaves_nothing_behind(
        self, session_factory: sessionmaker[Session], committed_contract_id: int
    ) -> None:
        class Cancelled(Exception):
            pass

        def cancel_when_writing(fraction: float, _message: str) -> None:
            if fraction >= 0.6:
                raise Cancelled("отмена пользователем")

        rows: list[RawRow | RowError] = [
            *day_rows(),
            make_row(9999, DAY + timedelta(hours=1), volume=Decimal("-1")),
        ]

        with pytest.raises(Cancelled):
            do_import(
                session_factory,
                committed_contract_id,
                rows,
                progress=cancel_when_writing,
            )

        assert count(session_factory, RawCandle1m) == 0
        assert count(session_factory, DataImportError) == 0
        assert count(session_factory, DatasetVersion) == 0
        with session_factory() as session:
            (failed,) = session.scalars(select(DataImport)).all()
        assert failed.status == "failed"
        assert failed.error is not None
        assert "Cancelled" in failed.error

    def test_reimport_after_failure_inserts_everything_again(
        self, session_factory: sessionmaker[Session], committed_contract_id: int
    ) -> None:
        def explode(fraction: float, _message: str) -> None:
            if fraction >= 0.6:
                raise RuntimeError("сбой")

        rows: list[RawRow | RowError] = [*day_rows(200)]
        with pytest.raises(RuntimeError):
            do_import(session_factory, committed_contract_id, rows, progress=explode)

        outcome = do_import(session_factory, committed_contract_id, rows)

        # Нет «призрачных» активных свечей от сорвавшегося импорта.
        assert (outcome.report["inserted"], outcome.report["duplicates"]) == (200, 0)
        assert outcome.dataset_version_id is not None

    def test_failure_while_reading_the_source_marks_import_failed(
        self, session_factory: sessionmaker[Session], committed_contract_id: int
    ) -> None:
        def broken_source() -> Any:
            yield make_row(1, DAY)
            raise OSError("диск недоступен")

        with pytest.raises(OSError):
            run_candle_import(
                session_factory,
                provider_code="csv",
                contract_id=committed_contract_id,
                source_type="file",
                rows=broken_source(),
                calendar=CALENDAR,
            )

        assert count(session_factory, RawCandle1m) == 0
        with session_factory() as session:
            (failed,) = session.scalars(select(DataImport)).all()
        assert failed.status == "failed"
        assert failed.error is not None
        assert "OSError" in failed.error

    def test_restarted_job_abandons_its_previous_attempt(
        self, session_factory: sessionmaker[Session], committed_contract_id: int
    ) -> None:
        with session_factory() as session:
            orphan = start_import(
                session,
                provider_code="csv",
                contract_id=committed_contract_id,
                source_type="file",
                params={"job_id": 5},
            )
            other = start_import(
                session,
                provider_code="csv",
                contract_id=committed_contract_id,
                source_type="file",
                params={"job_id": 6},
            )
            session.commit()

        do_import(session_factory, committed_contract_id, [*day_rows(3)], job_id=5)

        with session_factory() as session:
            old = session.get(DataImport, orphan)
            unrelated = session.get(DataImport, other)
        assert old is not None
        assert unrelated is not None
        assert (old.status, old.error) == ("failed", "прерван: задача перезапущена")
        assert unrelated.status == "running"


class TestPerformance:
    def test_one_year_of_minute_candles_imports_in_reasonable_time(
        self, session_factory: sessionmaker[Session], committed_contract_id: int
    ) -> None:
        rows = year_rows(CALENDAR)
        assert len(rows) > 200_000

        started = time.perf_counter()
        outcome = do_import(session_factory, committed_contract_id, rows)
        elapsed = time.perf_counter() - started

        assert outcome.report["inserted"] == len(rows)
        assert outcome.report["missing"]["minutes"] == 0
        assert outcome.report["warnings"]["outside_session_rows"] == 0
        assert elapsed < 120, f"{len(rows)} rows took {elapsed:.1f}s"


def year_rows(calendar: TradingCalendar) -> list[RawRow | RowError]:
    """Минутные свечи по всем сессиям календаря за год (оба режима расписания)."""
    start = datetime.combine(date(2025, 10, 1), datetime.min.time(), tzinfo=UTC)
    end = datetime.combine(date(2026, 10, 1), datetime.min.time(), tzinfo=UTC)
    rows: list[RawRow | RowError] = []
    number = 0
    for session in calendar.sessions_between(start, end):
        moment = max(session.start, start)
        while moment < min(session.end, end):
            number += 1
            rows.append(make_row(number, moment))
            moment += MINUTE
    return rows
