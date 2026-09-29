from sqlalchemy import UniqueConstraint
from sqlalchemy.orm import configure_mappers

from trader_db.models import Base

EXPECTED_TABLES = {
    "trading_calendars",
    "roots",
    "contracts",
    "data_providers",
    "contract_provider_ids",
    "timeframes",
    "contract_step_prices",
    "trading_calendar_rules",
    "trading_calendar_holidays",
    "trading_calendar_special_days",
    "data_imports",
    "data_import_errors",
    "raw_candles_1m",
    "import_conflicts",
    "dataset_versions",
    "dataset_version_imports",
    "jobs",
    "job_schedules",
    "import_presets",
    "derived_candles",
    "derived_builds",
}


def test_mappers_configure_and_tables_registered() -> None:
    configure_mappers()

    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_contract_key_is_root_and_expiration() -> None:
    contracts = Base.metadata.tables["contracts"]
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in contracts.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert ("root_id", "expiration_date") in unique_columns
