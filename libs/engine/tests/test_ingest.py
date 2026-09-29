from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from trader_engine.ingest import RawRow, RowError, validate_rows

T0 = datetime(2026, 9, 29, 4, 0, tzinfo=UTC)


def row(number: int = 1, **overrides: Any) -> RawRow:
    values: dict[str, Any] = {
        "row_number": number,
        "raw": f"line {number}",
        "timestamp": T0,
        "open": Decimal("100"),
        "high": Decimal("101"),
        "low": Decimal("99"),
        "close": Decimal("100.5"),
        "volume": Decimal("10"),
    }
    values.update(overrides)
    return RawRow(**values)


def only_error(**overrides: Any) -> str:
    result = validate_rows([row(**overrides)])

    assert result.candles == []
    (error,) = result.errors
    return error.code


class TestRowRules:
    def test_valid_row_becomes_candle(self) -> None:
        result = validate_rows([row(trade_count=5, quote_volume=Decimal("1000"))])

        (candle,) = result.candles
        assert (candle.timestamp, candle.close, candle.trade_count) == (
            T0,
            Decimal("100.5"),
            5,
        )
        assert result.errors == []

    @pytest.mark.parametrize(
        "field", ["timestamp", "open", "high", "low", "close", "volume"]
    )
    def test_missing_field(self, field: str) -> None:
        assert only_error(**{field: None}) == "missing_field"

    def test_naive_timestamp(self) -> None:
        assert only_error(timestamp=datetime(2026, 9, 29, 4, 0)) == "bad_timestamp"

    @pytest.mark.parametrize(
        "delta", [timedelta(seconds=30), timedelta(microseconds=1)]
    )
    def test_timestamp_must_be_minute_aligned(self, delta: timedelta) -> None:
        assert only_error(timestamp=T0 + delta) == "not_minute_aligned"

    def test_timestamp_is_normalized_to_utc(self) -> None:
        moscow = timezone(timedelta(hours=3))

        result = validate_rows(
            [row(timestamp=datetime(2026, 9, 29, 7, 0, tzinfo=moscow))]
        )

        assert result.candles[0].timestamp == T0
        assert result.candles[0].timestamp.utcoffset() == timedelta(0)

    @pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity")])
    def test_non_finite_values(self, value: Decimal) -> None:
        assert only_error(close=value) == "non_finite_value"

    def test_too_large_price_and_volume(self) -> None:
        assert only_error(high=Decimal(10) ** 10) == "value_out_of_range"
        assert only_error(volume=Decimal(10) ** 16) == "value_out_of_range"

    def test_precision_beyond_database_scale_is_rejected_not_rounded(self) -> None:
        assert only_error(close=Decimal("100.123456789")) == "value_too_precise"
        result = validate_rows([row(close=Decimal("100.12345678"))])
        assert result.candles[0].close == Decimal("100.12345678")

    @pytest.mark.parametrize("value", [Decimal(0), Decimal("-1")])
    def test_non_positive_price(self, value: Decimal) -> None:
        assert only_error(low=value) == "non_positive_price"

    def test_non_positive_price_can_be_allowed(self) -> None:
        result = validate_rows(
            [row(low=Decimal("-1"), open=Decimal("-0.5"), close=Decimal("-0.5"))],
            allow_non_positive_prices=True,
        )

        assert len(result.candles) == 1

    def test_negative_volume(self) -> None:
        assert only_error(volume=Decimal("-1")) == "negative_volume"

    def test_high_below_low(self) -> None:
        assert only_error(high=Decimal("98"), low=Decimal("99")) == "high_lt_low"

    def test_high_below_open_or_close(self) -> None:
        assert only_error(high=Decimal("100.2")) == "high_lt_open_close"
        assert (
            only_error(high=Decimal("100.4"), open=Decimal("99.5"))
            == "high_lt_open_close"
        )

    def test_low_above_open_or_close(self) -> None:
        assert only_error(low=Decimal("100.2")) == "low_gt_open_close"

    @pytest.mark.parametrize("count", [-1, 2**31])
    def test_trade_count_range(self, count: int) -> None:
        assert only_error(trade_count=count) == "value_out_of_range"

    def test_parser_errors_pass_through(self) -> None:
        parse_error = RowError(3, "garbage", "parse_error", "нечисловое значение")

        result = validate_rows([parse_error, row(4)])

        assert result.errors == [parse_error]
        assert len(result.candles) == 1

    def test_error_keeps_row_number_and_raw_text(self) -> None:
        result = validate_rows([row(7, volume=Decimal("-1"))])

        (error,) = result.errors
        assert (error.row_number, error.raw) == (7, "line 7")

    def test_error_counts_are_aggregated(self) -> None:
        result = validate_rows(
            [
                row(1, volume=Decimal("-1")),
                row(2, volume=Decimal("-2")),
                row(3, high=Decimal("98")),
            ]
        )

        assert result.error_counts == {"negative_volume": 2, "high_lt_low": 1}


class TestDuplicatesAndOrder:
    def test_exact_duplicates_collapse_and_are_counted(self) -> None:
        result = validate_rows([row(1), row(2), row(3)])

        assert len(result.candles) == 1
        assert result.exact_duplicates == 2
        assert result.errors == []

    def test_conflicting_duplicates_are_all_rejected(self) -> None:
        result = validate_rows([row(1), row(2, close=Decimal("100.4")), row(3)])

        assert result.candles == []
        assert sorted(e.row_number or 0 for e in result.errors) == [1, 2, 3]
        assert {e.code for e in result.errors} == {"conflicting_duplicate"}

    def test_invalid_row_does_not_conflict_with_valid_one(self) -> None:
        result = validate_rows([row(1), row(2, volume=Decimal("-1"))])

        assert len(result.candles) == 1
        assert [e.code for e in result.errors] == ["negative_volume"]

    def test_out_of_order_rows_are_sorted_and_counted(self) -> None:
        minute = timedelta(minutes=1)
        result = validate_rows(
            [
                row(1, timestamp=T0 + 2 * minute),
                row(2, timestamp=T0),
                row(3, timestamp=T0 + minute),
            ]
        )

        assert [c.timestamp for c in result.candles] == [
            T0,
            T0 + minute,
            T0 + 2 * minute,
        ]
        assert result.out_of_order_rows == 1

    def test_every_backward_step_is_counted(self) -> None:
        minute = timedelta(minutes=1)
        order = [3, 1, 2, 0]  # шаги назад: 3→1 и 2→0

        result = validate_rows(
            [row(i, timestamp=T0 + n * minute) for i, n in enumerate(order)]
        )

        assert result.out_of_order_rows == 2

    def test_sorted_input_has_no_out_of_order_rows(self) -> None:
        minute = timedelta(minutes=1)

        result = validate_rows([row(i, timestamp=T0 + i * minute) for i in range(5)])

        assert result.out_of_order_rows == 0
