"""Имитация ISS для тестов: воспроизводит формат и поведение, найденные на живом API."""

import re
from collections import deque
from collections.abc import Callable
from typing import Any

import httpx

PAGE = 500

SERIES_COLUMNS = [
    "secid",
    "name",
    "start_date",
    "expiration_date",
    "asset_code",
    "underlying_asset",
    "is_traded",
]

CONTRACTS: dict[str, dict[str, Any]] = {
    # secid: описание. ``BRZ0_2010`` — старый контракт, переименованный ISS.
    "BRZ0": dict(asset="BR", name="BR-12.20", last="2020-12-01", exp="2020-12-01"),
    "BRF1": dict(asset="BR", name="BR-1.21", last="2021-01-04", exp="2021-01-04"),
    "BRZ0_2010": dict(asset="BR", name="BR-12.10", last="2010-12-01", exp="2010-12-01"),
    "BRH1": dict(asset="OTHER", name="XX-3.21", last="2021-03-01", exp="2021-03-01"),
    "NGZ0": dict(asset="NG", name="NG-12.20", last="2020-12-29", exp="2020-12-29"),
    "GDZ6": dict(asset="GOLD", name="GOLD-12.26", last="2026-12-18", exp="2026-12-18"),
}

SERIES_ROWS = [
    ["BRZ6", "BR-12.26", "2026-04-23", "2026-12-01", "BR", None, 1],
    ["BRX6", "BR-11.26", "2026-03-24", "2026-11-02", "BR", None, 1],
    ["GDZ6", "GOLD-12.26", "2025-12-11", "2026-12-18", "GOLD", None, 1],
    ["GDH7", "GOLD-3.27", "2026-03-12", "2027-03-19", "GOLD", None, 1],
    ["GLDRUBF", "GLDRUBF", "2023-07-11", "2100-01-01", "GLDRUBTOM", "GLDRUB_TOM", 1],
]


def block(columns: list[str], rows: list[list[Any]]) -> dict[str, Any]:
    return {"columns": columns, "data": rows}


class FakeIss:
    """Обработчик ``httpx.MockTransport`` со счётчиком запросов и сбоями."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        # secid -> свечи ``[open, close, high, low, value, volume, begin, end]``
        self.candles: dict[str, list[list[Any]]] = {}
        self.borders: dict[str, list[list[Any]]] = {}
        # secid -> дни ``[TRADEDATE, VOLUME, NUMTRADES(, VALUE, WAPRICE)]``
        self.daily: dict[str, list[list[Any]]] = {}
        self.series_rows = list(SERIES_ROWS)
        # Очередь ответов-сбоев, отдаваемых до нормальной работы: коды или исключения.
        self.failures: deque[int | Exception] = deque()
        self.raw_override: Callable[[httpx.Request], httpx.Response | None] | None = (
            None
        )
        self.retry_after: str | None = None

    def client(self) -> httpx.Client:
        return httpx.Client(
            transport=httpx.MockTransport(self.handle), base_url="https://iss.moex.com"
        )

    def paths(self) -> list[str]:
        return [request.url.path for request in self.requests]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.raw_override is not None:
            response = self.raw_override(request)
            if response is not None:
                return response
        if self.failures:
            failure = self.failures.popleft()
            if isinstance(failure, Exception):
                raise failure
            headers = {"Retry-After": self.retry_after} if self.retry_after else {}
            return httpx.Response(failure, headers=headers)
        path = request.url.path
        params = request.url.params
        if path.endswith("/series.json"):
            return httpx.Response(
                200, json={"series": block(SERIES_COLUMNS, self.series_rows)}
            )
        if match := re.fullmatch(
            r"/iss/history/engines/.*/securities/(.+)\.json", path
        ):
            return httpx.Response(200, json=self._history(match.group(1), params))
        if match := re.fullmatch(r"/iss/securities/(.+)\.json", path):
            return httpx.Response(200, json=self._description(match.group(1)))
        if match := re.fullmatch(r".*/securities/(.+)/candleborders\.json", path):
            rows = self.borders.get(match.group(1), [])
            return httpx.Response(
                200,
                json={
                    "borders": block(
                        ["begin", "end", "interval", "board_group_id"], rows
                    )
                },
            )
        if match := re.fullmatch(r".*/securities/(.+)/candles\.json", path):
            return httpx.Response(200, json=self._candles(match.group(1), params))
        return httpx.Response(404)

    @staticmethod
    def _description(secid: str) -> dict[str, Any]:
        info = CONTRACTS.get(secid)
        if info is None:
            return {
                "description": block(["name", "title", "value"], []),
                "boards": block(["secid", "boardid"], []),
            }
        rows = [
            ["SECID", "", secid],
            ["SHORTNAME", "", info["name"]],
            ["ASSETCODE", "", info["asset"]],
            ["LSTTRADE", "", info["last"]],
            ["LSTDELDATE", "", info["exp"]],
        ]
        boards = [
            ["secid", "boardid", "history_from", "history_till"],
        ]
        return {
            "description": block(["name", "title", "value"], rows),
            "boards": block(
                boards[0],
                [
                    [secid, "RFUD", "2019-01-01", info["exp"]],
                    [secid, "FIQS", None, None],
                ],
            ),
        }

    def _history(self, secid: str, params: httpx.QueryParams) -> dict[str, Any]:
        first, last = params["from"], params["till"]
        start = int(params.get("start", "0"))
        rows = [
            [*r, None, None][:5]
            for r in self.daily.get(secid, [])
            if first <= r[0] <= last
        ]
        return {
            "history": block(
                ["TRADEDATE", "VOLUME", "NUMTRADES", "VALUE", "WAPRICE"],
                rows[start : start + 100],
            )
        }

    def _candles(self, secid: str, params: httpx.QueryParams) -> dict[str, Any]:
        first, last = params["from"], params["till"]
        start = int(params.get("start", "0"))
        rows = [
            row
            for row in self.candles.get(secid, [])
            if first <= str(row[6])[:10] <= last
        ]
        return {
            "candles": block(
                ["open", "close", "high", "low", "value", "volume", "begin", "end"],
                rows[start : start + PAGE],
            )
        }


def candle_row(begin: str, *, close: float = 47.15, volume: int = 100) -> list[Any]:
    """Свеча в порядке колонок ISS: open, close, high, low, value, volume, ..."""
    return [47.14, close, 47.19, 47.13, 0, volume, begin, begin[:-2] + "59"]
