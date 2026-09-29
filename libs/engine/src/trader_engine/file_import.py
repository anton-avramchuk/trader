"""Разбор файлов со свечами: маппинг колонок, CSV и JSON (spec §7–8).

Чистая логика без БД и файловой системы. Читатели (CSV, JSON, а в worker'е и
Parquet) отдают записи; ``map_records`` по маппингу превращает их в ``RawRow``
или ``RowError`` для конвейера импорта. Ошибки самого маппинга (нет такой
колонки, неверный часовой пояс) обнаруживаются до разбора и объясняют, что
поправить; ошибки отдельных строк не останавливают импорт.
"""

import codecs
import csv
import json
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trader_engine.ingest import RawRow, RowError

ColumnRef = str | int
DatetimeFormat = str  # strptime-формат, либо "iso", "epoch_s", "epoch_ms"

_MINUTE = timedelta(minutes=1)
_DELIMITERS = (",", ";", "\t", "|")
_RAW_LIMIT = 500


class MappingError(ValueError):
    """Маппинг не подходит к файлу; сообщение объясняет, что исправить."""


class SourceError(ValueError):
    """Файл нельзя прочитать как заявленный формат."""


class DatetimeSpec(BaseModel):
    """Откуда брать время свечи: одна колонка или две (дата и время) + формат."""

    model_config = ConfigDict(extra="forbid")

    columns: list[ColumnRef] = Field(min_length=1, max_length=2)
    format: DatetimeFormat

    @field_validator("format")
    @classmethod
    def _valid_format(cls, value: str) -> str:
        if value in ("iso", "epoch_s", "epoch_ms"):
            return value
        try:
            datetime.strptime(datetime(2000, 1, 2, 3, 4, 5).strftime(value), value)
        except ValueError as error:
            raise ValueError(
                f"формат времени {value!r} некорректен (нужен strptime-формат, "
                "'iso', 'epoch_s' или 'epoch_ms')"
            ) from error
        return value


class FileMapping(BaseModel):
    """Как читать файл со свечами 1m. Одинаков для CSV, JSON и Parquet."""

    model_config = ConfigDict(extra="forbid")

    # Только CSV:
    delimiter: str = Field(default=",", min_length=1, max_length=1)
    has_header: bool = True
    encoding: str = "utf-8-sig"
    skip_rows: int = Field(default=0, ge=0)
    # Все форматы:
    decimal_separator: Literal[".", ","] = "."
    # Часовой пояс времени без указания смещения; хранение всегда в UTC.
    timezone: str = "Europe/Moscow"
    datetime: DatetimeSpec
    open: ColumnRef
    high: ColumnRef
    low: ColumnRef
    close: ColumnRef
    volume: ColumnRef
    trade_count: ColumnRef | None = None
    quote_volume: ColumnRef | None = None
    # Время в файле — конец свечи (а не начало): сдвигается на минуту назад.
    timestamp_is_close: bool = False

    @field_validator("encoding")
    @classmethod
    def _valid_encoding(cls, value: str) -> str:
        try:
            codecs.lookup(value)
        except LookupError as error:
            raise ValueError(f"неизвестная кодировка {value!r}") from error
        return value

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError, OSError) as error:
            raise ValueError(f"неизвестный часовой пояс {value!r}") from error
        return value

    @model_validator(mode="after")
    def _columns_match_header_mode(self) -> "FileMapping":
        if not self.has_header:
            named = [ref for ref in self.column_refs() if isinstance(ref, str)]
            if named:
                raise ValueError(
                    "в файле без заголовка колонки задаются номерами (с 0), "
                    f"а не именами: {named}"
                )
        return self

    def column_refs(self) -> list[ColumnRef]:
        refs: list[ColumnRef] = [
            *self.datetime.columns,
            self.open,
            self.high,
            self.low,
            self.close,
            self.volume,
        ]
        for optional in (self.trade_count, self.quote_volume):
            if optional is not None:
                refs.append(optional)
        return refs


