"""calendar calibration: schedule history, holidays and special days (issue #59)

Расписание FORTS откалибровано по данным MOEX ISS (см. ADR-0014 и
scripts/calibrate_forts_calendar.py): даты переходов найдены бисекцией по
минутным свечам, праздники — по дневной истории. Начальные правила из 0002
(восстановленные по памяти) заменяются на 12 эпох; добавляются праздники и
«особые торговые дни» (рабочие субботы-переносы).

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-29 20:00:00
"""

from collections.abc import Sequence
from datetime import date, time
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HOLIDAY_NAME = "нет торгов (данные ISS)"
SPECIAL_NAME = "рабочая суббота-перенос (данные ISS)"


def _w(name: str, start: str, end: str, *, next_day: bool = False) -> dict[str, Any]:
    return {"name": name, "start": start, "end": end, "next_day": next_day}


_MORNING_0700 = _w("morning", "07:00", "10:00")
_MORNING_0859 = _w("morning", "08:59", "10:00")
_MAIN = _w("main", "10:00", "14:00")
_MAIN_AUCTION = _w("main", "09:59", "14:00")
_MAIN_2_1845 = _w("main_2", "14:05", "18:45")
_MAIN_2_1850 = _w("main_2", "14:05", "18:50")
_EVENING_1900 = _w("evening", "19:00", "23:50", next_day=True)
_EVENING_1905 = _w("evening", "19:05", "23:50", next_day=True)
_WEEKEND = [_w("weekend", "09:59", "19:00")]
_UNIFIED_MAIN = _w("main", "10:00", "19:00")
_UNIFIED_EVENING = _w("evening", "19:00", "23:50")

# (с, по включительно, окна будней, окна выходных)
ERAS: list[tuple[date, date | None, list[dict[str, Any]], list[dict[str, Any]]]] = [
    (date(2020, 1, 1), date(2021, 2, 28), [_MAIN, _MAIN_2_1845, _EVENING_1900], []),
    (
        date(2021, 3, 1),
        date(2022, 2, 24),
        [_MORNING_0700, _MAIN, _MAIN_2_1845, _EVENING_1900],
        [],
    ),
    (date(2022, 2, 25), date(2022, 3, 1), [_MAIN, _MAIN_2_1845, _EVENING_1900], []),
    (date(2022, 3, 2), date(2022, 5, 29), [_MAIN, _MAIN_2_1845], []),
    (date(2022, 5, 30), date(2022, 7, 11), [_MAIN, _MAIN_2_1850], []),
    (date(2022, 7, 12), date(2022, 9, 11), [_MAIN, _MAIN_2_1850, _EVENING_1905], []),
    (
        date(2022, 9, 12),
        date(2024, 6, 12),
        [_MORNING_0859, _MAIN, _MAIN_2_1850, _EVENING_1905],
        [],
    ),
    (
        date(2024, 6, 13),
        date(2025, 1, 26),
        [_MAIN_AUCTION, _MAIN_2_1850, _EVENING_1905],
        [],
    ),
    (
        date(2025, 1, 27),
        date(2025, 8, 15),
        [_MORNING_0859, _MAIN, _MAIN_2_1850, _EVENING_1905],
        [],
    ),
    (
        date(2025, 8, 16),
        date(2026, 3, 22),
        [_MORNING_0859, _MAIN, _MAIN_2_1850, _EVENING_1905],
        _WEEKEND,
    ),
    (
        date(2026, 3, 23),
        date(2026, 7, 13),
        [_MORNING_0859, _UNIFIED_MAIN, _UNIFIED_EVENING],
        _WEEKEND,
    ),
    (
        date(2026, 7, 14),
        None,
        [_w("morning", "06:59", "10:00"), _UNIFIED_MAIN, _UNIFIED_EVENING],
        _WEEKEND,
    ),
]

# Правила, поставленные миграцией 0002 (восстановлены при откате).
LEGACY_RULES: list[
    tuple[date, date | None, list[dict[str, Any]], list[dict[str, Any]]]
] = [
    (
        date(2020, 1, 1),
        date(2026, 3, 22),
        [
            _w("morning", "07:00", "09:50"),
            _w("main", "10:00", "14:00"),
            _w("main_2", "14:05", "18:45"),
            _w("evening", "19:05", "23:50", next_day=True),
        ],
        [],
    ),
    (
        date(2026, 3, 23),
        None,
        [
            _w("morning", "07:00", "09:00"),
            _w("main", "09:00", "19:00"),
            _w("evening", "19:00", "23:50"),
        ],
        [_w("weekend", "10:00", "19:00")],
    ),
]

