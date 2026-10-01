"""Источник MOEX ISS: акции режима торгов TQBR (прокси без хранения).

ISS отдаёт свечи интервалов 1, 10, 60 минут, день и неделю. Остальные таймфреймы
собираются здесь из более мелких: 15m — из минутных, 4h — из часовых (сетка по
полуночи Москвы). Время ISS — московское, наружу всё отдаётся в UTC.
"""

import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from trader_importer.aggregate import aggregate
from trader_importer.models import (
    DURATIONS,
    TIMEFRAMES,
    Candle,
    HistoryPage,
    Ticker,
    Timeframe,
)
from trader_importer.source import SourceError, UnknownTicker

MSK = ZoneInfo("Europe/Moscow")
PAGE = 500  # строк на страницу свечей у ISS
MAX_PAGES = 40  # потолок страниц ISS на один запрос history
RETRIES = 3
# таймфрейм → (интервал ISS, интервал агрегации в минутах или None)
PLAN: dict[str, tuple[int, int | None]] = {
    "15m": (1, 15),
    "1h": (60, None),
    "4h": (60, 240),
    "1d": (24, None),
    "1w": (7, None),
}


def _local(moment: datetime) -> str:
    return moment.astimezone(MSK).strftime("%Y-%m-%d %H:%M:%S")


def _utc(value: str) -> datetime:
    return (
        datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        .replace(tzinfo=MSK)
        .astimezone(UTC)
    )


def _rows(payload: dict[str, Any], block: str) -> list[dict[str, Any]]:
    data = payload.get(block) or {}
    columns: list[str] = data.get("columns", [])
    return [dict(zip(columns, row, strict=False)) for row in data.get("data", [])]


class IssSource:
    """Прокси к ISS; ``client`` и ``clock`` подменяются в тестах."""

    def __init__(
        self,
        client: httpx.Client | None = None,
        *,
        board: str = "TQBR",
        min_interval: float = 0.15,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client or httpx.Client(
            base_url="https://iss.moex.com", timeout=30.0
        )
        self._board = board
        self._min_interval = min_interval
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sleep = sleep
        self._last_call = 0.0

    def close(self) -> None:
        self._client.close()

    # --- HTTP -------------------------------------------------------------

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        query = {"iss.meta": "off", **(params or {})}
        last: Exception | None = None
        for attempt in range(RETRIES):
            wait = self._min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                self._sleep(wait)
            self._last_call = time.monotonic()
            try:
                response = self._client.get(path, params=query)
            except httpx.HTTPError as error:
                last = error
            else:
                if response.status_code == 404:
                    raise UnknownTicker(path)
                if response.status_code < 400:
                    return response.json()
                last = SourceError(f"ISS ответил {response.status_code}")
                if response.status_code < 500 and response.status_code != 429:
                    break
            self._sleep(0.5 * (attempt + 1))
        raise SourceError(f"ISS недоступен: {last}")

    @property
    def _base(self) -> str:
        return f"/iss/engines/stock/markets/shares/boards/{self._board}"

    # --- тикеры -----------------------------------------------------------

    def _ticker(self, row: dict[str, Any]) -> Ticker:
        return Ticker(
            ticker=str(row["SECID"]),
            name=str(row.get("SHORTNAME") or row["SECID"]),
            currency="RUB"
            if row.get("CURRENCYID") in (None, "SUR")
            else str(row["CURRENCYID"]),
            tick_size=Decimal(str(row.get("MINSTEP") or "0.01")),
            timezone="Europe/Moscow",
            timeframes=list(TIMEFRAMES),
        )

    def tickers(self) -> list[Ticker]:
        payload = self._get(
            f"{self._base}/securities.json",
            {"securities.columns": "SECID,SHORTNAME,CURRENCYID,MINSTEP"},
        )
        return [self._ticker(row) for row in _rows(payload, "securities")]

    def ticker(self, ticker: str) -> Ticker:
        payload = self._get(
            f"{self._base}/securities/{ticker}.json",
            {"securities.columns": "SECID,SHORTNAME,CURRENCYID,MINSTEP"},
        )
        rows = _rows(payload, "securities")
        if not rows:
            raise UnknownTicker(ticker)
        found = self._ticker(rows[0])
        borders = _rows(
            self._get(f"{self._base}/securities/{ticker}/candleborders.json"),
            "borders",
        )
        daily = next((b for b in borders if b.get("interval") == 24), None)
        if daily:
            first = date.fromisoformat(str(daily["begin"])[:10])
            last = date.fromisoformat(str(daily["end"])[:10])
            return found.model_copy(update={"first_date": first, "last_date": last})
        return found

    # --- история ----------------------------------------------------------

    def _fetch(
        self, ticker: str, interval: int, start: datetime, end: datetime
    ) -> tuple[list[Candle], bool]:
        """Свечи ISS за период; ``True`` — упёрлись в потолок страниц."""
        candles: list[Candle] = []
        for page in range(MAX_PAGES):
            payload = self._get(
                f"{self._base}/securities/{ticker}/candles.json",
                {
                    "interval": interval,
                    "from": _local(start),
                    "till": _local(end),
                    "start": page * PAGE,
                },
            )
            rows = _rows(payload, "candles")
            for row in rows:
                begin = _utc(str(row["begin"]))
                if start <= begin < end:
                    candles.append(
                        Candle(
                            t=begin,
                            o=Decimal(str(row["open"])),
                            h=Decimal(str(row["high"])),
                            l=Decimal(str(row["low"])),
                            c=Decimal(str(row["close"])),
                            v=Decimal(str(row["volume"])),
                        )
                    )
            if len(rows) < PAGE:
                return candles, False
        return candles, True

    def history(
        self, ticker: str, tf: Timeframe, start: datetime, end: datetime, limit: int
    ) -> HistoryPage:
        interval, minutes = PLAN[tf]
        raw, truncated = self._fetch(ticker, interval, start, end)
        if not raw and not truncated:
            self.ticker(ticker)  # неизвестный тикер → UnknownTicker, иначе пусто
        candles = aggregate(raw, minutes, MSK) if minutes else raw
        next_from: datetime | None = None
        if truncated and candles:
            # последняя свеча могла собраться не из всех строк — на след. страницу
            next_from = candles[-1].t
            candles = candles[:-1]
        closed_before = self._clock()
        candles = [c for c in candles if c.t + DURATIONS[tf] <= closed_before]
        if len(candles) > limit:
            next_from = candles[limit].t
            candles = candles[:limit]
        return HistoryPage(ticker=ticker, tf=tf, candles=candles, next_from=next_from)
