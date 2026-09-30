"""Стоимость шага цены фьючерса по итогам торгового дня (ADR-0007).

ISS не отдаёт историю стоимости шага, но её можно вывести из дневного итога:
оборот в рублях ``VALUE`` = объём × средневзвешенная цена × (₽ за единицу цены),
а стоимость шага = ₽ за единицу цены × шаг цены (``tick_size``).
"""

from decimal import ROUND_HALF_UP, Decimal

STEP_PRICE_PLACES = Decimal("0.00000001")


def derive_step_price(
    value: Decimal | None,
    volume: int,
    waprice: Decimal | None,
    tick_size: Decimal,
) -> Decimal | None:
    """Стоимость шага цены в ₽ или ``None``, если день не даёт оценки.

    Нет оборота, объёма или цены (день без сделок) — оценки нет.
    """
    if value is None or waprice is None:
        return None
    if value <= 0 or volume <= 0 or waprice <= 0 or tick_size <= 0:
        return None
    per_unit = value / (Decimal(volume) * waprice)
    return (per_unit * tick_size).quantize(STEP_PRICE_PLACES, rounding=ROUND_HALF_UP)
