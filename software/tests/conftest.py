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

import pytest
from typing import Generator

import squid.config
from squid.events import EventBus


# ============================================================================
# Camera Fixtures
# ============================================================================


@pytest.fixture
def camera_config() -> squid.config.CameraConfig:
    """Provide default camera configuration."""
    return squid.config.get_camera_config()


@pytest.fixture
def simulated_camera(camera_config) -> Generator:
    """
    Provide a SimulatedCamera instance.

    Uses control/peripherals/cameras/camera_utils.py SimulatedCamera class.
    The camera is created via the registry with simulated=True.
    """
    from control.peripherals.cameras.camera_utils import get_camera

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
    from control.peripherals.filter_wheel.utils import SimulatedFilterWheelController

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
    from control.peripherals.filter_wheel.utils import SimulatedFilterWheelController

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
    from control.peripherals.stage.serial import SimSerial

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
    from control.peripherals.stage.serial import get_microcontroller_serial_device
    from control.microcontroller import Microcontroller

    serial_device = get_microcontroller_serial_device(simulated=True)
    micro = Microcontroller(serial_device=serial_device)
    yield micro
    micro.close()


# ============================================================================
# Stage Fixtures
# ============================================================================


@pytest.fixture
def stage_config() -> squid.config.StageConfig:
    """Provide default stage configuration."""
    return squid.config.get_stage_config()


@pytest.fixture
def simulated_stage(stage_config) -> Generator:
    """
    Provide a SimulatedStage instance.

    Uses the new SimulatedStage class that directly implements AbstractStage
    without requiring a microcontroller. Faster and simpler for most tests.
    """
    from control.peripherals.stage.simulated import SimulatedStage

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
    from control.peripherals.stage.cephla import CephlaStage

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
    import control.microscope

    scope = control.microscope.Microscope.build_from_global_config(simulated=True)
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
# Piezo Stage Fixture
# ============================================================================


@pytest.fixture
def simulated_piezo_stage(simulated_microcontroller) -> Generator:
    """
    Provide a PiezoStage with simulated microcontroller.
    """
    from control.peripherals.piezo import PiezoStage
    import control._def

    config = {
        "OBJECTIVE_PIEZO_HOME_UM": control._def.OBJECTIVE_PIEZO_HOME_UM,
        "OBJECTIVE_PIEZO_RANGE_UM": control._def.OBJECTIVE_PIEZO_RANGE_UM,
        "OBJECTIVE_PIEZO_CONTROL_VOLTAGE_RANGE": control._def.OBJECTIVE_PIEZO_CONTROL_VOLTAGE_RANGE,
        "OBJECTIVE_PIEZO_FLIP_DIR": control._def.OBJECTIVE_PIEZO_FLIP_DIR,
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
