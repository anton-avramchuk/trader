"""Провайдер MOEX ISS: контракты срочного рынка FORTS и минутная история.

Факты об API (проверены на живом ISS 2026-09-29):
- свечи: колонки ``open, close, high, low, value, volume, begin, end`` (именно
  в таком порядке), время ``begin`` — открытие свечи, по Москве; страница — 500
  строк, следующая — ``start=N``; ISS **не отдаёт минуты без сделок**;
- повторяющиеся SECID (последняя цифра года) ISS разрешает сам: старый контракт
  переименовывается (``BRZ0`` — 2020, ``BRZ0_2010`` — 2010);
- описание ``/iss/securities/{SECID}`` даёт ``ASSETCODE``, ``LSTTRADE``,
  ``LSTDELDATE`` (экспирация) и диапазон истории по площадке ``RFUD``;
- справочник серий перечисляет действующие контракты с ``asset_code`` — по ним
  определяется префикс SECID (для золота ``GD``, а не ``GOLD``).
"""

import logging
import time
from collections import Counter
from collections.abc import Callable, Iterator
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from trader_engine.ingest import RawRow, RowError

from trader_providers.base import DailyBar, ProviderContract

log = logging.getLogger("trader_providers.iss")

BASE_URL = "https://iss.moex.com"
MONTH_LETTERS = "FGHJKMNQUVXZ"
PAGE_SIZE = 500
MSK = ZoneInfo("Europe/Moscow")
_RETRY_STATUSES = {429, 500, 502, 503, 504}
_BOARD = "RFUD"


class IssError(Exception):
    """Сбой обращения к ISS; сообщение объясняет причину."""


def _table(payload: dict[str, Any], block: str) -> list[dict[str, Any]]:
    """Блок ISS (``columns`` + ``data``) в список словарей."""
    section = payload.get(block)
    if not section:
        return []
    columns: list[str] = section["columns"]
    return [dict(zip(columns, row, strict=True)) for row in section["data"]]


def _date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _to_utc(value: str) -> datetime:
    return (
        datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        .replace(tzinfo=MSK)
        .astimezone(ZoneInfo("UTC"))
    )


