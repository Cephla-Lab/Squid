"""
Shared pytest fixtures for Squid test infrastructure.

This module provides fixtures using simulated hardware implementations:
- SimulatedCamera: control/peripherals/cameras/camera_utils.py
- SimulatedFilterWheelController: control/peripherals/filter_wheel/utils.py
- SimSerial: control/peripherals/stage/serial.py
- Microcontroller with SimSerial backend
- Full Microscope with simulated=True

Usage:
    def test_something(simulated_camera):
        camera = simulated_camera
        camera.send_trigger()
        frame = camera.read_frame()
        assert frame is not None
"""

import logging
from typing import Generator
from unittest.mock import patch
import sys
import importlib.util
import pathlib

import pytest

# Add src/ to Python path for imports
_repo_root = pathlib.Path(__file__).resolve().parent.parent
_src_dir = _repo_root / "src"
if str(_src_dir) not in sys.path:
    sys.path.insert(0, str(_src_dir))

# Force use of lightweight stub modules for napari/pyqtgraph to avoid heavy
# dependencies and cache writes during tests.
for _mod_name in ["napari", "pyqtgraph"]:
    _stub_init = _repo_root / _mod_name / "__init__.py"
    if _stub_init.exists():
        _spec = importlib.util.spec_from_file_location(
            _mod_name, _stub_init, submodule_search_locations=[str(_repo_root / _mod_name)]
        )
        if _spec and _spec.loader:
            _module = importlib.util.module_from_spec(_spec)
            _spec.loader.exec_module(_module)
            sys.modules[_mod_name] = _module

import squid.core.config
from squid.core.events import EventBus

