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
import shutil
import sys
import tempfile
import warnings
from unittest.mock import patch

import pytest

# Select the Qt binding (PyQt6 preferred when installed) before anything
# imports qtpy. Also keeps pytest-qt (PYTEST_QT_API) on the same binding —
# left alone it prefers PyQt6 on its own and the process would load both.
from squid.qt_binding import select_qt_api

select_qt_api()

import control.microcontroller
import control.microscope
from control.core.multi_point_controller import MultiPointController

logger = logging.getLogger(__name__)

# Junk dir for watchdog breadcrumbs written by leaked acquisitions while
# cleanup_leaked_hardware closes them; nothing reads it. A plain string, not
# pytest's tmp_path — tmp_path would cost a mkdir per test suite-wide, and the
# breadcrumb writer creates parent dirs itself.
_CLEANUP_STATE_DIR = os.path.join(tempfile.gettempdir(), f"squid-test-watchdog-cleanup-{os.getpid()}")


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


def _close_quietly(obj, label):
    try:
        obj.close()
    except Exception:
        logger.exception(f"Failed to close {label} in test cleanup")


@pytest.fixture(autouse=True)
def isolate_ambient_camera_registry(monkeypatch):
    """Pin the default-path camera registry to None (the CI truth) during tests.

    machine_configs/cameras.yaml is machine-specific and gitignored; a dev machine may
    carry a multi-camera file for manual GUI testing. Tests must not depend on it:
    Microscope.build_from_global_config reads the registry via a default-path
    ConfigRepository, so without this pin every microscope-building test would flip to
    a multi-camera facade build on such machines. Repositories constructed with an
    explicit base_path (e.g. tmp_path in repository tests) are unaffected, and tests
    that monkeypatch get_camera_registry themselves override this pin (autouse
    fixtures apply first).
    """
    from control.core.config.repository import ConfigRepository

    default_machine_configs_path = ConfigRepository().machine_configs_path
    original_get_camera_registry = ConfigRepository.get_camera_registry

    def _get_camera_registry(self):
        if self.machine_configs_path == default_machine_configs_path:
            return None
        return original_get_camera_registry(self)

    monkeypatch.setattr(ConfigRepository, "get_camera_registry", _get_camera_registry)


@pytest.fixture(scope="session")
def canonical_user_profiles_template(tmp_path_factory):
    """A freshly generated "default" profile to copy per test, or None if it can't be made.

    user_profiles/ is gitignored, so a CI checkout has none and the app generates it from
    machine_configs/illumination_channel_config.yaml on first use. Doing exactly that here
    gives every test the same starting point CI has: channels at their default exposure and
    gain, and none of them bound to a camera.

    Returns None instead of raising when generation is impossible. isolate_ambient_user_profiles
    is autouse, so a raise here fails EVERY test in the session — over a gitignored file the
    application itself merely warns about (ConfigRepository.load_profile swallows the same
    FileNotFoundError). Falling back to the ambient profile keeps such a checkout working
    exactly as it did before this fixture existed.
    """
    from control.core.config.repository import ConfigRepository
    from control.default_config_generator import ensure_default_configs
    import control._def

    profiles_root = tmp_path_factory.mktemp("user_profiles_template")
    (profiles_root / "default").mkdir()

    # Real base_path, so machine_configs (the illumination config) still resolves; only the
    # profile output is redirected. Built before isolate_ambient_user_profiles patches
    # __init__, so this repository is a plain one.
    repo = ConfigRepository()
    repo.user_profiles_path = profiles_root
    hint = (
        "Tests will use the machine's own user_profiles/ instead, so results may depend on "
        "local channel state. To get the isolated profile, populate the illumination config "
        "the way CI does: cp machine_configs/illumination_channel_config.yaml.example "
        "machine_configs/illumination_channel_config.yaml"
    )
    try:
        ensure_default_configs(
            repo,
            "default",
            list(control._def.OBJECTIVES) if hasattr(control._def, "OBJECTIVES") else None,
            include_confocal=False,
        )
    except (FileNotFoundError, OSError) as e:
        warnings.warn(f"Could not generate the test profile ({e}). {hint}")
        return None

    # Generation also declines silently when legacy XML configs are pending migration
    # (see has_legacy_configs_to_migrate), which would leave an empty profile behind.
    if not (profiles_root / "default" / "channel_configs" / "general.yaml").exists():
        warnings.warn(
            f"The test profile was not generated (no general.yaml); if "
            f"software/acquisition_configurations/ holds legacy XML configs, run "
            f"tools/migrate_acquisition_configs.py first. {hint}"
        )
        return None
    return profiles_root


@pytest.fixture(autouse=True)
def isolate_ambient_user_profiles(monkeypatch, tmp_path_factory, canonical_user_profiles_template):
    """Give each test its own copy of the canonical profile, not the machine's.

    user_profiles/ is machine-specific and gitignored, and it is read *and written* by the
    running application: the live-control exposure/gain/intensity spinboxes persist through
    ConfigRepository on every edit. Without this,

      * tests inherit whatever the developer's channels happen to hold - notably a
        `camera:` binding, which decides the active camera at startup and so changes what
        the trigger dropdown offers and whether an acquisition needs a camera switch, and
      * a test that drives those spinboxes rewrites the developer's channel configs.

    The copy is per test rather than one shared directory: a widget-driven edit persists, so
    a shared profile would carry one test's channel values (or a ProfileWidget-created
    profile) into every later test in the session, making outcomes order-dependent.

    Repositories constructed with an explicit base_path (tmp_path in the repository tests)
    are left alone, as in isolate_ambient_camera_registry.
    """
    if canonical_user_profiles_template is None:
        return  # nothing generated; leave the ambient profile in place (see the fixture above)

    from control.core.config.repository import ConfigRepository

    # A directory of our own, NOT the test's tmp_path: the repository tests use tmp_path as
    # a ConfigRepository base_path, so planting a profile under it would collide with the
    # profiles they build there themselves.
    profiles_root = tmp_path_factory.mktemp("isolated_profiles") / "user_profiles"
    shutil.copytree(canonical_user_profiles_template, profiles_root)

    default_user_profiles_path = ConfigRepository().user_profiles_path
    original_init = ConfigRepository.__init__

    def _init(self, base_path=None):
        original_init(self, base_path)
        if self.user_profiles_path == default_user_profiles_path:
            self.user_profiles_path = profiles_root

    monkeypatch.setattr(ConfigRepository, "__init__", _init)


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
