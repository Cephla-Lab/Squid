import logging

import pytest

pytest.importorskip("fluidics")

import squid.logging
from control.fluidics_system import FluidicsService, check_library_surface, install_logging_bridge


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


def test_library_surface_matches_what_squid_uses():
    check_library_surface()  # raises FluidicsLibraryError on drift


def test_uninitialized_service_is_inert():
    service = FluidicsService(default_config_path="does/not/exist.yaml", simulated=True)
    assert service.initialized is False
    assert service.system is None
    assert service.close() == []


def test_initialize_builds_a_simulated_system_and_close_releases_it(tmp_path, fluidics_config_path):
    service = FluidicsService(default_config_path=fluidics_config_path, simulated=True)
    service.initialize(report_dir=str(tmp_path), instant=True)
    try:
        assert service.initialized
        assert service.config.application == "Flow Cell"
        assert service.config_path == fluidics_config_path
        assert service.system.devices.syringe_pump is not None
        assert service.system.busy is False
        with pytest.raises(RuntimeError):
            service.initialize(instant=True)
    finally:
        assert service.close() == []
    assert service.initialized is False
    assert service.close() == []  # idempotent


def test_instant_is_refused_for_real_hardware(fluidics_config_path):
    service = FluidicsService(default_config_path=fluidics_config_path, simulated=False)
    with pytest.raises(ValueError):
        service.initialize(instant=True)


def test_library_log_records_reach_squid_handlers():
    install_logging_bridge()
    capture = _Capture()
    squid.logging.get_logger().addHandler(capture)
    try:
        logging.getLogger("fluidics.test_bridge").info("hello from the library")
        logging.getLogger("XCaliburD").debug("pump says hi")
    finally:
        squid.logging.get_logger().removeHandler(capture)
    names = [r.name for r in capture.records]
    assert "fluidics.test_bridge" in names
    assert "XCaliburD" in names
    assert install_logging_bridge() is install_logging_bridge()  # one handler, ever
