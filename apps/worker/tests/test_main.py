from threading import Event

import pytest

from trader_worker.main import run


def test_run_checks_database_and_stops_on_signal(
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[str] = []
    stop = Event()
    stop.set()

    run(stop, check_db=lambda: calls.append("db"))

    out = capsys.readouterr().out
    assert calls == ["db"]
    assert "database ok" in out
    assert "stopped" in out


def test_run_fails_when_database_unavailable() -> None:
    def broken() -> None:
        raise ConnectionError("db down")

    with pytest.raises(ConnectionError):
        run(Event(), check_db=broken)
