"""
Fluidics executor for orchestrated experiments.

Executes fluidics protocol steps (flow, incubate, wash, prime, aspirate)
by delegating to FluidicsService when available, or simulating operations
when running without hardware.
"""

from __future__ import annotations

import time
from typing import Optional, TYPE_CHECKING

import squid.core.logging
from squid.core.events import EventBus
from squid.core.utils.cancel_token import CancelToken, CancellationError
from squid.core.protocol import FluidicsStep, FluidicsCommand

if TYPE_CHECKING:
    from squid.backend.services import FluidicsService

_log = squid.core.logging.get_logger(__name__)

# Default flow parameters
DEFAULT_FLOW_RATE = 50.0  # ul/min
DEFAULT_WASH_VOLUME = 500.0  # ul
DEFAULT_WASH_REPEATS = 3


class FluidicsExecutor:
    """Executes fluidics protocol steps.

    Delegates to FluidicsService when available, or simulates operations
    when the service is not present. Supports cancellation via CancelToken.

    Usage:
        executor = FluidicsExecutor(
            event_bus=event_bus,
            fluidics_service=fluidics_service,  # Optional
        )

        success = executor.execute(
            step=fluidics_step,
            cancel_token=cancel_token,
        )
    """

    def __init__(
        self,
        event_bus: EventBus,
        fluidics_service: Optional["FluidicsService"] = None,
    ):
        """Initialize the fluidics executor.

        Args:
            event_bus: EventBus for event communication
            fluidics_service: Optional FluidicsService for hardware control
        """
        self._event_bus = event_bus
        self._fluidics_service = fluidics_service

    @property
    def is_available(self) -> bool:
        """Check if fluidics hardware is available.

        Returns:
            True if FluidicsService is present and has available hardware.
        """
        if self._fluidics_service is None:
            return False
        return self._fluidics_service.is_available

    def execute(
        self,
        step: FluidicsStep,
        cancel_token: CancelToken,
    ) -> bool:
        """Execute a single fluidics step.

        Args:
            step: FluidicsStep from protocol
            cancel_token: CancelToken for pause/abort support

        Returns:
            True if successful, False otherwise.

        Raises:
            CancellationError: If operation was cancelled.
        """
        try:
            cancel_token.check_point()

            _log.info(
                f"Fluidics: {step.command.value} "
                f"solution={step.solution or 'N/A'} "
                f"volume={step.volume_ul or 0}ul"
            )

            # Execute based on command type
            if step.command == FluidicsCommand.FLOW:
                return self._execute_flow(step, cancel_token)
            elif step.command == FluidicsCommand.INCUBATE:
                return self._execute_incubate(step, cancel_token)
            elif step.command == FluidicsCommand.WASH:
                return self._execute_wash(step, cancel_token)
            elif step.command == FluidicsCommand.PRIME:
                return self._execute_prime(step, cancel_token)
            elif step.command == FluidicsCommand.ASPIRATE:
                return self._execute_aspirate(step, cancel_token)
            else:
                _log.warning(f"Unknown fluidics command: {step.command}")
                return True

        except CancellationError:
            _log.info("Fluidics step cancelled")
            # Abort any in-progress operation
            if self._fluidics_service is not None:
                self._fluidics_service.abort()
            raise

        except Exception as e:
            _log.exception(f"Fluidics execution error: {e}")
            return False

    def _execute_flow(self, step: FluidicsStep, cancel_token: CancelToken) -> bool:
        """Execute a FLOW command (pump solution at specified rate).

        Args:
            step: FluidicsStep with solution, volume, flow_rate
            cancel_token: CancelToken for pause/abort

        Returns:
            True if successful
        """
        cancel_token.check_point()

        volume = step.volume_ul or 0
        flow_rate = step.flow_rate_ul_per_min or DEFAULT_FLOW_RATE
        solution = step.solution

        if not self.is_available:
            _log.debug(
                f"[SIMULATED] FLOW: {solution} "
                f"{volume}ul at {flow_rate}ul/min"
            )
            # Simulate timing
            self._wait_with_cancel(
                duration_s=volume / flow_rate * 60.0 if flow_rate > 0 else 0,
                cancel_token=cancel_token,
                max_duration_s=5.0,  # Cap simulation at 5 seconds
            )
            return True

        # Use FluidicsService
        try:
            if solution:
                return self._fluidics_service.flow_solution_by_name(
                    solution_name=solution,
                    volume_ul=volume,
                    flow_rate_ul_per_min=flow_rate,
                )
            else:
                _log.error("FLOW command without solution name - failing step")
                return False

        except RuntimeError as e:
            if "busy" in str(e).lower():
                _log.warning(f"Fluidics busy, cannot flow: {e}")
                return False
            raise

        except ValueError as e:
            _log.error(f"Invalid flow parameters: {e}")
            return False

    def _execute_incubate(self, step: FluidicsStep, cancel_token: CancelToken) -> bool:
        """Execute an INCUBATE command (wait for specified duration).

        Args:
            step: FluidicsStep with duration_s
            cancel_token: CancelToken for pause/abort

        Returns:
            True if successful
        """
        duration_s = step.duration_s or 0
        solution = step.solution

        _log.debug(f"INCUBATE: {duration_s}s (solution={solution})")

        if self.is_available:
            # Use service incubation with progress events and cancel token
            return self._fluidics_service.incubate(
                duration_seconds=duration_s,
                solution=solution,
                progress_interval=1.0,
                cancel_token=cancel_token,
            )
        else:
            # Simulate with cancel token checks
            self._wait_with_cancel(
                duration_s=duration_s,
                cancel_token=cancel_token,
            )
            return True

    def _execute_wash(self, step: FluidicsStep, cancel_token: CancelToken) -> bool:
        """Execute a WASH command (rinse with wash buffer).

        Args:
            step: FluidicsStep with wash parameters
            cancel_token: CancelToken for pause/abort

        Returns:
            True if successful
        """
        cancel_token.check_point()

        volume = step.volume_ul or DEFAULT_WASH_VOLUME
        flow_rate = step.flow_rate_ul_per_min or DEFAULT_FLOW_RATE
        repeats = step.repeats or DEFAULT_WASH_REPEATS
        wash_solution = step.solution or "wash_buffer"

        if not self.is_available:
            _log.debug(
                f"[SIMULATED] WASH: {repeats}x {volume}ul with {wash_solution}"
            )
            # Simulate timing
            self._wait_with_cancel(
                duration_s=volume * repeats / flow_rate * 60.0 if flow_rate > 0 else 0,
                cancel_token=cancel_token,
                max_duration_s=5.0,
            )
            return True

        # Use FluidicsService
        try:
            return self._fluidics_service.wash(
                wash_solution=wash_solution,
                volume_ul=volume,
                flow_rate_ul_per_min=flow_rate,
                repeats=repeats,
            )

        except RuntimeError as e:
            if "busy" in str(e).lower():
                _log.warning(f"Fluidics busy, cannot wash: {e}")
                return False
            raise

        except ValueError as e:
            _log.error(f"Invalid wash parameters: {e}")
            return False

    def _execute_prime(self, step: FluidicsStep, cancel_token: CancelToken) -> bool:
        """Execute a PRIME command (prime tubing with solution).

        Args:
            step: FluidicsStep with prime parameters
            cancel_token: CancelToken for pause/abort

        Returns:
            True if successful
        """
        cancel_token.check_point()

        volume = step.volume_ul or 500.0
        flow_rate = step.flow_rate_ul_per_min or 5000.0
        solution = step.solution

        if not self.is_available:
            _log.debug(f"[SIMULATED] PRIME: {solution}")
            return True

        # Use FluidicsService
        try:
            # If solution specified, prime just that port
            if solution:
                port = self._fluidics_service.get_port_for_solution(solution)
                if port is None:
                    _log.warning(f"Solution '{solution}' not found for priming")
                    return False
                ports = [port]
                final_port = port
            else:
                # Prime all ports
                ports = None
                final_port = None

            return self._fluidics_service.prime(
                ports=ports,
                volume_ul=volume,
                flow_rate_ul_per_min=flow_rate,
                final_port=final_port,
            )

        except RuntimeError as e:
            if "busy" in str(e).lower():
                _log.warning(f"Fluidics busy, cannot prime: {e}")
                return False
            raise

    def _execute_aspirate(self, step: FluidicsStep, cancel_token: CancelToken) -> bool:
        """Execute an ASPIRATE command (empty syringe to waste).

        Args:
            step: FluidicsStep (unused parameters)
            cancel_token: CancelToken for pause/abort

        Returns:
            True if successful
        """
        cancel_token.check_point()

        if not self.is_available:
            _log.debug("[SIMULATED] ASPIRATE: empty syringe to waste")
            return True

        # Use FluidicsService
        try:
            return self._fluidics_service.empty_syringe()

        except RuntimeError as e:
            if "busy" in str(e).lower():
                _log.warning(f"Fluidics busy, cannot aspirate: {e}")
                return False
            raise

    def _wait_with_cancel(
        self,
        duration_s: float,
        cancel_token: CancelToken,
        max_duration_s: Optional[float] = None,
    ) -> None:
        """Wait for specified duration with cancel token checks.

        Args:
            duration_s: Duration to wait in seconds
            cancel_token: CancelToken for pause/abort
            max_duration_s: Optional cap on wait duration (for simulation)
        """
        if max_duration_s is not None:
            duration_s = min(duration_s, max_duration_s)

        elapsed = 0.0
        interval = 0.5  # Check every 500ms

        while elapsed < duration_s:
            cancel_token.check_point()
            sleep_time = min(interval, duration_s - elapsed)
            time.sleep(sleep_time)
            elapsed += sleep_time

    def execute_sequence(
        self,
        steps: list,
        cancel_token: CancelToken,
    ) -> bool:
        """Execute a sequence of fluidics steps.

        Args:
            steps: List of FluidicsStep objects
            cancel_token: CancelToken for pause/abort

        Returns:
            True if all steps successful
        """
        for step in steps:
            for _ in range(step.repeats or 1):
                cancel_token.check_point()
                if not self.execute(step, cancel_token):
                    return False
        return True
