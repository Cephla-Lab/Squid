"""Command Router for bridging EventBus to BackendActor.

The BackendCommandRouter subscribes to command events on the EventBus
and routes them to the BackendActor's priority queue. This allows
commands published from any thread (including the GUI thread) to be
processed on the backend actor thread.
"""

from __future__ import annotations

from typing import Dict, List, Type

import squid.core.logging
from squid.core.events import Event, EventBus
from squid.core.actor.backend_actor import BackendActor, Priority

_log = squid.core.logging.get_logger(__name__)


# Keywords in command names that indicate high priority
# Stop/abort commands should always be processed ASAP
HIGH_PRIORITY_KEYWORDS = (
    "Stop",
    "Abort",
    "Cancel",
)


class BackendCommandRouter:
    """Routes commands from EventBus to BackendActor.

    The router subscribes to command events and enqueues them to the
    BackendActor with appropriate priority. This decouples command
    publishers (widgets, etc.) from the backend processing thread.

    Usage:
        router = BackendCommandRouter(event_bus, backend_actor)
        router.register_commands([
            StartLiveCommand,
            StopLiveCommand,
            MoveStageCommand,
        ])

        # Now any publish to event_bus will be routed to backend_actor
        event_bus.publish(StartLiveCommand(...))
    """

    def __init__(self, event_bus: EventBus, backend_actor: BackendActor):
        """Initialize the router.

        Args:
            event_bus: The EventBus to subscribe to
            backend_actor: The BackendActor to route commands to
        """
        self._event_bus = event_bus
        self._backend_actor = backend_actor
        self._registered_commands: List[Type[Event]] = []
        self._handlers: Dict[Type[Event], callable] = {}

    def register_commands(self, command_types: List[Type[Event]]) -> None:
        """Register command types to route to the backend.

        Args:
            command_types: List of command classes to subscribe to
        """
        for cmd_type in command_types:
            self.register_command(cmd_type)

    def register_command(self, command_type: Type[Event]) -> None:
        """Register a single command type to route.

        Args:
            command_type: The command class to subscribe to
        """
        if command_type in self._registered_commands:
            _log.warning(f"Command {command_type.__name__} already registered")
            return

        # Create a handler that routes to backend actor
        def make_handler(cmd_type: Type[Event]):
            def handler(command: Event) -> None:
                self._route(command)
            return handler

        handler = make_handler(command_type)
        self._handlers[command_type] = handler
        self._event_bus.subscribe(command_type, handler)
        self._registered_commands.append(command_type)
        _log.debug(f"Registered routing for {command_type.__name__}")

    def unregister_command(self, command_type: Type[Event]) -> None:
        """Unregister a command type.

        Args:
            command_type: The command class to unsubscribe
        """
        if command_type not in self._registered_commands:
            return

        handler = self._handlers.pop(command_type, None)
        if handler:
            self._event_bus.unsubscribe(command_type, handler)
        self._registered_commands.remove(command_type)
        _log.debug(f"Unregistered routing for {command_type.__name__}")

    def unregister_all(self) -> None:
        """Unregister all command types."""
        for cmd_type in list(self._registered_commands):
            self.unregister_command(cmd_type)

    def _route(self, command: Event) -> None:
        """Route a command to the backend actor.

        Args:
            command: The command event to route
        """
        priority = self._get_priority(command)
        self._backend_actor.enqueue(command, priority)

    def _get_priority(self, command: Event) -> int:
        """Determine the priority for a command.

        Stop/abort commands get highest priority to ensure they're
        processed before other pending commands.

        Args:
            command: The command to prioritize

        Returns:
            Priority level
        """
        command_name = type(command).__name__

        # Commands with stop/abort/cancel keywords get highest priority
        for keyword in HIGH_PRIORITY_KEYWORDS:
            if keyword in command_name:
                return Priority.STOP

        return Priority.NORMAL

    @property
    def registered_commands(self) -> List[Type[Event]]:
        """Get list of registered command types."""
        return list(self._registered_commands)
