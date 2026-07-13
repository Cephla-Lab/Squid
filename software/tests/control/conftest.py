"""
Pytest fixtures for control module tests.

This module provides fixtures to ensure proper cleanup of Microcontroller instances,
preventing background threads from causing segfaults in subsequent tests.
"""

import logging
import os
import sys
from unittest.mock import patch

import pytest

import control.microcontroller
from control.firmware_sim_serial import FirmwareSimSerial

logger = logging.getLogger(__name__)


def pytest_sessionfinish(session, exitstatus):
    session.config._squid_exitstatus = int(exitstatus)


def pytest_unconfigure(config):
    """Optionally skip interpreter teardown after the test session.

    Under PyQt5 on Linux, a pytest process that constructed the full HCS GUI
    segfaults during interpreter shutdown (Qt C++ destructor order conflicts
    with Python GC) even though every test passed. main_hcs.py sidesteps the
    same crash with os._exit(); SQUID_PYTEST_HARD_EXIT=1 lets CI's isolated
    GUI-test invocation do likewise, preserving pytest's exit status.
    """
    if os.environ.get("SQUID_PYTEST_HARD_EXIT") == "1":
        sys.stdout.flush()
        sys.stderr.flush()
        # Default 1, not 0: if pytest_sessionfinish never ran (e.g. a
        # sessionstart failure), an unrecorded status must fail the step.
        os._exit(getattr(config, "_squid_exitstatus", 1))


def _make_tracking_init(original_init, instances_list):
    """Create a wrapper that tracks Microcontroller instances."""

    def _tracking_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        instances_list.append(self)

    return _tracking_init


@pytest.fixture(autouse=True)
def cleanup_microcontrollers():
    """
    Fixture that automatically cleans up all Microcontroller instances after each test.

    This prevents background threads from causing segfaults when subsequent tests run,
    especially those involving Qt event loops. The Microcontroller.read_received_packet
    method runs in a background thread that must be stopped via close().
    """
    # Track instances created during this test (scoped to this fixture invocation)
    active_microcontrollers = []

    # Capture original __init__ at fixture runtime, not module load time
    original_init = control.microcontroller.Microcontroller.__init__

    with patch.object(
        control.microcontroller.Microcontroller, "__init__", _make_tracking_init(original_init, active_microcontrollers)
    ):
        yield

    # Clean up all tracked instances
    for micro in active_microcontrollers:
        try:
            if hasattr(micro, "terminate_reading_received_packet_thread"):
                if not micro.terminate_reading_received_packet_thread:
                    micro.close()
        except Exception as e:
            logger.warning(f"Failed to close Microcontroller in test cleanup: {e}")


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
