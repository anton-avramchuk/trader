"""Калибровка расписания FORTS по данным MOEX ISS (задача #59).

Запуск (из libs/providers, где есть зависимости)::

    uv run python ../../scripts/calibrate_forts_calendar.py collect
    uv run python ../../scripts/calibrate_forts_calendar.py report

``collect`` ходит в ISS и складывает данные в кэш (по умолчанию ``data/calibration``,
каталог в .gitignore); ``report`` разбирает кэш без сети. Скрипт нужен для
воспроизводимости: правила календаря в миграции получены его результатами.
"""

import argparse
import json
import sys
import time
from collections import defaultdict
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from trader_engine.ingest import RawRow
from trader_providers import IssClient

ASSET = "BR"
MSK_OFFSET = timedelta(hours=3)


def hhmm(value: datetime) -> str:
    """Время свечи по Москве, ``ЧЧ:ММ`` (в данных — UTC, aware)."""
    local = value + MSK_OFFSET
    return f"{local:%H:%M}"


def day_markers(client: IssClient, secid: str, day: date) -> dict[str, object] | None:
    """Признаки расписания по минутным свечам контракта за день."""
    rows = [
        r
        for r in client.get_historical_candles(secid, day, day)
        if isinstance(r, RawRow) and r.timestamp is not None
    ]
    if not rows:
        return None
    times = sorted(hhmm(r.timestamp) for r in rows if r.timestamp is not None)
    before_10 = [t for t in times if t < "10:00"]
    evening = [t for t in times if t >= "18:50"]
    main_end = [t for t in times if "18:00" <= t < "18:55"]
    return {
        "n": len(times),
        "first": times[0],
        "last": times[-1],
        "morning_n": len(before_10),
        "morning_first": before_10[0] if before_10 else None,
        "evening_first": evening[0] if evening else None,
        "main_last": main_end[-1] if main_end else None,
        "clearing_hits": sum(1 for t in times if "14:00" <= t < "14:05"),
    }


def collect(cache: Path, start: date) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    client = IssClient()
    today = date.today()
    clock = time.monotonic()

    def log(message: str) -> None:
        print(f"[{time.monotonic() - clock:6.0f}s] {message}", flush=True)

    contracts = client.list_contracts(ASSET, from_year=start.year, to_year=today.year + 1)
    log(f"контрактов {ASSET}: {len(contracts)}")

    # торговые дни и объёмы по контрактам
    volumes: dict[str, dict[str, int]] = defaultdict(dict)  # дата -> secid -> объём
    for contract in contracts:
        first = contract.history_from or (contract.expiration_date - timedelta(days=500))
        last = contract.history_till or contract.expiration_date
        for bar in client.daily_history(contract.provider_id, max(first, start), last):
            if bar.volume > 0:
                volumes[bar.trade_date.isoformat()][contract.provider_id] = bar.volume
        log(f"дневная история {contract.provider_id}")
    (cache / "volumes.json").write_text(json.dumps(volumes, sort_keys=True))

    # профиль внутридня раз в месяц: будний день около 15-го, самый ликвидный контракт
    samples: dict[str, dict[str, object]] = {}
    month = date(start.year, start.month, 1)
    while month <= today:
        candidates = [
            d
            for d in sorted(volumes)
            if d[:7] == f"{month:%Y-%m}" and date.fromisoformat(d).weekday() < 5
        ]
        if candidates:
            target = min(candidates, key=lambda d: abs(int(d[8:]) - 15))
            secid = max(volumes[target], key=lambda s: volumes[target][s])
            markers = day_markers(client, secid, date.fromisoformat(target))
            if markers:
                samples[target] = {"secid": secid, **markers}
                log(f"{target} {secid}: {markers['first']}–{markers['last']}")
        month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    (cache / "samples.json").write_text(json.dumps(samples, sort_keys=True, indent=1))
    log("готово")


