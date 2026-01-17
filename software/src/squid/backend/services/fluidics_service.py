"""
FluidicsService - Thread-safe wrapper for fluidics hardware.

Provides:
- Thread-safe driver operations via RLock
- Event publishing for UI updates
- Incubation with progress events
- Solution name <-> port lookups
- Sequence storage for widget/orchestrator coordination
"""

from __future__ import annotations

import threading
import time
from typing import Optional, TYPE_CHECKING


import squid.core.logging
from squid.backend.services.base import BaseService
from squid.core.events import (
    EventBus,
    FluidicsOperationStarted,
    FluidicsOperationCompleted,
    FluidicsOperationProgress,
    FluidicsPhaseChanged,
    FluidicsIncubationStarted,
    FluidicsIncubationProgress,
    FluidicsIncubationCompleted,
    FluidicsStatusChanged,
)

if TYPE_CHECKING:
    from squid.core.abc import AbstractFluidicsController, FluidicsStatus
    from squid.core.mode_gate import GlobalModeGate
    from squid.core.utils.cancel_token import CancelToken

_log = squid.core.logging.get_logger(__name__)


class FluidicsService(BaseService):
    """Service for fluidics operations.

    Wraps an AbstractFluidicsController driver with:
    - Thread-safe operations via RLock
    - Event publishing (Started/Completed/Status)
    - Incubation with progress updates
    - Solution name lookups

    Thread Safety:
        All driver method calls are protected by self._lock.
        Events are published OUTSIDE the lock to avoid deadlocks.
        Incubation does NOT hold the lock during sleep.

    Note:
        Long-running operations (flow, wash, etc.) hold the lock but run
        in background threads from the UI layer, so the GUI thread is not
        blocked. The lock prevents concurrent fluidics operations.
    """

    def __init__(
        self,
        driver: Optional["AbstractFluidicsController"],
        event_bus: EventBus,
        mode_gate: Optional["GlobalModeGate"] = None,
    ):
        """Initialize the FluidicsService.

        Args:
            driver: AbstractFluidicsController implementation (or None if unavailable)
            event_bus: EventBus for event communication
            mode_gate: Optional mode gate for blocking during acquisition
        """
        super().__init__(event_bus, mode_gate)
        self._driver = driver
        self._lock = threading.RLock()
        self._abort_incubation = threading.Event()
        self._is_incubating = False
        # Set up callbacks if driver supports them
        if driver is not None:
            if hasattr(driver, "set_phase_callback"):
                driver.set_phase_callback(self._on_phase_changed)
            if hasattr(driver, "set_progress_callback"):
                driver.set_progress_callback(self._on_progress_changed)

    def _on_progress_changed(
        self,
        operation: str,
        progress_percent: float,
        elapsed_s: float,
        remaining_s: float,
        syringe_volume_ul: float,
    ) -> None:
        """Callback for driver progress updates.

        Publishes FluidicsOperationProgress events to notify UI of real-time progress.
        """
        self.publish(
            FluidicsOperationProgress(
                operation=operation,
                progress_percent=progress_percent,
                elapsed_seconds=elapsed_s,
                remaining_seconds=remaining_s,
                syringe_volume_ul=syringe_volume_ul,
            )
        )

    def _on_phase_changed(
        self,
        phase: str,
        port: Optional[int],
        solution: Optional[str],
        volume_ul: Optional[float],
        flow_rate_ul_per_min: Optional[float],
    ) -> None:
        """Callback for driver phase change notifications.

        Publishes FluidicsPhaseChanged events to notify UI of detailed state changes.
        """
        self.publish(
            FluidicsPhaseChanged(
                phase=phase,
                port=port,
                solution=solution,
                volume_ul=volume_ul,
                flow_rate_ul_per_min=flow_rate_ul_per_min,
            )
        )

    @property
    def is_available(self) -> bool:
        """Check if fluidics hardware is available.

        Returns:
            True if driver is present, False otherwise.
        """
        return self._driver is not None

    @property
    def is_busy(self) -> bool:
        """Check if fluidics is currently busy.

        Returns:
            True if incubating or driver reports busy, False otherwise.
        """
        if self._is_incubating:
            _log.debug("is_busy: True (incubating)")
            return True
        if self._driver is not None:
            driver_busy = self._driver.is_busy
            if driver_busy:
                _log.debug("is_busy: True (driver busy)")
            return driver_busy
        return False

    def _check_busy(self) -> None:
        """Check if system is busy and raise if so.

        Raises:
            RuntimeError: If system is busy with another operation.
        """
        busy = self.is_busy
        if busy:
            _log.warning(f"_check_busy failed: is_incubating={self._is_incubating}, driver.is_busy={self._driver.is_busy if self._driver else 'N/A'}")
            raise RuntimeError("Fluidics system is busy with another operation")

    def _publish_status(self) -> None:
        """Publish current status as an event."""
        if self._driver is None:
            return

        # Acquire lock for thread-safe driver access
        with self._lock:
            status = self._driver.get_status()

        # Publish event outside lock (events should not hold locks)
        self.publish(
            FluidicsStatusChanged(
                status=status.status.value,
                current_port=status.current_port,
                current_solution=status.current_solution,
                syringe_volume_ul=status.syringe_volume_ul,
                is_busy=status.is_busy,
                error_message=status.error_message,
            )
        )

    def flow_solution(
        self,
        port: int,
        volume_ul: float,
        flow_rate_ul_per_min: float,
        fill_tubing_with_port: Optional[int] = None,
    ) -> bool:
        """Flow solution from specified port.

        Args:
            port: Port number to flow from
            volume_ul: Volume to flow in microliters
            flow_rate_ul_per_min: Flow rate in microliters per minute
            fill_tubing_with_port: Optional port to fill tubing with after flow

        Returns:
            True if successful, False otherwise.

        Raises:
            RuntimeError: If system is busy or driver not available.
        """
        if self._driver is None:
            raise RuntimeError("Fluidics driver not available")

        self._check_busy()

        # Get solution name for event
        solution = self._driver.get_port_name(port)

        # Publish started event before operation
        self.publish(
            FluidicsOperationStarted(
                operation="flow",
                port=port,
                solution=solution,
                volume_ul=volume_ul,
                flow_rate_ul_per_min=flow_rate_ul_per_min,
            )
        )

        start_time = time.monotonic()
        success = False
        error_message: Optional[str] = None

        try:
            with self._lock:
                success = self._driver.flow_solution(
                    port=port,
                    volume_ul=volume_ul,
                    flow_rate_ul_per_min=flow_rate_ul_per_min,
                    fill_tubing_with_port=fill_tubing_with_port,
                )

            if not success:
                with self._lock:
                    status = self._driver.get_status()
                error_message = status.error_message

        except Exception as e:
            _log.exception(f"Error in flow_solution: {e}")
            error_message = str(e)
            success = False

        finally:
            duration = time.monotonic() - start_time
            self.publish(
                FluidicsOperationCompleted(
                    operation="flow",
                    success=success,
                    error_message=error_message,
                    duration_seconds=duration,
                )
            )
            self._publish_status()

        return success

    def flow_solution_by_name(
        self,
        solution_name: str,
        volume_ul: float,
        flow_rate_ul_per_min: float,
        fill_tubing_with: Optional[str] = None,
    ) -> bool:
        """Flow solution by name (case-insensitive).

        Args:
            solution_name: Name of the solution to flow
            volume_ul: Volume to flow in microliters
            flow_rate_ul_per_min: Flow rate in microliters per minute
            fill_tubing_with: Optional solution name to fill tubing with

        Returns:
            True if successful, False otherwise.

        Raises:
            RuntimeError: If driver not available.
            ValueError: If solution name not found.
        """
        if self._driver is None:
            raise RuntimeError("Fluidics driver not available")

        port = self._driver.get_port_for_solution(solution_name)
        if port is None:
            available = self.get_available_solutions()
            raise ValueError(
                f"Solution '{solution_name}' not found. Available: {list(available.keys())}"
            )

        fill_port: Optional[int] = None
        if fill_tubing_with:
            fill_port = self._driver.get_port_for_solution(fill_tubing_with)
            if fill_port is None:
                available = self.get_available_solutions()
                raise ValueError(
                    f"Fill solution '{fill_tubing_with}' not found. Available: {list(available.keys())}"
                )

        return self.flow_solution(
            port=port,
            volume_ul=volume_ul,
            flow_rate_ul_per_min=flow_rate_ul_per_min,
            fill_tubing_with_port=fill_port,
        )

    def prime(
        self,
        ports: Optional[list[int]] = None,
        volume_ul: float = 500.0,
        flow_rate_ul_per_min: float = 5000.0,
        final_port: Optional[int] = None,
    ) -> bool:
        """Prime tubing with solutions from specified ports.

        Args:
            ports: List of ports to prime (defaults to all available)
            volume_ul: Volume per port in microliters
            flow_rate_ul_per_min: Flow rate in microliters per minute
            final_port: Port to leave selected after priming (defaults to first)

        Returns:
            True if successful, False otherwise.

        Raises:
            RuntimeError: If driver not available or system busy.
        """
        if self._driver is None:
            raise RuntimeError("Fluidics driver not available")

        self._check_busy()

        # Default to all available ports
        if ports is None:
            ports = self._driver.get_available_ports()

        if not ports:
            _log.warning("No ports to prime")
            return True

        # Default final port to first in list
        if final_port is None:
            final_port = ports[0]

        self.publish(
            FluidicsOperationStarted(
                operation="prime",
                port=final_port,
                solution=None,
                volume_ul=volume_ul,
                flow_rate_ul_per_min=flow_rate_ul_per_min,
            )
        )

        start_time = time.monotonic()
        success = False
        error_message: Optional[str] = None

        try:
            with self._lock:
                success = self._driver.prime(
                    ports=ports,
                    volume_ul=volume_ul,
                    flow_rate_ul_per_min=flow_rate_ul_per_min,
                    final_port=final_port,
                )

            if not success:
                with self._lock:
                    status = self._driver.get_status()
                error_message = status.error_message

        except Exception as e:
            _log.exception(f"Error in prime: {e}")
            error_message = str(e)
            success = False

        finally:
            duration = time.monotonic() - start_time
            self.publish(
                FluidicsOperationCompleted(
                    operation="prime",
                    success=success,
                    error_message=error_message,
                    duration_seconds=duration,
                )
            )
            self._publish_status()

        return success

    def wash(
        self,
        wash_solution: str,
        volume_ul: float,
        flow_rate_ul_per_min: float,
        repeats: int = 1,
    ) -> bool:
        """Wash with solution from specified port.

        Args:
            wash_solution: Name of wash solution
            volume_ul: Volume per wash cycle in microliters
            flow_rate_ul_per_min: Flow rate in microliters per minute
            repeats: Number of wash cycles

        Returns:
            True if successful, False otherwise.

        Raises:
            RuntimeError: If driver not available or system busy.
            ValueError: If wash solution not found.
        """
        if self._driver is None:
            raise RuntimeError("Fluidics driver not available")

        self._check_busy()

        # Look up wash port
        wash_port = self._driver.get_port_for_solution(wash_solution)
        if wash_port is None:
            available = self.get_available_solutions()
            raise ValueError(
                f"Wash solution '{wash_solution}' not found. Available: {list(available.keys())}"
            )

        self.publish(
            FluidicsOperationStarted(
                operation="wash",
                port=wash_port,
                solution=wash_solution,
                volume_ul=volume_ul * repeats,
                flow_rate_ul_per_min=flow_rate_ul_per_min,
            )
        )

        start_time = time.monotonic()
        success = False
        error_message: Optional[str] = None

        try:
            with self._lock:
                success = self._driver.wash(
                    wash_port=wash_port,
                    volume_ul=volume_ul,
                    flow_rate_ul_per_min=flow_rate_ul_per_min,
                    repeats=repeats,
                )

            if not success:
                with self._lock:
                    status = self._driver.get_status()
                error_message = status.error_message

        except Exception as e:
            _log.exception(f"Error in wash: {e}")
            error_message = str(e)
            success = False

        finally:
            duration = time.monotonic() - start_time
            self.publish(
                FluidicsOperationCompleted(
                    operation="wash",
                    success=success,
                    error_message=error_message,
                    duration_seconds=duration,
                )
            )
            self._publish_status()

        return success

    def empty_syringe(self) -> bool:
        """Empty syringe contents to waste.

        Returns:
            True if successful, False otherwise.

        Raises:
            RuntimeError: If driver not available or system busy.
        """
        if self._driver is None:
            raise RuntimeError("Fluidics driver not available")

        self._check_busy()

        self.publish(
            FluidicsOperationStarted(
                operation="empty_syringe",
                port=None,
                solution=None,
                volume_ul=0.0,
                flow_rate_ul_per_min=0.0,
            )
        )

        start_time = time.monotonic()
        success = False
        error_message: Optional[str] = None

        try:
            with self._lock:
                success = self._driver.empty_syringe()

            if not success:
                with self._lock:
                    status = self._driver.get_status()
                error_message = status.error_message

        except Exception as e:
            _log.exception(f"Error in empty_syringe: {e}")
            error_message = str(e)
            success = False

        finally:
            duration = time.monotonic() - start_time
            self.publish(
                FluidicsOperationCompleted(
                    operation="empty_syringe",
                    success=success,
                    error_message=error_message,
                    duration_seconds=duration,
                )
            )
            self._publish_status()

        return success

    def incubate(
        self,
        duration_seconds: float,
        solution: Optional[str] = None,
        progress_interval: float = 1.0,
        cancel_token: Optional["CancelToken"] = None,
    ) -> bool:
        """Incubate for specified duration with progress updates.

        Does NOT hold the lock during sleep, allowing abort to work.

        Args:
            duration_seconds: Duration of incubation in seconds
            solution: Optional solution name for event context
            progress_interval: How often to emit progress events (seconds)
            cancel_token: Optional CancelToken for orchestrator-level cancellation

        Returns:
            True if incubation completed, False if aborted.
        """
        self._abort_incubation.clear()
        self._is_incubating = True

        try:
            self.publish(
                FluidicsIncubationStarted(
                    duration_seconds=duration_seconds,
                    solution=solution,
                )
            )

            elapsed = 0.0
            while elapsed < duration_seconds:
                # Check for service-level abort
                if self._abort_incubation.is_set():
                    _log.info("Incubation aborted via service abort()")
                    self.publish(FluidicsIncubationCompleted(completed=False))
                    return False

                # Check for orchestrator-level cancellation
                if cancel_token is not None and cancel_token.is_cancelled:
                    _log.info("Incubation aborted via CancelToken")
                    self.publish(FluidicsIncubationCompleted(completed=False))
                    return False

                # Calculate remaining and progress
                remaining = max(0.0, duration_seconds - elapsed)
                progress = min(100.0, (elapsed / duration_seconds) * 100.0)

                # Emit progress
                self.publish(
                    FluidicsIncubationProgress(
                        elapsed_seconds=elapsed,
                        remaining_seconds=remaining,
                        progress_percent=progress,
                    )
                )

                # Sleep for interval (or remaining time if less)
                sleep_time = min(progress_interval, remaining)
                time.sleep(sleep_time)
                elapsed += sleep_time

            # Final progress at 100%
            self.publish(
                FluidicsIncubationProgress(
                    elapsed_seconds=duration_seconds,
                    remaining_seconds=0.0,
                    progress_percent=100.0,
                )
            )
            self.publish(FluidicsIncubationCompleted(completed=True))
            return True

        finally:
            self._is_incubating = False

    def abort(self) -> None:
        """Abort any pending operations.

        Sets abort flag for incubation and calls driver abort.

        NOTE: This method does NOT acquire self._lock because:
        1. Driver abort() just sets a flag - it's thread-safe
        2. Acquiring the lock would block if an operation is running
           (which is exactly when we need abort to work!)
        """
        self._abort_incubation.set()
        if self._driver is not None:
            # Don't acquire lock - abort must work while operations are running
            self._driver.abort()
        _log.info("Fluidics abort requested")

    def reset_abort(self) -> None:
        """Clear abort flag to allow new operations."""
        self._abort_incubation.clear()
        if self._driver is not None:
            with self._lock:
                self._driver.reset_abort()
        _log.info("Fluidics abort reset")

    def get_status(self) -> Optional["FluidicsStatus"]:
        """Get current status of the fluidics system.

        Returns:
            FluidicsStatus if driver available, None otherwise.
        """
        if self._driver is None:
            return None

        with self._lock:
            return self._driver.get_status()

    def get_port_for_solution(self, name: str) -> Optional[int]:
        """Get port number for a solution name.

        Args:
            name: Solution name (case-insensitive)

        Returns:
            Port number if found, None otherwise.
        """
        if self._driver is None:
            return None
        return self._driver.get_port_for_solution(name)

    def get_port_name(self, port: int) -> Optional[str]:
        """Get solution name for a port number."""
        if self._driver is None:
            return None
        return self._driver.get_port_name(port)

    def get_available_solutions(self) -> dict[str, int]:
        """Get mapping of solution names to port numbers.

        Returns:
            Dict mapping solution names to port numbers.
        """
        if self._driver is None:
            return {}

        result: dict[str, int] = {}
        for port in self._driver.get_available_ports():
            name = self._driver.get_port_name(port)
            if name is not None:
                result[name] = port
        return result

    def get_available_ports(self) -> list[int]:
        """Get list of available port numbers.

        Returns:
            Sorted list of port numbers.
        """
        if self._driver is None:
            return []
        return self._driver.get_available_ports()

    def shutdown(self) -> None:
        """Clean shutdown of the service and driver."""
        super().shutdown()

        if self._driver is not None:
            try:
                self._driver.close()
            except Exception as e:
                _log.exception(f"Error closing fluidics driver: {e}")
