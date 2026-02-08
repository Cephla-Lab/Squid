"""
Simulated Fluidics Controller.

Pure simulation driver with no fluidics_v2 dependency.
Useful for tests and when the fluidics_v2 submodule is not available.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

import squid.core.logging
from squid.core.config.test_timing import scale_duration
from squid.core.abc import (
    AbstractFluidicsController,
    FluidicsOperationStatus,
    FluidicsStatus,
    FluidicsPhaseCallback,
    FluidicsProgressCallback,
)

_log = squid.core.logging.get_logger(__name__)

# Legacy type alias for backwards compatibility
PhaseCallback = FluidicsPhaseCallback


class SimulatedFluidicsController(AbstractFluidicsController):
    """Simulation fluidics controller with no fluidics_v2 dependency.

    Provides a pure-Python simulation that:
    - Loads config for port mapping (same JSON format as MERFISH)
    - Tracks state in memory (current port, syringe volume, etc.)
    - Optionally simulates timing delays for realistic behavior
    - Useful for tests and when hardware/submodule is unavailable

    Thread safety note: This driver is NOT internally thread-safe.
    All thread synchronization is handled by FluidicsService.
    """

    def __init__(self, config_path: str, simulate_timing: bool = True):
        """Initialize the simulated fluidics controller.

        Args:
            config_path: Path to the fluidics configuration JSON file.
            simulate_timing: If True, simulate realistic timing delays based on
                volume and flow rate. Defaults to True for realistic behavior.
        """
        self._config_path = config_path
        self._simulate_timing = simulate_timing

        # State
        self._initialized = False
        self._current_status = FluidicsOperationStatus.IDLE
        self._current_port: Optional[int] = None
        self._syringe_volume_ul: float = 0.0
        self._error_message: Optional[str] = None
        self._is_aborted = False
        self._is_busy = False
        self._lock = threading.RLock()

        # Callbacks (set by FluidicsService for event publishing)
        self._phase_callback: Optional[FluidicsPhaseCallback] = None
        self._progress_callback: Optional[FluidicsProgressCallback] = None

        # Config
        self._config: dict[str, Any] = {}
        self._solution_to_port: dict[str, int] = {}
        self._port_to_solution: dict[int, str] = {}
        self._syringe_capacity_ul: float = 5000.0
        self._limits: dict[str, float] = {}

    def initialize(self) -> bool:
        """Initialize the simulated controller.

        Returns:
            True if initialization successful, False otherwise.
        """
        try:
            path = Path(self._config_path)
            if not path.exists():
                _log.warning(f"Simulated fluidics config not found: {self._config_path}")
                # Create minimal config for pure simulation
                self._config = {
                    "syringe_pump": {"volume_ul": 5000},
                    "solution_port_mapping": {},
                    "limits": {},
                }
            else:
                with open(path, "r") as f:
                    self._config = json.load(f)

            # Extract syringe capacity
            self._syringe_capacity_ul = float(
                self._config.get("syringe_pump", {}).get("volume_ul", 5000)
            )

            # Build port mappings
            mapping = self._config.get("solution_port_mapping", {})
            for solution, port in mapping.items():
                self._solution_to_port[solution.lower()] = port
                self._port_to_solution[port] = solution

            # Extract limits (default max flow rate is 1 mL/min = 1000 µL/min)
            self._limits = {
                "max_flow_rate_ul_per_min": 1000.0,
                "min_flow_rate_ul_per_min": 1.0,
                "max_volume_ul": self._syringe_capacity_ul,
            }
            self._limits.update(self._config.get("limits", {}))

            self._initialized = True
            self._current_status = FluidicsOperationStatus.IDLE
            _log.info(
                f"Simulated fluidics controller initialized "
                f"(timing={self._simulate_timing}, ports={len(self._port_to_solution)})"
            )
            return True

        except Exception as e:
            self._error_message = str(e)
            self._current_status = FluidicsOperationStatus.ERROR
            _log.exception(f"Failed to initialize simulated fluidics: {e}")
            return False

    def close(self) -> None:
        """Close the simulated controller."""
        if self._initialized:
            _log.info("Simulated fluidics controller closed")
        self._initialized = False
        self._current_status = FluidicsOperationStatus.IDLE
        self._syringe_volume_ul = 0.0

    def set_phase_callback(self, callback: Optional[PhaseCallback]) -> None:
        """Set callback for phase change notifications.

        Args:
            callback: Function to call when operation phase changes.
                      Signature: (phase, port, solution, volume_ul, flow_rate) -> None
        """
        self._phase_callback = callback

    def set_progress_callback(self, callback: Optional[FluidicsProgressCallback]) -> None:
        """Set callback for progress updates during long operations.

        Args:
            callback: Function to call periodically during operations.
                      Signature: (operation, progress_percent, elapsed_s, remaining_s, syringe_volume_ul) -> None
        """
        self._progress_callback = callback

    def _notify_phase(
        self,
        phase: str,
        port: Optional[int] = None,
        volume_ul: Optional[float] = None,
        flow_rate_ul_per_min: Optional[float] = None,
    ) -> None:
        """Notify phase change via callback.

        Args:
            phase: Phase name ("aspirating", "dispensing", "valve_switching")
            port: Target port number
            volume_ul: Volume being processed
            flow_rate_ul_per_min: Flow rate
        """
        # Update status based on phase
        if phase == "aspirating":
            self._current_status = FluidicsOperationStatus.ASPIRATING
        elif phase == "dispensing":
            self._current_status = FluidicsOperationStatus.DISPENSING
        elif phase == "valve_switching":
            self._current_status = FluidicsOperationStatus.VALVE_SWITCHING
        else:
            self._current_status = FluidicsOperationStatus.RUNNING

        # Get solution name
        solution = None
        if port is not None:
            solution = self._port_to_solution.get(port)

        # Call callback if set
        if self._phase_callback:
            try:
                self._phase_callback(phase, port, solution, volume_ul, flow_rate_ul_per_min)
            except Exception as e:
                _log.warning(f"Phase callback error: {e}")

    def _simulate_delay(
        self,
        volume_ul: float,
        flow_rate_ul_per_min: float,
        operation: str = "flow",
        start_syringe_vol: Optional[float] = None,
        end_syringe_vol: Optional[float] = None,
    ) -> None:
        """Simulate realistic timing delay for a flow operation.

        Calculates timing based on volume and flow rate.
        For example: 500 µL at 1000 µL/min = 0.5 min = 30 seconds.

        Emits progress callbacks every ~1 second during the delay.
        Interpolates syringe volume if start/end volumes are provided.

        Args:
            volume_ul: Volume being flowed in microliters.
            flow_rate_ul_per_min: Flow rate in microliters per minute.
            operation: Operation name for progress callback (default: "flow").
            start_syringe_vol: Starting syringe volume for interpolation.
            end_syringe_vol: Ending syringe volume for interpolation.
        """
        if not self._simulate_timing:
            # Even without timing simulation, update syringe volume and emit progress
            if start_syringe_vol is None:
                start_syringe_vol = self._syringe_volume_ul
            if end_syringe_vol is None:
                end_syringe_vol = self._syringe_volume_ul
            else:
                self._syringe_volume_ul = end_syringe_vol

            # Emit initial progress (0%)
            if self._progress_callback:
                try:
                    self._progress_callback(operation, 0.0, 0.0, 0.0, start_syringe_vol)
                except Exception as e:
                    _log.warning(f"Progress callback error: {e}")

            # Emit final progress (100%) so GUI updates
            if self._progress_callback:
                try:
                    self._progress_callback(operation, 100.0, 0.0, 0.0, end_syringe_vol)
                except Exception as e:
                    _log.warning(f"Progress callback error: {e}")
            return

        if flow_rate_ul_per_min <= 0:
            return

        # Clamp flow rate to configured limits
        max_rate = self._limits.get("max_flow_rate_ul_per_min", 1000.0)
        min_rate = self._limits.get("min_flow_rate_ul_per_min", 1.0)
        effective_rate = max(min_rate, min(flow_rate_ul_per_min, max_rate))

        # Calculate realistic timing: time = volume / rate
        duration_min = volume_ul / effective_rate
        duration_s = scale_duration(duration_min * 60.0)

        _log.debug(
            f"[SIMULATED] Flow timing: {volume_ul:.1f} µL at {effective_rate:.1f} µL/min "
            f"= {duration_s:.2f}s"
        )

        # Use current syringe volume if start/end not specified
        if start_syringe_vol is None:
            start_syringe_vol = self._syringe_volume_ul
        if end_syringe_vol is None:
            end_syringe_vol = self._syringe_volume_ul

        # Emit initial progress callback (0%)
        if self._progress_callback:
            try:
                self._progress_callback(
                    operation, 0.0, 0.0, duration_s, start_syringe_vol
                )
            except Exception as e:
                _log.warning(f"Progress callback error: {e}")

        # Sleep in increments, emitting progress every ~1 second
        elapsed = 0.0
        progress_interval_s = scale_duration(1.0, min_seconds=0.01)
        next_progress_time = progress_interval_s  # Next update in 1 second (initial already sent)
        while elapsed < duration_s:
            if self._is_aborted:
                return

            # Interpolate syringe volume based on elapsed time
            if duration_s > 0:
                progress_fraction = elapsed / duration_s
            else:
                progress_fraction = 1.0
            self._syringe_volume_ul = start_syringe_vol + (end_syringe_vol - start_syringe_vol) * progress_fraction

            # Emit progress callback at ~1 second intervals
            if elapsed >= next_progress_time and self._progress_callback:
                progress_pct = progress_fraction * 100.0
                remaining_s = duration_s - elapsed
                try:
                    self._progress_callback(
                        operation,
                        progress_pct,
                        elapsed,
                        remaining_s,
                        self._syringe_volume_ul,
                    )
                except Exception as e:
                    _log.warning(f"Progress callback error: {e}")
                next_progress_time = elapsed + progress_interval_s

            # Sleep in small increments to allow abort checking
            sleep_interval = scale_duration(0.1, min_seconds=1e-6)
            sleep_time = min(sleep_interval, duration_s - elapsed)
            time.sleep(sleep_time)
            elapsed += sleep_time

        # Ensure final volume is set
        self._syringe_volume_ul = end_syringe_vol

        # Emit final progress callback (100%)
        if self._progress_callback:
            try:
                self._progress_callback(
                    operation, 100.0, duration_s, 0.0, end_syringe_vol
                )
            except Exception as e:
                _log.warning(f"Progress callback error: {e}")

    def _check_aborted(self) -> bool:
        """Check if abort has been requested.

        Returns:
            True if aborted, False otherwise.
        """
        if self._is_aborted:
            self._current_status = FluidicsOperationStatus.ABORTED
            return True
        return False

    def flow_solution(
        self,
        port: int,
        volume_ul: float,
        flow_rate_ul_per_min: float,
        fill_tubing_with_port: Optional[int] = None,
    ) -> bool:
        """Simulate flowing solution from specified port.

        Args:
            port: Port number to flow from
            volume_ul: Volume to flow in microliters
            flow_rate_ul_per_min: Flow rate in microliters per minute
            fill_tubing_with_port: Optional port to fill tubing with after flow

        Returns:
            True if successful, False otherwise.
        """
        if not self._initialized:
            _log.error("Cannot flow_solution: simulated driver not initialized")
            return False

        if self._check_aborted():
            return False

        try:
            self._is_busy = True
            self._current_port = port

            solution_name = self._port_to_solution.get(port, f"port_{port}")
            _log.debug(
                f"[SIMULATED] FLOW: port={port} ({solution_name}) "
                f"volume={volume_ul}ul rate={flow_rate_ul_per_min}ul/min"
            )

            # Phase 1: Switch valve to source port
            self._notify_phase("valve_switching", port, volume_ul, flow_rate_ul_per_min)
            if self._simulate_timing:
                time.sleep(scale_duration(0.1, min_seconds=1e-6))  # Valve switch time

            if self._check_aborted():
                return False

            # Phase 2: Aspirate from source port through imaging chamber into syringe
            # (the physical hardware pulls solution THROUGH the chamber)
            self._notify_phase("aspirating", port, volume_ul, flow_rate_ul_per_min)
            start_vol = self._syringe_volume_ul
            end_vol = min(start_vol + volume_ul, self._syringe_capacity_ul)
            self._simulate_delay(
                volume_ul,
                flow_rate_ul_per_min,
                operation="aspirate",
                start_syringe_vol=start_vol,
                end_syringe_vol=end_vol,
            )

            if self._check_aborted():
                return False

            self._current_status = FluidicsOperationStatus.COMPLETED
            return True

        except Exception as e:
            self._error_message = str(e)
            self._current_status = FluidicsOperationStatus.ERROR
            _log.exception(f"Error in simulated flow_solution: {e}")
            return False

        finally:
            self._is_busy = False

    def prime(
        self,
        ports: list[int],
        volume_ul: float,
        flow_rate_ul_per_min: float,
        final_port: int,
    ) -> bool:
        """Simulate priming tubing with solutions from specified ports.

        Args:
            ports: List of ports to prime
            volume_ul: Volume per port in microliters
            flow_rate_ul_per_min: Flow rate in microliters per minute
            final_port: Port to leave selected after priming

        Returns:
            True if successful, False otherwise.
        """
        if not self._initialized:
            _log.error("Cannot prime: simulated driver not initialized")
            return False

        if self._check_aborted():
            return False

        try:
            self._is_busy = True

            _log.debug(
                f"[SIMULATED] PRIME: ports={ports} volume={volume_ul}ul "
                f"rate={flow_rate_ul_per_min}ul/min final_port={final_port}"
            )

            # Simulate priming each port
            for port in ports:
                if self._check_aborted():
                    return False

                self._current_port = port
                solution_name = self._port_to_solution.get(port, f"port_{port}")
                _log.debug(f"[SIMULATED] PRIME port={port} ({solution_name})")

                # Phase: Switch to this port
                self._notify_phase("valve_switching", port, volume_ul, flow_rate_ul_per_min)
                if self._simulate_timing:
                    time.sleep(scale_duration(0.1, min_seconds=1e-6))

                if self._check_aborted():
                    return False

                # Phase: Aspirate from port through chamber into syringe
                self._notify_phase("aspirating", port, volume_ul, flow_rate_ul_per_min)
                start_vol = self._syringe_volume_ul
                end_vol = min(start_vol + volume_ul, self._syringe_capacity_ul)
                self._simulate_delay(
                    volume_ul,
                    flow_rate_ul_per_min,
                    operation="prime_aspirate",
                    start_syringe_vol=start_vol,
                    end_syringe_vol=end_vol,
                )

                if self._check_aborted():
                    return False

                # Phase: Empty syringe to waste
                self._notify_phase("dispensing", None, self._syringe_volume_ul, None)
                max_rate = self._limits.get("max_flow_rate_ul_per_min", 1000.0)
                start_vol = self._syringe_volume_ul
                self._simulate_delay(
                    start_vol,
                    max_rate,
                    operation="prime_waste",
                    start_syringe_vol=start_vol,
                    end_syringe_vol=0.0,
                )

            # Set final port
            self._notify_phase("valve_switching", final_port, None, None)
            self._current_port = final_port
            self._current_status = FluidicsOperationStatus.COMPLETED
            return True

        except Exception as e:
            self._error_message = str(e)
            self._current_status = FluidicsOperationStatus.ERROR
            _log.exception(f"Error in simulated prime: {e}")
            return False

        finally:
            self._is_busy = False

    def wash(
        self,
        wash_port: int,
        volume_ul: float,
        flow_rate_ul_per_min: float,
    ) -> bool:
        """Simulate washing with solution from specified port (flow + empty to waste).

        Args:
            wash_port: Port number for wash solution
            volume_ul: Volume in microliters
            flow_rate_ul_per_min: Flow rate in microliters per minute

        Returns:
            True if successful, False otherwise.
        """
        if not self._initialized:
            _log.error("Cannot wash: simulated driver not initialized")
            return False

        if self._check_aborted():
            return False

        try:
            self._is_busy = True
            self._current_port = wash_port

            solution_name = self._port_to_solution.get(wash_port, f"port_{wash_port}")
            _log.debug(
                f"[SIMULATED] WASH: port={wash_port} ({solution_name}) "
                f"volume={volume_ul}ul rate={flow_rate_ul_per_min}ul/min"
            )

            # Switch valve to wash port
            self._notify_phase("valve_switching", wash_port, volume_ul, flow_rate_ul_per_min)
            if self._simulate_timing:
                time.sleep(scale_duration(0.1, min_seconds=1e-6))

            if self._check_aborted():
                return False

            # Aspirate wash solution through chamber into syringe
            self._notify_phase("aspirating", wash_port, volume_ul, flow_rate_ul_per_min)
            start_vol = self._syringe_volume_ul
            end_vol = min(start_vol + volume_ul, self._syringe_capacity_ul)
            self._simulate_delay(
                volume_ul,
                flow_rate_ul_per_min,
                operation="wash_aspirate",
                start_syringe_vol=start_vol,
                end_syringe_vol=end_vol,
            )

            if self._check_aborted():
                return False

            # Empty syringe to waste
            self._notify_phase("dispensing", None, self._syringe_volume_ul, None)
            max_rate = self._limits.get("max_flow_rate_ul_per_min", 1000.0)
            start_vol = self._syringe_volume_ul
            self._simulate_delay(
                start_vol,
                max_rate,
                operation="wash_waste",
                start_syringe_vol=start_vol,
                end_syringe_vol=0.0,
            )

            self._current_status = FluidicsOperationStatus.COMPLETED
            return True

        except Exception as e:
            self._error_message = str(e)
            self._current_status = FluidicsOperationStatus.ERROR
            _log.exception(f"Error in simulated wash: {e}")
            return False

        finally:
            self._is_busy = False

    def empty_syringe(self) -> bool:
        """Simulate emptying syringe contents to waste.

        Returns:
            True if successful, False otherwise.
        """
        if not self._initialized:
            _log.error("Cannot empty_syringe: simulated driver not initialized")
            return False

        try:
            self._is_busy = True
            volume_to_dispense = self._syringe_volume_ul

            if volume_to_dispense <= 0:
                _log.info("[SIMULATED] EMPTY_SYRINGE: Syringe already empty (0ul)")
            else:
                _log.debug(
                    f"[SIMULATED] EMPTY_SYRINGE: {volume_to_dispense}ul to waste"
                )

            # Dispensing to waste
            self._notify_phase("dispensing", None, volume_to_dispense, None)

            # Use a default flow rate for emptying (max rate for quick emptying)
            max_rate = self._limits.get("max_flow_rate_ul_per_min", 1000.0)
            start_vol = self._syringe_volume_ul
            self._simulate_delay(
                volume_to_dispense,
                max_rate,
                operation="empty",
                start_syringe_vol=start_vol,
                end_syringe_vol=0.0,
            )

            self._current_status = FluidicsOperationStatus.COMPLETED
            return True

        except Exception as e:
            self._error_message = str(e)
            self._current_status = FluidicsOperationStatus.ERROR
            _log.exception(f"Error in simulated empty_syringe: {e}")
            return False

        finally:
            self._is_busy = False

    def abort(self) -> None:
        """Set abort flag to stop pending operations."""
        self._is_aborted = True
        self._current_status = FluidicsOperationStatus.ABORTED
        _log.info("[SIMULATED] Fluidics abort requested")

    def reset_abort(self) -> None:
        """Clear abort flag to allow new operations."""
        self._is_aborted = False
        self._current_status = FluidicsOperationStatus.IDLE
        self._error_message = None
        _log.info("[SIMULATED] Fluidics abort reset")

    def get_status(self) -> FluidicsStatus:
        """Get current status of the simulated fluidics system.

        Returns:
            FluidicsStatus with current state information.
        """
        current_solution = None
        if self._current_port is not None:
            current_solution = self._port_to_solution.get(self._current_port)

        return FluidicsStatus(
            status=self._current_status,
            current_port=self._current_port,
            current_solution=current_solution,
            syringe_volume_ul=self._syringe_volume_ul,
            is_busy=self._is_busy,
            error_message=self._error_message,
        )

    def get_port_name(self, port: int) -> Optional[str]:
        """Get solution name for a port number.

        Args:
            port: Port number

        Returns:
            Solution name if mapped, None otherwise.
        """
        return self._port_to_solution.get(port)

    def get_port_for_solution(self, solution_name: str) -> Optional[int]:
        """Get port number for a solution name.

        Args:
            solution_name: Name of the solution (case-insensitive)

        Returns:
            Port number if found, None otherwise.
        """
        return self._solution_to_port.get(solution_name.lower())

    def get_available_ports(self) -> list[int]:
        """Get list of configured port numbers.

        Returns:
            Sorted list of available port numbers.
        """
        return sorted(self._port_to_solution.keys())

    def get_syringe_capacity_ul(self) -> float:
        """Get the syringe capacity in microliters."""
        return self._syringe_capacity_ul

    @property
    def is_busy(self) -> bool:
        """Check if an operation is in progress.

        Returns:
            True if busy, False otherwise.
        """
        return self._is_busy