# Проверки переходов: (название, дата «до», дата «после», признак «после»).
# Признак берётся по самому ликвидному контракту дня; бисекция ищет первый день,
# в который он становится истинным.
CHECKS: list[tuple[str, str, str, Callable[[dict[str, Any]], bool]]] = [
    (
        "утренняя сессия 07:00 введена",
        "2021-02-15",
        "2021-03-15",
        lambda m: m["morning_first"] is not None and m["morning_first"] <= "07:05",
    ),
    (
        "утренняя сессия остановлена (2022)",
        "2022-02-15",
        "2022-03-15",
        lambda m: m["morning_n"] < 50,
    ),
    (
        "вечерняя сессия остановлена (2022)",
        "2022-02-15",
        "2022-03-15",
        lambda m: m["last"] < "20:00",
    ),
    (
        "основная до 18:50 (вместо 18:45)",
        "2022-05-16",
        "2022-06-15",
        lambda m: m["main_last"] is not None and m["main_last"] >= "18:49",
    ),
    (
        "вечерняя сессия вернулась после остановки",
        "2022-06-15",
        "2022-07-15",
        lambda m: m["last"] >= "23:00",
    ),
    (
        "утренняя сессия с 09:00 введена",
        "2022-08-15",
        "2022-09-15",
        lambda m: m["morning_n"] >= 50,
    ),
    (
        "утренняя сессия отменена",
        "2024-05-15",
        "2024-06-14",
        lambda m: m["morning_n"] < 50,
    ),
    (
        "утренняя сессия возвращена",
        "2025-01-15",
        "2025-02-14",
        lambda m: m["morning_n"] >= 50,
    ),
    (
        "клиринг в 14:00 отменён",
        "2026-03-16",
        "2026-04-15",
        lambda m: m["clearing_hits"] >= 3,
    ),
    (
        "утренняя сессия с 07:00 (единая)",
        "2026-06-15",
        "2026-07-15",
        lambda m: m["morning_n"] >= 150,
    ),
]


def _trading_weekdays(volumes: dict[str, dict[str, int]]) -> list[date]:
    return sorted(
        d for d in (date.fromisoformat(k) for k in volumes) if d.weekday() < 5
    )


def refine(cache: Path) -> None:
    """Бисекция: точные даты переходов расписания."""
    volumes: dict[str, dict[str, int]] = json.loads((cache / "volumes.json").read_text())
    weekdays = _trading_weekdays(volumes)
    client = IssClient()
    markers_cache: dict[date, dict[str, Any] | None] = {}

    def markers(day: date) -> dict[str, Any] | None:
        if day not in markers_cache:
            per_contract = volumes[day.isoformat()]
            secid = max(per_contract, key=lambda s: per_contract[s])
            markers_cache[day] = day_markers(client, secid, day)
        return markers_cache[day]

    found: dict[str, object] = {}
    for name, before, after, predicate in CHECKS:
        low, high = date.fromisoformat(before), date.fromisoformat(after)
        days = [d for d in weekdays if low < d <= high]
        lo, hi = -1, len(days) - 1  # days[hi] — заведомо «после»
        while hi - lo > 1:
            mid = (lo + hi) // 2
            m = markers(days[mid])
            if m is not None and predicate(m):
                hi = mid
            else:
                lo = mid
        first_after = days[hi]
        last_before = days[lo] if lo >= 0 else low
        found[name] = {
            "last_before": last_before.isoformat(),
            "first_after": first_after.isoformat(),
            "markers_after": markers(first_after),
        }
        print(f"{name}: последний день «до» {last_before}, первый «после» {first_after}", flush=True)
    (cache / "transitions.json").write_text(json.dumps(found, indent=1, sort_keys=True))


def weekends(cache: Path) -> None:
    """Регулярные выходные сессии: есть ли минутные свечи в субботу/воскресенье."""
    volumes: dict[str, dict[str, int]] = json.loads((cache / "volumes.json").read_text())
    weekdays = _trading_weekdays(volumes)
    client = IssClient()
    results: dict[str, object] = {}
    day = date(2020, 1, 4)  # первая суббота
    today = date.today()
    while day <= today:
        # ликвидный контракт ближайшего будня до выходных
        prior = [d for d in weekdays if d < day]
        if prior:
            per_contract = volumes[prior[-1].isoformat()]
            secid = max(per_contract, key=lambda s: per_contract[s])
            for offset in (0, 1):
                target = day + timedelta(days=offset)
                markers = day_markers(client, secid, target)
                results[target.isoformat()] = (
                    {"secid": secid, **markers} if markers else None
                )
                if markers:
                    print(f"{target} {secid}: {markers['first']}–{markers['last']} n={markers['n']}", flush=True)
        day += timedelta(days=28)  # раз в четыре недели, суббота и воскресенье
    (cache / "weekends.json").write_text(json.dumps(results, indent=1, sort_keys=True))
    print("готово", flush=True)


WEEKEND_SESSIONS_FROM = date(2025, 8, 10)  # найдено плотным перебором суббот


