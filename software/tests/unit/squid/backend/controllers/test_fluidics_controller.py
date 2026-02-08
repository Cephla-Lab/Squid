"""
Tests for FluidicsController bug fixes.

Tests ValueError handling and solution validation.
"""

import pytest
from unittest.mock import MagicMock, patch

from squid.core.events import EventBus
from squid.core.protocol.fluidics_protocol import (
    FluidicsProtocol,
    FluidicsProtocolStep,
    FluidicsCommand,
)
from squid.backend.controllers.fluidics_controller import (
    FluidicsController,
    FluidicsControllerState,
)


@pytest.fixture
def event_bus():
    return EventBus()


@pytest.fixture
def mock_service():
    """Create a mock FluidicsService."""
    service = MagicMock()
    service.is_available = True
    service.get_available_solutions.return_value = {
        "wash_buffer": 1,
        "probe_mix": 2,
        "SSC": 3,
    }
    service.reset_abort.return_value = None
    return service


@pytest.fixture
def controller(event_bus, mock_service):
    """Create a FluidicsController with a mock service."""
    ctrl = FluidicsController(
        event_bus=event_bus,
        fluidics_service=mock_service,
    )
    return ctrl


class TestExecuteFlowCatchesValueError:
    """Test that _execute_flow catches ValueError from service."""

    def test_value_error_returns_false(self, controller, mock_service):
        """When flow_solution_by_name raises ValueError, step should return False (not exception)."""
        mock_service.flow_solution_by_name.side_effect = ValueError(
            "Solution 'unknown' not found. Available: ['wash_buffer', 'probe_mix']"
        )

        step = FluidicsProtocolStep(
            operation=FluidicsCommand.FLOW,
            solution="unknown",
            volume_ul=500,
            flow_rate_ul_per_min=100,
        )

        result = controller._execute_flow(step)
        assert result is False

    def test_runtime_error_returns_false(self, controller, mock_service):
        """When flow_solution_by_name raises RuntimeError, step should return False."""
        mock_service.flow_solution_by_name.side_effect = RuntimeError("Fluidics busy")

        step = FluidicsProtocolStep(
            operation=FluidicsCommand.FLOW,
            solution="wash_buffer",
            volume_ul=500,
            flow_rate_ul_per_min=100,
        )

        result = controller._execute_flow(step)
        assert result is False


class TestExecuteWashCatchesValueError:
    """Test that _execute_wash catches ValueError from service."""

    def test_wash_value_error_returns_false(self, controller, mock_service):
        """When wash raises ValueError, step should return False."""
        mock_service.wash.side_effect = ValueError(
            "Wash solution 'unknown' not found"
        )

        step = FluidicsProtocolStep(
            operation=FluidicsCommand.WASH,
            solution="unknown",
            volume_ul=500,
            flow_rate_ul_per_min=100,
        )

        result = controller._execute_wash(step)
        assert result is False


class TestExecutePrimeCatchesValueError:
    """Test that _execute_prime catches ValueError from service."""

    def test_prime_value_error_returns_false(self, controller, mock_service):
        """When prime raises ValueError, step should return False."""
        mock_service.get_port_for_solution.return_value = 1
        mock_service.prime.side_effect = ValueError("Prime failed")

        step = FluidicsProtocolStep(
            operation=FluidicsCommand.PRIME,
            solution="wash_buffer",
            volume_ul=500,
            flow_rate_ul_per_min=5000,
        )

        result = controller._execute_prime(step)
        assert result is False


class TestValidateProtocolSolutions:
    """Test solution validation against available hardware."""

    def test_finds_missing_solutions(self, controller, mock_service):
        """Validation should report solutions not available in hardware."""
        protocol = FluidicsProtocol(
            steps=[
                FluidicsProtocolStep(
                    operation=FluidicsCommand.FLOW,
                    solution="wash_buffer",
                    volume_ul=500,
                    flow_rate_ul_per_min=100,
                ),
                FluidicsProtocolStep(
                    operation=FluidicsCommand.FLOW,
                    solution="nonexistent_solution",
                    volume_ul=500,
                    flow_rate_ul_per_min=100,
                ),
                FluidicsProtocolStep(
                    operation=FluidicsCommand.INCUBATE,
                    duration_s=60,
                ),
            ]
        )
        controller.add_protocol("test_protocol", protocol)

        warnings = controller.validate_protocol_solutions()

        assert "test_protocol" in warnings
        assert "nonexistent_solution" in warnings["test_protocol"]

    def test_all_valid_returns_empty(self, controller, mock_service):
        """Validation should return empty dict when all solutions are valid."""
        protocol = FluidicsProtocol(
            steps=[
                FluidicsProtocolStep(
                    operation=FluidicsCommand.FLOW,
                    solution="wash_buffer",
                    volume_ul=500,
                    flow_rate_ul_per_min=100,
                ),
                FluidicsProtocolStep(
                    operation=FluidicsCommand.FLOW,
                    solution="probe_mix",
                    volume_ul=500,
                    flow_rate_ul_per_min=100,
                ),
            ]
        )
        controller.add_protocol("valid_protocol", protocol)

        warnings = controller.validate_protocol_solutions()

        assert warnings == {}

    def test_no_service_returns_empty(self, event_bus):
        """Validation should return empty dict when no service is available."""
        controller = FluidicsController(
            event_bus=event_bus,
            fluidics_service=None,
        )
        protocol = FluidicsProtocol(
            steps=[
                FluidicsProtocolStep(
                    operation=FluidicsCommand.FLOW,
                    solution="anything",
                    volume_ul=500,
                    flow_rate_ul_per_min=100,
                ),
            ]
        )
        controller.add_protocol("test", protocol)

        warnings = controller.validate_protocol_solutions()
        assert warnings == {}

    def test_steps_without_solution_ignored(self, controller, mock_service):
        """Steps without solution (like incubate/empty) should not trigger warnings."""
        protocol = FluidicsProtocol(
            steps=[
                FluidicsProtocolStep(
                    operation=FluidicsCommand.INCUBATE,
                    duration_s=60,
                ),
                FluidicsProtocolStep(
                    operation=FluidicsCommand.EMPTY,
                ),
            ]
        )
        controller.add_protocol("no_solution_protocol", protocol)

        warnings = controller.validate_protocol_solutions()
        assert warnings == {}