HOLIDAYS = (
    # 2020
    "2020-01-07",
    "2020-02-24",
    "2020-03-09",
    "2020-05-01",
    "2020-05-11",
    "2020-06-12",
    "2020-06-24",
    "2020-07-01",
    "2020-09-11",
    "2020-09-14",
    "2020-11-04",
    "2020-12-31",
    # 2021
    "2021-01-01",
    "2021-01-07",
    "2021-02-23",
    "2021-03-08",
    "2021-05-03",
    "2021-11-04",
    "2021-12-31",
    # 2022
    "2022-03-01",
    "2022-03-07",
    "2022-03-08",
    "2022-05-02",
    "2022-05-03",
    "2022-05-09",
    "2022-05-10",
    "2022-06-13",
    "2022-11-04",
    # 2023
    "2023-01-02",
    "2023-02-23",
    "2023-03-08",
    "2023-05-01",
    "2023-05-09",
    "2023-06-12",
    # 2024
    "2024-01-01",
    "2024-01-02",
    "2024-02-23",
    "2024-03-08",
    "2024-05-01",
    "2024-05-09",
    "2024-06-12",
    "2024-11-04",
    "2024-12-31",
    # 2025
    "2025-01-01",
    "2025-01-02",
    "2025-01-07",
    "2025-05-01",
    "2025-05-09",
    "2025-06-12",
    "2025-09-20",
    "2025-09-21",
    "2025-10-25",
    "2025-10-26",
    "2025-11-02",
    "2025-11-04",
    "2025-11-22",
    "2025-11-23",
    "2025-12-06",
    "2025-12-07",
    "2025-12-31",
    # 2026
    "2026-01-01",
    "2026-01-02",
    "2026-01-03",
    "2026-01-04",
    "2026-01-07",
    "2026-01-10",
    "2026-01-11",
    "2026-02-14",
    "2026-02-15",
    "2026-02-23",
    "2026-03-07",
    "2026-03-08",
    "2026-03-21",
    "2026-03-22",
    "2026-05-01",
    "2026-05-09",
    "2026-05-10",
    "2026-06-12",
    "2026-06-20",
    "2026-06-21",
    "2026-08-01",
    "2026-08-02",
    "2026-08-15",
    "2026-08-16",
    "2026-09-12",
    "2026-09-13",
)

SPECIAL_DAYS = ("2021-02-20", "2024-04-27", "2024-11-02", "2024-12-28", "2025-11-01")


def upgrade() -> None:
    op.create_table(
        "trading_calendar_special_days",
        sa.Column("calendar_id", sa.BigInteger(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=True),
        sa.ForeignKeyConstraint(
            ["calendar_id"],
            ["trading_calendars.id"],
            name=op.f("fk_trading_calendar_special_days_calendar_id_trading_calendars"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "calendar_id", "date", name=op.f("pk_trading_calendar_special_days")
        ),
    )
    calendar_id = _calendar_id()
    if calendar_id is None:
        return
    _replace_rules(calendar_id, ERAS)
    op.bulk_insert(
        _holidays_table(),
        [
            {
                "calendar_id": calendar_id,
                "date": date.fromisoformat(day),
                "name": HOLIDAY_NAME,
            }
            for day in HOLIDAYS
        ],
    )
    op.bulk_insert(
        _special_days_table(),
        [
            {
                "calendar_id": calendar_id,
                "date": date.fromisoformat(day),
                "name": SPECIAL_NAME,
            }
            for day in SPECIAL_DAYS
        ],
    )


def downgrade() -> None:
    calendar_id = _calendar_id()
    if calendar_id is not None:
        connection = op.get_bind()
        connection.execute(
            sa.text(
                "DELETE FROM trading_calendar_holidays "
                "WHERE calendar_id = :c AND name = :n"
            ),
            {"c": calendar_id, "n": HOLIDAY_NAME},
        )
        _replace_rules(calendar_id, LEGACY_RULES)
    op.drop_table("trading_calendar_special_days")


def _calendar_id() -> int | None:
    return (
        op.get_bind()
        .execute(sa.text("SELECT id FROM trading_calendars WHERE code = 'moex_forts'"))
        .scalar_one_or_none()
    )


def _rules_table() -> sa.TableClause:
    return sa.table(
        "trading_calendar_rules",
        sa.column("calendar_id", sa.BigInteger),
        sa.column("effective_from", sa.Date),
        sa.column("effective_to", sa.Date),
        sa.column("bar_anchor", sa.Time),
        sa.column("weekday_windows", postgresql.JSONB),
        sa.column("weekend_windows", postgresql.JSONB),
    )


def _holidays_table() -> sa.TableClause:
    return sa.table(
        "trading_calendar_holidays",
        sa.column("calendar_id", sa.BigInteger),
        sa.column("date", sa.Date),
        sa.column("name", sa.String),
    )


def _special_days_table() -> sa.TableClause:
    return sa.table(
        "trading_calendar_special_days",
        sa.column("calendar_id", sa.BigInteger),
        sa.column("date", sa.Date),
        sa.column("name", sa.String),
    )


def _replace_rules(
    calendar_id: int,
    rules: list[tuple[date, date | None, list[dict[str, Any]], list[dict[str, Any]]]],
) -> None:
    """Заменить правила календаря (исключающее ограничение не допускает перекрытий)."""
    op.get_bind().execute(
        sa.text("DELETE FROM trading_calendar_rules WHERE calendar_id = :c"),
        {"c": calendar_id},
    )
    op.bulk_insert(
        _rules_table(),
        [
            {
                "calendar_id": calendar_id,
                "effective_from": start,
                "effective_to": end,
                "bar_anchor": time(7, 0),
                "weekday_windows": weekday,
                "weekend_windows": weekend,
            }
            for start, end, weekday, weekend in rules
        ],
    )