def derive(cache: Path) -> None:
    """Праздники, особые торговые дни и остановка торгов — по данным ISS."""
    volumes: dict[str, dict[str, int]] = json.loads((cache / "volumes.json").read_text())
    traded = {date.fromisoformat(k) for k in volumes}
    first, last = min(traded), max(traded)

    weekday_holidays: list[date] = []
    day = first
    while day <= last:
        if day.weekday() < 5 and day not in traded:
            weekday_holidays.append(day)
        day += timedelta(days=1)

    # серии подряд идущих нерабочих будней: короткие — праздники, длинные — остановка торгов
    runs: list[list[date]] = []
    for holiday in weekday_holidays:
        if runs and (holiday - runs[-1][-1]).days <= 3 and all(
            (runs[-1][-1] + timedelta(days=i)).weekday() >= 5 or (runs[-1][-1] + timedelta(days=i)) in weekday_holidays
            for i in range(1, (holiday - runs[-1][-1]).days)
        ):
            runs[-1].append(holiday)
        else:
            runs.append([holiday])
    halts = [r for r in runs if len(r) >= 8]

    special = sorted(d for d in traded if d.weekday() >= 5)

    # выходные с регулярной сессией: минутные свечи, а в дневной истории дня нет
    volumes_by_day = {date.fromisoformat(k): v for k, v in volumes.items()}
    weekdays = sorted(d for d in volumes_by_day if d.weekday() < 5)
    client = IssClient()
    weekend_traded: list[str] = []
    weekend_idle: list[str] = []
    day = WEEKEND_SESSIONS_FROM
    while day <= date.today() - timedelta(days=1):
        if day.weekday() >= 5 and day not in traded:
            prior = [d for d in weekdays if d < day][-1]
            per_contract = volumes_by_day[prior]
            secid = max(per_contract, key=lambda s: per_contract[s])
            markers = day_markers(client, secid, day)
            (weekend_traded if markers else weekend_idle).append(day.isoformat())
            print(f"{day} {day:%a}: {'торги' if markers else '—'}", flush=True)
        day += timedelta(days=1)

    result = {
        "first_trading_day": first.isoformat(),
        "last_trading_day": last.isoformat(),
        "weekday_holidays": [d.isoformat() for d in weekday_holidays],
        "halts": [[r[0].isoformat(), r[-1].isoformat()] for r in halts],
        "special_trading_days": [d.isoformat() for d in special],
        "weekend_sessions_from_probe": WEEKEND_SESSIONS_FROM.isoformat(),
        "weekend_with_trading": weekend_traded,
        "weekend_without_trading": weekend_idle,
    }
    (cache / "derived.json").write_text(json.dumps(result, indent=1, sort_keys=True))
    print(json.dumps({k: (v if len(str(v)) < 300 else f"<{len(v)} шт.>") for k, v in result.items()}, ensure_ascii=False, indent=1))


def report(cache: Path) -> None:
    volumes: dict[str, dict[str, int]] = json.loads((cache / "volumes.json").read_text())
    samples: dict[str, dict[str, object]] = json.loads((cache / "samples.json").read_text())
    days = sorted(date.fromisoformat(d) for d in volumes)
    weekend = [d for d in days if d.weekday() >= 5]
    print(f"торговых дней: {len(days)} ({days[0]} … {days[-1]}), из них в выходные: {len(weekend)}")
    if weekend:
        by_year: dict[int, int] = defaultdict(int)
        for d in weekend:
            by_year[d.year] += 1
        print("выходные дни с торгами по годам:", dict(sorted(by_year.items())))
        print("первый выходной день с торгами:", weekend[0])
    print()
    print("день        ctr    n   первая  посл.  утро(n,первая)  вечер_нач  основная_кон  клиринг14:00")
    for d in sorted(samples):
        s = samples[d]
        print(
            f"{d} {str(s['secid']):<6}{s['n']:>5}  {s['first']}  {s['last']}   "
            f"{s['morning_n']:>3} {str(s['morning_first']):<6}   {str(s['evening_first']):<8}   "
            f"{str(s['main_last']):<10}    {s['clearing_hits']}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["collect", "refine", "weekends", "derive", "report"])
    parser.add_argument("--cache", type=Path, default=Path("../../data/calibration"))
    parser.add_argument("--start", type=date.fromisoformat, default=date(2020, 1, 1))
    args = parser.parse_args()
    if args.command == "collect":
        collect(args.cache, args.start)
    elif args.command == "refine":
        refine(args.cache)
    elif args.command == "weekends":
        weekends(args.cache)
    elif args.command == "derive":
        derive(args.cache)
    else:
        report(args.cache)
    return 0


if __name__ == "__main__":
    sys.exit(main())