logger = logging.getLogger(__name__)


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
    # Import at fixture runtime to avoid module load errors when optional deps missing
    try:
        from squid.backend.microcontroller import Microcontroller
    except ImportError:
        # If microcontroller module can't be imported, skip cleanup
        yield
        return

    # Track instances created during this test (scoped to this fixture invocation)
    active_microcontrollers = []

    # Capture original __init__ at fixture runtime, not module load time
    original_init = Microcontroller.__init__

    with patch.object(
        Microcontroller,
        "__init__",
        _make_tracking_init(original_init, active_microcontrollers),
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


# ============================================================================
# Camera Fixtures
# ============================================================================


@pytest.fixture
def camera_config() -> squid.core.config.CameraConfig:
    """Provide default camera configuration."""
    return squid.core.config.get_camera_config()


@pytest.fixture
def simulated_camera(camera_config) -> Generator:
    """
    Provide a SimulatedCamera instance.

    Uses control/peripherals/cameras/camera_utils.py SimulatedCamera class.
    The camera is created via the registry with simulated=True.
    """
    from squid.backend.drivers.cameras.camera_utils import get_camera

    camera = get_camera(camera_config, simulated=True)
    yield camera
    camera.close()


@pytest.fixture
def simulated_camera_streaming(simulated_camera) -> Generator:
    """
    Provide a SimulatedCamera that is already streaming.

    Useful for tests that need to receive frames immediately.
    """
    simulated_camera.start_streaming()
    yield simulated_camera
    simulated_camera.stop_streaming()


# ============================================================================
# Filter Wheel Fixtures
# ============================================================================


@pytest.fixture
def simulated_filter_wheel() -> Generator:
    """
    Provide a SimulatedFilterWheelController instance.

    Uses control/peripherals/filter_wheel/utils.py SimulatedFilterWheelController.
    Configured with 1 wheel, 8 slots, no simulated delays for fast testing.
    """
    from squid.backend.drivers.filter_wheels.utils import SimulatedFilterWheelController

    controller = SimulatedFilterWheelController(
        number_of_wheels=1,
        slots_per_wheel=8,
        simulate_delays=False,
    )
    controller.initialize([1])
    yield controller
    controller.close()


@pytest.fixture
def simulated_filter_wheel_multi() -> Generator:
    """
    Provide a multi-wheel SimulatedFilterWheelController.

    Configured with 2 wheels, 8 slots each, no simulated delays.
    """
    from squid.backend.drivers.filter_wheels.utils import SimulatedFilterWheelController

    controller = SimulatedFilterWheelController(
        number_of_wheels=2,
        slots_per_wheel=8,
        simulate_delays=False,
    )
    controller.initialize([1, 2])
    yield controller
    controller.close()


# ============================================================================
# Serial / Microcontroller Fixtures
# ============================================================================


@pytest.fixture
def sim_serial() -> Generator:
    """
    Provide a SimSerial instance for microcontroller testing.

    Uses control/peripherals/stage/serial.py SimSerial class.
    Simulates the Cephla microcontroller serial protocol.
    """
    from squid.backend.drivers.stages.serial import SimSerial

    serial = SimSerial()
    yield serial
    serial.close()


@pytest.fixture
def simulated_microcontroller() -> Generator:
    """
    Provide a Microcontroller with SimSerial backend.

    This is the main low-level hardware abstraction used for stage control,
    illumination, and other microcontroller-driven peripherals.
    """
    from squid.backend.drivers.stages.serial import get_microcontroller_serial_device
    from squid.backend.microcontroller import Microcontroller

    serial_device = get_microcontroller_serial_device(simulated=True)
    micro = Microcontroller(serial_device=serial_device)
    yield micro
    micro.close()


# ============================================================================
# Stage Fixtures
# ============================================================================


@pytest.fixture
def stage_config() -> squid.core.config.StageConfig:
    """Provide default stage configuration."""
    return squid.core.config.get_stage_config()


@pytest.fixture
def simulated_stage(stage_config) -> Generator:
    """
    Provide a SimulatedStage instance.

    Uses the new SimulatedStage class that directly implements AbstractStage
    without requiring a microcontroller. Faster and simpler for most tests.
    """
    from squid.backend.drivers.stages.simulated import SimulatedStage

    stage = SimulatedStage(stage_config, simulate_delays=False)
    yield stage


@pytest.fixture
def simulated_cephla_stage(simulated_microcontroller, stage_config) -> Generator:
    """
    Provide a CephlaStage with simulated microcontroller.

    Uses the simulated_microcontroller fixture which has SimSerial backend.
    Returns a CephlaStage that operates on the simulated serial.
    Use this when you need to test CephlaStage-specific behavior.
    """
    from squid.backend.drivers.stages.cephla import CephlaStage

    stage = CephlaStage(simulated_microcontroller, stage_config)
    yield stage
    # CephlaStage doesn't have a close method, cleanup happens via microcontroller


# ============================================================================
# Full Microscope Fixtures
# ============================================================================


@pytest.fixture
def simulated_microscope() -> Generator:
    """
    Provide a fully simulated Microscope instance.

    Creates a complete microscope stack with:
    - Simulated camera
    - Simulated microcontroller (SimSerial)
    - Simulated stage (CephlaStage)
    - Simulated illumination controller
    - Optional addons based on control._def configuration

    This is the highest-level fixture for integration tests.
    """
    from squid.backend.microscope import Microscope

    scope = Microscope.build_from_global_config(simulated=True)
    yield scope
    scope.close()


# ============================================================================
# Event Bus Fixture
# ============================================================================


@pytest.fixture
def event_bus() -> Generator[EventBus, None, None]:
    """Provide a fresh EventBus instance for service testing."""
    bus = EventBus()
    yield bus
    bus.clear()


# QtBot fixture: prefer real pytest-qt implementation, fall back to stub.
@pytest.fixture
def qtbot(request):
    try:
        from pytestqt.qtbot import QtBot

        # Ensure QApplication exists via pytest-qt's qapp fixture
        try:
            request.getfixturevalue("qapp")
        except Exception:
            pass
        return QtBot(request)
    except Exception:
        import time
        import pytest

        class _QtBot:
            def add_widget(self, widget):
                return widget

            def wait(self, ms: int):
                time.sleep(ms / 1000.0)

            def waitUntil(self, predicate, timeout: int = 1000, interval: int = 10):
                deadline = time.time() + timeout / 1000.0
                while time.time() < deadline:
                    if predicate():
                        return
                    time.sleep(interval / 1000.0)
                pytest.fail("waitUntil timed out")

        return _QtBot()


# ============================================================================
# Application Context Fixtures
# ============================================================================


@pytest.fixture
def simulated_application_context() -> Generator:
    """
    Provide a simulated ApplicationContext.

    This includes the full microscope and all services configured for simulation.
    """
    from squid.application import ApplicationContext

    context = ApplicationContext(simulation=True)
    yield context
    context.shutdown()


# ============================================================================
# Headless GUI Fixtures
# ============================================================================


@pytest.fixture
def headless_qt_env(monkeypatch):
    """Force offscreen Qt platform for headless GUI tests."""
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def qapp(headless_qt_env):
    """Ensure a QApplication exists for GUI tests."""
    from qtpy.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def gui_dialog_patches(monkeypatch, tmp_path):
    """Patch modal dialogs to avoid blocking in headless tests."""
    from qtpy.QtWidgets import QFileDialog, QMessageBox, QInputDialog

    input_path = tmp_path / "input.txt"
    input_path.write_text("test")
    save_path = tmp_path / "output.txt"

    monkeypatch.setattr(
        QFileDialog,
        "getExistingDirectory",
        staticmethod(lambda *args, **kwargs: str(tmp_path)),
    )
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *args, **kwargs: (str(input_path), "")),
    )
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        staticmethod(lambda *args, **kwargs: (str(save_path), "")),
    )

    def _msg_exec(self):
        return QMessageBox.Ok

    monkeypatch.setattr(QMessageBox, "exec_", _msg_exec, raising=False)
    monkeypatch.setattr(
        QMessageBox, "warning", staticmethod(lambda *args, **kwargs: QMessageBox.Ok)
    )
    monkeypatch.setattr(
        QMessageBox, "information", staticmethod(lambda *args, **kwargs: QMessageBox.Ok)
    )
    monkeypatch.setattr(
        QMessageBox, "critical", staticmethod(lambda *args, **kwargs: QMessageBox.Ok)
    )
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *args, **kwargs: QMessageBox.Yes)
    )

    def _get_item(_parent, _title, _label, items, current=0, editable=False, *args, **kwargs):
        selection = items[current] if items else ""
        return selection, True

    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(_get_item))
    return tmp_path


