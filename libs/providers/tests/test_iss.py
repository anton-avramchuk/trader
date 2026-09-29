from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from trader_engine.ingest import RawRow, RowError

from tests.fake_iss import FakeIss, candle_row
from trader_providers import IssClient, IssError


class FakeTime:
    """Управляемые часы: sleep продвигает время, реального ожидания нет."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def make_client(
    fake: FakeIss, time_: FakeTime | None = None, **kwargs: object
) -> IssClient:
    time_ = time_ or FakeTime()
    return IssClient(
        client=fake.client(),
        sleep=time_.sleep,
        clock=time_.clock,
        **kwargs,  # type: ignore[arg-type]
    )


class TestPrefix:
    def test_prefix_is_taken_from_active_series(self) -> None:
        client = make_client(FakeIss())

        assert client.discover_prefix("BR") == "BR"
        assert client.discover_prefix("GOLD") == "GD"

    def test_unknown_asset_asks_for_manual_prefix(self) -> None:
        client = make_client(FakeIss())

        with pytest.raises(IssError, match="secid_prefix"):
            client.discover_prefix("NOPE")

    def test_perpetual_futures_are_not_mistaken_for_dated_contracts(self) -> None:
        client = make_client(FakeIss())

        with pytest.raises(IssError):
            client.discover_prefix("GLDRUBTOM")


class TestDescribe:
    def test_description_fields_and_board_history(self) -> None:
        contract = make_client(FakeIss()).describe("BRZ0")

        assert contract is not None
        assert (contract.provider_id, contract.name, contract.asset_code) == (
            "BRZ0",
            "BR-12.20",
            "BR",
        )
        assert contract.expiration_date == date(2020, 12, 1)
        assert contract.last_trade_date == date(2020, 12, 1)
        assert contract.history_from == date(2019, 1, 1)
        assert contract.history_till == date(2020, 12, 1)

    def test_unknown_instrument_is_none(self) -> None:
        assert make_client(FakeIss()).describe("BRA0") is None


class TestListContracts:
    def test_finds_contracts_of_the_asset_sorted_by_expiration(self) -> None:
        contracts = make_client(FakeIss()).list_contracts(
            "BR", from_year=2020, to_year=2021
        )

        assert [(c.provider_id, c.expiration_date) for c in contracts] == [
            ("BRZ0", date(2020, 12, 1)),
            ("BRF1", date(2021, 1, 4)),
        ]

    def test_other_assets_with_the_same_secid_pattern_are_ignored(self) -> None:
        contracts = make_client(FakeIss()).list_contracts(
            "BR", from_year=2021, to_year=2021
        )

        assert [c.provider_id for c in contracts] == ["BRF1"]  # BRH1 — актив OTHER

    def test_renamed_old_contract_is_found_by_year_suffix(self) -> None:
        contracts = make_client(FakeIss()).list_contracts(
            "BR", from_year=2010, to_year=2010
        )

        assert [(c.provider_id, c.expiration_date.year) for c in contracts] == [
            ("BRZ0_2010", 2010)
        ]

    def test_current_secid_is_not_taken_for_a_contract_ten_years_earlier(self) -> None:
        fake = FakeIss()

        make_client(fake).list_contracts("BR", from_year=2010, to_year=2010)

        assert (
            "/iss/securities/BRZ0.json" in fake.paths()
        )  # сначала пробуем основной SECID

    def test_explicit_prefix_skips_series_lookup(self) -> None:
        fake = FakeIss()

        contracts = make_client(fake).list_contracts(
            "GOLD", from_year=2026, to_year=2026, secid_prefix="GD"
        )

        assert [c.provider_id for c in contracts] == ["GDZ6"]
        assert not any(path.endswith("series.json") for path in fake.paths())

    def test_gold_prefix_comes_from_series_not_from_the_asset_code(self) -> None:
        contracts = make_client(FakeIss()).list_contracts(
            "GOLD", from_year=2026, to_year=2026
        )

        assert [c.provider_id for c in contracts] == ["GDZ6"]

    def test_progress_is_reported_per_candidate_and_can_abort(self) -> None:
        seen: list[tuple[int, int]] = []

        make_client(FakeIss()).list_contracts(
            "NG",
            from_year=2020,
            to_year=2021,
            secid_prefix="NG",
            progress=lambda d, t: seen.append((d, t)),
        )

        assert seen[0] == (1, 24)
        assert seen[-1] == (24, 24)

        def stop(_done: int, _total: int) -> None:
            raise RuntimeError("отмена")

        with pytest.raises(RuntimeError):
            make_client(FakeIss()).list_contracts(
                "NG", from_year=2020, to_year=2020, secid_prefix="NG", progress=stop
            )

    def test_request_count_is_bounded(self) -> None:
        fake = FakeIss()

        make_client(fake).list_contracts(
            "NG", from_year=2020, to_year=2021, secid_prefix="NG"
        )

        # 12 месяцев × 2 года, для несуществующих пробуется ещё вариант с суффиксом
        assert len(fake.requests) <= 12 * 2 * 2


class TestCandles:
    def rows(
        self,
        fake: FakeIss,
        secid: str = "BRZ0",
        start: str = "2020-11-30",
        end: str = "2020-11-30",
    ) -> list[RawRow | RowError]:
        client = make_client(fake)
        return list(
            client.get_historical_candles(
                secid, date.fromisoformat(start), date.fromisoformat(end)
            )
        )

    def test_moscow_time_becomes_utc_and_columns_are_mapped_by_name(self) -> None:
        fake = FakeIss()
        fake.candles["BRZ0"] = [
            candle_row("2020-11-30 10:00:00", close=47.15, volume=17216)
        ]

        (row,) = self.rows(fake)

        assert isinstance(row, RawRow)
        assert row.timestamp == datetime(2020, 11, 30, 7, 0, tzinfo=UTC)
        # ISS отдаёт колонки в порядке open, close, high, low — не OHLC.
        assert (row.open, row.high, row.low, row.close) == (
            Decimal("47.14"),
            Decimal("47.19"),
            Decimal("47.13"),
            Decimal("47.15"),
        )
        assert row.volume == Decimal(17216)

    def test_pagination_reads_every_page_and_numbers_rows_continuously(self) -> None:
        fake = FakeIss()
        base = datetime(2020, 11, 30, 0, 0)  # 1200 минут укладываются в одни сутки
        fake.candles["BRZ0"] = [
            candle_row((base + timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:00"))
            for i in range(1200)
        ]

        rows = self.rows(fake)

        assert len(rows) == 1200
        assert [r.row_number for r in rows if isinstance(r, RawRow)] == list(
            range(1, 1201)
        )
        starts = [
            r.url.params["start"]
            for r in fake.requests
            if r.url.path.endswith("candles.json")
        ]
        assert starts == ["0", "500", "1000", "1200"]  # последняя страница пустая

    def test_exact_multiple_of_page_size_needs_one_more_request(self) -> None:
        fake = FakeIss()
        base = datetime(2020, 11, 30, 10, 0)
        fake.candles["BRZ0"] = [
            candle_row((base + timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:00"))
            for i in range(500)
        ]

        assert len(self.rows(fake)) == 500

    def test_dates_are_inclusive_and_bounded_by_the_window(self) -> None:
        fake = FakeIss()
        fake.candles["BRZ0"] = [
            candle_row("2020-11-29 10:00:00"),
            candle_row("2020-11-30 10:00:00"),
            candle_row("2020-12-01 10:00:00"),
            candle_row("2020-12-02 10:00:00"),
        ]

        rows = self.rows(fake, start="2020-11-30", end="2020-12-01")

        assert [r.row_number for r in rows] == [1, 2]

    def test_empty_result(self) -> None:
        assert self.rows(FakeIss()) == []

    def test_null_price_is_passed_on_for_validation(self) -> None:
        fake = FakeIss()
        broken = candle_row("2020-11-30 10:00:00")
        broken[0] = None
        fake.candles["BRZ0"] = [broken]

        (row,) = self.rows(fake)

        assert isinstance(row, RawRow)
        assert row.open is None

    def test_unparsable_time_becomes_a_row_error(self) -> None:
        fake = FakeIss()
        fake.candles["BRZ0"] = [candle_row("2020-11-30 10:00:00")]
        fake.candles["BRZ0"][0][6] = "2020-11-30 ??:??:??"

        (row,) = self.rows(fake)

        assert isinstance(row, RowError)
        assert row.code == "bad_datetime"

    def test_page_callback_reports_running_total(self) -> None:
        fake = FakeIss()
        base = datetime(2020, 11, 30, 10, 0)
        fake.candles["BRZ0"] = [
            candle_row((base + timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:00"))
            for i in range(700)
        ]
        seen: list[int] = []

        list(
            make_client(fake).get_historical_candles(
                "BRZ0", date(2020, 11, 30), date(2020, 11, 30), on_page=seen.append
            )
        )

        assert seen == [500, 700, 700]

    def test_candle_range_is_converted_to_utc(self) -> None:
        fake = FakeIss()
        fake.borders["BRZ0"] = [
            ["2019-11-27 16:17:00", "2020-12-01 18:44:00", 1, 45],
            ["2019-10-01 00:00:00", "2020-10-01 00:00:00", 4, 45],
        ]

        first, last = make_client(fake).candle_range("BRZ0") or (None, None)

        assert first == datetime(2019, 11, 27, 13, 17, tzinfo=UTC)
        assert last == datetime(2020, 12, 1, 15, 44, tzinfo=UTC)

    def test_candle_range_without_minute_interval_is_none(self) -> None:
        assert make_client(FakeIss()).candle_range("BRZ0") is None


class TestResilience:
    def test_retries_server_errors_with_backoff_then_succeeds(self) -> None:
        fake, time_ = FakeIss(), FakeTime()
        fake.failures.extend([500, 503])

        contract = make_client(fake, time_, min_interval=0, backoff=1.0).describe(
            "BRZ0"
        )

        assert contract is not None
        assert time_.sleeps == [1.0, 2.0]  # экспоненциальная пауза

    def test_rate_limit_honours_retry_after(self) -> None:
        fake, time_ = FakeIss(), FakeTime()
        fake.failures.append(429)
        fake.retry_after = "7"

        make_client(fake, time_, min_interval=0, backoff=1.0).describe("BRZ0")

        assert 7.0 in time_.sleeps

    def test_connection_errors_are_retried(self) -> None:
        fake = FakeIss()
        fake.failures.append(httpx.ConnectError("нет связи"))

        assert make_client(fake, min_interval=0).describe("BRZ0") is not None

    def test_gives_up_after_the_retry_budget_with_a_clear_message(self) -> None:
        fake = FakeIss()
        fake.failures.extend([503] * 10)

        with pytest.raises(IssError, match=r"недоступен.*503.*5 попыток"):
            make_client(fake, min_interval=0, max_retries=4).describe("BRZ0")

        assert len(fake.requests) == 5

    def test_client_errors_are_not_retried(self) -> None:
        fake = FakeIss()
        fake.failures.append(404)

        with pytest.raises(IssError, match="404"):
            make_client(fake, min_interval=0).describe("BRZ0")

        assert len(fake.requests) == 1

    def test_non_json_answer_is_reported(self) -> None:
        fake = FakeIss()
        fake.raw_override = lambda _request: httpx.Response(
            200, text="<html>login</html>"
        )

        with pytest.raises(IssError, match="не JSON"):
            make_client(fake, min_interval=0).describe("BRZ0")

    def test_requests_are_spaced_by_the_minimum_interval(self) -> None:
        fake, time_ = FakeIss(), FakeTime()
        client = make_client(fake, time_, min_interval=0.5)

        for _ in range(3):
            client.describe("BRZ0")

        # Первый запрос без ожидания; остальные — не чаще одного раза в 0,5 с.
        assert time_.sleeps == [0.5, 0.5]
