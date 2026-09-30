"""Собирает регрессионный датасет 1m из ISS (один раз; результат хранится в git).

Запуск (нужен интернет): ``cd libs/providers && uv run python
../../scripts/build_regression_fixture.py``.

Окно 2026-03-09 … 2026-03-31, два контракта NG (NGH6 и NGJ6): внутри него
ролл H → J (неделя с 16.03 при N = 5) и смена режима сессий 23.03.2026
(ADR-0014). Данные сохраняются как есть; пересборка меняет фикстуру и
ожидаемые результаты, поэтому делается только сознательно.
"""

import csv
import gzip
import io
import sys
from datetime import date
from pathlib import Path

from trader_engine.ingest import RawRow, RowError
from trader_providers import IssClient

CONTRACTS = ("NGH6", "NGJ6")
START, END = date(2026, 3, 9), date(2026, 3, 31)
TARGET = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "ng_2026_03.csv.gz"
COLUMNS = ("secid", "timestamp", "open", "high", "low", "close", "volume", "trade_count")


def main() -> int:
    client = IssClient()
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(COLUMNS)
    try:
        for secid in CONTRACTS:
            rows = 0
            for item in client.get_historical_candles(secid, START, END):
                if isinstance(item, RowError):
                    print(f"{secid}: пропущена строка {item.row_number}: {item.message}")
                    continue
                assert isinstance(item, RawRow) and item.timestamp is not None
                writer.writerow(
                    [
                        secid,
                        item.timestamp.isoformat().replace("+00:00", "Z"),
                        item.open,
                        item.high,
                        item.low,
                        item.close,
                        item.volume,
                        item.trade_count if item.trade_count is not None else "",
                    ]
                )
                rows += 1
            print(f"{secid}: {rows} свечей")
    finally:
        client.close()
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    with gzip.GzipFile(TARGET, "wb", mtime=0) as handle:
        handle.write(buffer.getvalue().encode("utf-8"))
    print(f"{TARGET} ({TARGET.stat().st_size} байт)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
