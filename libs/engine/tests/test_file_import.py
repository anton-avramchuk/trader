from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from trader_engine.file_import import (
    DatetimeSpec,
    FileMapping,
    MappingError,
    Record,
    SourceError,
    check_record_columns,
    detect_delimiter,
    map_records,
    preview_csv,
    read_csv_records,
    read_json_records,
)
from trader_engine.ingest import RawRow, RowError, validate_rows

FINAM_TEXT = """<TICKER>,<PER>,<DATE>,<TIME>,<OPEN>,<HIGH>,<LOW>,<CLOSE>,<VOL>
BR-12.26,1,20260929,100000,70.10,70.20,70.05,70.15,120
BR-12.26,1,20260929,100100,70.15,70.25,70.10,70.20,80
"""


def finam_mapping(**overrides: Any) -> FileMapping:
    values: dict[str, Any] = {
        "datetime": DatetimeSpec(columns=["<DATE>", "<TIME>"], format="%Y%m%d %H%M%S"),
        "open": "<OPEN>",
        "high": "<HIGH>",
        "low": "<LOW>",
        "close": "<CLOSE>",
        "volume": "<VOL>",
    }
    values.update(overrides)
    return FileMapping(**values)


def parse_csv(text: str, mapping: FileMapping) -> list[RawRow | RowError]:
    return list(
        map_records(read_csv_records(text.splitlines(keepends=True), mapping), mapping)
    )


def rows_only(items: list[RawRow | RowError]) -> list[RawRow]:
    assert all(isinstance(item, RawRow) for item in items), items
    return [item for item in items if isinstance(item, RawRow)]


class TestFinam:
    def test_finam_file_is_parsed_with_moscow_time_converted_to_utc(self) -> None:
        rows = rows_only(parse_csv(FINAM_TEXT, finam_mapping()))

        assert len(rows) == 2
        first = rows[0]
        assert first.timestamp == datetime(2026, 9, 29, 7, 0, tzinfo=UTC)
        assert (first.open, first.high, first.low, first.close, first.volume) == (
            Decimal("70.10"),
            Decimal("70.20"),
            Decimal("70.05"),
            Decimal("70.15"),
            Decimal("120"),
        )

    def test_row_numbers_are_physical_lines_and_raw_text_is_kept(self) -> None:
        rows = rows_only(parse_csv(FINAM_TEXT, finam_mapping()))

        assert [r.row_number for r in rows] == [2, 3]
        assert rows[0].raw == "BR-12.26,1,20260929,100000,70.10,70.20,70.05,70.15,120"

    def test_finam_file_goes_through_validation_into_candles(self) -> None:
        result = validate_rows(parse_csv(FINAM_TEXT, finam_mapping()))

        assert result.errors == []
        assert [c.timestamp.hour for c in result.candles] == [7, 7]
        assert [c.timestamp.minute for c in result.candles] == [0, 1]

    def test_headerless_finam_uses_column_numbers(self) -> None:
        text = "BR-12.26,1,20260929,100000,70.10,70.20,70.05,70.15,120\n"
        mapping = FileMapping(
            has_header=False,
            datetime=DatetimeSpec(columns=[2, 3], format="%Y%m%d %H%M%S"),
            open=4,
            high=5,
            low=6,
            close=7,
            volume=8,
        )

        (row,) = rows_only(parse_csv(text, mapping))

        assert (row.row_number, row.close) == (1, Decimal("70.15"))
        assert row.timestamp == datetime(2026, 9, 29, 7, 0, tzinfo=UTC)


