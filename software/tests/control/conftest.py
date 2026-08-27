"""
Pytest fixtures for control module tests.

Microcontroller/Microscope/MultiPointController cleanup is handled suite-wide
by the autouse fixture in tests/conftest.py.
"""

import os
import shutil

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


@pytest.fixture
def catalog_tree(tmp_path, monkeypatch):
    """Isolated cwd holding the shipped catalog: sidecar writes (machine_configs/,
    cache/, objective_and_sample_formats/sample_formats_user.yaml) land in tmp,
    never in the repo. images/ is symlinked read-only for widgets that draw."""
    repo = os.getcwd()
    (tmp_path / "objective_and_sample_formats").mkdir()
    (tmp_path / "machine_configs").mkdir()
    (tmp_path / "cache").mkdir()
    for name in ("sample_formats.csv", "objectives.csv"):
        shutil.copy(
            os.path.join(repo, "objective_and_sample_formats", name),
            tmp_path / "objective_and_sample_formats" / name,
        )
    os.symlink(os.path.join(repo, "images"), tmp_path / "images")
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def design_travel_limits(monkeypatch):
    """The stage limits (and zero legacy offset) the design doc's reference
    rings were derived against - pins reference-well computation regardless of
    the machine config the tests happen to run under."""
    import control._def as _def

    monkeypatch.setattr(_def.SOFTWARE_POS_LIMIT, "X_NEGATIVE", 5.0)
    monkeypatch.setattr(_def.SOFTWARE_POS_LIMIT, "X_POSITIVE", 115.0)
    monkeypatch.setattr(_def.SOFTWARE_POS_LIMIT, "Y_NEGATIVE", 4.0)
    monkeypatch.setattr(_def.SOFTWARE_POS_LIMIT, "Y_POSITIVE", 76.0)
    monkeypatch.setattr(_def, "WELLPLATE_OFFSET_X_mm", 0.0)
    monkeypatch.setattr(_def, "WELLPLATE_OFFSET_Y_mm", 0.0)
