"""Хранение бэктестов: эксперименты, сделки, окна, журнал, блокировка test (#156)."""

from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from trader_db import (
    LockAttempt,
    add_trades,
    add_window,
    count_runs,
    create_experiment,
    fail_experiment,
    finish_experiment,
    list_log,
    list_trades,
    list_windows,
    mark_running,
    open_test_period,
    unlock_test_period,
)
from trader_db.models import BacktestExperiment, BacktestTrade, Root

NOON = datetime(2026, 9, 28, 12, tzinfo=UTC)


@pytest.fixture
def root_id(session: Session, contract_id: int) -> int:
    del contract_id
    return session.scalars(select(Root.id)).one()


def experiment(session: Session, root_id: int, **kw: Any) -> BacktestExperiment:
    params: dict[str, Any] = {
        "kind": "single",
        "root_id": root_id,
        "timeframe_code": "15m",
        "family": "pattern",
        "strategy": {"source": "pattern"},
        "costs": {"slippage_ticks": 1},
        "period_from": date(2026, 1, 1),
        "period_to": date(2026, 6, 30),
        "params_hash": "abc123",
    }
    return create_experiment(session, **(params | kw))


SPAN = (date(2026, 7, 1), date(2026, 9, 1))


def lock(
    session: Session,
    experiment_id: int,
    root_id: int,
    *,
    tf: str = "15m",
    family: str = "pattern",
) -> LockAttempt:
    return open_test_period(
        session,
        experiment_id=experiment_id,
        root_id=root_id,
        timeframe_code=tf,
        family=family,
        test_from=SPAN[0],
        test_to=SPAN[1],
    )


def unlock(session: Session, root_id: int, note: str) -> bool:
    return unlock_test_period(
        session,
        root_id=root_id,
        timeframe_code="15m",
        family="pattern",
        note=note,
    )


def runs(session: Session, root_id: int, family: str = "pattern") -> int:
    return count_runs(session, root_id=root_id, timeframe_code="15m", family=family)


def trade_row(**kw: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "side": "long",
        "ref": "double_triple:1",
        "signal_price": 101.25,
        "contracts": 1,
        "entry_time": NOON,
        "exit_time": NOON,
        "reason": "target",
        "legs": [{"contract_id": 1, "gross_ticks": 5.0}],
        "gross_ticks": 5.0,
        "cost_ticks": 2.0,
        "mfe_ticks": 6.0,
        "mae_ticks": -1.0,
        "gross_points": 0.05,
        "net_points": 0.03,
        "commission_rub": 7.0,
        "gross_rub": 100.0,
        "net_rub": 60.0,
    }
    return row | kw


def test_experiment_lifecycle_and_log(session: Session, root_id: int) -> None:
    e = experiment(session, root_id, versions={"engines": {"double_triple": "1"}})
    assert e.status == "queued" and e.result is None and e.contracts == 1

    mark_running(session, e.id)
    finish_experiment(session, e.id, {"metrics": {"trades": 3}})
    session.commit()

    stored = session.get_one(BacktestExperiment, e.id)
    assert stored.status == "succeeded" and stored.finished_at is not None
    assert stored.result == {"metrics": {"trades": 3}}
    assert stored.versions == {"engines": {"double_triple": "1"}}
    [entry] = list_log(session, root_id=root_id)
    assert entry.event == "run" and entry.experiment_id == e.id
    assert entry.params_hash == "abc123" and not entry.touches_test


def test_failed_experiment_is_logged(session: Session, root_id: int) -> None:
    e = experiment(session, root_id)

    fail_experiment(session, e.id, "ошибка " * 100)

    assert session.get_one(BacktestExperiment, e.id).status == "failed"
    events = [entry.event for entry in list_log(session, root_id=root_id)]
    assert events == ["failed", "run"]  # новые записи первыми
    assert (
        count_runs(session, root_id=root_id, timeframe_code="15m", family="pattern")
        == 1
    )


def test_trades_roundtrip_in_order_and_segments(session: Session, root_id: int) -> None:
    e = experiment(session, root_id)
    window = add_window(
        session,
        e.id,
        0,
        train=(date(2026, 1, 1), date(2026, 3, 31)),
        valid=(date(2026, 4, 1), date(2026, 4, 30)),
        params={"stop_atr": 1.5},
        train_metrics={"net": 10.0},
    )
    add_trades(session, e.id, [trade_row(), trade_row(reason="stop", net_rub=None)])
    add_trades(
        session,
        e.id,
        [trade_row(ambiguous_bar=True)],
        segment="validation",
        window_id=window.id,
    )
    session.commit()

    single = list_trades(session, e.id, segment="single")
    assert [t.sequence for t in single] == [0, 1] and single[1].net_rub is None
    assert single[0].legs == [{"contract_id": 1, "gross_ticks": 5.0}]
    [validation] = list_trades(session, e.id, window_id=window.id)
    assert validation.segment == "validation" and validation.ambiguous_bar
    assert len(list_trades(session, e.id)) == 3
    [stored] = list_windows(session, e.id)
    assert stored.params == {"stop_atr": 1.5} and stored.valid_metrics is None