class IssClient:
    """Клиент ISS с ограничением частоты запросов и повторами при сбоях."""

    code = "moex_iss"

    def __init__(
        self,
        *,
        base_url: str = BASE_URL,
        client: httpx.Client | None = None,
        min_interval: float = 0.25,
        max_retries: int = 4,
        backoff: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client or httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(30.0),
            headers={"User-Agent": "trader-research/0.1"},
        )
        self._min_interval = min_interval
        self._max_retries = max_retries
        self._backoff = backoff
        self._sleep = sleep
        self._clock = clock
        self._last_request = float("-inf")

    def close(self) -> None:
        self._client.close()

    # --- HTTP ---------------------------------------------------------------

    def _throttle(self) -> None:
        wait = self._min_interval - (self._clock() - self._last_request)
        if wait > 0:
            self._sleep(wait)
        self._last_request = self._clock()

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        query = {"iss.meta": "off", **(params or {})}
        last_problem = ""
        for attempt in range(self._max_retries + 1):
            self._throttle()
            try:
                response = self._client.get(path, params=query)
            except httpx.TransportError as error:
                last_problem = f"{type(error).__name__}: {error}"
            else:
                if response.status_code == 200:
                    try:
                        return response.json()
                    except ValueError as error:
                        raise IssError(f"ISS вернул не JSON на {path}") from error
                if response.status_code not in _RETRY_STATUSES:
                    raise IssError(f"ISS ответил {response.status_code} на {path}")
                last_problem = f"HTTP {response.status_code}"
                retry_after = response.headers.get("Retry-After", "")
                if retry_after.isdigit():
                    self._sleep(float(retry_after))
            if attempt < self._max_retries:
                delay = self._backoff * 2**attempt
                log.warning(
                    "ISS %s: %s, повтор через %.1f с", path, last_problem, delay
                )
                self._sleep(delay)
        raise IssError(
            f"ISS недоступен: {path} — {last_problem} "
            f"(после {self._max_retries + 1} попыток)"
        )

    # --- контракты ----------------------------------------------------------

    def discover_prefix(self, asset_code: str) -> str:
        """Префикс SECID по действующим сериям базового актива."""
        payload = self._get("/iss/statistics/engines/futures/markets/forts/series.json")
        prefixes = Counter(
            row["secid"][:-2]
            for row in _table(payload, "series")
            if row["asset_code"] == asset_code
            and len(row["secid"]) > 2
            and row["secid"][-2] in MONTH_LETTERS
            and row["secid"][-1].isdigit()
        )
        if not prefixes:
            raise IssError(
                f"Нет действующих контрактов с базовым активом {asset_code!r}: "
                "укажите префикс SECID (secid_prefix) вручную"
            )
        return prefixes.most_common(1)[0][0]

    def describe(self, secid: str) -> ProviderContract | None:
        """Описание контракта по SECID; ``None``, если инструмента нет."""
        payload = self._get(f"/iss/securities/{secid}.json")
        description = {
            row["name"]: row["value"] for row in _table(payload, "description")
        }
        expiration = _date(description.get("LSTDELDATE"))
        asset_code = description.get("ASSETCODE")
        if expiration is None or not asset_code:
            return None
        board: dict[str, Any] = {}
        for row in _table(payload, "boards"):
            if row["boardid"] == _BOARD:
                board = row
                break
        return ProviderContract(
            provider_id=secid,
            name=str(description.get("SHORTNAME") or secid),
            asset_code=str(asset_code),
            expiration_date=expiration,
            last_trade_date=_date(description.get("LSTTRADE")),
            history_from=_date(board.get("history_from")),
            history_till=_date(board.get("history_till")),
        )

    def _find_contract(
        self, prefix: str, letter: str, year: int, asset_code: str
    ) -> ProviderContract | None:
        base = f"{prefix}{letter}{year % 10}"
        # Старые контракты с тем же SECID ISS переименовывает: ``BRZ0_2010``.
        for secid in (base, f"{base}_{year}"):
            found = self.describe(secid)
            if (
                found is not None
                and found.asset_code == asset_code
                and abs(found.expiration_date.year - year) <= 1
            ):
                return found
        return None

    def list_contracts(
        self,
        asset_code: str,
        *,
        from_year: int,
        to_year: int,
        secid_prefix: str | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> list[ProviderContract]:
        prefix = secid_prefix or self.discover_prefix(asset_code)
        found: dict[date, ProviderContract] = {}
        total = (to_year - from_year + 1) * len(MONTH_LETTERS)
        done = 0
        for year in range(from_year, to_year + 1):
            for letter in MONTH_LETTERS:
                contract = self._find_contract(prefix, letter, year, asset_code)
                if contract is not None:
                    found[contract.expiration_date] = contract
                done += 1
                if progress is not None:
                    progress(done, total)
        return sorted(found.values(), key=lambda c: c.expiration_date)

    # --- свечи --------------------------------------------------------------

    def candle_range(self, provider_id: str) -> tuple[datetime, datetime] | None:
        payload = self._get(
            f"/iss/engines/futures/markets/forts/securities/{provider_id}/candleborders.json"
        )
        for row in _table(payload, "borders"):
            if row["interval"] == 1:
                return _to_utc(row["begin"]), _to_utc(row["end"])
        return None

    def get_historical_candles(
        self,
        provider_id: str,
        start: date,
        end: date,
        *,
        on_page: Callable[[int], None] | None = None,
    ) -> Iterator[RawRow | RowError]:
        path = (
            f"/iss/engines/futures/markets/forts/securities/{provider_id}/candles.json"
        )
        offset = 0
        row_number = 0
        while True:
            payload = self._get(
                path,
                {
                    "interval": 1,
                    "from": start.isoformat(),
                    "till": end.isoformat(),
                    "start": offset,
                    "iss.only": "candles",
                },
            )
            page = _table(payload, "candles")
            if on_page is not None:
                on_page(offset + len(page))
            # Заканчиваем только на пустой странице: не полагаемся на размер страницы.
            if not page:
                return
            for row in page:
                row_number += 1
                yield self._to_row(row_number, row)
            offset += len(page)

    def daily_history(self, provider_id: str, start: date, end: date) -> list[DailyBar]:
        """Дни, в которые контракт торговался, с объёмом и числом сделок."""
        path = (
            f"/iss/history/engines/futures/markets/forts/securities/{provider_id}.json"
        )
        bars: list[DailyBar] = []
        offset = 0
        while True:
            payload = self._get(
                path,
                {
                    "from": start.isoformat(),
                    "till": end.isoformat(),
                    "start": offset,
                    "iss.only": "history",
                },
            )
            page = _table(payload, "history")
            if not page:
                return bars
            for row in page:
                trade_date = _date(row.get("TRADEDATE"))
                if trade_date is not None:
                    bars.append(
                        DailyBar(
                            trade_date,
                            int(row.get("VOLUME") or 0),
                            int(row.get("NUMTRADES") or 0),
                            _decimal(row.get("VALUE")),
                            _decimal(row.get("WAPRICE")),
                        )
                    )
            offset += len(page)

    @staticmethod
    def _to_row(row_number: int, row: dict[str, Any]) -> RawRow | RowError:
        raw = str(row)
        try:
            moment = _to_utc(str(row["begin"]))
        except (KeyError, ValueError):
            return RowError(
                row_number, raw, "bad_datetime", f"время свечи {row.get('begin')!r}"
            )
        return RawRow(
            row_number=row_number,
            raw=raw,
            timestamp=moment,
            open=_decimal(row.get("open")),
            high=_decimal(row.get("high")),
            low=_decimal(row.get("low")),
            close=_decimal(row.get("close")),
            volume=_decimal(row.get("volume")),
        )
