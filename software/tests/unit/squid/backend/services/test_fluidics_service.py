"""
Tests for FluidicsService.
"""

import threading
import time
import pytest
from unittest.mock import MagicMock, patch

from squid.core.abc import FluidicsOperationStatus, FluidicsStatus
from squid.core.events import EventBus
from squid.backend.services.fluidics_service import FluidicsService


@pytest.fixture
def event_bus():
    """Create a test EventBus."""
    return EventBus()


@pytest.fixture
def mock_driver():
    """Create a mock AbstractFluidicsController."""
    driver = MagicMock()
    driver.is_busy = False
    driver.get_status.return_value = FluidicsStatus(
        status=FluidicsOperationStatus.IDLE,
        current_port=None,
        current_solution=None,
        syringe_volume_ul=0.0,
        is_busy=False,
        error_message=None,
    )
    driver.get_port_name.return_value = "test_solution"
    driver.get_port_for_solution.return_value = 1
    driver.get_available_ports.return_value = [1, 2, 3]
    driver.flow_solution.return_value = True
    driver.prime.return_value = True
    driver.wash.return_value = True
    driver.empty_syringe.return_value = True
    return driver


@pytest.fixture
def service(mock_driver, event_bus):
    """Create FluidicsService with mock driver."""
    svc = FluidicsService(mock_driver, event_bus)
    yield svc
    svc.shutdown()


class TestFluidicsServiceInit:
    """Tests for service initialization."""

    def test_is_available_with_driver(self, mock_driver, event_bus):
        """Test is_available returns True when driver is present."""
        svc = FluidicsService(mock_driver, event_bus)
        assert svc.is_available is True
        svc.shutdown()

    def test_is_available_without_driver(self, event_bus):
        """Test is_available returns False when driver is None."""
        svc = FluidicsService(None, event_bus)
        assert svc.is_available is False
        svc.shutdown()

    def test_is_busy_when_incubating(self, mock_driver, event_bus):
        """Test is_busy returns True during incubation."""
        svc = FluidicsService(mock_driver, event_bus)
        svc._is_incubating = True
        assert svc.is_busy is True
        svc.shutdown()

    def test_is_busy_delegates_to_driver(self, mock_driver, event_bus):
        """Test is_busy checks driver.is_busy."""
        mock_driver.is_busy = True
        svc = FluidicsService(mock_driver, event_bus)
        assert svc.is_busy is True
        svc.shutdown()


class TestFluidicsServiceFlowOperations:
    """Tests for flow operations."""

    def test_flow_solution(self, service, mock_driver):
        """Test flow_solution calls driver and publishes events."""
        result = service.flow_solution(
            port=1, volume_ul=100, flow_rate_ul_per_min=50
        )

        assert result is True
        mock_driver.flow_solution.assert_called_once_with(
            port=1,
            volume_ul=100,
            flow_rate_ul_per_min=50,
            fill_tubing_with_port=None,
        )

    def test_flow_solution_by_name(self, service, mock_driver):
        """Test flow_solution_by_name resolves name to port."""
        result = service.flow_solution_by_name(
            solution_name="test_solution",
            volume_ul=100,
            flow_rate_ul_per_min=50,
        )

        assert result is True
        mock_driver.get_port_for_solution.assert_called_with("test_solution")

    def test_flow_solution_by_name_not_found(self, service, mock_driver):
        """Test flow_solution_by_name raises ValueError for unknown solution."""
        mock_driver.get_port_for_solution.return_value = None

        with pytest.raises(ValueError, match="not found"):
            service.flow_solution_by_name(
                solution_name="unknown",
                volume_ul=100,
                flow_rate_ul_per_min=50,
            )

    def test_flow_solution_busy_raises(self, service, mock_driver):
        """Test flow_solution raises RuntimeError when busy."""
        mock_driver.is_busy = True

        with pytest.raises(RuntimeError, match="busy"):
            service.flow_solution(port=1, volume_ul=100, flow_rate_ul_per_min=50)

    def test_flow_solution_without_driver(self, event_bus):
        """Test flow_solution raises RuntimeError without driver."""
        svc = FluidicsService(None, event_bus)

        with pytest.raises(RuntimeError, match="not available"):
            svc.flow_solution(port=1, volume_ul=100, flow_rate_ul_per_min=50)

        svc.shutdown()