@dataclass(frozen=True, slots=True)
class Record:
    """Запись источника: номер строки, исходный текст и значения по колонкам."""

    row_number: int
    raw: str | None
    values: Mapping[ColumnRef, Any]


def detect_delimiter(sample: str) -> str:
    """Угадать разделитель CSV по первым строкам (по умолчанию запятая)."""
    lines = [line for line in sample.splitlines() if line.strip()][:10]
    if not lines:
        return ","
    best, best_score = ",", 0
    for candidate in _DELIMITERS:
        counts = [line.count(candidate) for line in lines]
        if counts[0] > 0 and len(set(counts)) == 1 and counts[0] > best_score:
            best, best_score = candidate, counts[0]
    return best


def preview_csv(text: str, *, limit: int = 20) -> tuple[str, list[list[str]]]:
    """Первые ``limit`` строк для мастера сопоставления: разделитель и ячейки."""
    delimiter = detect_delimiter(text[:20000])
    rows: list[list[str]] = []
    for row in csv.reader(text.splitlines(), delimiter=delimiter):
        rows.append(row)
        if len(rows) >= limit:
            break
    return delimiter, rows


def read_csv_records(lines: Iterable[str], mapping: FileMapping) -> Iterator[Record]:
    """Записи CSV; имена колонок — из заголовка, номера доступны всегда."""
    current: list[str] = []

    def tracked() -> Iterator[str]:
        for line in lines:
            current[:] = [line.rstrip("\r\n")]
            yield line

    reader = csv.reader(tracked(), delimiter=mapping.delimiter)
    header: list[str] | None = None
    checked_positional = False
    to_skip = mapping.skip_rows
    for fields in reader:
        line_number = reader.line_num
        if to_skip > 0:
            to_skip -= 1
            continue
        if not any(field.strip() for field in fields):
            continue
        if mapping.has_header and header is None:
            header = [name.strip() for name in fields]
            _check_columns(mapping, header)
            continue
        if header is None and not checked_positional:
            checked_positional = True
            _check_columns(
                mapping, [str(i) for i in range(len(fields))], positional=True
            )
        values: dict[ColumnRef, Any] = dict(enumerate(fields))
        if header is not None:
            values.update(zip(header, fields, strict=False))
        yield Record(line_number, current[0] if current else None, values)
    if mapping.has_header and header is None:
        raise SourceError("файл пуст: нет строки заголовка")


def _check_columns(
    mapping: FileMapping, available: Sequence[str], *, positional: bool = False
) -> None:
    missing = [
        ref
        for ref in mapping.column_refs()
        if (isinstance(ref, int) and ref >= len(available))
        or (isinstance(ref, str) and not positional and ref not in available)
    ]
    if missing:
        shown = ", ".join(repr(name) for name in available)
        raise MappingError(
            f"в файле нет колонок {missing}; доступные: {shown}"
            if not positional
            else f"в файле {len(available)} колонок, а маппинг ссылается на {missing}"
        )


def read_json_records(text: str) -> Iterator[Record]:
    """Записи JSON: массив объектов либо NDJSON (по объекту в строке)."""
    stripped = text.lstrip()
    if not stripped:
        raise SourceError("файл пуст")
    if stripped.startswith("["):
        try:
            items = json.loads(text)
        except json.JSONDecodeError as error:
            raise SourceError(f"некорректный JSON: {error}") from error
        for index, item in enumerate(items, start=1):
            yield _json_record(index, item)
        return
    for index, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as error:
            raise SourceError(f"строка {index}: некорректный JSON: {error}") from error
        yield _json_record(index, item)


def _json_record(index: int, item: Any) -> Record:
    if not isinstance(item, dict):
        return Record(index, str(item)[:_RAW_LIMIT], {})
    typed: dict[ColumnRef, Any] = {
        str(key): value for key, value in cast(dict[Any, Any], item).items()
    }
    raw = json.dumps(typed, ensure_ascii=False, default=str)[:_RAW_LIMIT]
    return Record(index, raw, typed)


