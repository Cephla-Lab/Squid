"""Focus lock test context for E2E testing.

Provides a lightweight test harness that wires up a FocusLockSimulator
with a real EventBus and simulated laser AF controller / piezo service.
"""

from __future__ import annotations

import math
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Optional, Type

import numpy as np

from squid.backend.controllers.autofocus.focus_lock_simulator import FocusLockSimulator
from squid.backend.controllers.autofocus.laser_auto_focus_controller import LaserAFResult
from squid.core.config.focus_lock import FocusLockConfig
from squid.core.events import (
    Event,
    EventBus,
    FocusLockModeChanged,
    FocusLockStatusChanged,
    FocusLockWarning,
    LaserAFInitialized,
)


class FakePiezoService:
    """Minimal piezo service for testing."""

    def __init__(self, range_um: tuple[float, float] = (0.0, 300.0)) -> None:
        self._position = sum(range_um) / 2.0
        self._range = range_um
        self._lock = threading.Lock()

    def get_position(self) -> float:
        with self._lock:
            return self._position

    def get_range(self) -> tuple[float, float]:
        return self._range

    def move_to(self, position_um: float) -> None:
        with self._lock:
            self._position = max(self._range[0], min(self._range[1], position_um))

    def move_to_fast(self, position_um: float) -> None:
        self.move_to(position_um)

    def move_relative(self, delta_um: float) -> None:
        with self._lock:
            new_pos = self._position + delta_um
            self._position = max(self._range[0], min(self._range[1], new_pos))


class FakeLaserAF:
    """Minimal laser AF controller for focus lock testing."""

    def __init__(self) -> None:
        self.is_initialized = False
        self._displacement_um = 0.0
        self._spot_snr = 12.0
        self._spot_intensity = 200.0
        self._correlation = 0.95
        self._signal_present = True
        self._should_fail = False
        self._lock = threading.Lock()
        self.laser_af_properties = _FakeLaserAFProps()

    def measure_displacement_continuous(self) -> LaserAFResult:
        with self._lock:
            if self._should_fail:
                raise RuntimeError("Simulated measurement failure")
            spot_x_px = 100.0 if self._signal_present else None
            spot_y_px = 50.0 if self._signal_present else None
            displacement_um = self._displacement_um if self._signal_present else math.nan
            return LaserAFResult(
                displacement_um=displacement_um,
                spot_intensity=self._spot_intensity,
                spot_snr=self._spot_snr,
                correlation=self._correlation,
                spot_x_px=spot_x_px,
                spot_y_px=spot_y_px,
                timestamp=time.monotonic(),
                image=np.zeros((64, 256), dtype=np.uint8),
            )

    def set_displacement(self, um: float) -> None:
        with self._lock:
            self._displacement_um = um

    def set_snr(self, snr: float) -> None:
        with self._lock:
            self._spot_snr = snr

    def set_should_fail(self, fail: bool) -> None:
        with self._lock:
            self._should_fail = fail

    def set_signal_present(self, present: bool) -> None:
        with self._lock:
            self._signal_present = present

    def turn_on_laser(self, bypass_mode_gate: bool = False) -> None:
        pass

    def turn_off_laser(self, bypass_mode_gate: bool = False) -> None:
        pass


class _FakeLaserAFProps:
    has_reference = True
    x_reference = 100.0
    correlation_threshold = 0.5


@dataclass
class EventCollector:
    """Collects events by type for assertions."""

    _events: dict[type, list[Any]] = field(default_factory=lambda: defaultdict(list))
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def handler_for(self, event_type: Type[Event]):
        def _handler(event: Event) -> None:
            with self._lock:
                self._events[event_type].append(event)

        return _handler

    def get(self, event_type: Type[Event]) -> list:
        with self._lock:
            return list(self._events.get(event_type, []))

    def clear(self) -> None:
        with self._lock:
            self._events.clear()

    def wait_for(
        self,
        event_type: Type[Event],
        predicate=None,
        timeout_s: float = 5.0,
    ) -> Optional[Event]:
        """Wait for an event matching the predicate."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            events = self.get(event_type)
            for e in events:
                if predicate is None or predicate(e):
                    return e
            time.sleep(0.02)
        return None


class FocusLockTestContext:
    """Context manager for focus lock E2E tests.

    Usage::

        with FocusLockTestContext() as ctx:
            ctx.initialize()
            ctx.simulator.start()
            ctx.wait_for_status("ready")
    """

    def __init__(self, config: Optional[FocusLockConfig] = None) -> None:
        self.config = config or FocusLockConfig(
            loop_rate_hz=50,
            metrics_rate_hz=50,
            buffer_length=3,
            recovery_delay_s=0.05,
            recovery_attempts=2,
            recovery_window_readings=2,
        )
        self.event_bus = EventBus()
        self.collector = EventCollector()
        self.piezo = FakePiezoService()
        self.laser_af = FakeLaserAF()
        self.simulator: Optional[FocusLockSimulator] = None
        self._subscriptions: list = []

    def __enter__(self) -> "FocusLockTestContext":
        # Subscribe collector to key events
        for event_type in (FocusLockStatusChanged, FocusLockModeChanged, FocusLockWarning):
            handler = self.collector.handler_for(event_type)
            self.event_bus.subscribe(event_type, handler)
            self._subscriptions.append((event_type, handler))

        self.simulator = FocusLockSimulator(
            event_bus=self.event_bus,
            config=self.config,
            laser_autofocus=self.laser_af,
            piezo_service=self.piezo,
        )

        return self

    def __exit__(self, *args) -> None:
        if self.simulator is not None:
            self.simulator.shutdown()
        for event_type, handler in self._subscriptions:
            self.event_bus.unsubscribe(event_type, handler)
        self._subscriptions.clear()

    def initialize(self) -> None:
        """Mark laser AF as initialized so focus lock can start."""
        self.laser_af.is_initialized = True
        self.laser_af.laser_af_properties.has_reference = True
        # Publish initialization event
        self.event_bus.publish(LaserAFInitialized(is_initialized=True, success=True))
        self.event_bus.drain()

    def wait_for_status(self, status: str, timeout_s: float = 5.0) -> bool:
        """Wait for a specific focus lock status."""
        result = self.collector.wait_for(
            FocusLockStatusChanged,
            predicate=lambda e: e.status == status,
            timeout_s=timeout_s,
        )
        return result is not None

    def get_statuses(self) -> list[str]:
        """Get all status changes in order."""
        self.event_bus.drain()
        return [e.status for e in self.collector.get(FocusLockStatusChanged)]
