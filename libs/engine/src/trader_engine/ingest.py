"""Проверка входящих строк свечей 1m: валидация, дубликаты, порядок (spec §9).

Чистая логика без БД. Парсеры источников (CSV, ISS…) выдают ``RawRow`` или
``RowError``; ``validate_rows`` отделяет годные свечи от ошибочных. Ошибочные
строки не пропадают: каждая возвращается с кодом причины.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

# Соответствуют колонкам NUMERIC(18,8) и NUMERIC(24,8) в БД: иначе PostgreSQL
# молча округлит значение или откажется его записать.
PRICE_MAX = Decimal(10) ** 10
VOLUME_MAX = Decimal(10) ** 16
MAX_DECIMAL_PLACES = 8
MAX_TRADE_COUNT = 2**31 - 1


@dataclass(frozen=True, slots=True)
class Candle1m:
    """Валидная минутная свеча; ``timestamp`` — время открытия, aware."""

    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    trade_count: int | None = None
    quote_volume: Decimal | None = None


@dataclass(frozen=True, slots=True)
class RowError:
    """Отклонённая строка источника."""

    row_number: int | None
    raw: str | None
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class RawRow:
    """Строка источника после разбора, до валидации; поля могут быть пустыми."""

    row_number: int
    raw: str | None = None
    timestamp: datetime | None = None
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    close: Decimal | None = None
    volume: Decimal | None = None
    trade_count: int | None = None
    quote_volume: Decimal | None = None


@dataclass(frozen=True, slots=True)
class ValidationResult:
    candles: list[Candle1m]
    errors: list[RowError]
    # Строки, полностью совпавшие с уже виденной в этом же источнике.
    exact_duplicates: int = 0
    # Годные строки, пришедшие раньше предыдущей по времени (порядок исправлен).
    out_of_order_rows: int = 0
    error_counts: dict[str, int] = field(default_factory=dict[str, int])


def _check_decimal(value: Decimal, name: str, limit: Decimal) -> tuple[str, str] | None:
    if not value.is_finite():
        return "non_finite_value", f"{name}: значение не является конечным числом"
    if abs(value) >= limit:
        return (
            "value_out_of_range",
            f"{name}: значение {value} вне допустимого диапазона",
        )
    exponent = value.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -MAX_DECIMAL_PLACES:
        return (
            "value_too_precise",
            f"{name}: больше {MAX_DECIMAL_PLACES} знаков после запятой",
        )
    return None


def _validate_row(
    row: RawRow, *, allow_non_positive_prices: bool
) -> tuple[Candle1m | None, RowError | None]:
    def fail(code: str, message: str) -> tuple[None, RowError]:
        return None, RowError(row.row_number, row.raw, code, message)

    prices = {
        "open": row.open,
        "high": row.high,
        "low": row.low,
        "close": row.close,
    }
    if row.timestamp is None:
        return fail("missing_field", "нет времени свечи")
    for name, value in (*prices.items(), ("volume", row.volume)):
        if value is None:
            return fail("missing_field", f"нет значения {name}")
    if row.timestamp.tzinfo is None:
        return fail("bad_timestamp", "время без часового пояса")
    moment = row.timestamp.astimezone(UTC)
    if moment.second or moment.microsecond:
        return fail("not_minute_aligned", f"время {row.timestamp} не кратно минуте")

    open_, high, low, close, volume = (
        row.open,
        row.high,
        row.low,
        row.close,
        row.volume,
    )
    assert open_ is not None and high is not None and low is not None
    assert close is not None and volume is not None

    for name, value in prices.items():
        assert value is not None
        problem = _check_decimal(value, name, PRICE_MAX)
        if problem:
            return fail(*problem)
    problem = _check_decimal(volume, "volume", VOLUME_MAX)
    if problem:
        return fail(*problem)
    if row.quote_volume is not None:
        problem = _check_decimal(row.quote_volume, "quote_volume", VOLUME_MAX)
        if problem:
            return fail(*problem)
    if row.trade_count is not None and not 0 <= row.trade_count <= MAX_TRADE_COUNT:
        return fail("value_out_of_range", f"trade_count: {row.trade_count}")

    if not allow_non_positive_prices and min(open_, high, low, close) <= 0:
        return fail("non_positive_price", "цена не положительна")
    if volume < 0:
        return fail("negative_volume", f"отрицательный объём {volume}")
    if high < low:
        return fail("high_lt_low", f"high {high} меньше low {low}")
    if high < max(open_, close):
        return fail("high_lt_open_close", f"high {high} ниже open/close")
    if low > min(open_, close):
        return fail("low_gt_open_close", f"low {low} выше open/close")

    return (
        Candle1m(
            timestamp=moment,
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=volume,
            trade_count=row.trade_count,
            quote_volume=row.quote_volume,
        ),
        None,
    )


def validate_rows(
    items: Iterable[RawRow | RowError],
    *,
    allow_non_positive_prices: bool = False,
) -> ValidationResult:
    """Проверить строки; вернуть годные свечи (по времени, без дублей) и ошибки.

    - каждая ошибочная строка получает код причины и не теряется;
    - полностью совпавшие строки с одним временем схлопываются (счётчик
      ``exact_duplicates``);
    - строки с одним временем, но разными значениями отвергаются все:
      неизвестно, какая верна (``conflicting_duplicate``);
    - нарушение порядка не ошибка: строки сортируются, число нарушений
      возвращается для отчёта.
    """
    errors: list[RowError] = []
    valid: list[tuple[int, Candle1m]] = []
    for item in items:
        if isinstance(item, RowError):
            errors.append(item)
            continue
        candle, error = _validate_row(
            item, allow_non_positive_prices=allow_non_positive_prices
        )
        if error is not None:
            errors.append(error)
        else:
            assert candle is not None
            valid.append((item.row_number, candle))

    out_of_order = sum(
        1
        for (_, previous), (_, current) in zip(valid, valid[1:], strict=False)
        if current.timestamp < previous.timestamp
    )

    groups: dict[datetime, list[tuple[int, Candle1m]]] = {}
    for row_number, candle in valid:
        groups.setdefault(candle.timestamp, []).append((row_number, candle))

    candles: list[Candle1m] = []
    exact_duplicates = 0
    for moment in sorted(groups):
        rows = groups[moment]
        first = rows[0][1]
        if all(candle == first for _, candle in rows):
            candles.append(first)
            exact_duplicates += len(rows) - 1
        else:
            errors.extend(
                RowError(
                    row_number,
                    None,
                    "conflicting_duplicate",
                    f"несколько строк с временем {moment} и разными значениями",
                )
                for row_number, _ in rows
            )

    counts: dict[str, int] = {}
    for error in errors:
        counts[error.code] = counts.get(error.code, 0) + 1
    return ValidationResult(
        candles=candles,
        errors=errors,
        exact_duplicates=exact_duplicates,
        out_of_order_rows=out_of_order,
        error_counts=counts,
    )
