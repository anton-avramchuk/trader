"""Построение баров TF в БД (нужен TRADER_DATABASE_URL)."""

import time
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from trader_engine.aggregation import TIMEFRAMES, Bar, aggregate
from trader_engine.calendar import TradingCalendar, moex_forts_calendar
from trader_engine.ingest import Candle1m

from trader_db import (
    build_bars,
    enqueue_aggregation,
    extend_dataset_version,
    finish_import,
    insert_candles,
    read_bars,
    start_import,
)
from trader_db.models import DerivedBuild, DerivedCandle

CALENDAR = moex_forts_calendar()
FAR = datetime(2030, 1, 1, tzinfo=UTC)
MINUTE = timedelta(minutes=1)


def day_candles(day: date, *, step: int = 7, offset: int = 0) -> list[Candle1m]:
    """Минутные свечи дня по расписанию календаря, каждая ``step``-я минута."""
    candles: list[Candle1m] = []
    index = offset
    for session in CALENDAR.sessions_on(day):
        moment = session.start
        while moment < session.end:
            if index % step == 0:
                base = 100 + (index % 17)
                candles.append(
                    Candle1m(
                        timestamp=moment,
                        open=Decimal(base),
                        high=Decimal(base + 3),
                        low=Decimal(base - 2),
                        close=Decimal(base + 1),
                        volume=Decimal(index % 5 + 1),
                    )
                )
            index += 1
            moment += MINUTE
    return candles


def load(session: Session, contract_id: int, candles: list[Candle1m]) -> int:
    """Импорт свечей и новая версия датасета; вернуть id версии."""
    import_id = start_import(
        session, provider_code="csv", contract_id=contract_id, source_type="file"
    )
    insert_candles(session, import_id, candles)
    finish_import(session, import_id)
    return extend_dataset_version(session, import_id).id


def build_all(
    session: Session,
    contract_id: int,
    *,
    calendar: TradingCalendar = CALENDAR,
    include_weekend_sessions: bool = True,
    complete_until: datetime = FAR,
    force: bool = False,
) -> dict[str, str]:
    return {
        timeframe: build_bars(
            session,
            contract_id,
            timeframe,
            calendar,
            include_weekend_sessions=include_weekend_sessions,
            complete_until=complete_until,
            force=force,
        ).mode
        for timeframe in TIMEFRAMES
    }


def expected_bars(
    candles: list[Candle1m],
    timeframe: str,
    *,
    calendar: TradingCalendar = CALENDAR,
    include_weekend_sessions: bool = True,
    complete_until: datetime = FAR,
) -> list[Bar]:
    effective = (
        calendar if include_weekend_sessions else calendar.excluding_weekend_sessions()
    )
    return aggregate(
        sorted(candles, key=lambda c: c.timestamp),
        effective,
        timeframe,
        complete_until=complete_until,
    )[0]


def stored(session: Session, contract_id: int, timeframe: str) -> list[Bar]:
    return read_bars(session, contract_id, timeframe)


WEEK1 = [date(2026, 9, 28), date(2026, 9, 29)]
WEEK2 = [date(2026, 10, 5), date(2026, 10, 6)]
WEEK0 = [date(2026, 9, 21), date(2026, 9, 22)]


def candles_of(days: list[date]) -> list[Candle1m]:
    return [c for day in days for c in day_candles(day)]


