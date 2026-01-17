"""
Tests for SimulatedFluidicsController.
"""

import json
import pytest
from pathlib import Path
from tempfile import TemporaryDirectory

from squid.core.abc import FluidicsOperationStatus
from squid.backend.drivers.fluidics.simulation import SimulatedFluidicsController


@pytest.fixture
def config_data():
    """Return test configuration data."""
    return {
        "microcontroller": {"serial_number": "SIMULATION"},
        "syringe_pump": {"volume_ul": 5000},
        "selector_valves": {},
        "solution_port_mapping": {
            "wash_buffer": 1,
            "imaging_buffer": 2,
            "probe_1": 3,
        },
        "limits": {
            "max_flow_rate_ul_per_min": 10000.0,
            "min_flow_rate_ul_per_min": 1.0,
            "max_volume_ul": 5000.0,
        },
    }


@pytest.fixture
def config_file(config_data):
    """Create a temporary config file."""
    with TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "test_config.json"
        with open(config_path, "w") as f:
            json.dump(config_data, f)
        yield str(config_path)


@pytest.fixture
def controller(config_file):
    """Create initialized controller."""
    ctrl = SimulatedFluidicsController(config_file, simulate_timing=False)
    ctrl.initialize()
    yield ctrl
    ctrl.close()


class TestSimulatedFluidicsController:
    """Tests for SimulatedFluidicsController."""

    def test_initialize(self, config_file):
        """Test controller initialization."""
        ctrl = SimulatedFluidicsController(config_file)
        assert ctrl.initialize() is True
        assert ctrl.is_busy is False
        ctrl.close()

    def test_initialize_missing_config(self):
        """Test initialization with missing config file still works."""
        ctrl = SimulatedFluidicsController("/nonexistent/config.json")
        # Should not raise - creates minimal config
        assert ctrl.initialize() is True
        ctrl.close()

    def test_flow_solution(self, controller):
        """Test flow_solution operation."""
        result = controller.flow_solution(
            port=1, volume_ul=100, flow_rate_ul_per_min=50
        )
        assert result is True
        assert controller.get_status().status == FluidicsOperationStatus.COMPLETED

    def test_flow_solution_with_fill_tubing(self, controller):
        """Test flow_solution with fill_tubing_with_port."""
        result = controller.flow_solution(
            port=3,
            volume_ul=100,
            flow_rate_ul_per_min=50,
            fill_tubing_with_port=1,
        )
        assert result is True

    def test_prime(self, controller):
        """Test prime operation."""
        result = controller.prime(
            ports=[1, 2, 3], volume_ul=200, flow_rate_ul_per_min=100, final_port=1
        )
        assert result is True
        assert controller.get_status().status == FluidicsOperationStatus.COMPLETED

    def test_wash(self, controller):
        """Test wash operation."""
        result = controller.wash(
            wash_port=1, volume_ul=100, flow_rate_ul_per_min=50, repeats=2
        )
        assert result is True
        assert controller.get_status().status == FluidicsOperationStatus.COMPLETED

    def test_empty_syringe(self, controller):
        """Test empty_syringe operation."""
        # First add some volume
        controller.flow_solution(port=1, volume_ul=500, flow_rate_ul_per_min=100)

        result = controller.empty_syringe()
        assert result is True
        status = controller.get_status()
        assert status.syringe_volume_ul == 0.0

    def test_abort(self, controller):
        """Test abort sets status."""
        controller.abort()
        assert controller.get_status().status == FluidicsOperationStatus.ABORTED

    def test_reset_abort(self, controller):
        """Test reset_abort clears status."""
        controller.abort()
        controller.reset_abort()
        assert controller.get_status().status == FluidicsOperationStatus.IDLE

    def test_get_port_name(self, controller):
        """Test get_port_name returns solution name."""
        assert controller.get_port_name(1) == "wash_buffer"
        assert controller.get_port_name(2) == "imaging_buffer"
        assert controller.get_port_name(99) is None

    def test_get_port_for_solution(self, controller):
        """Test get_port_for_solution returns port number."""
        assert controller.get_port_for_solution("wash_buffer") == 1
        assert controller.get_port_for_solution("WASH_BUFFER") == 1  # case-insensitive
        assert controller.get_port_for_solution("nonexistent") is None

    def test_get_available_ports(self, controller):
        """Test get_available_ports returns sorted list."""
        ports = controller.get_available_ports()
        assert isinstance(ports, list)
        assert ports == sorted(ports)
        assert 1 in ports
        assert 2 in ports
        assert 3 in ports

    def test_is_busy(self, controller):
        """Test is_busy property."""
        assert controller.is_busy is False

    def test_get_status(self, controller):
        """Test get_status returns FluidicsStatus."""
        status = controller.get_status()
        assert status.status == FluidicsOperationStatus.IDLE
        assert status.is_busy is False

    def test_not_initialized_returns_false(self):
        """Test operations return False when not initialized."""
        ctrl = SimulatedFluidicsController("/nonexistent.json")
        # Don't call initialize()

        assert ctrl.flow_solution(1, 100, 50) is False
        assert ctrl.prime([1], 100, 50, 1) is False
        assert ctrl.wash(1, 100, 50) is False
        assert ctrl.empty_syringe() is False


class TestSimulatedTimingMode:
    """Test simulation timing mode."""

    def test_timing_mode(self, config_file):
        """Test that timing mode creates delays."""
        ctrl = SimulatedFluidicsController(config_file, simulate_timing=True)
        ctrl.initialize()

        # With timing=True, operations should still complete
        result = ctrl.flow_solution(port=1, volume_ul=10, flow_rate_ul_per_min=1000)
        assert result is True

        ctrl.close()
