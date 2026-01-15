"""
MERFISH Fluidics Driver.

Wraps fluidics_v2.MERFISHOperations to implement AbstractFluidicsController.
Provides port mapping from solution names to physical ports via JSON config.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Optional

import time

import squid.core.logging
from squid.core.abc import (
    AbstractFluidicsController,
    FluidicsOperationStatus,
    FluidicsStatus,
    FluidicsProgressCallback,
)

_log = squid.core.logging.get_logger(__name__)


class MERFISHFluidicsConfig:
    """Configuration loader and validator for MERFISH fluidics.

    Loads JSON config and provides:
    - Bidirectional port <-> solution name mapping
    - Validation of required sections
    - Access to raw config for fluidics_v2 components
    """

    def __init__(self, config_path: str):
        """Load and validate configuration from JSON file.

        Args:
            config_path: Path to the configuration JSON file.

        Raises:
            FileNotFoundError: If config file doesn't exist.
            json.JSONDecodeError: If config file is invalid JSON.
            ValueError: If config validation fails.
        """
        self._config_path = config_path
        self._config: dict[str, Any] = {}
        self._solution_to_port: dict[str, int] = {}
        self._port_to_solution: dict[int, str] = {}
        self._allowed_ports: set[int] = set()

        self._load_config()
        self._validate()
        self._build_port_mapping()

    def _load_config(self) -> None:
        """Load configuration from JSON file."""
        path = Path(self._config_path)
        if not path.exists():
            raise FileNotFoundError(f"Fluidics config not found: {self._config_path}")

        with open(path, "r") as f:
            self._config = json.load(f)

    def _validate(self) -> None:
        """Validate configuration on load.

        Raises:
            ValueError: If validation fails with descriptive message.
        """
        # Required sections
        required = ["microcontroller", "syringe_pump", "selector_valves", "solution_port_mapping"]
        for key in required:
            if key not in self._config:
                raise ValueError(f"Missing required config section: '{key}'")

        # Syringe pump validation
        sp = self._config.get("syringe_pump", {})
        volume = sp.get("volume_ul", 0)
        if volume <= 0:
            raise ValueError(f"syringe_pump.volume_ul must be > 0, got {volume}")

        # Speed code limit validation (valid range: 1-40)
        speed_limit = sp.get("speed_code_limit")
        if speed_limit is not None:
            if not isinstance(speed_limit, int) or speed_limit < 1 or speed_limit > 40:
                raise ValueError(f"syringe_pump.speed_code_limit must be int 1-40, got {speed_limit}")

        # Derive allowed ports
        self._allowed_ports = self._derive_allowed_ports()

        # Port mapping validation
        mapping = self._config.get("solution_port_mapping", {})
        ports_used = list(mapping.values())

        # Check for duplicate port numbers
        if len(ports_used) != len(set(ports_used)):
            seen: set[int] = set()
            duplicates: list[int] = []
            for p in ports_used:
                if p in seen:
                    duplicates.append(p)
                seen.add(p)
            raise ValueError(f"Duplicate port numbers in solution_port_mapping: {duplicates}")

        # Validate each port is in allowed range
        for solution, port in mapping.items():
            if port not in self._allowed_ports:
                raise ValueError(
                    f"Port {port} for '{solution}' not in allowed ports: {sorted(self._allowed_ports)}"
                )

    def _derive_allowed_ports(self) -> set[int]:
        """Derive allowed port numbers from selector_valves config.

        Supports two config styles:
        1. Explicit: "allowed_ports": [1, 2, 3, 25, 26, 27] - use directly
        2. Per-valve: "number_of_ports": {"0": 10, "1": 10, "2": 10}
           - Assumes contiguous 1-based IDs per valve
           - Valve 0: 1-10, Valve 1: 11-20, Valve 2: 21-30

        Returns:
            Set of allowed port numbers.
        """
        sv = self._config.get("selector_valves", {})

        # Check for explicit allowed_ports first (PREFERRED for production)
        if "allowed_ports" in sv:
            return set(sv["allowed_ports"])

        # Fall back to deriving from number_of_ports (simulation/simple setups)
        # WARNING: This assumes contiguous port numbering, which may not match
        # actual hardware (e.g., pass-through ports). For production, use
        # explicit "allowed_ports" in config.
        _log.warning(
            "Deriving allowed ports from number_of_ports - "
            "verify this matches actual hardware. "
            "For production, use explicit 'allowed_ports' in config."
        )

        allowed: set[int] = set()
        offset = 0
        valve_ids = sorted(sv.get("valve_ids_allowed", []))

        for valve_id in valve_ids:
            num_ports = sv.get("number_of_ports", {}).get(str(valve_id), 0)
            for i in range(1, num_ports + 1):
                allowed.add(offset + i)
            offset += num_ports

        return allowed

    def _build_port_mapping(self) -> None:
        """Build bidirectional port <-> solution mappings."""
        mapping = self._config.get("solution_port_mapping", {})

        for solution, port in mapping.items():
            # Store with lowercase key for case-insensitive lookup
            self._solution_to_port[solution.lower()] = port
            self._port_to_solution[port] = solution

    def get_port_for_solution(self, name: str) -> Optional[int]:
        """Get port number for a solution name (case-insensitive).

        Args:
            name: Solution name to look up.

        Returns:
            Port number if found, None otherwise.
        """
        return self._solution_to_port.get(name.lower())

    def get_solution_for_port(self, port: int) -> Optional[str]:
        """Get solution name for a port number.

        Args:
            port: Port number to look up.

        Returns:
            Solution name if found, None otherwise.
        """
        return self._port_to_solution.get(port)

    @property
    def raw_config(self) -> dict[str, Any]:
        """Return raw config dict for fluidics_v2 components."""
        return self._config

    @property
    def available_ports(self) -> list[int]:
        """Return sorted list of configured port numbers."""
        return sorted(self._port_to_solution.keys())

    @property
    def limits(self) -> dict[str, float]:
        """Return limits dict (max_flow_rate, min_flow_rate, max_volume)."""
        default_limits = {
            "max_flow_rate_ul_per_min": 10000.0,
            "min_flow_rate_ul_per_min": 1.0,
            "max_volume_ul": float(self._config.get("syringe_pump", {}).get("volume_ul", 5000)),
        }
        config_limits = self._config.get("limits", {})
        return {**default_limits, **config_limits}

    @property
    def syringe_volume_ul(self) -> float:
        """Return syringe pump volume in microliters."""
        return float(self._config.get("syringe_pump", {}).get("volume_ul", 5000))


class MERFISHFluidicsDriver(AbstractFluidicsController):
    """Fluidics driver wrapping fluidics_v2.MERFISHOperations.

    Implements AbstractFluidicsController to provide a unified interface
    for MERFISH fluidics hardware (syringe pump, selector valves).

    Thread safety note: This driver is NOT internally thread-safe.
    All thread synchronization is handled by FluidicsService.
    """

    def __init__(self, config_path: str, simulation: bool = False):
        """Initialize the MERFISH fluidics driver.

        Args:
            config_path: Path to the fluidics configuration JSON file.
            simulation: If True, use simulation hardware components.
        """
        self._config_path = config_path
        self._simulation = simulation

        # State
        self._initialized = False
        self._current_status = FluidicsOperationStatus.IDLE
        self._current_port: Optional[int] = None
        self._error_message: Optional[str] = None
        self._lock = threading.RLock()

        # Hardware components (set during initialize)
        self._config: Optional[MERFISHFluidicsConfig] = None
        self._controller: Any = None  # FluidController or FluidControllerSimulation
        self._syringe_pump: Any = None  # SyringePump or SyringePumpSimulation
        self._selector_valve_system: Any = None  # SelectorValveSystem
        self._merfish_ops: Any = None  # MERFISHOperations

        # Progress callback for real-time updates during operations
        self._progress_callback: Optional[FluidicsProgressCallback] = None
        self._progress_thread: Optional[threading.Thread] = None
        self._progress_stop_event = threading.Event()

    def set_progress_callback(self, callback: Optional[FluidicsProgressCallback]) -> None:
        """Set callback for progress updates during long operations.

        Args:
            callback: Function to call periodically during operations.
                      Signature: (operation, progress_percent, elapsed_s, remaining_s, syringe_volume_ul) -> None
        """
        self._progress_callback = callback

    def _start_progress_thread(self, operation: str, duration_s: float) -> None:
        """Start background thread to emit progress during blocking operations.

        Args:
            operation: Operation name for progress callback.
            duration_s: Expected operation duration in seconds.
        """
        if self._progress_callback is None:
            return

        self._progress_stop_event.clear()

        def emit_progress() -> None:
            start = time.monotonic()
            while not self._progress_stop_event.is_set():
                elapsed = time.monotonic() - start
                if elapsed >= duration_s:
                    break
                progress = (elapsed / duration_s) * 100.0 if duration_s > 0 else 100.0
                remaining = max(0.0, duration_s - elapsed)
                # Get syringe volume if available
                syringe_vol = 0.0
                if self._syringe_pump is not None:
                    try:
                        syringe_vol = float(self._syringe_pump.current_volume_ul)
                    except Exception:
                        pass
                try:
                    self._progress_callback(operation, progress, elapsed, remaining, syringe_vol)
                except Exception as e:
                    _log.warning(f"Progress callback error: {e}")
                self._progress_stop_event.wait(1.0)  # 1 second intervals

        self._progress_thread = threading.Thread(target=emit_progress, daemon=True)
        self._progress_thread.start()

    def _stop_progress_thread(self) -> None:
        """Stop progress thread."""
        self._progress_stop_event.set()
        if self._progress_thread is not None:
            self._progress_thread.join(timeout=1.0)
            self._progress_thread = None

    def initialize(self) -> bool:
        """Initialize hardware connections.

        Returns:
            True if initialization successful, False otherwise.
        """
        try:
            # Load and validate config
            self._config = MERFISHFluidicsConfig(self._config_path)
            raw_config = self._config.raw_config

            # Import fluidics_v2 modules (path must be set by caller)
            from fluidics.control.controller import FluidController, FluidControllerSimulation
            from fluidics.control.syringe_pump import SyringePump, SyringePumpSimulation
            from fluidics.control.selector_valve import SelectorValveSystem
            from fluidics.merfish_operations import MERFISHOperations
            from fluidics._def import CMD_SET

            # Create hardware components
            if self._simulation:
                self._controller = FluidControllerSimulation(
                    raw_config["microcontroller"]["serial_number"]
                )
                self._syringe_pump = SyringePumpSimulation(
                    sn=raw_config["syringe_pump"]["serial_number"],
                    syringe_ul=raw_config["syringe_pump"]["volume_ul"],
                    speed_code_limit=raw_config["syringe_pump"]["speed_code_limit"],
                    waste_port=raw_config["syringe_pump"].get("waste_port", 3),
                )
            else:
                self._controller = FluidController(
                    raw_config["microcontroller"]["serial_number"]
                )
                self._syringe_pump = SyringePump(
                    sn=raw_config["syringe_pump"]["serial_number"],
                    syringe_ul=raw_config["syringe_pump"]["volume_ul"],
                    speed_code_limit=raw_config["syringe_pump"]["speed_code_limit"],
                    waste_port=raw_config["syringe_pump"].get("waste_port", 3),
                )

            # Initialize controller
            self._controller.begin()
            self._controller.send_command(CMD_SET.CLEAR)

            # Create selector valve system and MERFISH operations
            self._selector_valve_system = SelectorValveSystem(self._controller, raw_config)
            self._merfish_ops = MERFISHOperations(
                raw_config, self._syringe_pump, self._selector_valve_system
            )

            self._initialized = True
            self._current_status = FluidicsOperationStatus.IDLE
            _log.info(
                f"MERFISH fluidics driver initialized (simulation={self._simulation})"
            )
            return True

        except Exception as e:
            self._error_message = str(e)
            self._current_status = FluidicsOperationStatus.ERROR
            _log.exception(f"Failed to initialize MERFISH fluidics driver: {e}")
            return False

    def close(self) -> None:
        """Close connections and release resources."""
        if not self._initialized:
            return

        try:
            if self._syringe_pump is not None:
                # Empty to waste before closing
                try:
                    self._syringe_pump.close(to_waste=True)
                except Exception as e:
                    _log.warning(f"Error emptying syringe on close: {e}")
        except Exception as e:
            _log.exception(f"Error closing fluidics driver: {e}")
        finally:
            self._initialized = False
            self._current_status = FluidicsOperationStatus.IDLE
            _log.info("MERFISH fluidics driver closed")

    def _validate_params(self, volume_ul: float, flow_rate_ul_per_min: float) -> None:
        """Validate flow parameters against config limits.

        Raises:
            ValueError: If parameters are out of range.
        """
        if self._config is None:
            raise RuntimeError("Driver not initialized")

        limits = self._config.limits
        max_rate = limits["max_flow_rate_ul_per_min"]
        min_rate = limits["min_flow_rate_ul_per_min"]
        max_vol = limits["max_volume_ul"]

        if not (min_rate <= flow_rate_ul_per_min <= max_rate):
            raise ValueError(
                f"Flow rate {flow_rate_ul_per_min} out of range ({min_rate}-{max_rate})"
            )
        if volume_ul > max_vol:
            raise ValueError(f"Volume {volume_ul} exceeds max {max_vol}")
        if volume_ul <= 0:
            raise ValueError(f"Volume must be > 0, got {volume_ul}")

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
        """
        if not self._initialized or self._merfish_ops is None:
            _log.error("Cannot flow_solution: driver not initialized")
            return False

        if self._syringe_pump.is_aborted:
            _log.warning("Cannot flow_solution: abort flag is set")
            return False

        try:
            self._validate_params(volume_ul, flow_rate_ul_per_min)

            self._current_status = FluidicsOperationStatus.RUNNING
            self._current_port = port

            # Calculate expected duration for progress tracking
            duration_s = (volume_ul / flow_rate_ul_per_min) * 60.0 if flow_rate_ul_per_min > 0 else 0.0

            # Start progress thread before blocking call
            self._start_progress_thread("flow", duration_s)
            try:
                # Call MERFISHOperations.flow_reagent (blocking)
                self._merfish_ops.flow_reagent(
                    port=port,
                    flow_rate=int(flow_rate_ul_per_min),
                    volume=int(volume_ul),
                    fill_tubing_with_port=fill_tubing_with_port or 0,
                )
            finally:
                self._stop_progress_thread()

            if self._syringe_pump.is_aborted:
                self._current_status = FluidicsOperationStatus.ABORTED
                return False

            self._current_status = FluidicsOperationStatus.COMPLETED
            return True

        except Exception as e:
            self._error_message = str(e)
            self._current_status = FluidicsOperationStatus.ERROR
            _log.exception(f"Error in flow_solution: {e}")
            return False

    def prime(
        self,
        ports: list[int],
        volume_ul: float,
        flow_rate_ul_per_min: float,
        final_port: int,
    ) -> bool:
        """Prime tubing with solutions from specified ports.

        Args:
            ports: List of ports to prime
            volume_ul: Volume per port in microliters
            flow_rate_ul_per_min: Flow rate in microliters per minute
            final_port: Port to leave selected after priming

        Returns:
            True if successful, False otherwise.
        """
        if not self._initialized or self._merfish_ops is None:
            _log.error("Cannot prime: driver not initialized")
            return False

        if self._syringe_pump.is_aborted:
            _log.warning("Cannot prime: abort flag is set")
            return False

        try:
            self._validate_params(volume_ul, flow_rate_ul_per_min)

            self._current_status = FluidicsOperationStatus.RUNNING
            self._current_port = final_port

            # Calculate expected duration for progress tracking (per port * num ports)
            per_port_duration_s = (volume_ul / flow_rate_ul_per_min) * 60.0 if flow_rate_ul_per_min > 0 else 0.0
            total_duration_s = per_port_duration_s * len(ports)

            # Start progress thread before blocking call
            self._start_progress_thread("prime", total_duration_s)
            try:
                # Call MERFISHOperations.priming_or_clean_up (blocking)
                self._merfish_ops.priming_or_clean_up(
                    port=final_port,
                    flow_rate=int(flow_rate_ul_per_min),
                    volume=int(volume_ul),
                    use_ports=ports,
                )
            finally:
                self._stop_progress_thread()

            if self._syringe_pump.is_aborted:
                self._current_status = FluidicsOperationStatus.ABORTED
                return False

            self._current_status = FluidicsOperationStatus.COMPLETED
            return True

        except Exception as e:
            self._error_message = str(e)
            self._current_status = FluidicsOperationStatus.ERROR
            _log.exception(f"Error in prime: {e}")
            return False

    def wash(
        self,
        wash_port: int,
        volume_ul: float,
        flow_rate_ul_per_min: float,
        repeats: int = 1,
    ) -> bool:
        """Wash with solution from specified port.

        Args:
            wash_port: Port number for wash solution
            volume_ul: Volume per wash cycle in microliters
            flow_rate_ul_per_min: Flow rate in microliters per minute
            repeats: Number of wash cycles

        Returns:
            True if successful, False otherwise.
        """
        if not self._initialized:
            _log.error("Cannot wash: driver not initialized")
            return False

        if self._syringe_pump.is_aborted:
            _log.warning("Cannot wash: abort flag is set")
            return False

        try:
            self._validate_params(volume_ul, flow_rate_ul_per_min)

            self._current_status = FluidicsOperationStatus.RUNNING
            self._current_port = wash_port

            # Wash is repeated flow_solution calls
            for i in range(repeats):
                if self._syringe_pump.is_aborted:
                    self._current_status = FluidicsOperationStatus.ABORTED
                    return False

                success = self.flow_solution(
                    port=wash_port,
                    volume_ul=volume_ul,
                    flow_rate_ul_per_min=flow_rate_ul_per_min,
                    fill_tubing_with_port=None,
                )
                if not success:
                    return False

            self._current_status = FluidicsOperationStatus.COMPLETED
            return True

        except Exception as e:
            self._error_message = str(e)
            self._current_status = FluidicsOperationStatus.ERROR
            _log.exception(f"Error in wash: {e}")
            return False

    def empty_syringe(self) -> bool:
        """Empty syringe contents to waste.

        Returns:
            True if successful, False otherwise.
        """
        if not self._initialized or self._syringe_pump is None:
            _log.error("Cannot empty_syringe: driver not initialized")
            return False

        try:
            self._current_status = FluidicsOperationStatus.RUNNING

            self._syringe_pump.reset_chain()
            self._syringe_pump.dispense_to_waste()
            self._syringe_pump.execute()

            if self._syringe_pump.is_aborted:
                self._current_status = FluidicsOperationStatus.ABORTED
                return False

            self._current_status = FluidicsOperationStatus.COMPLETED
            return True

        except Exception as e:
            self._error_message = str(e)
            self._current_status = FluidicsOperationStatus.ERROR
            _log.exception(f"Error in empty_syringe: {e}")
            return False

    def abort(self) -> None:
        """Set abort flag to stop pending operations."""
        if self._syringe_pump is not None:
            self._syringe_pump.abort()
        self._current_status = FluidicsOperationStatus.ABORTED
        _log.info("Fluidics abort requested")

    def reset_abort(self) -> None:
        """Clear abort flag to allow new operations."""
        if self._syringe_pump is not None:
            self._syringe_pump.reset_abort()
        self._current_status = FluidicsOperationStatus.IDLE
        self._error_message = None
        _log.info("Fluidics abort reset")

    def get_status(self) -> FluidicsStatus:
        """Get current status of the fluidics system.

        Returns:
            FluidicsStatus with current state information.
        """
        syringe_vol = 0.0
        if self._syringe_pump is not None:
            try:
                syringe_vol = float(self._syringe_pump.get_current_volume())
            except Exception:
                pass

        current_solution = None
        if self._current_port is not None and self._config is not None:
            current_solution = self._config.get_solution_for_port(self._current_port)

        return FluidicsStatus(
            status=self._current_status,
            current_port=self._current_port,
            current_solution=current_solution,
            syringe_volume_ul=syringe_vol,
            is_busy=self.is_busy,
            error_message=self._error_message,
        )

    def get_port_name(self, port: int) -> Optional[str]:
        """Get solution name for a port number.

        Args:
            port: Port number

        Returns:
            Solution name if mapped, None otherwise.
        """
        if self._config is None:
            return None
        return self._config.get_solution_for_port(port)

    def get_port_for_solution(self, solution_name: str) -> Optional[int]:
        """Get port number for a solution name.

        Args:
            solution_name: Name of the solution (case-insensitive)

        Returns:
            Port number if found, None otherwise.
        """
        if self._config is None:
            return None
        return self._config.get_port_for_solution(solution_name)

    def get_available_ports(self) -> list[int]:
        """Get list of configured port numbers.

        Returns:
            Sorted list of available port numbers.
        """
        if self._config is None:
            return []
        return self._config.available_ports

    @property
    def is_busy(self) -> bool:
        """Check if an operation is in progress.

        Returns:
            True if hardware is busy, False otherwise.
        """
        if self._syringe_pump is None:
            return False

        # Check if status indicates running
        if self._current_status == FluidicsOperationStatus.RUNNING:
            return True

        # Check syringe pump busy state if available
        try:
            return bool(getattr(self._syringe_pump, "is_busy", False))
        except Exception:
            return False