class TestFullBuild:
    def test_stored_bars_equal_the_pure_aggregation(
        self, session: Session, contract_id: int
    ) -> None:
        candles = candles_of(WEEK1)
        load(session, contract_id, candles)

        modes = build_all(session, contract_id)

        assert set(modes.values()) == {"full"}
        for timeframe in TIMEFRAMES:
            assert stored(session, contract_id, timeframe) == expected_bars(
                candles, timeframe
            )

    def test_build_is_journaled_with_calendar_fingerprint_and_version(
        self, session: Session, contract_id: int
    ) -> None:
        version = load(session, contract_id, candles_of(WEEK1))

        result = build_bars(
            session,
            contract_id,
            "1h",
            CALENDAR,
            include_weekend_sessions=True,
            complete_until=FAR,
        )

        build = session.scalars(select(DerivedBuild)).one()
        assert (build.mode, build.source_dataset_version_id) == ("full", version)
        assert build.calendar_hash == CALENDAR.fingerprint()
        assert build.rows_written == result.rows_written > 0
        assert build.report["candles_in"] == len(candles_of(WEEK1))
        rows = session.scalars(select(DerivedCandle)).all()
        assert {r.built_from_version_id for r in rows} == {version}

    def test_forming_bar_is_not_published(
        self, session: Session, contract_id: int
    ) -> None:
        candles = candles_of(WEEK1)
        load(session, contract_id, candles)
        cutoff = candles[-1].timestamp  # данные полны только до последней свечи

        build_bars(
            session,
            contract_id,
            "15m",
            CALENDAR,
            include_weekend_sessions=True,
            complete_until=cutoff,
        )

        bars = stored(session, contract_id, "15m")
        assert all(bar.close_time <= cutoff for bar in bars)
        assert len(bars) == len(expected_bars(candles, "15m", complete_until=cutoff))

    def test_no_minute_data_is_an_error(
        self, session: Session, contract_id: int
    ) -> None:
        with pytest.raises(LookupError, match="нет минутных данных"):
            build_bars(
                session,
                contract_id,
                "1d",
                CALENDAR,
                include_weekend_sessions=True,
                complete_until=FAR,
            )

    def test_candles_outside_sessions_are_reported_not_hidden(
        self, session: Session, contract_id: int
    ) -> None:
        late = Candle1m(
            timestamp=datetime(2026, 9, 29, 21, 0, tzinfo=UTC),
            open=Decimal(1),
            high=Decimal(1),
            low=Decimal(1),
            close=Decimal(1),
            volume=Decimal(1),
        )
        load(session, contract_id, [*candles_of(WEEK1), late])

        result = build_bars(
            session,
            contract_id,
            "1h",
            CALENDAR,
            include_weekend_sessions=True,
            complete_until=FAR,
        )

        assert result.report["skipped_outside_session"] == 1
        assert result.report["skipped_sample"] == ["2026-09-29T21:00:00+00:00"]


class TestIncrementalBuild:
    def test_nothing_changed_is_a_noop(
        self, session: Session, contract_id: int
    ) -> None:
        load(session, contract_id, candles_of(WEEK1))
        build_all(session, contract_id)
        builds = session.scalar(select(func.count()).select_from(DerivedBuild))

        modes = build_all(session, contract_id)

        assert set(modes.values()) == {"noop"}
        assert session.scalar(select(func.count()).select_from(DerivedBuild)) == builds

    def test_new_week_rebuilds_only_that_week_and_matches_a_full_build(
        self, session: Session, contract_id: int
    ) -> None:
        first = load(session, contract_id, candles_of(WEEK1))
        build_all(session, contract_id)
        second = load(session, contract_id, candles_of(WEEK2))

        modes = build_all(session, contract_id)

        assert set(modes.values()) == {"incremental"}
        rows = session.scalars(select(DerivedCandle)).all()
        assert {
            r.built_from_version_id for r in rows if r.trading_day < date(2026, 10, 5)
        } == {first}
        assert {
            r.built_from_version_id for r in rows if r.trading_day >= date(2026, 10, 5)
        } == {second}
        everything = candles_of(WEEK1) + candles_of(WEEK2)
        for timeframe in TIMEFRAMES:
            assert stored(session, contract_id, timeframe) == expected_bars(
                everything, timeframe
            )

    def test_data_added_into_an_earlier_week_rebuilds_from_that_week(
        self, session: Session, contract_id: int
    ) -> None:
        load(session, contract_id, candles_of(WEEK2))
        build_all(session, contract_id)
        load(session, contract_id, candles_of(WEEK0))

        build_all(session, contract_id)

        everything = candles_of(WEEK0) + candles_of(WEEK2)
        for timeframe in TIMEFRAMES:
            assert stored(session, contract_id, timeframe) == expected_bars(
                everything, timeframe
            )

    def test_new_candles_in_a_known_week_rewrite_that_week(
        self, session: Session, contract_id: int
    ) -> None:
        early = day_candles(WEEK1[0])
        load(session, contract_id, early)
        build_all(session, contract_id)
        later = day_candles(WEEK1[1])
        second = load(session, contract_id, later)

        modes = build_all(session, contract_id)

        assert set(modes.values()) == {"incremental"}
        rows = session.scalars(select(DerivedCandle)).all()
        assert {r.built_from_version_id for r in rows} == {second}
        for timeframe in TIMEFRAMES:
            assert stored(session, contract_id, timeframe) == expected_bars(
                early + later, timeframe
            )

    def test_friday_evening_of_the_legacy_regime_lands_in_the_next_week(
        self, session: Session, contract_id: int
    ) -> None:
        # Пятничная вечерняя сессия 20.03.2026 — торговый день понедельника 23.03.
        first = day_candles(date(2026, 3, 16))
        load(session, contract_id, first)
        build_all(session, contract_id)
        friday = day_candles(date(2026, 3, 20))
        monday = day_candles(date(2026, 3, 23))
        load(session, contract_id, friday + monday)

        build_all(session, contract_id)

        everything = first + friday + monday
        for timeframe in TIMEFRAMES:
            assert stored(session, contract_id, timeframe) == expected_bars(
                everything, timeframe
            )
        daily = {b.trading_day: b for b in stored(session, contract_id, "1d")}
        assert date(2026, 3, 23) in daily
        assert daily[date(2026, 3, 23)].timestamp == datetime(
            2026, 3, 20, 16, 5, tzinfo=UTC
        )

    def test_advancing_complete_until_publishes_the_withheld_bar(
        self, session: Session, contract_id: int
    ) -> None:
        candles = candles_of(WEEK1)
        load(session, contract_id, candles)
        cutoff = candles[-1].timestamp
        build_all(session, contract_id, complete_until=cutoff)
        withheld = len(stored(session, contract_id, "1w"))

        modes = build_all(session, contract_id, complete_until=FAR)

        assert set(modes.values()) == {"incremental"}
        assert len(stored(session, contract_id, "1w")) == withheld + 1
        for timeframe in TIMEFRAMES:
            assert stored(session, contract_id, timeframe) == expected_bars(
                candles, timeframe
            )