class TestFluidicsServiceOperations:
    """Tests for prime, wash, empty_syringe."""

    def test_prime(self, service, mock_driver):
        """Test prime calls driver."""
        result = service.prime(
            ports=[1, 2], volume_ul=500, flow_rate_ul_per_min=5000, final_port=1
        )

        assert result is True
        mock_driver.prime.assert_called_once()

    def test_prime_defaults(self, service, mock_driver):
        """Test prime uses defaults for optional parameters."""
        result = service.prime()

        assert result is True
        # Should use get_available_ports() for default ports
        mock_driver.get_available_ports.assert_called()

    def test_wash(self, service, mock_driver):
        """Test wash calls driver."""
        result = service.wash(
            wash_solution="wash_buffer",
            volume_ul=500,
            flow_rate_ul_per_min=5000,
            repeats=3,
        )

        assert result is True
        mock_driver.wash.assert_called_once()

    def test_wash_solution_not_found(self, service, mock_driver):
        """Test wash raises ValueError for unknown solution."""
        mock_driver.get_port_for_solution.return_value = None

        with pytest.raises(ValueError, match="not found"):
            service.wash(
                wash_solution="unknown",
                volume_ul=500,
                flow_rate_ul_per_min=5000,
            )

    def test_empty_syringe(self, service, mock_driver):
        """Test empty_syringe calls driver."""
        result = service.empty_syringe()

        assert result is True
        mock_driver.empty_syringe.assert_called_once()


class TestFluidicsServiceIncubation:
    """Tests for incubation."""

    def test_incubate_short_duration(self, service):
        """Test short incubation completes."""
        result = service.incubate(duration_seconds=0.1, progress_interval=0.05)
        assert result is True

    def test_incubate_abort(self, service):
        """Test incubation can be aborted."""
        # Start incubation in background
        result_holder = [None]

        def run_incubation():
            result_holder[0] = service.incubate(
                duration_seconds=10.0, progress_interval=0.1
            )

        thread = threading.Thread(target=run_incubation)
        thread.start()

        # Wait briefly then abort
        time.sleep(0.2)
        service.abort()

        thread.join(timeout=1.0)
        assert result_holder[0] is False

    def test_incubate_sets_is_incubating(self, service):
        """Test incubation sets _is_incubating flag."""
        flag_seen = [False]

        def check_flag():
            time.sleep(0.05)
            if service._is_incubating:
                flag_seen[0] = True

        thread = threading.Thread(target=check_flag)
        thread.start()

        service.incubate(duration_seconds=0.2)
        thread.join()

        assert flag_seen[0] is True


class TestFluidicsServiceControl:
    """Tests for abort/reset."""

    def test_abort(self, service, mock_driver):
        """Test abort calls driver.abort()."""
        service.abort()
        mock_driver.abort.assert_called_once()

    def test_reset_abort(self, service, mock_driver):
        """Test reset_abort calls driver.reset_abort()."""
        service.reset_abort()
        mock_driver.reset_abort.assert_called_once()


class TestFluidicsServiceQuery:
    """Tests for query methods."""

    def test_get_status(self, service, mock_driver):
        """Test get_status returns driver status."""
        status = service.get_status()
        assert status is not None
        assert status.status == FluidicsOperationStatus.IDLE

    def test_get_port_for_solution(self, service, mock_driver):
        """Test get_port_for_solution delegates to driver."""
        port = service.get_port_for_solution("test_solution")
        assert port == 1

    def test_get_available_solutions(self, service, mock_driver):
        """Test get_available_solutions builds mapping."""
        solutions = service.get_available_solutions()
        assert isinstance(solutions, dict)
        # Driver mock returns "test_solution" for all ports
        assert len(solutions) > 0

    def test_get_available_ports(self, service, mock_driver):
        """Test get_available_ports delegates to driver."""
        ports = service.get_available_ports()
        assert ports == [1, 2, 3]


class TestFluidicsServiceShutdown:
    """Tests for shutdown."""

    def test_shutdown_closes_driver(self, mock_driver, event_bus):
        """Test shutdown calls driver.close()."""
        svc = FluidicsService(mock_driver, event_bus)
        svc.shutdown()
        mock_driver.close.assert_called_once()

    def test_shutdown_without_driver(self, event_bus):
        """Test shutdown works without driver."""
        svc = FluidicsService(None, event_bus)
        svc.shutdown()  # Should not raise