class TestFormats:
    def test_russian_style_semicolons_decimal_comma_and_thousand_spaces(self) -> None:
        text = (
            "date;time;open;high;low;close;volume\n"
            "29.09.2026;10:00;100,5;101,5;99,5;100,0;1 234\n"
        )
        mapping = FileMapping(
            delimiter=";",
            decimal_separator=",",
            datetime=DatetimeSpec(columns=["date", "time"], format="%d.%m.%Y %H:%M"),
            open="open",
            high="high",
            low="low",
            close="close",
            volume="volume",
        )

        (row,) = rows_only(parse_csv(text, mapping))

        assert (row.open, row.volume) == (Decimal("100.5"), Decimal("1234"))
        assert row.timestamp == datetime(2026, 9, 29, 7, 0, tzinfo=UTC)

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("2026-09-29T10:00:00+03:00", datetime(2026, 9, 29, 7, 0, tzinfo=UTC)),
            ("2026-09-29T07:00:00Z", datetime(2026, 9, 29, 7, 0, tzinfo=UTC)),
            # без смещения — берётся timezone маппинга (по умолчанию Москва)
            ("2026-09-29 10:00:00", datetime(2026, 9, 29, 7, 0, tzinfo=UTC)),
        ],
    )
    def test_iso_timestamps(self, value: str, expected: datetime) -> None:
        text = f"ts,o,h,l,c,v\n{value},1,2,0.5,1.5,3\n"
        mapping = FileMapping(
            datetime=DatetimeSpec(columns=["ts"], format="iso"),
            open="o",
            high="h",
            low="l",
            close="c",
            volume="v",
        )

        (row,) = rows_only(parse_csv(text, mapping))

        assert row.timestamp == expected

    def test_epoch_milliseconds_are_utc(self) -> None:
        text = "t,o,h,l,c,v\n1790665200000,1,2,0.5,1.5,3\n"
        mapping = FileMapping(
            datetime=DatetimeSpec(columns=["t"], format="epoch_ms"),
            open="o",
            high="h",
            low="l",
            close="c",
            volume="v",
        )

        (row,) = rows_only(parse_csv(text, mapping))

        assert row.timestamp == datetime.fromtimestamp(1790665200, UTC)

    def test_explicit_timezone_is_applied_to_naive_time(self) -> None:
        rows = rows_only(parse_csv(FINAM_TEXT, finam_mapping(timezone="UTC")))

        assert rows[0].timestamp == datetime(2026, 9, 29, 10, 0, tzinfo=UTC)

    def test_close_time_stamps_are_shifted_to_open_time(self) -> None:
        rows = rows_only(parse_csv(FINAM_TEXT, finam_mapping(timestamp_is_close=True)))

        assert rows[0].timestamp == datetime(2026, 9, 29, 6, 59, tzinfo=UTC)

    def test_optional_columns_trade_count_and_quote_volume(self) -> None:
        text = "ts,o,h,l,c,v,n,q\n2026-09-29T07:00:00Z,1,2,0.5,1.5,3,17,4.5\n"
        mapping = FileMapping(
            datetime=DatetimeSpec(columns=["ts"], format="iso"),
            open="o",
            high="h",
            low="l",
            close="c",
            volume="v",
            trade_count="n",
            quote_volume="q",
        )

        (row,) = rows_only(parse_csv(text, mapping))

        assert (row.trade_count, row.quote_volume) == (17, Decimal("4.5"))

    def test_skip_rows_and_blank_lines_and_quoted_delimiters(self) -> None:
        text = (
            "экспорт от 29.09.2026\n"
            "\n"
            "ticker,ts,o,h,l,c,v\n"
            '"BR,12",2026-09-29T07:00:00Z,1,2,0.5,1.5,3\n'
            "\n"
        )
        mapping = FileMapping(
            skip_rows=1,
            datetime=DatetimeSpec(columns=["ts"], format="iso"),
            open="o",
            high="h",
            low="l",
            close="c",
            volume="v",
        )

        rows = rows_only(parse_csv(text, mapping))

        assert len(rows) == 1
        assert rows[0].close == Decimal("1.5")

    def test_a_column_can_be_referenced_by_number_even_with_a_header(self) -> None:
        mapping = finam_mapping(open=4)

        (first, _second) = rows_only(parse_csv(FINAM_TEXT, mapping))

        assert first.open == Decimal("70.10")


