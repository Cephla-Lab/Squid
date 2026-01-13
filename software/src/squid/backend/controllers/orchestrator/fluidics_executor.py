"""
Fluidics executor for orchestrated experiments.

Executes fluidics protocol steps (flow, incubate, wash, prime).
This is a skeleton for future hardware integration - currently logs
commands but does not execute actual hardware operations.
"""

from typing import Optional, TYPE_CHECKING

import squid.core.logging
from squid.core.events import EventBus
from squid.core.utils.cancel_token import CancelToken, CancellationError
from squid.core.protocol import FluidicsStep, FluidicsCommand

if TYPE_CHECKING:
    from squid.backend.services import FluidicsService

_log = squid.core.logging.get_logger(__name__)


class FluidicsExecutor:
    """Executes fluidics protocol steps.

    This is a skeleton for future fluidics hardware integration.
    Currently logs commands but doesn't execute hardware operations.

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
            True if successful, False otherwise
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
            else:
                _log.warning(f"Unknown fluidics command: {step.command}")
                return True

        except CancellationError:
            _log.info("Fluidics step cancelled")
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
        if self._fluidics_service is None:
            _log.debug(
                f"[SIMULATED] FLOW: {step.solution} "
                f"{step.volume_ul}ul at {step.flow_rate_ul_per_min}ul/min"
            )
            return True

        _log.error("FluidicsService FLOW execution is not implemented")
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

        _log.debug(f"[SIMULATED] INCUBATE: {duration_s}s")

        # Wait with cancel token checks
        import time
        elapsed = 0.0
        while elapsed < duration_s:
            cancel_token.check_point()
            time.sleep(min(0.5, duration_s - elapsed))
            elapsed += 0.5

        return True

    def _execute_wash(self, step: FluidicsStep, cancel_token: CancelToken) -> bool:
        """Execute a WASH command (rinse with wash buffer).

        Args:
            step: FluidicsStep with wash parameters
            cancel_token: CancelToken for pause/abort

        Returns:
            True if successful
        """
        if self._fluidics_service is None:
            _log.debug(
                f"[SIMULATED] WASH: {step.repeats}x "
                f"{step.volume_ul or 0}ul"
            )
            return True

        _log.error("FluidicsService WASH execution is not implemented")
        return False

    def _execute_prime(self, step: FluidicsStep, cancel_token: CancelToken) -> bool:
        """Execute a PRIME command (prime tubing with solution).

        Args:
            step: FluidicsStep with prime parameters
            cancel_token: CancelToken for pause/abort

        Returns:
            True if successful
        """
        if self._fluidics_service is None:
            _log.debug(f"[SIMULATED] PRIME: {step.solution}")
            return True

        _log.error("FluidicsService PRIME execution is not implemented")
        return False

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
            for repeat in range(step.repeats):
                cancel_token.check_point()
                if not self.execute(step, cancel_token):
                    return False
        return True
