"""Интеграционные тесты raw 1m, конфликтов и dataset versions (TRADER_DATABASE_URL)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from trader_db import (
    Candle1m,
    create_dataset_version,
    extend_dataset_version,
    finish_import,
    insert_candles,
    latest_dataset_version,
    read_candles,
    record_error,
    resolve_conflicts,
    start_import,
)
from trader_db.models import (
    DataImport,
    DataImportError,
    DatasetVersion,
    ImportConflict,
    RawCandle1m,
)

T0 = datetime(2026, 9, 29, 4, 0, tzinfo=UTC)


def candle(minute: int, close: str = "100.5", **overrides: Any) -> Candle1m:
    values: dict[str, Any] = {
        "timestamp": T0 + timedelta(minutes=minute),
        "open": Decimal("100"),
        "high": Decimal("110"),
        "low": Decimal("80"),
        "close": Decimal(close),
        "volume": Decimal(10),
    }
    values.update(overrides)
    return Candle1m(**values)


def run_import(
    session: Session,
    contract_id: int,
    candles: list[Candle1m],
    *,
    finish: bool = True,
) -> int:
    import_id = start_import(
        session, provider_code="csv", contract_id=contract_id, source_type="file"
    )
    insert_candles(session, import_id, candles)
    if finish:
        finish_import(session, import_id)
    return import_id


def closes(candles: list[Candle1m]) -> list[Decimal]:
    return [c.close for c in candles]


def active_count(session: Session) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(RawCandle1m)
            .where(RawCandle1m.superseded_by_import_id.is_(None))
        )
        or 0
    )


class TestInsert:
    def test_new_candles_are_inserted(self, session: Session, contract_id: int) -> None:
        import_id = start_import(
            session, provider_code="csv", contract_id=contract_id, source_type="file"
        )

        report = insert_candles(session, import_id, [candle(0), candle(1), candle(2)])

        assert (report.inserted, report.duplicates, report.conflicts) == (3, 0, 0)
        assert active_count(session) == 3

    def test_identical_reimport_is_all_duplicates(
        self, session: Session, contract_id: int
    ) -> None:
        run_import(session, contract_id, [candle(0), candle(1)])
        second = start_import(
            session, provider_code="csv", contract_id=contract_id, source_type="file"
        )

        report = insert_candles(session, second, [candle(0), candle(1)])

        assert (report.inserted, report.duplicates, report.conflicts) == (0, 2, 0)
        assert active_count(session) == 2
        assert session.scalar(select(func.count()).select_from(ImportConflict)) == 0

    def test_changed_candle_is_a_conflict_and_does_not_touch_data(
        self, session: Session, contract_id: int
    ) -> None:
        run_import(session, contract_id, [candle(0), candle(1)])
        second = start_import(
            session, provider_code="csv", contract_id=contract_id, source_type="file"
        )

        report = insert_candles(session, second, [candle(1, close="105")])

        assert (report.inserted, report.duplicates, report.conflicts) == (0, 0, 1)
        conflict = session.scalars(select(ImportConflict)).one()
        assert (conflict.status, conflict.new_close) == ("pending", Decimal("105"))
        active = session.scalars(
            select(RawCandle1m).where(
                RawCandle1m.timestamp == T0 + timedelta(minutes=1)
            )
        ).one()
        assert active.close == Decimal("100.5")

    def test_mixed_batch_is_classified_per_candle(
        self, session: Session, contract_id: int
    ) -> None:
        run_import(session, contract_id, [candle(0), candle(1)])
        second = start_import(
            session, provider_code="csv", contract_id=contract_id, source_type="file"
        )

        report = insert_candles(
            session, second, [candle(0), candle(1, close="105"), candle(2)]
        )

        assert (report.inserted, report.duplicates, report.conflicts) == (1, 1, 1)

    def test_small_batches_give_the_same_result(
        self, session: Session, contract_id: int
    ) -> None:
        run_import(session, contract_id, [candle(i) for i in range(3)])
        second = start_import(
            session, provider_code="csv", contract_id=contract_id, source_type="file"
        )
        candles = [candle(0), candle(1, close="105"), candle(2), candle(3), candle(4)]

        report = insert_candles(session, second, candles, batch_size=2)

        assert (report.inserted, report.duplicates, report.conflicts) == (2, 2, 1)

    def test_naive_timestamp_is_rejected(
        self, session: Session, contract_id: int
    ) -> None:
        import_id = start_import(
            session, provider_code="csv", contract_id=contract_id, source_type="file"
        )

        with pytest.raises(ValueError, match="timezone-aware"):
            insert_candles(
                session, import_id, [candle(0, timestamp=datetime(2026, 9, 29, 4))]
            )

    def test_invalid_candle_is_rejected_even_when_it_would_conflict(
        self, session: Session, contract_id: int
    ) -> None:
        run_import(session, contract_id, [candle(0)])
        second = start_import(
            session, provider_code="csv", contract_id=contract_id, source_type="file"
        )

        with pytest.raises(IntegrityError), session.begin_nested():
            insert_candles(session, second, [candle(0, high=Decimal("50"))])

    def test_duplicate_timestamps_in_one_import_are_rejected(
        self, session: Session, contract_id: int
    ) -> None:
        import_id = start_import(
            session, provider_code="csv", contract_id=contract_id, source_type="file"
        )

        with pytest.raises(ValueError, match="Повторяющийся"):
            insert_candles(session, import_id, [candle(0), candle(0)])

    def test_finished_import_cannot_accept_more_candles(
        self, session: Session, contract_id: int
    ) -> None:
        import_id = run_import(session, contract_id, [candle(0)])

        with pytest.raises(ValueError, match="уже завершён"):
            insert_candles(session, import_id, [candle(1)])

    def test_unknown_provider_is_rejected(
        self, session: Session, contract_id: int
    ) -> None:
        with pytest.raises(LookupError):
            start_import(
                session,
                provider_code="nope",
                contract_id=contract_id,
                source_type="file",
            )

    def test_finish_and_errors_are_recorded(
        self, session: Session, contract_id: int
    ) -> None:
        import_id = start_import(
            session, provider_code="csv", contract_id=contract_id, source_type="file"
        )
        record_error(
            session, import_id, reason_code="high_lt_low", message="bad", row_number=7
        )

        finish_import(session, import_id, report={"rows": 1})

        data_import = session.get(DataImport, import_id)
        assert data_import is not None
        assert (data_import.status, data_import.report) == ("completed", {"rows": 1})
        assert data_import.finished_at is not None
        error = session.scalars(select(DataImportError)).one()
        assert (error.row_number, error.reason_code) == (7, "high_lt_low")


class TestConflictResolution:
    def _setup_conflict(
        self, session: Session, contract_id: int
    ) -> tuple[int, int, DatasetVersion]:
        first = run_import(session, contract_id, [candle(0), candle(1), candle(2)])
        v1 = extend_dataset_version(session, first)
        second = start_import(
            session, provider_code="csv", contract_id=contract_id, source_type="file"
        )
        insert_candles(session, second, [candle(1, close="105")])
        finish_import(session, second)
        return first, second, v1

    def test_accept_supersedes_old_row_under_a_resolution_import(
        self, session: Session, contract_id: int
    ) -> None:
        first, second, _ = self._setup_conflict(session, contract_id)

        resolution_id = resolve_conflicts(session, second, accept=True)

        assert resolution_id is not None
        resolution = session.get(DataImport, resolution_id)
        assert resolution is not None
        assert (resolution.kind, resolution.resolves_import_id, resolution.status) == (
            "conflict_resolution",
            second,
            "completed",
        )
        moment = T0 + timedelta(minutes=1)
        rows = session.scalars(
            select(RawCandle1m)
            .where(RawCandle1m.timestamp == moment)
            .order_by(RawCandle1m.id)
        ).all()
        assert [
            (r.data_import_id, r.close, r.superseded_by_import_id) for r in rows
        ] == [
            (first, Decimal("100.5"), resolution_id),
            (resolution_id, Decimal("105"), None),
        ]
        conflict = session.scalars(select(ImportConflict)).one()
        assert (conflict.status, conflict.resolution_import_id) == (
            "accepted",
            resolution_id,
        )

    def test_old_dataset_versions_keep_their_data_after_accept(
        self, session: Session, contract_id: int
    ) -> None:
        _, second, v1 = self._setup_conflict(session, contract_id)
        v2 = extend_dataset_version(session, second)
        before = closes(read_candles(session, v1.id))

        resolution_id = resolve_conflicts(session, second, accept=True)
        assert resolution_id is not None
        v3 = extend_dataset_version(session, resolution_id)

        assert closes(read_candles(session, v1.id)) == before
        assert closes(read_candles(session, v1.id)) == [
            Decimal("100.5"),
            Decimal("100.5"),
            Decimal("100.5"),
        ]
        assert closes(read_candles(session, v2.id)) == before
        assert closes(read_candles(session, v3.id)) == [
            Decimal("100.5"),
            Decimal("105"),
            Decimal("100.5"),
        ]
        assert (v1.version_number, v2.version_number, v3.version_number) == (1, 2, 3)

    def test_reject_leaves_data_unchanged(
        self, session: Session, contract_id: int
    ) -> None:
        _, second, v1 = self._setup_conflict(session, contract_id)

        assert resolve_conflicts(session, second, accept=False) is None

        conflict = session.scalars(select(ImportConflict)).one()
        assert (conflict.status, conflict.resolution_import_id) == ("rejected", None)
        assert active_count(session) == 3
        assert closes(read_candles(session, v1.id))[1] == Decimal("100.5")
        assert (
            session.scalar(
                select(func.count())
                .select_from(DataImport)
                .where(DataImport.kind == "conflict_resolution")
            )
            == 0
        )

    def test_only_selected_conflicts_are_resolved(
        self, session: Session, contract_id: int
    ) -> None:
        run_import(session, contract_id, [candle(0), candle(1)])
        second = start_import(
            session, provider_code="csv", contract_id=contract_id, source_type="file"
        )
        insert_candles(session, second, [candle(0, close="90"), candle(1, close="95")])
        finish_import(session, second)
        first_conflict = session.scalars(
            select(ImportConflict).order_by(ImportConflict.timestamp)
        ).first()
        assert first_conflict is not None

        resolve_conflicts(
            session, second, accept=True, conflict_ids=[first_conflict.id]
        )

        statuses = session.scalars(
            select(ImportConflict.status).order_by(ImportConflict.timestamp)
        ).all()
        assert statuses == ["accepted", "pending"]

    def test_second_resolution_finds_nothing_pending(
        self, session: Session, contract_id: int
    ) -> None:
        _, second, _ = self._setup_conflict(session, contract_id)
        resolve_conflicts(session, second, accept=True)

        assert resolve_conflicts(session, second, accept=True) is None


class TestDatasetVersions:
    def test_version_records_range_rows_and_manifest(
        self, session: Session, contract_id: int
    ) -> None:
        first = run_import(session, contract_id, [candle(2), candle(0), candle(1)])

        version = extend_dataset_version(session, first)

        assert version.version_number == 1
        assert version.row_count == 3
        assert (version.range_start, version.range_end) == (
            T0,
            T0 + timedelta(minutes=2),
        )
        assert latest_dataset_version(session, contract_id) == version

    def test_extend_is_idempotent_for_the_same_import(
        self, session: Session, contract_id: int
    ) -> None:
        first = run_import(session, contract_id, [candle(0)])

        one = extend_dataset_version(session, first)
        two = extend_dataset_version(session, first)

        assert one.id == two.id

    def test_new_import_extends_previous_manifest(
        self, session: Session, contract_id: int
    ) -> None:
        first = run_import(session, contract_id, [candle(0)])
        v1 = extend_dataset_version(session, first)
        second = run_import(session, contract_id, [candle(1)])

        v2 = extend_dataset_version(session, second)

        assert v2.row_count == 2
        assert closes(read_candles(session, v1.id)) == [Decimal("100.5")]
        assert len(read_candles(session, v2.id)) == 2

    def test_running_import_cannot_be_in_manifest(
        self, session: Session, contract_id: int
    ) -> None:
        running = run_import(session, contract_id, [candle(0)], finish=False)

        with pytest.raises(ValueError, match="не завершён"):
            extend_dataset_version(session, running)

    def test_empty_manifest_is_rejected(
        self, session: Session, contract_id: int
    ) -> None:
        with pytest.raises(ValueError, match="пустым"):
            create_dataset_version(session, contract_id, "1m", [])

    def test_inconsistent_manifest_is_rejected(
        self, session: Session, contract_id: int
    ) -> None:
        first = run_import(session, contract_id, [candle(0)])
        resolutions: list[int] = []
        for close in ("101", "102"):
            source = start_import(
                session,
                provider_code="csv",
                contract_id=contract_id,
                source_type="file",
            )
            insert_candles(session, source, [candle(0, close=close)])
            finish_import(session, source)
            resolution = resolve_conflicts(session, source, accept=True)
            assert resolution is not None
            resolutions.append(resolution)

        # Манифест без промежуточного разрешения показал бы две строки одной свечи.
        with pytest.raises(ValueError, match="неконсистентен"):
            create_dataset_version(session, contract_id, "1m", [first, resolutions[1]])

    def test_read_candles_filters_by_half_open_range(
        self, session: Session, contract_id: int
    ) -> None:
        first = run_import(session, contract_id, [candle(i) for i in range(5)])
        version = extend_dataset_version(session, first)

        window = read_candles(
            session,
            version.id,
            start=T0 + timedelta(minutes=1),
            end=T0 + timedelta(minutes=3),
        )

        assert [c.timestamp for c in window] == [
            T0 + timedelta(minutes=1),
            T0 + timedelta(minutes=2),
        ]


class TestDatabaseGuards:
    def _row_id(self, session: Session, contract_id: int) -> int:
        run_import(session, contract_id, [candle(0)])
        return session.scalars(select(RawCandle1m.id)).one()

    def test_raw_values_cannot_be_updated(
        self, session: Session, contract_id: int
    ) -> None:
        row_id = self._row_id(session, contract_id)

        with pytest.raises(DBAPIError, match="неизменяемы"), session.begin_nested():
            session.execute(
                text("UPDATE raw_candles_1m SET close = 1 WHERE id = :id"),
                {"id": row_id},
            )

    def test_raw_rows_cannot_be_deleted(
        self, session: Session, contract_id: int
    ) -> None:
        row_id = self._row_id(session, contract_id)

        with (
            pytest.raises(DBAPIError, match="удаление запрещено"),
            session.begin_nested(),
        ):
            session.execute(
                text("DELETE FROM raw_candles_1m WHERE id = :id"), {"id": row_id}
            )

    def test_supersede_is_allowed_once(
        self, session: Session, contract_id: int
    ) -> None:
        row_id = self._row_id(session, contract_id)
        other = run_import(session, contract_id, [candle(5)])
        session.execute(
            text(
                "UPDATE raw_candles_1m SET superseded_by_import_id = :i WHERE id = :id"
            ),
            {"i": other, "id": row_id},
        )

        with pytest.raises(DBAPIError, match="уже замещена"), session.begin_nested():
            session.execute(
                text(
                    "UPDATE raw_candles_1m "
                    "SET superseded_by_import_id = :i WHERE id = :id"
                ),
                {"i": other, "id": row_id},
            )

    def test_only_one_active_row_per_candle(
        self, session: Session, contract_id: int
    ) -> None:
        run_import(session, contract_id, [candle(0)])
        other = start_import(
            session, provider_code="csv", contract_id=contract_id, source_type="file"
        )
        duplicate = RawCandle1m(
            contract_id=contract_id,
            timestamp=T0,
            open=Decimal(1),
            high=Decimal(1),
            low=Decimal(1),
            close=Decimal(1),
            volume=Decimal(0),
            data_import_id=other,
        )

        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(duplicate)
            session.flush()

    @pytest.mark.parametrize(
        "overrides",
        [
            {"high": Decimal("98")},
            {"low": Decimal("102")},
            {"volume": Decimal("-1")},
        ],
    )
    def test_invalid_candles_are_rejected_by_check_constraints(
        self, session: Session, contract_id: int, overrides: dict[str, Any]
    ) -> None:
        import_id = start_import(
            session, provider_code="csv", contract_id=contract_id, source_type="file"
        )

        with pytest.raises(IntegrityError), session.begin_nested():
            insert_candles(session, import_id, [candle(0, **overrides)])

    def test_dataset_versions_are_immutable(
        self, session: Session, contract_id: int
    ) -> None:
        first = run_import(session, contract_id, [candle(0)])
        version = extend_dataset_version(session, first)

        with pytest.raises(DBAPIError, match="запрещены"), session.begin_nested():
            session.execute(
                text("UPDATE dataset_versions SET row_count = 0 WHERE id = :id"),
                {"id": version.id},
            )
        with pytest.raises(DBAPIError, match="запрещены"), session.begin_nested():
            session.execute(
                text(
                    "DELETE FROM dataset_version_imports WHERE dataset_version_id = :id"
                ),
                {"id": version.id},
            )
