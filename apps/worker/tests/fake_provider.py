"""Поддельный провайдер: HistoricalDataProvider без сети, с записью вызовов."""

from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from trader_engine.ingest import RawRow, RowError
from trader_providers import DailyBar, IssError, ProviderContract

MSK = ZoneInfo("Europe/Moscow")
TODAY = date(2026, 10, 5)
NOW = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
MON, TUE, WED = date(2026, 9, 28), date(2026, 9, 29), date(2026, 9, 30)


def contract(
    secid: str, expiration: date, *, asset: str = "BR", last_trade: date | None = None
) -> ProviderContract:
    return ProviderContract(
        provider_id=secid,
        name=secid,
        asset_code=asset,
        expiration_date=expiration,
        last_trade_date=last_trade or expiration,
        history_from=None,
        history_till=None,
    )


def day_rows(day: date, count: int = 30) -> list[RawRow]:
    """Минутные свечи с 10:00 МСК указанного дня (сессия единого режима)."""
    start = datetime(day.year, day.month, day.day, 10, 0, tzinfo=MSK).astimezone(UTC)
    return [
        RawRow(
            row_number=i + 1,
            raw=f"{day} #{i}",
            timestamp=start + timedelta(minutes=i),
            open=Decimal("70"),
            high=Decimal("71"),
            low=Decimal("69"),
            close=Decimal("70.5"),
            volume=Decimal("10"),
        )
        for i in range(count)
    ]


class FakeProvider:
    code = "moex_iss"

    def __init__(self, contracts: list[ProviderContract] | None = None) -> None:
        self.contracts = contracts or []
        self.days: dict[str, dict[date, list[RawRow | RowError]]] = {}
        self.range_override: dict[str, tuple[datetime, datetime] | None] = {}
        self.list_calls: list[dict[str, object]] = []
        self.candle_calls: list[tuple[str, date, date]] = []
        self.daily: dict[str, list[DailyBar]] = {}
        self.daily_calls: list[tuple[str, date, date]] = []
        self.closed = 0
        self.list_error: IssError | None = None
        self.fail_on: Callable[[str, date, date], IssError | None] | None = None

    def set_days(self, secid: str, days: list[date], count: int = 30) -> None:
        self.days[secid] = {day: list(day_rows(day, count)) for day in days}

    def daily_history(self, provider_id: str, start: date, end: date) -> list[DailyBar]:
        self.daily_calls.append((provider_id, start, end))
        if self.fail_on is not None and (
            error := self.fail_on(provider_id, start, end)
        ):
            raise error
        return [
            bar
            for bar in self.daily.get(provider_id, [])
            if start <= bar.trade_date <= end
        ]

    def factory(self) -> "FakeProvider":
        return self

    def list_contracts(
        self,
        asset_code: str,
        *,
        from_year: int,
        to_year: int,
        secid_prefix: str | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> list[ProviderContract]:
        self.list_calls.append(
            {
                "asset": asset_code,
                "from": from_year,
                "to": to_year,
                "prefix": secid_prefix,
            }
        )
        if self.list_error is not None:
            raise self.list_error
        if progress is not None:
            progress(1, 1)
        return [c for c in self.contracts if c.asset_code == asset_code]

    def candle_range(self, provider_id: str) -> tuple[datetime, datetime] | None:
        if provider_id in self.range_override:
            return self.range_override[provider_id]
        by_day = self.days.get(provider_id)
        if not by_day:
            return None
        stamps = [
            row.timestamp
            for rows in by_day.values()
            for row in rows
            if isinstance(row, RawRow) and row.timestamp is not None
        ]
        return min(stamps), max(stamps)

    def get_historical_candles(
        self,
        provider_id: str,
        start: date,
        end: date,
        *,
        on_page: Callable[[int], None] | None = None,
    ) -> Iterator[RawRow | RowError]:
        self.candle_calls.append((provider_id, start, end))
        if self.fail_on is not None:
            error = self.fail_on(provider_id, start, end)
            if error is not None:
                raise error
        total = 0
        for day in sorted(self.days.get(provider_id, {})):
            if start <= day <= end:
                for row in self.days[provider_id][day]:
                    total += 1
                    yield row
        if on_page is not None:
            on_page(total)

    def close(self) -> None:
        self.closed += 1
