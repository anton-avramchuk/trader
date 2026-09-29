"""Пресеты файлового импорта (нужен TRADER_DATABASE_URL)."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session
from trader_engine.file_import import (
    DatetimeSpec,
    FileMapping,
    map_records,
    read_csv_records,
)
from trader_engine.ingest import RawRow

from trader_db import (
    delete_preset,
    get_preset,
    list_presets,
    save_preset,
)

BUILTIN = {"finam", "finam_no_header", "iso_utc"}


def custom_mapping() -> FileMapping:
    return FileMapping(
        delimiter=";",
        decimal_separator=",",
        datetime=DatetimeSpec(columns=["d", "t"], format="%d.%m.%Y %H:%M"),
        open="o",
        high="h",
        low="l",
        close="c",
        volume="v",
    )


def parse(text: str, mapping: FileMapping) -> list[RawRow]:
    rows = list(map_records(read_csv_records(text.splitlines(True), mapping), mapping))
    assert all(isinstance(row, RawRow) for row in rows), rows
    return [row for row in rows if isinstance(row, RawRow)]


class TestBuiltin:
    def test_builtin_presets_are_seeded_and_listed_first(
        self, session: Session
    ) -> None:
        save_preset(session, "aaa-my", custom_mapping())

        names = [p.name for p in list_presets(session)]

        assert set(names[:3]) == BUILTIN
        assert names[3:] == ["aaa-my"]

    @pytest.mark.parametrize("name", sorted(BUILTIN))
    def test_every_builtin_preset_is_a_valid_mapping(
        self, session: Session, name: str
    ) -> None:
        mapping = get_preset(session, name)

        assert mapping.timezone in {"Europe/Moscow", "UTC"}

    def test_finam_preset_parses_a_finam_file(self, session: Session) -> None:
        text = (
            "<TICKER>,<PER>,<DATE>,<TIME>,<OPEN>,<HIGH>,<LOW>,<CLOSE>,<VOL>\n"
            "BR-12.26,1,20260929,100000,70.10,70.20,70.05,70.15,120\n"
        )

        (row,) = parse(text, get_preset(session, "finam"))

        assert row.timestamp == datetime(2026, 9, 29, 7, 0, tzinfo=UTC)

    def test_headerless_finam_preset(self, session: Session) -> None:
        text = "BR-12.26,1,20260929,100000,70.10,70.20,70.05,70.15,120\n"

        (row,) = parse(text, get_preset(session, "finam_no_header"))

        assert row.timestamp == datetime(2026, 9, 29, 7, 0, tzinfo=UTC)

    def test_iso_utc_preset(self, session: Session) -> None:
        text = (
            "timestamp,open,high,low,close,volume\n2026-09-29T07:00:00Z,1,2,0.5,1.5,3\n"
        )

        (row,) = parse(text, get_preset(session, "iso_utc"))

        assert row.timestamp == datetime(2026, 9, 29, 7, 0, tzinfo=UTC)

    def test_builtin_presets_cannot_be_changed_or_deleted(
        self, session: Session
    ) -> None:
        with pytest.raises(ValueError, match="встроенный"):
            save_preset(session, "finam", custom_mapping())
        with pytest.raises(ValueError, match="встроенный"):
            delete_preset(session, "finam")


class TestCustom:
    def test_save_get_roundtrip(self, session: Session) -> None:
        save_preset(session, "my", custom_mapping(), description="мой формат")

        assert get_preset(session, "my") == custom_mapping()

    def test_saving_again_updates_the_preset(self, session: Session) -> None:
        save_preset(session, "my", custom_mapping())
        changed = custom_mapping().model_copy(update={"timezone": "UTC"})

        save_preset(session, "my", changed, description="v2")

        assert get_preset(session, "my").timezone == "UTC"
        assert [p.description for p in list_presets(session) if p.name == "my"] == [
            "v2"
        ]

    def test_delete(self, session: Session) -> None:
        save_preset(session, "my", custom_mapping())

        assert delete_preset(session, "my") is True
        assert delete_preset(session, "my") is False
        with pytest.raises(LookupError):
            get_preset(session, "my")

    def test_unknown_preset_lists_the_available_ones(self, session: Session) -> None:
        with pytest.raises(LookupError) as error:
            get_preset(session, "nope")

        assert "'finam'" in str(error.value)

    @pytest.mark.parametrize("name", ["", "   ", "x" * 65])
    def test_invalid_names_are_rejected(self, session: Session, name: str) -> None:
        with pytest.raises(ValueError, match="Имя пресета"):
            save_preset(session, name, custom_mapping())

    def test_name_is_trimmed(self, session: Session) -> None:
        save_preset(session, "  my  ", custom_mapping())

        assert get_preset(session, "my") == custom_mapping()
