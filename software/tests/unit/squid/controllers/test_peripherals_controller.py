"""Tests for PeripheralsController."""

from unittest.mock import MagicMock
import pytest

from squid.core.events import (
    EventBus,
    SetObjectiveCommand,
    SetSpinningDiskPositionCommand,
    SetSpinningDiskSpinningCommand,
    SetDiskDichroicCommand,
    SetDiskEmissionFilterCommand,
    SetPiezoPositionCommand,
    MovePiezoRelativeCommand,
    ObjectiveChanged,
    SpinningDiskStateChanged,
    PiezoPositionChanged,
    PixelSizeChanged,
)
from squid.backend.controllers.peripherals_controller import PeripheralsController


class MockObjectiveInfo:
    """Mock objective info."""

    def __init__(self, index: int, name: str, pixel_size_um: float):
        self.index = index
        self.name = name
        self.pixel_size_um = pixel_size_um
        self.magnification = 20.0
        self.na = 0.75


class TestPeripheralsController:
    """Test suite for PeripheralsController."""

    @pytest.fixture
    def event_bus(self):
        return EventBus()

    @pytest.fixture
    def mock_objective_service(self):
        service = MagicMock()
        service.get_current_position.return_value = 0
        service.get_objective_info.return_value = MockObjectiveInfo(0, "20x", 0.325)
        return service

    @pytest.fixture
    def mock_spinning_disk_service(self):
        service = MagicMock()
        service.is_available.return_value = True
        service.is_disk_in.return_value = False
        service.is_spinning.return_value = False
        service.motor_speed.return_value = 0
        service.current_dichroic.return_value = 0
        service.current_emission_filter.return_value = 0
        return service

    @pytest.fixture
    def mock_piezo_service(self):
        service = MagicMock()
        service.get_position.return_value = 50.0
        service.get_range.return_value = (0.0, 100.0)
        return service

    @pytest.fixture
    def mock_objective_store(self):
        store = MagicMock()
        return store

    # --- Initialization Tests ---

    def test_initial_state_with_all_hardware(
        self,
        event_bus,
        mock_objective_service,
        mock_spinning_disk_service,
        mock_piezo_service,
        mock_objective_store,
    ):
        """Initial state should read from hardware."""
        controller = PeripheralsController(
            objective_service=mock_objective_service,
            spinning_disk_service=mock_spinning_disk_service,
            piezo_service=mock_piezo_service,
            objective_store=mock_objective_store,
            event_bus=event_bus,
        )

        assert controller.state.objective_position == 0
        assert controller.state.objective_name == "20x"
        assert controller.state.pixel_size_um == 0.325
        assert controller.state.spinning_disk is not None
        assert controller.state.piezo_position_um == 50.0

    def test_initial_state_without_hardware(self, event_bus):
        """Initial state should handle missing hardware gracefully."""
        controller = PeripheralsController(
            objective_service=None,
            spinning_disk_service=None,
            piezo_service=None,
            objective_store=None,
            event_bus=event_bus,
        )

        assert controller.state.objective_position is None
        assert controller.state.spinning_disk is None
        assert controller.state.piezo_position_um is None

    # --- Objective Tests ---

    def test_handles_set_objective_command(
        self, event_bus, mock_objective_service, mock_objective_store
    ):
        """Controller should handle SetObjectiveCommand."""
        # Update mock for position 1
        def get_info_side_effect(pos):
            return MockObjectiveInfo(pos, f"{pos}0x", 0.5 if pos == 1 else 0.325)

        mock_objective_service.get_objective_info.side_effect = get_info_side_effect
        mock_objective_service.get_current_position.side_effect = [0, 1]  # Initial read, then after set

        controller = PeripheralsController(
            objective_service=mock_objective_service,
            spinning_disk_service=None,
            piezo_service=None,
            objective_store=mock_objective_store,
            event_bus=event_bus,
        )

        events_received = []
        event_bus.subscribe(ObjectiveChanged, events_received.append)

        event_bus.publish(SetObjectiveCommand(position=1))
        event_bus.drain()

        mock_objective_service.set_position.assert_called_with(1)
        assert len(events_received) == 1
        assert events_received[0].position == 1

    def test_set_objective_publishes_pixel_size_changed(
        self, event_bus, mock_objective_service, mock_objective_store
    ):
        """Setting objective should publish PixelSizeChanged."""
        mock_objective_service.get_current_position.return_value = 0

        controller = PeripheralsController(
            objective_service=mock_objective_service,
            spinning_disk_service=None,
            piezo_service=None,
            objective_store=mock_objective_store,
            event_bus=event_bus,
        )

        pixel_events = []
        event_bus.subscribe(PixelSizeChanged, pixel_events.append)

        event_bus.publish(SetObjectiveCommand(position=1))
        event_bus.drain()

        assert len(pixel_events) == 1
        assert pixel_events[0].pixel_size_um == 0.325

    # --- Spinning Disk Tests ---

    def test_handles_set_disk_position_command(self, event_bus, mock_spinning_disk_service):
        """Controller should handle SetSpinningDiskPositionCommand."""
        controller = PeripheralsController(
            objective_service=None,
            spinning_disk_service=mock_spinning_disk_service,
            piezo_service=None,
            objective_store=None,
            event_bus=event_bus,
        )

        events_received = []
        event_bus.subscribe(SpinningDiskStateChanged, events_received.append)

        event_bus.publish(SetSpinningDiskPositionCommand(in_beam=True))
        event_bus.drain()

        mock_spinning_disk_service.set_disk_position.assert_called_with(True)
        assert len(events_received) == 1

    def test_handles_set_spinning_command(self, event_bus, mock_spinning_disk_service):
        """Controller should handle SetSpinningDiskSpinningCommand."""
        controller = PeripheralsController(
            objective_service=None,
            spinning_disk_service=mock_spinning_disk_service,
            piezo_service=None,
            objective_store=None,
            event_bus=event_bus,
        )

        event_bus.publish(SetSpinningDiskSpinningCommand(spinning=True))
        event_bus.drain()

        mock_spinning_disk_service.set_spinning.assert_called_with(True)

    def test_handles_set_dichroic_command(self, event_bus, mock_spinning_disk_service):
        """Controller should handle SetDiskDichroicCommand."""
        controller = PeripheralsController(
            objective_service=None,
            spinning_disk_service=mock_spinning_disk_service,
            piezo_service=None,
            objective_store=None,
            event_bus=event_bus,
        )

        event_bus.publish(SetDiskDichroicCommand(position=2))
        event_bus.drain()

        mock_spinning_disk_service.set_dichroic.assert_called_with(2)

    def test_handles_set_emission_filter_command(self, event_bus, mock_spinning_disk_service):
        """Controller should handle SetDiskEmissionFilterCommand."""
        controller = PeripheralsController(
            objective_service=None,
            spinning_disk_service=mock_spinning_disk_service,
            piezo_service=None,
            objective_store=None,
            event_bus=event_bus,
        )

        event_bus.publish(SetDiskEmissionFilterCommand(position=3))
        event_bus.drain()

        mock_spinning_disk_service.set_emission_filter.assert_called_with(3)

    # --- Piezo Tests ---

    def test_handles_set_piezo_command(self, event_bus, mock_piezo_service):
        """Controller should handle SetPiezoPositionCommand."""
        controller = PeripheralsController(
            objective_service=None,
            spinning_disk_service=None,
            piezo_service=mock_piezo_service,
            objective_store=None,
            event_bus=event_bus,
        )

        events_received = []
        event_bus.subscribe(PiezoPositionChanged, events_received.append)

        event_bus.publish(SetPiezoPositionCommand(position_um=75.0))
        event_bus.drain()

        mock_piezo_service.move_to.assert_called_with(75.0)
        assert len(events_received) == 1

    def test_piezo_position_clamped_to_range(self, event_bus, mock_piezo_service):
        """Piezo position should be clamped to valid range."""
        controller = PeripheralsController(
            objective_service=None,
            spinning_disk_service=None,
            piezo_service=mock_piezo_service,
            objective_store=None,
            event_bus=event_bus,
        )

        # Request position beyond max (100.0)
        event_bus.publish(SetPiezoPositionCommand(position_um=150.0))
        event_bus.drain()

        mock_piezo_service.move_to.assert_called_with(100.0)

    def test_handles_move_piezo_relative_command(self, event_bus, mock_piezo_service):
        """Controller should handle MovePiezoRelativeCommand."""
        controller = PeripheralsController(
            objective_service=None,
            spinning_disk_service=None,
            piezo_service=mock_piezo_service,
            objective_store=None,
            event_bus=event_bus,
        )

        events_received = []
        event_bus.subscribe(PiezoPositionChanged, events_received.append)

        event_bus.publish(MovePiezoRelativeCommand(delta_um=10.0))
        event_bus.drain()

        mock_piezo_service.move_relative.assert_called_with(10.0)
        assert len(events_received) == 1

    # --- Convenience Method Tests ---

    def test_has_hardware_methods(
        self,
        event_bus,
        mock_objective_service,
        mock_spinning_disk_service,
        mock_piezo_service,
    ):
        """Convenience methods should report hardware availability."""
        controller = PeripheralsController(
            objective_service=mock_objective_service,
            spinning_disk_service=mock_spinning_disk_service,
            piezo_service=mock_piezo_service,
            objective_store=None,
            event_bus=event_bus,
        )

        assert controller.has_objective_changer() is True
        assert controller.has_spinning_disk() is True
        assert controller.has_piezo() is True

    def test_has_hardware_methods_without_hardware(self, event_bus):
        """Convenience methods should report missing hardware."""
        controller = PeripheralsController(
            objective_service=None,
            spinning_disk_service=None,
            piezo_service=None,
            objective_store=None,
            event_bus=event_bus,
        )

        assert controller.has_objective_changer() is False
        assert controller.has_spinning_disk() is False
        assert controller.has_piezo() is False
