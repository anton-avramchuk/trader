import pytest

from trader_worker.main import main


def test_main_reports_engine_version(capsys: pytest.CaptureFixture[str]) -> None:
    main()

    assert "engine" in capsys.readouterr().out
