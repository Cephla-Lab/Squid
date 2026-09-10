"""Marshals ProtocolRunner events (emitted on the runner's thread) onto the GUI thread."""

from qtpy.QtCore import QObject, Signal


class RunnerEventBridge(QObject):
    event_received = Signal(object)  # a control.core.fluidics_protocol.events.RunnerEvent

    def listener(self, event) -> None:
        """Safe from any thread: only copies the event into a queued Qt signal."""
        self.event_received.emit(event)
