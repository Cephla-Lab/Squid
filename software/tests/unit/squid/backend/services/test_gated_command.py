"""Tests for the @gated_command decorator."""

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

from squid.backend.services.base import BaseService, gated_command
from squid.core.events import Event, EventBus
from squid.core.mode_gate import GlobalModeGate


@dataclass
class SampleCommand(Event):
    """Sample command event for testing."""

    value: int


class SampleService(BaseService):
    """Test service implementation."""

    def __init__(self, event_bus: EventBus, mode_gate: GlobalModeGate = None):
        super().__init__(event_bus, mode_gate)
        self.handler_calls = []
        self.subscribe(SampleCommand, self._on_sample_command)

    @gated_command
    def _on_sample_command(self, cmd: SampleCommand) -> int:
        """Handler that returns a value for testing."""
        self.handler_calls.append(cmd)
        return cmd.value * 2


class TestGatedCommandDecorator:
    """Tests for the @gated_command decorator."""

    def test_handler_called_when_not_blocked(self):
        """Handler executes normally when mode gate is not active."""
        event_bus = EventBus()
        mode_gate = MagicMock(spec=GlobalModeGate)
        mode_gate.blocked_for_ui_hardware_commands.return_value = False

        service = SampleService(event_bus, mode_gate)
        cmd = SampleCommand(value=21)

        result = service._on_sample_command(cmd)

        assert result == 42
        assert len(service.handler_calls) == 1
        assert service.handler_calls[0].value == 21

    def test_handler_skipped_when_blocked(self):
        """Handler is skipped when mode gate is active."""
        event_bus = EventBus()
        mode_gate = MagicMock(spec=GlobalModeGate)
        mode_gate.blocked_for_ui_hardware_commands.return_value = True

        service = SampleService(event_bus, mode_gate)
        cmd = SampleCommand(value=21)

        result = service._on_sample_command(cmd)

        assert result is None
        assert len(service.handler_calls) == 0

    def test_returns_none_when_blocked(self):
        """Decorated method returns None when blocked."""
        event_bus = EventBus()
        mode_gate = MagicMock(spec=GlobalModeGate)
        mode_gate.blocked_for_ui_hardware_commands.return_value = True

        service = SampleService(event_bus, mode_gate)
        cmd = SampleCommand(value=100)

        result = service._on_sample_command(cmd)

        assert result is None

    def test_logs_debug_when_blocked(self):
        """Debug message is logged when handler is blocked."""
        event_bus = EventBus()
        mode_gate = MagicMock(spec=GlobalModeGate)
        mode_gate.blocked_for_ui_hardware_commands.return_value = True

        service = SampleService(event_bus, mode_gate)
        cmd = SampleCommand(value=21)

        with patch.object(service._log, "debug") as mock_debug:
            service._on_sample_command(cmd)
            mock_debug.assert_called_once()
            call_args = mock_debug.call_args[0]
            assert "mode gate" in call_args[0].lower()
            assert "SampleCommand" in str(call_args)

    def test_functools_wraps_preserves_metadata(self):
        """functools.wraps preserves method name and docstring."""

        class TestService(BaseService):
            @gated_command
            def my_handler(self, event: Event) -> None:
                """Handler docstring."""
                pass

        assert TestService.my_handler.__name__ == "my_handler"
        assert "Handler docstring" in (TestService.my_handler.__doc__ or "")

    def test_handler_works_without_mode_gate(self):
        """Handler works normally when no mode gate is provided."""
        event_bus = EventBus()
        # No mode gate provided
        service = SampleService(event_bus, mode_gate=None)
        cmd = SampleCommand(value=10)

        result = service._on_sample_command(cmd)

        assert result == 20
        assert len(service.handler_calls) == 1

    def test_on_blocked_hook_runs(self):
        """Blocked handler hook executes when mode gate is active."""

        class HookedService(BaseService):
            def __init__(self, event_bus: EventBus, mode_gate: GlobalModeGate = None):
                super().__init__(event_bus, mode_gate)
                self.blocked_calls = []

            def _on_blocked(self, cmd: SampleCommand) -> None:
                self.blocked_calls.append(cmd.value)

            @gated_command(on_blocked=_on_blocked)
            def _on_sample_command(self, cmd: SampleCommand) -> int:
                return cmd.value * 3

        event_bus = EventBus()
        mode_gate = MagicMock(spec=GlobalModeGate)
        mode_gate.blocked_for_ui_hardware_commands.return_value = True

        service = HookedService(event_bus, mode_gate)
        cmd = SampleCommand(value=7)

        result = service._on_sample_command(cmd)

        assert result is None
        assert service.blocked_calls == [7]

    def test_handler_receives_correct_event(self):
        """Handler receives the correct event object when not blocked."""
        event_bus = EventBus()
        mode_gate = MagicMock(spec=GlobalModeGate)
        mode_gate.blocked_for_ui_hardware_commands.return_value = False

        service = SampleService(event_bus, mode_gate)
        cmd1 = SampleCommand(value=1)
        cmd2 = SampleCommand(value=2)

        service._on_sample_command(cmd1)
        service._on_sample_command(cmd2)

        assert len(service.handler_calls) == 2
        assert service.handler_calls[0].value == 1
        assert service.handler_calls[1].value == 2

    def test_via_event_bus_dispatch(self):
        """Handler works correctly when called via event bus dispatch."""
        event_bus = EventBus()
        mode_gate = MagicMock(spec=GlobalModeGate)
        mode_gate.blocked_for_ui_hardware_commands.return_value = False

        service = SampleService(event_bus, mode_gate)
        cmd = SampleCommand(value=5)

        # Dispatch via event bus (not using the queue for simpler test)
        event_bus._dispatch(cmd)

        assert len(service.handler_calls) == 1
        assert service.handler_calls[0].value == 5

    def test_blocked_via_event_bus_dispatch(self):
        """Handler is blocked when dispatched via event bus with active gate."""
        event_bus = EventBus()
        mode_gate = MagicMock(spec=GlobalModeGate)
        mode_gate.blocked_for_ui_hardware_commands.return_value = True

        service = SampleService(event_bus, mode_gate)
        cmd = SampleCommand(value=5)

        # Dispatch via event bus
        event_bus._dispatch(cmd)

        # Handler should not have been called
        assert len(service.handler_calls) == 0