@pytest.fixture
def gui_factory(monkeypatch, qapp, qtbot, gui_dialog_patches):
    """Build simulated GUI contexts with optional feature flag overrides."""
    from tests.gui_helpers import apply_gui_flags
    from squid.application import ApplicationContext

    contexts = []

    def _factory(**flags):
        if flags:
            apply_gui_flags(monkeypatch, **flags)
        context = ApplicationContext(simulation=True)
        gui = context.create_gui()
        if hasattr(qtbot, "add_widget"):
            qtbot.add_widget(gui)
        contexts.append(context)
        return context, gui

    yield _factory
    for context in contexts:
        context.shutdown()

# ============================================================================
# Piezo Stage Fixture
# ============================================================================


@pytest.fixture
def simulated_piezo_stage(simulated_microcontroller) -> Generator:
    """
    Provide a PiezoStage with simulated microcontroller.
    """
    from squid.backend.drivers.peripherals.piezo import PiezoStage
    import _def

    config = {
        "OBJECTIVE_PIEZO_HOME_UM": _def.OBJECTIVE_PIEZO_HOME_UM,
        "OBJECTIVE_PIEZO_RANGE_UM": _def.OBJECTIVE_PIEZO_RANGE_UM,
        "OBJECTIVE_PIEZO_CONTROL_VOLTAGE_RANGE": _def.OBJECTIVE_PIEZO_CONTROL_VOLTAGE_RANGE,
        "OBJECTIVE_PIEZO_FLIP_DIR": _def.OBJECTIVE_PIEZO_FLIP_DIR,
    }
    piezo = PiezoStage(microcontroller=simulated_microcontroller, config=config)
    yield piezo


# ============================================================================
# Matplotlib Backend Fixture (for tests that conflict with Qt)
# ============================================================================


@pytest.fixture
def matplotlib_agg_backend():
    """
    Set matplotlib to use non-interactive Agg backend.

    Use this fixture for tests that would otherwise conflict with Qt.
    """
    import matplotlib

    original_backend = matplotlib.get_backend()
    matplotlib.use("Agg")
    yield
    matplotlib.use(original_backend)


# ============================================================================
# Test Data Fixtures
# ============================================================================


@pytest.fixture
def test_data_dir():
    """Provide path to test data directory."""
    import pathlib

    return pathlib.Path(__file__).parent / "data"


@pytest.fixture
def repo_root():
    """Provide path to repository root."""
    import git
    import os
    import pathlib

    git_repo = git.Repo(os.getcwd(), search_parent_directories=True)
    git_root = git_repo.git.rev_parse("--show-toplevel")
    return pathlib.Path(git_root).absolute()
