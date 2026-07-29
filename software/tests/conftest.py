"""
Suite-wide pytest fixtures.

Ensures hardware-simulation objects created during a test (Microscope,
MultiPointController, Microcontroller) are closed at test teardown. Leaked
instances keep daemon threads (camera streaming, laser-engine tick, slack
notifier) and JobRunner child processes alive; those have caused CI segfaults
both mid-suite (a leftover thread touching a destroyed Qt object) and at
interpreter shutdown (daemon threads frozen inside C code during
finalization).
"""

import logging
import os
import sys
from unittest.mock import patch

import pytest

import control.microcontroller
import control.microscope
from control.core.multi_point_controller import MultiPointController

logger = logging.getLogger(__name__)


def pytest_sessionfinish(session, exitstatus):
    session.config._squid_exitstatus = int(exitstatus)


def pytest_unconfigure(config):
    """Optionally skip interpreter teardown after the test session.

    A pytest process that constructed the full HCS GUI segfaults during
    interpreter shutdown (Qt C++ destructor order conflicts with Python GC)
    even though every test passed. main_hcs.py sidesteps the same crash with
    os._exit(); SQUID_PYTEST_HARD_EXIT=1 lets CI do likewise, preserving
    pytest's exit status so real test failures still fail the step.
    """
    if os.environ.get("SQUID_PYTEST_HARD_EXIT") == "1":
        sys.stdout.flush()
        sys.stderr.flush()
        # Default 1, not 0: if pytest_sessionfinish never ran (e.g. a
        # sessionstart failure), an unrecorded status must fail the step.
        os._exit(getattr(config, "_squid_exitstatus", 1))


def _make_tracking_init(original_init, instances_list):
    """Create an __init__ wrapper that records constructed instances."""

    def _tracking_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        instances_list.append(self)

    return _tracking_init


@pytest.fixture(autouse=True)
def cleanup_leaked_hardware(tmp_path, monkeypatch):
    """
    Automatically close hardware-simulation objects created during each test.

    Teardown order matters:
    1. MultiPointControllers first — joins the acquisition thread and shuts
       down JobRunner child processes while the microcontroller is still
       alive, so the worker's stage-return move can complete instead of
       timing out.
    2. Microscopes next — stops camera streaming threads and closes the
       microcontroller and addons.
    3. Any Microcontrollers created standalone (skipped if a Microscope
       already closed them).
    """
    # Safety net for teardown-time breadcrumb writes: a leaked acquisition
    # finishing during the cleanup below must not write to the real user state
    # dir. Ordering: monkeypatches set up after this fixture (e.g. the autouse
    # _watchdog_state_to_tmp in tests/control/conftest.py, or a test's own)
    # are already undone when this teardown runs, restoring the value set
    # here; our own monkeypatch reverts only after this fixture finishes.
    cleanup_state_dir = str(tmp_path / "watchdog-cleanup")
    monkeypatch.setenv("SQUID_WATCHDOG_STATE_DIR", cleanup_state_dir)

    microscopes = []
    controllers = []
    microcontrollers = []

    with patch.object(
        control.microscope.Microscope,
        "__init__",
        _make_tracking_init(control.microscope.Microscope.__init__, microscopes),
    ), patch.object(
        MultiPointController,
        "__init__",
        _make_tracking_init(MultiPointController.__init__, controllers),
    ), patch.object(
        control.microcontroller.Microcontroller,
        "__init__",
        _make_tracking_init(control.microcontroller.Microcontroller.__init__, microcontrollers),
    ):
        yield

    # Re-apply in case the test body changed the env var with a raw
    # os.environ write, which nothing has undone at this point.
    monkeypatch.setenv("SQUID_WATCHDOG_STATE_DIR", cleanup_state_dir)

    for controller in reversed(controllers):
        try:
            controller.close()
        except Exception:
            logger.exception("Failed to close MultiPointController in test cleanup")

    for microscope in reversed(microscopes):
        try:
            microscope.close()
        except Exception:
            logger.exception("Failed to close Microscope in test cleanup")

    for micro in reversed(microcontrollers):
        try:
            if not micro.terminate_reading_received_packet_thread:
                micro.close()
        except Exception:
            logger.exception("Failed to close Microcontroller in test cleanup")
