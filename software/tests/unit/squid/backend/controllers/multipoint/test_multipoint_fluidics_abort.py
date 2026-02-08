"""
Tests for MultiPointController fluidics abort on stop.

Verifies that request_abort_aquisition() calls fluidics_service.abort()
to stop fluidics operations when acquisition is stopped.
"""

import pytest
from unittest.mock import MagicMock, patch

from squid.core.events import EventBus
from squid.backend.controllers.multipoint.multi_point_controller import (
    MultiPointController,
    AcquisitionControllerState,
)


@pytest.fixture
def event_bus():
    return EventBus()


@pytest.fixture
def mock_fluidics_service():
    """Create a mock FluidicsService."""
    service = MagicMock()
    service.is_available = True
    return service


@pytest.fixture
def controller(event_bus, mock_fluidics_service):
    """Create a MultiPointController with all required mocked dependencies."""
    live_controller = MagicMock()
    autofocus_controller = MagicMock()
    objective_store = MagicMock()
    channel_config_manager = MagicMock()
    camera_service = MagicMock()
    stage_service = MagicMock()
    peripheral_service = MagicMock()

    ctrl = MultiPointController(
        live_controller=live_controller,
        autofocus_controller=autofocus_controller,
        objective_store=objective_store,
        channel_configuration_manager=channel_config_manager,
        camera_service=camera_service,
        stage_service=stage_service,
        peripheral_service=peripheral_service,
        event_bus=event_bus,
        fluidics_service=mock_fluidics_service,
    )
    return ctrl


class TestMultiPointFluidicsAbort:
    """Test that stopping acquisition also stops fluidics."""

    def test_abort_calls_fluidics_abort(self, controller, mock_fluidics_service):
        """request_abort_aquisition should call fluidics_service.abort()."""
        # Set up state so abort is allowed
        controller._state = AcquisitionControllerState.RUNNING
        controller.experiment_ID = "test_experiment"
        controller.multiPointWorker = MagicMock()

        controller.request_abort_aquisition()

        # Verify fluidics abort was called
        mock_fluidics_service.abort.assert_called_once()

    def test_abort_without_fluidics_service(self, event_bus):
        """request_abort_aquisition should work without fluidics service."""
        live_controller = MagicMock()
        autofocus_controller = MagicMock()
        objective_store = MagicMock()
        channel_config_manager = MagicMock()
        camera_service = MagicMock()
        stage_service = MagicMock()
        peripheral_service = MagicMock()

        ctrl = MultiPointController(
            live_controller=live_controller,
            autofocus_controller=autofocus_controller,
            objective_store=objective_store,
            channel_configuration_manager=channel_config_manager,
            camera_service=camera_service,
            stage_service=stage_service,
            peripheral_service=peripheral_service,
            event_bus=event_bus,
            fluidics_service=None,
        )

        ctrl._state = AcquisitionControllerState.RUNNING
        ctrl.experiment_ID = "test_experiment"
        ctrl.multiPointWorker = MagicMock()

        # Should not raise
        ctrl.request_abort_aquisition()

    def test_abort_not_running_does_not_stop_fluidics(self, controller, mock_fluidics_service):
        """request_abort_aquisition in non-RUNNING state should not stop fluidics."""
        # Controller starts in IDLE state
        assert controller.state == AcquisitionControllerState.IDLE

        controller.request_abort_aquisition()

        # Fluidics abort should NOT be called when not running
        mock_fluidics_service.abort.assert_not_called()
