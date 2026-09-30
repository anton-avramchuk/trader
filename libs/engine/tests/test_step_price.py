"""Стоимость шага цены из дневного итога ISS."""

from decimal import Decimal

import pytest

from trader_engine.step_price import derive_step_price

D = Decimal


def test_matches_real_ng_day_from_iss() -> None:
    # NGH6 10.03.2026: оборот 41 886 672 738,78 ₽, 1 706 416 контрактов, WAPRICE 3,117.
    step = derive_step_price(D("41886672738.78"), 1706416, D("3.117"), D("0.001"))

    assert step is not None
    assert D("7.87") < step < D("7.88")  # ≈ 10 000 mmBtu × курс 78,7 ₽/$ × 0,001


def test_scales_linearly_with_tick_size() -> None:
    base = derive_step_price(D("1000"), 10, D("5"), D("0.01"))
    double = derive_step_price(D("1000"), 10, D("5"), D("0.02"))

    assert base == D("0.20000000") and double == D("0.40000000")


@pytest.mark.parametrize(
    ("value", "volume", "waprice"),
    [
        (None, 10, D("5")),
        (D("1000"), 10, None),
        (D("0"), 10, D("5")),
        (D("1000"), 0, D("5")),
        (D("1000"), 10, D("0")),
        (D("-1"), 10, D("5")),
    ],
)
def test_days_without_a_usable_estimate_give_none(
    value: Decimal | None, volume: int, waprice: Decimal | None
) -> None:
    assert derive_step_price(value, volume, waprice, D("0.01")) is None


def test_non_positive_tick_gives_none() -> None:
    assert derive_step_price(D("1000"), 10, D("5"), D("0")) is None


def test_result_is_rounded_to_price_precision() -> None:
    step = derive_step_price(D("1"), 3, D("1"), D("1"))

    assert step == D("0.33333333")
