"""Pytest configuration: network and credentials tests are opt-in."""

from pathlib import Path

import pytest

REAL_SETTINGS_PATH = Path("settings/settings.json")


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add the --network option."""
    parser.addoption(
        "--network",
        action="store_true",
        default=False,
        help="run the tests that need internet access",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Skip network and credentials tests, unless explicitly enabled."""
    network = config.getoption("--network")
    skip_network = pytest.mark.skip(reason="needs --network")
    skip_credentials = pytest.mark.skip(
        reason=f"needs --network and real credentials in {REAL_SETTINGS_PATH}"
    )

    for item in items:
        if "network" in item.keywords and not network:
            item.add_marker(skip_network)
        if "credentials" in item.keywords and not (
            network and REAL_SETTINGS_PATH.exists()
        ):
            item.add_marker(skip_credentials)
