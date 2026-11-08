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

import collections
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Must run before any control.* import (which pulls in control._def, which reads this path at
# import time): a real machine_configs/objectives.yaml on a dev/bench machine would otherwise
# change OBJECTIVES for the whole in-process suite, or sys.exit(1) the collection itself.
import control.objectives_config as _objectives_config

_objectives_config.OBJECTIVES_YAML_PATH = Path(tempfile.gettempdir()) / "squid-tests-no-objectives.yaml"

import control.microcontroller
import control.microscope
from control.core.multi_point_controller import MultiPointController

from tests.control.test_stubs import cleanup_stub_acquisition_dirs
from tests.sharding import files_for_shard, parse_shard

logger = logging.getLogger(__name__)

# Junk dir for watchdog breadcrumbs written by leaked acquisitions while
# cleanup_leaked_hardware closes them; nothing reads it. A plain string, not
# pytest's tmp_path — tmp_path would cost a mkdir per test suite-wide, and the
# breadcrumb writer creates parent dirs itself.
_CLEANUP_STATE_DIR = os.path.join(tempfile.gettempdir(), f"squid-test-watchdog-cleanup-{os.getpid()}")


def pytest_report_header(config):
    shard = os.environ.get("SQUID_TEST_SHARD")
    return f"SQUID_TEST_SHARD={shard}: running this shard's test files only" if shard else None


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items):
    """With SQUID_TEST_SHARD=k/n (CI), keep only shard k's test files; the rest are deselected."""
    spec = os.environ.get("SQUID_TEST_SHARD")
    if not spec:
        return
    try:
        index, total = parse_shard(spec)
    except ValueError as e:
        raise pytest.UsageError(str(e))
    tests_per_file = collections.Counter(item.nodeid.split("::")[0] for item in items)
    keep = files_for_shard(tests_per_file, tests_per_file, index, total)
    deselected = [item for item in items if item.nodeid.split("::")[0] not in keep]
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = [item for item in items if item.nodeid.split("::")[0] in keep]


def pytest_sessionfinish(session, exitstatus):
    session.config._squid_exitstatus = int(exitstatus)
    # Nothing reads the watchdog cleanup breadcrumbs after the session.
    shutil.rmtree(_CLEANUP_STATE_DIR, ignore_errors=True)


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


def _close_quietly(obj, label):
    try:
        obj.close()
    except Exception:
        logger.exception(f"Failed to close {label} in test cleanup")


@pytest.fixture(autouse=True)
def _loading_state_in_tmp(tmp_path, monkeypatch):
    """Every move to the loading position on a Z lift system writes Z_LIFT_LOADING_STATE_PATH: keep tests from
    writing it into the real cache/ (a startup there would then begin at the loading position)."""
    import control._def

    monkeypatch.setattr(control._def, "Z_LIFT_LOADING_STATE_PATH", str(tmp_path / "z_lift_loading_state.json"))


@pytest.fixture(autouse=True)
def cleanup_leaked_hardware(monkeypatch):
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

    # A breadcrumb written by a leaked acquisition while it is closed below
    # must not land in the real user state dir. Setting the env var here, at
    # teardown start, is deterministic regardless of what the test body or
    # other fixtures did with it: their monkeypatches are already undone (they
    # were set up after this fixture), raw os.environ writes are overwritten,
    # and our own monkeypatch reverts this value after the fixture finishes.
    monkeypatch.setenv("SQUID_WATCHDOG_STATE_DIR", _CLEANUP_STATE_DIR)

    for controller in reversed(controllers):
        _close_quietly(controller, "MultiPointController")

    for microscope in reversed(microscopes):
        _close_quietly(microscope, "Microscope")

    for micro in reversed(microcontrollers):
        if not micro.terminate_reading_received_packet_thread:
            _close_quietly(micro, "Microcontroller")

    # Last, after every writer above is closed: delete this test's simulated-acquisition output.
    cleanup_stub_acquisition_dirs()
