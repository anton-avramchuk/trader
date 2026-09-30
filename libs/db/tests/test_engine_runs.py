"""Прогоны движков и неизменяемый лог событий (issue #33)."""

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session
from trader_engine.events import (
    EventEngine,
    current_events,
    register,
    unregister,
)
from trader_engine.indicators import BarInput

from trader_db import advance_run, find_latest_run, load_events
from trader_db.models import Contract, EngineEvent, EngineRun

START = datetime(2026, 9, 28, 4, tzinfo=UTC)
CLOSES = [10.0, 11, 12, 11, 10, 13, 12, 11, 15, 14, 13, 12, 16, 15, 14, 13, 17, 16]


class HighsParams(BaseModel):
    min_step: float = 0.0


class Highs(EventEngine):
    """Новый максимум — ``detected``, следующий пересматривает его (``revised``)."""

    name = "db_highs"
    title = "Максимумы (тест)"
    Params = HighsParams

    def __init__(self, params: BaseModel | None = None) -> None:
        super().__init__(params)
        self.best: float | None = None
        self.last: int | None = None

    def on_bar(self, bar: BarInput) -> None:
        step = self.typed_params(HighsParams).min_step
        if self.best is None or bar.close > self.best + step:
            status = "detected" if self.last is None else "revised"
            event = self.emit("high", status, {"price": bar.close}, revises=self.last)
            self.best, self.last = bar.close, event.seq

    def get_state(self) -> dict[str, Any]:
        return {"best": self.best, "last": self.last}

    def set_state(self, state: dict[str, Any]) -> None:
        self.best, self.last = state["best"], state["last"]


@pytest.fixture(autouse=True)
def highs() -> Iterator[None]:
    register(Highs)
    yield
    unregister(Highs.name)


def make_bars(closes: list[float]) -> list[BarInput]:
    bars: list[BarInput] = []
    for index, close in enumerate(closes):
        opened = START + timedelta(minutes=15 * index)
        bars.append(
            BarInput(
                timestamp=opened,
                close_time=opened + timedelta(minutes=15),
                open=close,
                high=close,
                low=close,
                close=close,
                volume=1.0,
                trading_day=opened.date(),
            )
        )
    return bars


def test_new_run_stores_events_and_state(session: Session, contract_id: int) -> None:
    bars = make_bars(CLOSES)

    result = advance_run(
        session, "db_highs", None, bars, "15m", contract_id=contract_id
    )

    assert result.outcome == "created" and result.bars_new == len(bars)
    run = session.get(EngineRun, result.run_id)
    assert run is not None
    assert (run.engine, run.algorithm_version, run.timeframe_code) == (
        "db_highs",
        1,
        "15m",
    )
    assert run.params == {"min_step": 0.0} and len(run.params_hash) == 16
    assert run.bars_processed == len(bars)
    assert run.last_close_time == bars[-1].close_time
    events = load_events(session, run.id)
    assert len(events) == result.events_written > 0
    assert [e.seq for e in events] == list(range(len(events)))
    assert events[0].status == "detected" and events[1].revises == 0
    assert [e.payload["price"] for e in events] == [10.0, 11, 12, 13, 15, 16, 17]


def test_continuation_equals_a_full_run(session: Session, contract_id: int) -> None:
    bars = make_bars(CLOSES)
    first = advance_run(
        session, "db_highs", None, bars[:9], "15m", contract_id=contract_id
    )
    second = advance_run(
        session, "db_highs", None, bars, "15m", contract_id=contract_id
    )
    fresh = advance_run(
        session, "db_highs", None, bars, "15m", contract_id=contract_id, mode="full"
    )

    assert second.run_id == first.run_id and second.outcome == "continued"
    assert second.bars_new == len(bars) - 9
    assert fresh.run_id != first.run_id and fresh.reason == "запрошен полный прогон"
    assert load_events(session, second.run_id) == load_events(session, fresh.run_id)
    run = session.get(EngineRun, second.run_id)
    other = session.get(EngineRun, fresh.run_id)
    assert run is not None and other is not None
    assert run.input_fingerprint == other.input_fingerprint
    assert run.state == other.state


def test_same_bars_change_nothing(session: Session, contract_id: int) -> None:
    bars = make_bars(CLOSES)
    first = advance_run(session, "db_highs", None, bars, "15m", contract_id=contract_id)

    again = advance_run(session, "db_highs", None, bars, "15m", contract_id=contract_id)

    assert (again.run_id, again.outcome, again.bars_new) == (
        first.run_id,
        "unchanged",
        0,
    )
    assert session.scalar(select(func.count()).select_from(EngineRun)) == 1