class TestRowProblems:
    def parse(self, body: str) -> list[RawRow | RowError]:
        return parse_csv(FINAM_TEXT.splitlines()[0] + "\n" + body, finam_mapping())

    def test_short_row_is_missing_column(self) -> None:
        (error,) = self.parse("BR-12.26,1,20260929\n")

        assert isinstance(error, RowError)
        assert (error.code, error.row_number) == ("missing_column", 2)
        assert error.raw == "BR-12.26,1,20260929"

    def test_non_numeric_value_names_the_column(self) -> None:
        (error,) = self.parse("BR-12.26,1,20260929,100000,abc,70.2,70.0,70.1,5\n")

        assert isinstance(error, RowError)
        assert error.code == "bad_number"
        assert "<OPEN>" in error.message
        assert "'abc'" in error.message

    def test_bad_time_names_the_expected_format(self) -> None:
        (error,) = self.parse("BR-12.26,1,20261345,100000,70,70.2,70.0,70.1,5\n")

        assert isinstance(error, RowError)
        assert error.code == "bad_datetime"
        assert "%Y%m%d %H%M%S" in error.message

    def test_good_rows_around_a_bad_one_still_parse(self) -> None:
        items = self.parse(
            "BR-12.26,1,20260929,100000,70,70.2,70.0,70.1,5\n"
            "garbage\n"
            "BR-12.26,1,20260929,100100,70,70.2,70.0,70.1,5\n"
        )

        assert [type(item).__name__ for item in items] == [
            "RawRow",
            "RowError",
            "RawRow",
        ]

    def test_empty_cell_becomes_missing_field_in_validation(self) -> None:
        items = self.parse("BR-12.26,1,20260929,100000,70,70.2,70.0,70.1,\n")

        result = validate_rows(items)

        assert [e.code for e in result.errors] == ["missing_field"]

    def test_non_integer_trade_count(self) -> None:
        mapping = FileMapping(
            datetime=DatetimeSpec(columns=["ts"], format="iso"),
            open="o",
            high="h",
            low="l",
            close="c",
            volume="v",
            trade_count="n",
        )

        (error,) = parse_csv(
            "ts,o,h,l,c,v,n\n2026-09-29T07:00:00Z,1,2,0.5,1.5,3,1.5\n", mapping
        )

        assert isinstance(error, RowError)
        assert error.code == "bad_number"


class TestMappingErrors:
    def test_unknown_column_lists_what_is_available(self) -> None:
        mapping = finam_mapping(open="<OPN>")

        with pytest.raises(MappingError) as error:
            parse_csv(FINAM_TEXT, mapping)

        message = str(error.value)
        assert "'<OPN>'" in message
        assert "'<OPEN>'" in message  # подсказка: доступные колонки

    def test_column_number_out_of_range(self) -> None:
        mapping = FileMapping(
            has_header=False,
            datetime=DatetimeSpec(columns=[0, 1], format="%Y%m%d %H%M%S"),
            open=2,
            high=3,
            low=4,
            close=5,
            volume=99,
        )

        with pytest.raises(MappingError, match="99"):
            parse_csv("20260929,100000,1,2,0.5,1.5\n", mapping)

    def test_named_columns_without_header_are_rejected_upfront(self) -> None:
        with pytest.raises(ValidationError, match="номерами"):
            finam_mapping(has_header=False)

    def test_unknown_timezone(self) -> None:
        with pytest.raises(ValidationError, match="часовой пояс"):
            finam_mapping(timezone="Mars/Olympus")

    def test_unknown_encoding(self) -> None:
        with pytest.raises(ValidationError, match="кодировка"):
            finam_mapping(encoding="klingon")

    def test_invalid_time_format(self) -> None:
        with pytest.raises(ValidationError, match="формат времени"):
            DatetimeSpec(columns=["a"], format="%Q")

    def test_unknown_field_in_mapping(self) -> None:
        with pytest.raises(ValidationError):
            finam_mapping(colour="red")

    def test_header_only_file_is_empty(self) -> None:
        assert parse_csv(FINAM_TEXT.splitlines()[0] + "\n", finam_mapping()) == []

    def test_completely_empty_file(self) -> None:
        with pytest.raises(SourceError, match="пуст"):
            parse_csv("", finam_mapping())

    def test_mapping_roundtrips_through_json(self) -> None:
        mapping = finam_mapping(timezone="UTC", decimal_separator=",")

        assert FileMapping.model_validate_json(mapping.model_dump_json()) == mapping


