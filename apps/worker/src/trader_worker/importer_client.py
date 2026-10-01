"""HTTP-клиент сервиса importer (ADR-0028): тикеры и страницы закрытых свечей."""

import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx

DEFAULT_URL = "http://127.0.0.1:8100"
PAGE_LIMIT = 5000
MAX_PAGES = 100_000  # защита от зацикливания при некорректном ответе


class ImporterError(Exception):
    """Importer недоступен или ответил ошибкой (понятное сообщение для пользователя)."""


@dataclass(frozen=True, slots=True)
class ImporterCandle:
    t: datetime
    o: Decimal
    h: Decimal
    l: Decimal  # noqa: E741 — поля контракта importer
    c: Decimal
    v: Decimal


def importer_url() -> str:
    return os.environ.get("TRADER_IMPORTER_URL", DEFAULT_URL).rstrip("/")


def _moment(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


class ImporterClient:
    """Клиент importer; ``client`` подменяется в тестах (``httpx.MockTransport``)."""

    def __init__(
        self, base_url: str | None = None, client: httpx.Client | None = None
    ) -> None:
        self._client = client or httpx.Client(
            base_url=base_url or importer_url(), timeout=120.0
        )

    def close(self) -> None:
        self._client.close()

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        try:
            response = self._client.get(path, params=params)
        except httpx.HTTPError as error:
            raise ImporterError(f"Importer недоступен: {error}") from error
        if response.status_code == 404:
            raise ImporterError(_detail(response, "Тикер не найден в importer"))
        if response.status_code >= 400:
            raise ImporterError(
                f"Importer ответил {response.status_code}: "
                + _detail(response, "ошибка источника")
            )
        return response.json()

    def tickers(self) -> list[dict[str, Any]]:
        return self._get("/tickers")

    def ticker(self, ticker: str) -> dict[str, Any]:
        return self._get(f"/tickers/{ticker}")

    def history(
        self,
        ticker: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        limit: int = PAGE_LIMIT,
    ) -> tuple[list[ImporterCandle], datetime | None]:
        """Одна страница свечей и ``next_from`` (``None`` — период закончился)."""
        body = self._get(
            "/history",
            {
                "ticker": ticker,
                "tf": timeframe,
                "from": _iso(start),
                "to": _iso(end),
                "limit": limit,
            },
        )
        candles = [
            ImporterCandle(
                _moment(c["t"]),
                Decimal(c["o"]),
                Decimal(c["h"]),
                Decimal(c["l"]),
                Decimal(c["c"]),
                Decimal(c["v"]),
            )
            for c in body["candles"]
        ]
        next_from = body.get("next_from")
        return candles, None if next_from is None else _moment(next_from)

    def pages(
        self, ticker: str, timeframe: str, start: datetime, end: datetime
    ) -> Iterator[list[ImporterCandle]]:
        """Все страницы периода по порядку; без продвижения вперёд — ошибка."""
        cursor = start
        for _ in range(MAX_PAGES):
            candles, next_from = self.history(ticker, timeframe, cursor, end)
            if candles:
                yield candles
            if next_from is None:
                return
            if next_from <= cursor:
                raise ImporterError(
                    "Importer не продвигается вперёд: next_from не растёт"
                )
            cursor = next_from
        raise ImporterError("Слишком много страниц от importer")


def _detail(response: httpx.Response, default: str) -> str:
    try:
        detail = response.json().get("detail")
    except ValueError:
        return default
    return str(detail) if detail else default