@pytest.mark.parametrize(
    ("edit", "reason"),
    [
        ("edited", "ранее обработанные бары изменились"),
        ("shorter", "ряд стал короче обработанной части"),
        ("regrid", "сетка баров изменилась"),
    ],
)
def test_changed_history_starts_a_new_run_and_keeps_the_old_one(
    session: Session, contract_id: int, edit: str, reason: str
) -> None:
    bars = make_bars(CLOSES)
    old = advance_run(session, "db_highs", None, bars, "15m", contract_id=contract_id)
    before = load_events(session, old.run_id)

    if edit == "edited":
        changed = make_bars([*CLOSES[:3], 50.0, *CLOSES[4:], 1.0])
    elif edit == "shorter":
        changed = bars[:5]
    else:
        changed = [
            replace(bar, close_time=bar.close_time + timedelta(1)) for bar in bars
        ]
    new = advance_run(
        session, "db_highs", None, changed, "15m", contract_id=contract_id
    )

    assert new.outcome == "created" and new.run_id != old.run_id
    assert new.reason == reason
    assert load_events(session, old.run_id) == before
    latest = find_latest_run(
        session,
        "db_highs",
        session.get(EngineRun, new.run_id).params_hash,  # type: ignore[union-attr]
        1,
        "15m",
        contract_id=contract_id,
    )
    assert latest is not None and latest.id == new.run_id


def test_different_params_and_subjects_have_separate_runs(
    session: Session, contract_id: int
) -> None:
    bars = make_bars(CLOSES)
    root_id = session.get(Contract, contract_id).root_id  # type: ignore[union-attr]

    a = advance_run(session, "db_highs", None, bars, "15m", contract_id=contract_id)
    b = advance_run(
        session, "db_highs", {"min_step": 1.5}, bars, "15m", contract_id=contract_id
    )
    c = advance_run(session, "db_highs", None, bars, "15m", root_id=root_id)
    d = advance_run(session, "db_highs", None, bars, "1h", contract_id=contract_id)

    assert len({a.run_id, b.run_id, c.run_id, d.run_id}) == 4
    assert all(r.outcome == "created" for r in (a, b, c, d))
    assert len(load_events(session, b.run_id)) < len(load_events(session, a.run_id))


def test_events_as_of_show_only_what_was_known(
    session: Session, contract_id: int
) -> None:
    bars = make_bars(CLOSES)
    result = advance_run(
        session, "db_highs", None, bars, "15m", contract_id=contract_id
    )

    moment = bars[5].close_time  # бар с максимумом 13 закрылся ровно сейчас
    known = load_events(session, result.run_id, as_of=moment)
    everything = load_events(session, result.run_id)

    assert known == [e for e in everything if e.available_at <= moment]
    assert known[-1].payload["price"] == 13 and known[-1].available_at == moment
    assert [e.payload["price"] for e in current_events(known)] == [13]
    assert load_events(session, result.run_id, as_of=START) == []
    assert load_events(session, result.run_id, kinds=["nope"]) == []


def test_events_are_immutable(session: Session, contract_id: int) -> None:
    result = advance_run(
        session, "db_highs", None, make_bars(CLOSES), "15m", contract_id=contract_id
    )
    session.commit()

    with pytest.raises(DBAPIError, match="запрещены"):
        session.execute(
            text("UPDATE engine_events SET kind = 'x' WHERE run_id = :r"),
            {"r": result.run_id},
        )
    session.rollback()
    with pytest.raises(DBAPIError, match="запрещены"):
        session.execute(
            text("DELETE FROM engine_events WHERE run_id = :r"), {"r": result.run_id}
        )
    session.rollback()
    assert session.scalar(select(func.count()).select_from(EngineEvent)) > 0  # type: ignore[operator]


@pytest.mark.parametrize(
    "row",
    [
        {"status": "revised", "revises_seq": None},
        {"status": "detected", "revises_seq": 0},
        {"status": "revised", "revises_seq": 99},
        {"status": "unknown", "revises_seq": None},
    ],
)
def test_event_rules_are_enforced_by_the_database(
    session: Session, contract_id: int, row: dict[str, Any]
) -> None:
    result = advance_run(
        session, "db_highs", None, make_bars(CLOSES), "15m", contract_id=contract_id
    )
    at = START + timedelta(days=1)

    with pytest.raises(IntegrityError):
        session.add(
            EngineEvent(
                run_id=result.run_id,
                seq=1000,
                kind="high",
                payload={},
                detected_at=at,
                available_at=at,
                **row,
            )
        )
        session.flush()


def test_bad_arguments_are_rejected(session: Session, contract_id: int) -> None:
    bars = make_bars(CLOSES)

    with pytest.raises(ValueError, match="ровно один"):
        advance_run(session, "db_highs", None, bars, "15m")
    with pytest.raises(ValueError, match="ровно один"):
        advance_run(
            session, "db_highs", None, bars, "15m", contract_id=contract_id, root_id=1
        )
    with pytest.raises(ValueError, match="Нет баров"):
        advance_run(session, "db_highs", None, [], "15m", contract_id=contract_id)
    with pytest.raises(KeyError, match="Неизвестный движок"):
        advance_run(session, "nope", None, bars, "15m", contract_id=contract_id)
    with pytest.raises(ValueError, match="Некорректные параметры"):
        advance_run(
            session, "db_highs", {"min_step": "x"}, bars, "15m", contract_id=contract_id
        )