def check_record_columns(first: Mapping[ColumnRef, Any], mapping: FileMapping) -> None:
    """Для JSON/Parquet: колонки — только имена, и они есть в первой записи."""
    numbered = [ref for ref in mapping.column_refs() if isinstance(ref, int)]
    if numbered:
        raise MappingError(
            f"в JSON и Parquet колонки задаются именами, а не номерами: {numbered}"
        )
    _check_columns(mapping, [str(key) for key in first])


class _RowProblem(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _decimal(value: Any, column: ColumnRef, decimal_separator: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise _RowProblem(
            "bad_number", f"колонка {column!r}: логическое значение вместо числа"
        )
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    text = str(value).strip().replace("\xa0", "").replace(" ", "")
    if not text:
        return None
    if decimal_separator == ",":
        text = text.replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation as error:
        raise _RowProblem(
            "bad_number", f"колонка {column!r}: {value!r} не число"
        ) from error


def _integer(value: Any, column: ColumnRef) -> int | None:
    number = _decimal(value, column, ".")
    if number is None:
        return None
    if number != number.to_integral_value():
        raise _RowProblem("bad_number", f"колонка {column!r}: {value!r} не целое число")
    return int(number)


def _parse_datetime(parts: list[Any], spec: DatetimeSpec, tz: ZoneInfo) -> datetime:
    if len(parts) == 1 and isinstance(parts[0], datetime):
        result = parts[0]
    elif len(parts) == 2 and isinstance(parts[0], date) and isinstance(parts[1], time):
        result = datetime.combine(parts[0], parts[1])
    else:
        text = " ".join(str(part).strip() for part in parts)
        try:
            if spec.format == "iso":
                result = datetime.fromisoformat(text)
            elif spec.format in ("epoch_s", "epoch_ms"):
                seconds = float(text) / (1000 if spec.format == "epoch_ms" else 1)
                result = datetime.fromtimestamp(seconds, UTC)
            else:
                result = datetime.strptime(text, spec.format)
        except (ValueError, OverflowError, OSError) as error:
            raise _RowProblem(
                "bad_datetime",
                f"время {text!r} не соответствует формату {spec.format!r}",
            ) from error
    if result.tzinfo is None:
        result = result.replace(tzinfo=tz)
    return result.astimezone(UTC)


def _cell(record: Record, ref: ColumnRef) -> Any:
    if ref not in record.values:
        raise _RowProblem("missing_column", f"в строке нет колонки {ref!r}")
    return record.values[ref]


def map_records(
    records: Iterable[Record], mapping: FileMapping
) -> Iterator[RawRow | RowError]:
    """Превратить записи в ``RawRow``; нечитаемые строки — в ``RowError``."""
    tz = ZoneInfo(mapping.timezone)
    decimal_separator = mapping.decimal_separator
    for record in records:
        try:
            parts = [_cell(record, ref) for ref in mapping.datetime.columns]
            moment = _parse_datetime(parts, mapping.datetime, tz)
            if mapping.timestamp_is_close:
                moment -= _MINUTE
            trade_count = (
                _integer(_cell(record, mapping.trade_count), mapping.trade_count)
                if mapping.trade_count is not None
                else None
            )
            quote_volume = (
                _decimal(
                    _cell(record, mapping.quote_volume),
                    mapping.quote_volume,
                    decimal_separator,
                )
                if mapping.quote_volume is not None
                else None
            )
            yield RawRow(
                row_number=record.row_number,
                raw=record.raw,
                timestamp=moment,
                open=_decimal(
                    _cell(record, mapping.open), mapping.open, decimal_separator
                ),
                high=_decimal(
                    _cell(record, mapping.high), mapping.high, decimal_separator
                ),
                low=_decimal(
                    _cell(record, mapping.low), mapping.low, decimal_separator
                ),
                close=_decimal(
                    _cell(record, mapping.close), mapping.close, decimal_separator
                ),
                volume=_decimal(
                    _cell(record, mapping.volume), mapping.volume, decimal_separator
                ),
                trade_count=trade_count,
                quote_volume=quote_volume,
            )
        except _RowProblem as problem:
            yield RowError(record.row_number, record.raw, problem.code, str(problem))
