"""Регрессионные снапшоты движков MVP-3 на фиксированном датасете (Old/New/Diff).

Ожидаемые события лежат в ``snapshots/*.json``. При расхождении тест показывает
Old/New/Diff; осознанное изменение алгоритма — новая версия движка и обновление
снапшотов: ``UPDATE_SNAPSHOTS=1 pytest tests/events/test_regression.py``.
"""

import difflib
import json
import os
from pathlib import Path
from typing import Any, cast

import pytest

from tests.events.dataset import fixed_bars
from tests.events.test_leakage import CONFIGS, IDS
from trader_engine.events import Event, create, run_engine

SNAPSHOTS = Path(__file__).parent / "snapshots"


def rounded(value: object) -> Any:
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, dict):
        mapping = cast(dict[str, object], value)
        return {key: rounded(item) for key, item in mapping.items()}
    if isinstance(value, list):
        return [rounded(item) for item in cast(list[object], value)]
    return value


def render(events: list[Event]) -> str:
    rows = [
        {
            "seq": e.seq,
            "kind": e.kind,
            "status": e.status,
            "available_at": e.available_at.isoformat(),
            "revises": e.revises,
            "payload": rounded(e.payload),
        }
        for e in events
    ]
    return (
        "\n".join(json.dumps(row, sort_keys=True, ensure_ascii=False) for row in rows)
        + "\n"
    )


CASES = [(key, name, params) for key, (name, params) in zip(IDS, CONFIGS, strict=True)]


@pytest.mark.parametrize(("key", "name", "params"), CASES, ids=IDS)
def test_events_match_the_saved_snapshot(
    key: str, name: str, params: dict[str, Any]
) -> None:
    version = create(name, params).version
    path = SNAPSHOTS / f"{key}-v{version}.jsonl"
    new = render(run_engine(create(name, params), fixed_bars()))

    if os.environ.get("UPDATE_SNAPSHOTS"):
        SNAPSHOTS.mkdir(exist_ok=True)
        path.write_text(new, encoding="utf-8", newline="\n")
    assert path.exists(), f"нет снапшота {path.name}: UPDATE_SNAPSHOTS=1"
    old = path.read_text(encoding="utf-8")

    if old != new:
        diff = "".join(
            difflib.unified_diff(
                old.splitlines(keepends=True),
                new.splitlines(keepends=True),
                fromfile="Old",
                tofile="New",
                n=1,
            )
        )
        pytest.fail(
            f"{name}: результаты изменились без новой версии алгоритма\n{diff[:4000]}"
        )
