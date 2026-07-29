"""
Pytest fixtures for control module tests.

Microcontroller/Microscope/MultiPointController cleanup is handled suite-wide
by the autouse fixture in tests/conftest.py.
"""

import pytest

from control.firmware_sim_serial import FirmwareSimSerial


@pytest.fixture
def firmware_sim():
    """
    Provide a FirmwareSimSerial instance with automatic cleanup.

    Validation errors and command counts are cleared before each test
    to ensure test isolation.
    """
    sim = FirmwareSimSerial(strict=True)
    yield sim
    sim.close()


@pytest.fixture
def firmware_sim_nonstrict():
    """
    Provide a non-strict FirmwareSimSerial instance for negative testing.

    In non-strict mode, invalid commands log warnings instead of raising
    FirmwareProtocolError, useful for testing error handling paths.
    """
    sim = FirmwareSimSerial(strict=False)
    yield sim
    sim.close()


@pytest.fixture(autouse=True)
def _watchdog_state_to_tmp(tmp_path, monkeypatch):
    # Keep acquisition breadcrumbs out of the real user state dir during tests.
    monkeypatch.setenv("SQUID_WATCHDOG_STATE_DIR", str(tmp_path / "watchdog"))
