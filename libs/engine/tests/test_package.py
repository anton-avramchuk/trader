import re

import trader_engine


def test_version_is_semver() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", trader_engine.__version__)