def test_trades_of_several_windows_are_numbered_continuously(
    session: Session, root_id: int
) -> None:
    e = experiment(session, root_id)
    ids = [
        add_window(
            session,
            e.id,
            i,
            train=(date(2026, 1, 1), date(2026, 2, 1)),
            valid=(date(2026, 2, 2), date(2026, 3, 1)),
            params={},
        ).id
        for i in range(2)
    ]
    add_trades(
        session,
        e.id,
        [trade_row(), trade_row()],
        segment="validation",
        window_id=ids[0],
    )
    add_trades(session, e.id, [trade_row()], segment="validation", window_id=ids[1])
    add_trades(session, e.id, [trade_row()], segment="test")
    session.commit()

    stored = list_trades(session, e.id, segment="validation")
    assert [(t.window_id, t.sequence) for t in stored] == [
        (ids[0], 0),
        (ids[0], 1),
        (ids[1], 2),
    ]
    assert [t.sequence for t in list_trades(session, e.id, segment="test")] == [0]


def test_long_error_is_truncated(session: Session, root_id: int) -> None:
    e = experiment(session, root_id)

    fail_experiment(session, e.id, "x" * 10_000)

    assert len(session.get_one(BacktestExperiment, e.id).error or "") == 2000


def test_constraints_reject_bad_rows(session: Session, root_id: int) -> None:
    e = experiment(session, root_id)
    add_trades(session, e.id, [trade_row()])
    session.commit()

    with pytest.raises(IntegrityError):  # тот же (эксперимент, сегмент, номер)
        session.add(
            BacktestTrade(
                experiment_id=e.id, segment="single", sequence=0, **trade_row()
            )
        )
        session.flush()
    session.rollback()
    with pytest.raises(IntegrityError):
        add_trades(session, e.id, [trade_row(side="flat")])
    session.rollback()
    with pytest.raises(IntegrityError):  # период наоборот
        experiment(
            session, root_id, period_from=date(2026, 7, 1), period_to=date(2026, 1, 1)
        )
    session.rollback()
    with pytest.raises(IntegrityError):
        experiment(session, root_id, kind="paper")
    session.rollback()


def test_deleting_experiment_cascades_to_trades_and_windows(
    session: Session, root_id: int
) -> None:
    e = experiment(session, root_id)
    add_window(
        session,
        e.id,
        0,
        train=(date(2026, 1, 1), date(2026, 2, 1)),
        valid=(date(2026, 2, 2), date(2026, 3, 1)),
        params={},
    )
    add_trades(session, e.id, [trade_row()])
    session.commit()

    session.delete(session.get_one(BacktestExperiment, e.id))
    session.commit()

    assert session.scalar(text("select count(*) from backtest_trades")) == 0
    assert session.scalar(text("select count(*) from backtest_windows")) == 0
    # журнал переживает удаление эксперимента, ссылка обнуляется
    [entry] = list_log(session, root_id=root_id)
    assert entry.experiment_id is None


def test_test_period_opens_once_and_rejects_repeats(
    session: Session, root_id: int
) -> None:
    first = experiment(session, root_id, test_from=SPAN[0], test_to=SPAN[1])
    second = experiment(session, root_id, test_from=SPAN[0], test_to=SPAN[1])

    opened = lock(session, first.id, root_id)
    repeat = lock(session, second.id, root_id)

    assert opened.opened and not repeat.opened
    assert repeat.lock.experiment_id == first.id  # владелец — первый эксперимент
    rejected = [
        e for e in list_log(session, root_id=root_id) if e.event == "test_rejected"
    ]
    assert len(rejected) == 1 and rejected[0].experiment_id == second.id
    assert rejected[0].touches_test and "уже открыт" in (rejected[0].note or "")
    assert lock(session, first.id, root_id, tf="1h").opened  # другая связка независима


def test_unlock_is_explicit_logged_and_allows_reopening(
    session: Session, root_id: int
) -> None:
    e = experiment(session, root_id)
    lock(session, e.id, root_id)

    assert unlock(session, root_id, "ошибка в данных") is True
    assert unlock(session, root_id, "ещё раз") is False  # уже снята
    assert lock(session, e.id, root_id).opened

    entries = list_log(session, root_id=root_id)
    unlocked = next(entry for entry in entries if entry.event == "unlock")
    assert unlocked.note == "ошибка в данных" and unlocked.touches_test
    assert [entry.event for entry in entries].count("test_opened") == 2


def test_count_runs_counts_every_launch(session: Session, root_id: int) -> None:
    experiment(session, root_id)
    experiment(session, root_id, kind="walk_forward")
    experiment(session, root_id, family="level_touch")

    assert runs(session, root_id) == 2
    assert runs(session, root_id, "level_touch") == 1


def test_trade_model_defaults(session: Session, root_id: int) -> None:
    e = experiment(session, root_id)
    add_trades(session, e.id, [trade_row()])
    session.commit()

    stored = session.scalars(select(BacktestTrade)).one()
    assert not stored.ambiguous_bar and not stored.rolled
    assert not stored.step_price_estimated and stored.segment == "single"
    assert stored.signal_price == 101.25
