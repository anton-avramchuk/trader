"""Импорт файлов как задача ``import.file`` (нужен TRADER_DATABASE_URL)."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Event
from typing import Any

import polars as pl
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from trader_db.models import DataImport, DataImportError, RawCandle1m

from tests.helpers import enqueue, load
from trader_worker.runner import Worker

FINAM_HEADER = "<TICKER>,<PER>,<DATE>,<TIME>,<OPEN>,<HIGH>,<LOW>,<CLOSE>,<VOL>\n"
FINAM_ROWS = (
    "BR-12.26,1,20260929,100000,70.10,70.20,70.05,70.15,120\n"
    "BR-12.26,1,20260929,100100,70.15,70.25,70.10,70.20,80\n"
    "BR-12.26,1,20260929,100200,70.20,70.30,70.15,70.25,60\n"
)
OPEN_0700_UTC = datetime(2026, 9, 29, 7, 0, tzinfo=UTC)

INLINE_MAPPING: dict[str, Any] = {
    "datetime": {"columns": ["time"], "format": "iso"},
    "open": "o",
    "high": "h",
    "low": "l",
    "close": "c",
    "volume": "v",
}


def json_candles() -> list[dict[str, Any]]:
    return [
        {
            "time": f"2026-09-29T07:0{i}:00Z",
            "o": 1,
            "h": 2,
            "l": 0.5,
            "c": 1.5,
            "v": 9 - i,
        }
        for i in range(2)
    ]


def run_file_job(
    worker: Worker,
    session_factory: sessionmaker[Session],
    contract_id: int,
    **params: Any,
) -> tuple[int, Any]:
    job_id = enqueue(
        session_factory, "import.file", {"contract_id": contract_id, **params}
    )
    worker.run_once(Event())
    return job_id, load(session_factory, job_id)


def stored_stamps(factory: sessionmaker[Session]) -> list[datetime]:
    with factory() as session:
        return list(
            session.scalars(
                select(RawCandle1m.timestamp).order_by(RawCandle1m.timestamp)
            )
        )


def count(factory: sessionmaker[Session], model: type) -> int:
    with factory() as session:
        return session.scalar(select(func.count()).select_from(model)) or 0


class TestFormats:
    def test_finam_csv_through_the_builtin_preset(
        self,
        file_worker: Worker,
        session_factory: sessionmaker[Session],
        import_dir: Path,
        contract_id: int,
    ) -> None:
        (import_dir / "finam.csv").write_text(
            FINAM_HEADER + FINAM_ROWS, encoding="utf-8"
        )

        _, job = run_file_job(
            file_worker, session_factory, contract_id, file="finam.csv", preset="finam"
        )

        assert job.status == "succeeded", job.error
        assert job.result["report"]["inserted"] == 3
        assert job.result["report"]["range"]["start"] == OPEN_0700_UTC.isoformat()
        assert stored_stamps(session_factory) == [
            OPEN_0700_UTC + timedelta(minutes=i) for i in range(3)
        ]
        with session_factory() as session:
            candle = session.scalars(
                select(RawCandle1m).order_by(RawCandle1m.timestamp)
            ).first()
            assert candle is not None
            assert (candle.open, candle.close, candle.volume) == (
                Decimal("70.10"),
                Decimal("70.15"),
                Decimal("120"),
            )
            data_import = session.get(DataImport, job.result["import_id"])
            assert data_import is not None
            assert (data_import.source_name, data_import.status) == (
                "finam.csv",
                "completed",
            )

    def test_headerless_finam_file(
        self,
        file_worker: Worker,
        session_factory: sessionmaker[Session],
        import_dir: Path,
        contract_id: int,
    ) -> None:
        (import_dir / "raw.txt").write_text(FINAM_ROWS, encoding="utf-8")

        _, job = run_file_job(
            file_worker,
            session_factory,
            contract_id,
            file="raw.txt",
            preset="finam_no_header",
        )

        assert job.status == "succeeded", job.error
        assert job.result["report"]["inserted"] == 3

    def test_preset_can_be_adjusted_with_overrides(
        self,
        file_worker: Worker,
        session_factory: sessionmaker[Session],
        import_dir: Path,
        contract_id: int,
    ) -> None:
        (import_dir / "finam.csv").write_text(
            FINAM_HEADER + FINAM_ROWS, encoding="utf-8"
        )

        _, job = run_file_job(
            file_worker,
            session_factory,
            contract_id,
            file="finam.csv",
            preset="finam",
            overrides={"timezone": "UTC"},
        )

        assert job.status == "succeeded", job.error
        assert stored_stamps(session_factory)[0] == datetime(
            2026, 9, 29, 10, 0, tzinfo=UTC
        )

    def test_json_array_with_inline_mapping(
        self,
        file_worker: Worker,
        session_factory: sessionmaker[Session],
        import_dir: Path,
        contract_id: int,
    ) -> None:
        (import_dir / "c.json").write_text(json.dumps(json_candles()), encoding="utf-8")

        _, job = run_file_job(
            file_worker,
            session_factory,
            contract_id,
            file="c.json",
            mapping=INLINE_MAPPING,
        )

        assert job.status == "succeeded", job.error
        assert job.result["report"]["inserted"] == 2

    def test_ndjson_with_inline_mapping(
        self,
        file_worker: Worker,
        session_factory: sessionmaker[Session],
        import_dir: Path,
        contract_id: int,
    ) -> None:
        lines = "\n".join(json.dumps(candle) for candle in json_candles())
        (import_dir / "c.ndjson").write_text(lines + "\n", encoding="utf-8")

        _, job = run_file_job(
            file_worker,
            session_factory,
            contract_id,
            file="c.ndjson",
            mapping=INLINE_MAPPING,
        )

        assert job.status == "succeeded", job.error
        assert job.result["report"]["inserted"] == 2

    def test_parquet_with_inline_mapping(
        self,
        file_worker: Worker,
        session_factory: sessionmaker[Session],
        import_dir: Path,
        contract_id: int,
    ) -> None:
        pl.DataFrame(
            {
                "time": [datetime(2026, 9, 29, 10, 0), datetime(2026, 9, 29, 10, 1)],
                "o": [70.1, 70.15],
                "h": [70.2, 70.25],
                "l": [70.05, 70.1],
                "c": [70.15, 70.2],
                "v": [120, 80],
            }
        ).write_parquet(import_dir / "c.parquet")

        _, job = run_file_job(
            file_worker,
            session_factory,
            contract_id,
            file="c.parquet",
            mapping=INLINE_MAPPING,
        )

        assert job.status == "succeeded", job.error
        # Время без пояса в Parquet трактуется в поясе маппинга (Москва).
        assert stored_stamps(session_factory)[0] == OPEN_0700_UTC

    def test_bad_rows_do_not_fail_the_job_and_are_reported_with_line_numbers(
        self,
        file_worker: Worker,
        session_factory: sessionmaker[Session],
        import_dir: Path,
        contract_id: int,
    ) -> None:
        (import_dir / "dirty.csv").write_text(
            FINAM_HEADER
            + "BR-12.26,1,20260929,100000,70.10,70.20,70.05,70.15,120\n"
            + "BR-12.26,1,20260929,100100,abc,70.25,70.10,70.20,80\n"
            + "BR-12.26,1,20260929,100200,70.20,70.30,70.15,70.25,-5\n",
            encoding="utf-8",
        )

        _, job = run_file_job(
            file_worker, session_factory, contract_id, file="dirty.csv", preset="finam"
        )

        assert job.status == "succeeded", job.error
        report = job.result["report"]
        assert (report["inserted"], report["rows_rejected"]) == (1, 2)
        assert report["error_counts"] == {"bad_number": 1, "negative_volume": 1}
        with session_factory() as session:
            errors = session.scalars(
                select(DataImportError).order_by(DataImportError.row_number)
            ).all()
        assert [(e.row_number, e.reason_code) for e in errors] == [
            (3, "bad_number"),
            (4, "negative_volume"),
        ]
        assert (
            errors[0].raw_row == "BR-12.26,1,20260929,100100,abc,70.25,70.10,70.20,80"
        )


class TestUnderstandableErrors:
    """Каждая ошибка: понятное сообщение без traceback, данные не остаются."""

    def assert_failed_cleanly(
        self, factory: sessionmaker[Session], job: Any, *fragments: str
    ) -> None:
        assert job.status == "failed"
        assert job.error is not None
        assert "Traceback" not in job.error
        for fragment in fragments:
            assert fragment in job.error, job.error
        assert count(factory, RawCandle1m) == 0

    def test_unknown_preset_lists_the_available_ones(
        self,
        file_worker: Worker,
        session_factory: sessionmaker[Session],
        import_dir: Path,
        contract_id: int,
    ) -> None:
        (import_dir / "f.csv").write_text(FINAM_HEADER + FINAM_ROWS, encoding="utf-8")

        _, job = run_file_job(
            file_worker, session_factory, contract_id, file="f.csv", preset="nope"
        )

        self.assert_failed_cleanly(session_factory, job, "'nope'", "'finam'")

    def test_wrong_column_lists_the_columns_of_the_file(
        self,
        file_worker: Worker,
        session_factory: sessionmaker[Session],
        import_dir: Path,
        contract_id: int,
    ) -> None:
        (import_dir / "f.csv").write_text(FINAM_HEADER + FINAM_ROWS, encoding="utf-8")

        _, job = run_file_job(
            file_worker,
            session_factory,
            contract_id,
            file="f.csv",
            preset="finam",
            overrides={"open": "<OPN>"},
        )

        self.assert_failed_cleanly(session_factory, job, "'<OPN>'", "'<OPEN>'")
        with session_factory() as session:
            (failed,) = session.scalars(select(DataImport)).all()
        assert failed.status == "failed"

    def test_invalid_mapping_field_is_explained(
        self,
        file_worker: Worker,
        session_factory: sessionmaker[Session],
        import_dir: Path,
        contract_id: int,
    ) -> None:
        (import_dir / "f.csv").write_text(FINAM_HEADER + FINAM_ROWS, encoding="utf-8")

        _, job = run_file_job(
            file_worker,
            session_factory,
            contract_id,
            file="f.csv",
            preset="finam",
            overrides={"timezone": "Mars/Olympus"},
        )

        self.assert_failed_cleanly(
            session_factory, job, "Некорректный маппинг", "timezone"
        )

    @pytest.mark.parametrize("params", [{}, {"preset": "finam", "mapping": {}}])
    def test_exactly_one_of_preset_and_mapping(
        self,
        file_worker: Worker,
        session_factory: sessionmaker[Session],
        import_dir: Path,
        contract_id: int,
        params: dict[str, Any],
    ) -> None:
        (import_dir / "f.csv").write_text(FINAM_HEADER + FINAM_ROWS, encoding="utf-8")

        _, job = run_file_job(
            file_worker, session_factory, contract_id, file="f.csv", **params
        )

        self.assert_failed_cleanly(session_factory, job, "preset", "mapping")

    def test_file_outside_the_import_directory_is_refused(
        self,
        file_worker: Worker,
        session_factory: sessionmaker[Session],
        import_dir: Path,
        contract_id: int,
    ) -> None:
        (import_dir.parent / "secret.csv").write_text(
            FINAM_HEADER + FINAM_ROWS, encoding="utf-8"
        )

        _, job = run_file_job(
            file_worker,
            session_factory,
            contract_id,
            file="../secret.csv",
            preset="finam",
        )

        self.assert_failed_cleanly(session_factory, job, "вне каталога импорта")

    def test_missing_file(
        self,
        file_worker: Worker,
        session_factory: sessionmaker[Session],
        contract_id: int,
    ) -> None:
        _, job = run_file_job(
            file_worker, session_factory, contract_id, file="ghost.csv", preset="finam"
        )

        self.assert_failed_cleanly(session_factory, job, "ghost.csv", "не найден")

    def test_unknown_format(
        self,
        file_worker: Worker,
        session_factory: sessionmaker[Session],
        import_dir: Path,
        contract_id: int,
    ) -> None:
        (import_dir / "data.xml").write_text("<a/>", encoding="utf-8")

        _, job = run_file_job(
            file_worker, session_factory, contract_id, file="data.xml", preset="finam"
        )

        self.assert_failed_cleanly(
            session_factory, job, "Неизвестный формат", "csv, json, parquet"
        )

    def test_missing_file_parameter(
        self,
        file_worker: Worker,
        session_factory: sessionmaker[Session],
        contract_id: int,
    ) -> None:
        _, job = run_file_job(file_worker, session_factory, contract_id, preset="finam")

        self.assert_failed_cleanly(session_factory, job, "file")

    def test_missing_contract_parameter(
        self, file_worker: Worker, session_factory: sessionmaker[Session]
    ) -> None:
        job_id = enqueue(
            session_factory, "import.file", {"file": "f.csv", "preset": "finam"}
        )

        file_worker.run_once(Event())

        self.assert_failed_cleanly(
            session_factory, load(session_factory, job_id), "contract_id"
        )

    def test_wrong_encoding_suggests_a_fix(
        self,
        file_worker: Worker,
        session_factory: sessionmaker[Session],
        import_dir: Path,
        contract_id: int,
    ) -> None:
        text = FINAM_HEADER + FINAM_ROWS.replace("BR-12.26", "Нефть")
        (import_dir / "old.csv").write_bytes(text.encode("cp1251"))

        _, job = run_file_job(
            file_worker, session_factory, contract_id, file="old.csv", preset="finam"
        )

        self.assert_failed_cleanly(session_factory, job, "кодировке", "cp1251")

    def test_encoding_can_be_fixed_with_an_override(
        self,
        file_worker: Worker,
        session_factory: sessionmaker[Session],
        import_dir: Path,
        contract_id: int,
    ) -> None:
        text = FINAM_HEADER + FINAM_ROWS.replace("BR-12.26", "Нефть")
        (import_dir / "old.csv").write_bytes(text.encode("cp1251"))

        _, job = run_file_job(
            file_worker,
            session_factory,
            contract_id,
            file="old.csv",
            preset="finam",
            overrides={"encoding": "cp1251"},
        )

        assert job.status == "succeeded", job.error

    def test_broken_json(
        self,
        file_worker: Worker,
        session_factory: sessionmaker[Session],
        import_dir: Path,
        contract_id: int,
    ) -> None:
        (import_dir / "bad.json").write_text("[{oops", encoding="utf-8")

        _, job = run_file_job(
            file_worker,
            session_factory,
            contract_id,
            file="bad.json",
            mapping=INLINE_MAPPING,
        )

        self.assert_failed_cleanly(session_factory, job, "некорректный JSON")

    def test_json_without_the_mapped_columns(
        self,
        file_worker: Worker,
        session_factory: sessionmaker[Session],
        import_dir: Path,
        contract_id: int,
    ) -> None:
        (import_dir / "c.json").write_text(
            '[{"time": "x", "price": 1}]', encoding="utf-8"
        )

        _, job = run_file_job(
            file_worker,
            session_factory,
            contract_id,
            file="c.json",
            mapping=INLINE_MAPPING,
        )

        self.assert_failed_cleanly(session_factory, job, "'o'", "'price'")

    def test_corrupted_parquet(
        self,
        file_worker: Worker,
        session_factory: sessionmaker[Session],
        import_dir: Path,
        contract_id: int,
    ) -> None:
        (import_dir / "bad.parquet").write_bytes(b"this is not parquet")

        _, job = run_file_job(
            file_worker,
            session_factory,
            contract_id,
            file="bad.parquet",
            mapping=INLINE_MAPPING,
        )

        self.assert_failed_cleanly(session_factory, job, "Parquet")

    def test_empty_csv_file(
        self,
        file_worker: Worker,
        session_factory: sessionmaker[Session],
        import_dir: Path,
        contract_id: int,
    ) -> None:
        (import_dir / "empty.csv").write_text("", encoding="utf-8")

        _, job = run_file_job(
            file_worker, session_factory, contract_id, file="empty.csv", preset="finam"
        )

        self.assert_failed_cleanly(session_factory, job, "пуст")