class TestJson:
    MAPPING = FileMapping(
        datetime=DatetimeSpec(columns=["time"], format="iso"),
        open="o",
        high="h",
        low="l",
        close="c",
        volume="v",
    )

    def records(self, text: str) -> list[Record]:
        return list(read_json_records(text))

    def test_array_of_objects_with_numbers_and_iso_time(self) -> None:
        text = (
            '[{"time": "2026-09-29T10:00:00", '
            '"o": 1.1, "h": 2, "l": 0.5, "c": 1.5, "v": 7}]'
        )

        (row,) = rows_only(list(map_records(self.records(text), self.MAPPING)))

        assert (row.open, row.high, row.volume) == (
            Decimal("1.1"),
            Decimal("2"),
            Decimal("7"),
        )
        assert row.timestamp == datetime(2026, 9, 29, 7, 0, tzinfo=UTC)

    def test_ndjson(self) -> None:
        text = (
            '{"time": "2026-09-29T10:00:00", '
            '"o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 7}\n'
            "\n"
            '{"time": "2026-09-29T10:01:00", '
            '"o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 8}\n'
        )

        rows = rows_only(list(map_records(self.records(text), self.MAPPING)))

        assert [r.volume for r in rows] == [Decimal("7"), Decimal("8")]
        assert [r.row_number for r in rows] == [1, 3]

    def test_invalid_json_is_a_source_error(self) -> None:
        with pytest.raises(SourceError, match="некорректный JSON"):
            self.records("[{oops")

    def test_invalid_ndjson_line_names_the_line(self) -> None:
        with pytest.raises(SourceError, match="строка 2"):
            self.records('{"a": 1}\n{oops\n')

    def test_non_object_items_become_row_errors(self) -> None:
        (error,) = list(map_records(self.records("[42]"), self.MAPPING))

        assert isinstance(error, RowError)
        assert error.code == "missing_column"

    def test_unknown_column_is_reported_from_the_first_record(self) -> None:
        (first, *_rest) = self.records('[{"time": "x", "o": 1}]')

        with pytest.raises(MappingError, match="'h'"):
            check_record_columns(first.values, self.MAPPING)

    def test_numbered_columns_are_rejected_for_json(self) -> None:
        mapping = FileMapping(
            has_header=False,
            datetime=DatetimeSpec(columns=[0], format="iso"),
            open=1,
            high=2,
            low=3,
            close=4,
            volume=5,
        )

        with pytest.raises(MappingError, match="именами"):
            check_record_columns({"a": 1}, mapping)


class TestTypedValues:
    """Значения из Parquet приходят уже типизированными."""

    def test_naive_datetime_gets_the_mapping_timezone(self) -> None:
        mapping = FileMapping(
            datetime=DatetimeSpec(columns=["ts"], format="iso"),
            open="o",
            high="h",
            low="l",
            close="c",
            volume="v",
        )
        record = Record(
            1,
            None,
            {
                "ts": datetime(2026, 9, 29, 10, 0),
                "o": 1.5,
                "h": Decimal("2"),
                "l": 1,
                "c": 1.75,
                "v": 3,
            },
        )

        (row,) = rows_only(list(map_records([record], mapping)))

        assert row.timestamp == datetime(2026, 9, 29, 7, 0, tzinfo=UTC)
        assert (row.open, row.close) == (Decimal("1.5"), Decimal("1.75"))

    def test_separate_date_and_time_objects(self) -> None:
        mapping = FileMapping(
            datetime=DatetimeSpec(columns=["d", "t"], format="iso"),
            open="o",
            high="h",
            low="l",
            close="c",
            volume="v",
        )
        record = Record(
            1,
            None,
            {
                "d": date(2026, 9, 29),
                "t": time(10, 0),
                "o": 1,
                "h": 2,
                "l": 1,
                "c": 1,
                "v": 1,
            },
        )

        (row,) = rows_only(list(map_records([record], mapping)))

        assert row.timestamp == datetime(2026, 9, 29, 7, 0, tzinfo=UTC)

    def test_boolean_is_not_a_number(self) -> None:
        mapping = FileMapping(
            datetime=DatetimeSpec(columns=["ts"], format="iso"),
            open="o",
            high="h",
            low="l",
            close="c",
            volume="v",
        )
        record = Record(
            1,
            None,
            {
                "ts": datetime(2026, 9, 29, 10),
                "o": True,
                "h": 2,
                "l": 1,
                "c": 1,
                "v": 1,
            },
        )

        (error,) = list(map_records([record], mapping))

        assert isinstance(error, RowError)
        assert error.code == "bad_number"


class TestPreview:
    @pytest.mark.parametrize(
        ("text", "delimiter"),
        [
            ("a,b,c\n1,2,3\n", ","),
            ("a;b;c\n1;2;3\n", ";"),
            ("a\tb\tc\n1\t2\t3\n", "\t"),
            ("single\nline\n", ","),
            ("", ","),
        ],
    )
    def test_delimiter_detection(self, text: str, delimiter: str) -> None:
        assert detect_delimiter(text) == delimiter

    def test_preview_returns_the_first_rows_split_by_the_detected_delimiter(
        self,
    ) -> None:
        text = "a;b;c\n" + "".join(f"{i};{i};{i}\n" for i in range(50))

        delimiter, rows = preview_csv(text, limit=3)

        assert delimiter == ";"
        assert rows == [["a", "b", "c"], ["0", "0", "0"], ["1", "1", "1"]]
