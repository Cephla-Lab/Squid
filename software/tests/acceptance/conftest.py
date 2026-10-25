"""
Fixtures for the simulation-mode acquisition acceptance suite.

These tests run full acquisitions end-to-end against simulated hardware and
assert on observable artifacts (files on disk, process state), not internals.
They are headless-safe: no QApplication is created (MultiPointController uses
plain threads and plain-callable callbacks), so they run under Xvfb in CI.
"""

import logging
from unittest.mock import patch

import pytest

import control._def
import control.core.multi_point_worker
import control.microcontroller

logger = logging.getLogger(__name__)


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "acceptance: end-to-end simulation-mode acquisition acceptance tests",
    )


def _make_tracking_init(original_init, instances_list):
    """Create a wrapper that tracks Microcontroller instances."""

    def _tracking_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        instances_list.append(self)

    return _tracking_init


@pytest.fixture(autouse=True)
def cleanup_microcontrollers():
    """
    Automatically close all Microcontroller instances after each test.

    Same rationale as tests/control/conftest.py: the packet-reading background
    thread must be stopped or subsequent tests can segfault.
    """
    active_microcontrollers = []
    original_init = control.microcontroller.Microcontroller.__init__

    with patch.object(
        control.microcontroller.Microcontroller,
        "__init__",
        _make_tracking_init(original_init, active_microcontrollers),
    ):
        yield

    for micro in active_microcontrollers:
        try:
            if hasattr(micro, "terminate_reading_received_packet_thread"):
                if not micro.terminate_reading_received_packet_thread:
                    micro.close()
        except Exception as e:
            logger.warning(f"Failed to close Microcontroller in test cleanup: {e}")


@pytest.fixture(autouse=True)
def _watchdog_state_to_tmp(tmp_path, monkeypatch):
    # Keep acquisition breadcrumbs out of the real user state dir during tests.
    monkeypatch.setenv("SQUID_WATCHDOG_STATE_DIR", str(tmp_path / "watchdog"))


def set_file_saving_option(monkeypatch, option):
    """
    Point acquisition file saving at `option` (a control._def.FileSavingOption).

    multi_point_worker star-imports FILE_SAVING_OPTION, freezing its own
    binding at import time, while job_processing reads _def.FILE_SAVING_OPTION
    at runtime — so both locations must be patched together.
    """
    monkeypatch.setattr(control._def, "FILE_SAVING_OPTION", option)
    monkeypatch.setattr(control.core.multi_point_worker, "FILE_SAVING_OPTION", option)


@pytest.fixture
def acquisition_defaults(monkeypatch):
    """
    Pin the control._def knobs the acceptance scenarios depend on to known
    values, restoring them afterwards. Individual tests override per-scenario
    (e.g. FILE_SAVING_OPTION, backpressure limits) with the same monkeypatch.
    """
    monkeypatch.setattr(control._def, "MERGE_CHANNELS", False)
    set_file_saving_option(monkeypatch, control._def.FileSavingOption.INDIVIDUAL_IMAGES)
    return monkeypatch