class TestFullRebuildTriggers:
    def test_calendar_change_forces_a_full_rebuild(
        self, session: Session, contract_id: int
    ) -> None:
        candles = candles_of(WEEK1)
        load(session, contract_id, candles)
        build_all(session, contract_id)
        # Праздник 29.09: календарь другой, значит, бары этого дня больше не строятся.
        changed = moex_forts_calendar(extra_holidays=(date(2026, 9, 29),))

        modes = build_all(session, contract_id, calendar=changed)

        assert set(modes.values()) == {"full"}
        for timeframe in TIMEFRAMES:
            assert stored(session, contract_id, timeframe) == expected_bars(
                candles, timeframe, calendar=changed
            )

    def test_weekend_flag_change_forces_a_full_rebuild(
        self, session: Session, contract_id: int
    ) -> None:
        candles = candles_of(
            [date(2026, 9, 25), date(2026, 9, 26)]
        )  # пятница и суббота
        load(session, contract_id, candles)
        build_all(session, contract_id, include_weekend_sessions=True)
        with_weekend = len(stored(session, contract_id, "1d"))

        modes = build_all(session, contract_id, include_weekend_sessions=False)

        assert set(modes.values()) == {"full"}
        assert len(stored(session, contract_id, "1d")) == with_weekend - 1
        assert stored(session, contract_id, "1d") == expected_bars(
            candles, "1d", include_weekend_sessions=False
        )

    def test_force_rebuilds_everything(
        self, session: Session, contract_id: int
    ) -> None:
        load(session, contract_id, candles_of(WEEK1))
        build_all(session, contract_id)

        modes = build_all(session, contract_id, force=True)

        assert set(modes.values()) == {"full"}


class TestReadAndQueue:
    def test_read_bars_filters_by_half_open_range(
        self, session: Session, contract_id: int
    ) -> None:
        load(session, contract_id, candles_of(WEEK1))
        build_all(session, contract_id)
        bars = stored(session, contract_id, "1h")
        start, end = bars[3].timestamp, bars[6].timestamp

        window = read_bars(session, contract_id, "1h", start, end)

        assert window == bars[3:6]

    def test_aggregation_job_is_not_enqueued_twice(
        self, session: Session, contract_id: int
    ) -> None:
        first = enqueue_aggregation(session, contract_id)
        second = enqueue_aggregation(session, contract_id)

        assert first is not None
        assert second is None


class TestPerformance:
    def test_a_year_of_minutes_aggregates_into_all_timeframes_quickly(
        self, session: Session, contract_id: int
    ) -> None:
        candles: list[Candle1m] = []
        day = date(2025, 10, 1)
        while day < date(2026, 10, 1):
            candles.extend(day_candles(day, step=1))
            day += timedelta(days=1)
        assert len(candles) > 200_000
        load(session, contract_id, candles)

        started = time.perf_counter()
        modes = build_all(session, contract_id)
        elapsed = time.perf_counter() - started

        assert set(modes.values()) == {"full"}
        assert stored(session, contract_id, "1d")
        assert elapsed < 180, f"{len(candles)} минут за {elapsed:.1f} с"
