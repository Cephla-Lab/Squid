"""
FluidicsController - State machine controller for fluidics protocol execution.

Manages named fluidics protocols, state machine for execution control,
and provides a high-level interface for the orchestrator and GUI.

Usage:
    controller = FluidicsController(
        event_bus=event_bus,
        fluidics_service=fluidics_service,
    )

    # Load protocols from YAML
    controller.load_protocols("/path/to/fluidics_protocols.yaml")

    # Run a named protocol
    controller.run_protocol("Wash_Round1")

    # Control execution
    controller.pause()
    controller.resume()
    controller.skip_to_next_step()
    controller.stop()
"""

from __future__ import annotations

import threading
import time
from enum import Enum, auto
from typing import Callable, Dict, List, Optional, TYPE_CHECKING

import squid.core.logging
from squid.core.config.test_timing import scale_duration
from squid.core.events import (
    EventBus,
    handles,
    LoadFluidicsProtocolsCommand,
    RunFluidicsProtocolCommand,
    PauseFluidicsCommand,
    ResumeFluidicsCommand,
    StopFluidicsCommand,
    SkipFluidicsStepCommand,
    FluidicsControllerStateChanged,
    FluidicsProtocolStarted,
    FluidicsProtocolStepStarted,
    FluidicsProtocolCompleted,
    FluidicsProtocolsLoaded,
    FluidicsProtocolsLoadFailed,
    FluidicsOperationStarted,
    FluidicsOperationCompleted,
    FluidicsOperationProgress,
)
from squid.core.protocol import FluidicsCommand
from squid.core.protocol.fluidics_protocol import (
    FluidicsProtocol,
    FluidicsProtocolStep,
    FluidicsProtocolFile,
)
from squid.core.state_machine import StateMachine
from squid.core.utils.cancel_token import CancelToken

if TYPE_CHECKING:
    from squid.backend.services import FluidicsService, ServiceRegistry

_log = squid.core.logging.get_logger(__name__)

# Default flow parameters
DEFAULT_FLOW_RATE = 50.0  # ul/min
DEFAULT_WASH_VOLUME = 500.0  # ul
DEFAULT_WASH_REPEATS = 3
DEFAULT_PRIME_VOLUME = 500.0  # ul
DEFAULT_PRIME_FLOW_RATE = 5000.0  # ul/min


class FluidicsControllerState(Enum):
    """State machine states for FluidicsController."""

    IDLE = auto()
    RUNNING = auto()
    PAUSED = auto()
    STOPPED = auto()
    COMPLETED = auto()
    FAILED = auto()


class FluidicsController(StateMachine[FluidicsControllerState]):
    """Controller for fluidics protocol execution.

    Responsibilities:
    1. Load and store named protocols from YAML
    2. Execute protocols with state machine management
    3. Support pause/resume/stop/skip operations
    4. Publish progress events for UI and orchestrator
    5. Delegate low-level operations to FluidicsService

    Thread Safety:
        - State machine protected by RLock (inherited from StateMachine)
        - Protocol execution runs in worker thread
        - Pause/stop via threading.Event signals
    """

    def __init__(
        self,
        event_bus: EventBus,
        fluidics_service: Optional["FluidicsService"] = None,
        service_registry: Optional["ServiceRegistry"] = None,
    ) -> None:
        """Initialize the FluidicsController.

        Args:
            event_bus: EventBus for event communication
            fluidics_service: Optional FluidicsService for hardware control.
                              If None, operations are simulated.
            service_registry: Optional ServiceRegistry for dynamic service lookup.
                              If provided, enables late-binding to FluidicsService
                              (e.g., when service is initialized after controller).
        """
        # Initialize state machine with transitions
        transitions = {
            FluidicsControllerState.IDLE: {FluidicsControllerState.RUNNING},
            FluidicsControllerState.RUNNING: {
                FluidicsControllerState.PAUSED,
                FluidicsControllerState.STOPPED,
                FluidicsControllerState.COMPLETED,
                FluidicsControllerState.FAILED,
            },
            FluidicsControllerState.PAUSED: {
                FluidicsControllerState.RUNNING,
                FluidicsControllerState.STOPPED,
            },
            FluidicsControllerState.STOPPED: {FluidicsControllerState.IDLE},
            FluidicsControllerState.COMPLETED: {FluidicsControllerState.IDLE},
            FluidicsControllerState.FAILED: {FluidicsControllerState.IDLE},
        }
        super().__init__(
            initial_state=FluidicsControllerState.IDLE,
            transitions=transitions,
            event_bus=event_bus,
            name="FluidicsController",
        )

        self._fluidics_service_direct = fluidics_service
        self._service_registry = service_registry

        # Protocol storage
        self._protocols: Dict[str, FluidicsProtocol] = {}

        # Execution state
        self._current_protocol_name: Optional[str] = None
        self._current_step_index: int = 0
        self._total_steps: int = 0
        self._worker_thread: Optional[threading.Thread] = None
        self._last_result: Optional[FluidicsProtocolCompleted] = None
        self._last_terminal_state: Optional[FluidicsControllerState] = None

        # Control signals
        self._pause_event = threading.Event()
        self._stop_event = threading.Event()
        self._skip_event = threading.Event()
        self._empty_syringe_on_skip = True

        # Register valid commands per state
        self.register_valid_commands(
            FluidicsControllerState.IDLE,
            {RunFluidicsProtocolCommand},
        )
        self.register_valid_commands(
            FluidicsControllerState.RUNNING,
            {PauseFluidicsCommand, StopFluidicsCommand, SkipFluidicsStepCommand},
        )
        self.register_valid_commands(
            FluidicsControllerState.PAUSED,
            {ResumeFluidicsCommand, StopFluidicsCommand},
        )

    # =========================================================================
    # Protocol Management
    # =========================================================================

    def load_protocols(self, path: str) -> int:
        """Load protocols from a YAML file.

        Args:
            path: Path to the YAML file

        Returns:
            Number of protocols loaded

        Raises:
            FileNotFoundError: If file doesn't exist
            yaml.YAMLError: If YAML is malformed
            pydantic.ValidationError: If schema validation fails
        """
        try:
            protocol_file = FluidicsProtocolFile.load_from_yaml(path)
            self._protocols.update(protocol_file.protocols)
            count = len(protocol_file.protocols)
            _log.info(f"Loaded {count} protocols from {path}")

            # Publish event so UI can update
            if self._event_bus:
                self._event_bus.publish(
                    FluidicsProtocolsLoaded(
                        path=path,
                        protocols=dict(protocol_file.protocols),
                    )
                )

            return count
        except Exception as e:
            _log.error(f"Failed to load protocols from {path}: {e}")
            if self._event_bus:
                self._event_bus.publish(
                    FluidicsProtocolsLoadFailed(
                        path=path,
                        error_message=str(e),
                    )
                )
            raise

    def add_protocol(self, name: str, protocol: FluidicsProtocol) -> None:
        """Add a protocol programmatically.

        Args:
            name: Name for the protocol
            protocol: Protocol definition
        """
        self._protocols[name] = protocol

    def get_protocol(self, name: str) -> Optional[FluidicsProtocol]:
        """Get a protocol by name (case-insensitive).

        Args:
            name: Protocol name to look up

        Returns:
            FluidicsProtocol if found, None otherwise
        """
        # Try exact match first
        if name in self._protocols:
            return self._protocols[name]
        # Try case-insensitive match
        name_lower = name.lower()
        for proto_name, proto in self._protocols.items():
            if proto_name.lower() == name_lower:
                return proto
        return None

    def list_protocols(self) -> List[str]:
        """List all available protocol names.

        Returns:
            List of protocol names
        """
        return list(self._protocols.keys())

    # =========================================================================
    # Status Properties
    # =========================================================================

    @property
    def current_protocol(self) -> Optional[str]:
        """Get the name of the currently running/paused protocol."""
        return self._current_protocol_name

    @property
    def current_step_index(self) -> int:
        """Get the current step index (0-based)."""
        return self._current_step_index

    @property
    def total_steps(self) -> int:
        """Get the total number of steps in current protocol."""
        return self._total_steps

    @property
    def last_result(self) -> Optional[FluidicsProtocolCompleted]:
        """Get the last protocol completion event, if any."""
        return self._last_result

    @property
    def last_terminal_state(self) -> Optional[FluidicsControllerState]:
        """Get the last terminal state reached by the controller."""
        return self._last_terminal_state

    def _get_fluidics_service(self) -> Optional["FluidicsService"]:
        """Get the FluidicsService, with dynamic lookup fallback.

        If a direct service was provided at init, use that.
        Otherwise, look up from service registry (enables late-binding
        when service is initialized after controller creation).
        """
        # Direct reference takes priority
        if self._fluidics_service_direct is not None:
            return self._fluidics_service_direct

        # Fall back to registry lookup
        if self._service_registry is not None:
            return self._service_registry.get("fluidics")

        return None

    @property
    def is_available(self) -> bool:
        """Check if fluidics hardware is available.

        Returns:
            True if FluidicsService is present and available.
        """
        service = self._get_fluidics_service()
        if service is None:
            return False
        return service.is_available

    @property
    def is_running(self) -> bool:
        """Check if a protocol is currently running."""
        return self._is_in_state(FluidicsControllerState.RUNNING)

    @property
    def is_paused(self) -> bool:
        """Check if execution is paused."""
        return self._is_in_state(FluidicsControllerState.PAUSED)

    # =========================================================================
    # Execution Control (Public API)
    # =========================================================================

    def run_protocol(self, name: str) -> bool:
        """Start executing a named protocol.

        Args:
            name: Name of the protocol to run

        Returns:
            True if protocol was started, False if not found or invalid state
        """
        if not self._is_in_state(FluidicsControllerState.IDLE):
            _log.warning(f"Cannot run protocol '{name}': not in IDLE state")
            return False

        protocol = self.get_protocol(name)
        if protocol is None:
            _log.error(f"Protocol '{name}' not found")
            return False

        # Reset state
        self._current_protocol_name = name
        self._current_step_index = 0
        self._total_steps = protocol.total_steps()
        self._last_result = None
        self._last_terminal_state = None
        self._pause_event.clear()
        self._stop_event.clear()
        self._skip_event.clear()

        # Reset abort flag on fluidics service
        service = self._get_fluidics_service()
        if service is not None:
            service.reset_abort()

        # Transition to RUNNING
        self._transition_to(FluidicsControllerState.RUNNING)

        # Publish protocol started event
        if self._event_bus:
            self._event_bus.publish(
                FluidicsProtocolStarted(
                    protocol_name=name,
                    total_steps=self._total_steps,
                    estimated_duration_s=protocol.estimated_duration_s(),
                )
            )

        # Start worker thread
        self._worker_thread = threading.Thread(
            target=self._run_protocol_worker,
            args=(name, protocol),
            name=f"FluidicsController-{name}",
            daemon=True,
        )
        self._worker_thread.start()

        return True

    def run_protocol_blocking(
        self,
        name: str,
        cancel_token: Optional[CancelToken] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> Optional[FluidicsProtocolCompleted]:
        """Run a protocol and block until completion.

        Wraps the async ``run_protocol()`` with a polling loop that waits for
        the controller to reach a terminal state.  Supports cooperative
        cancellation via *cancel_token* and periodic progress reporting via
        *progress_callback(current_step, total_steps)*.

        Args:
            name: Name of the protocol to run.
            cancel_token: Optional CancelToken for cooperative cancellation.
            progress_callback: Optional callback ``(current_step, total_steps)``
                invoked each poll cycle while the protocol is running.

        Returns:
            The ``FluidicsProtocolCompleted`` event produced by the worker
            thread, or ``None`` if the protocol could not be started.
        """
        if not self.run_protocol(name):
            return None

        poll_interval_s = scale_duration(0.1, min_seconds=0.01)
        terminal_states = frozenset(
            {
                FluidicsControllerState.IDLE,
                FluidicsControllerState.COMPLETED,
                FluidicsControllerState.FAILED,
                FluidicsControllerState.STOPPED,
            }
        )

        while True:
            if cancel_token is not None:
                cancel_token.check_point()

            state = self.state
            if state in terminal_states:
                break

            if progress_callback is not None:
                progress_callback(self.current_step_index, self.total_steps)

            time.sleep(poll_interval_s)

        # Wait for the worker thread's _reset_to_idle() to finish so the
        # controller is ready for the next run_protocol() call immediately.
        idle_deadline = time.monotonic() + scale_duration(2.0, min_seconds=0.5)
        while self.state != FluidicsControllerState.IDLE:
            if time.monotonic() > idle_deadline:
                _log.warning("Timed out waiting for controller to return to IDLE")
                break
            time.sleep(poll_interval_s)

        return self._last_result

    def pause(self) -> bool:
        """Pause protocol execution at the next checkpoint.

        Returns:
            True if pause was requested, False if not in RUNNING state
        """
        if not self._is_in_state(FluidicsControllerState.RUNNING):
            return False

        _log.info("Pause requested")
        self._pause_event.set()

        # Abort any in-progress incubation so we pause quickly
        service = self._get_fluidics_service()
        if service is not None:
            service.abort()

        return True

    def resume(self) -> bool:
        """Resume paused protocol execution.

        Always clears the pause event to prevent the worker thread from
        entering PAUSED after the caller has already decided to resume.
        This handles the race where resume() is called before the worker
        thread reaches its pause checkpoint.

        Returns:
            True if resume was successful, False if not in PAUSED state
        """
        # Always clear pause event first to prevent late-arriving pause
        self._pause_event.clear()

        if not self._is_in_state(FluidicsControllerState.PAUSED):
            return False

        _log.info("Resuming protocol execution")

        # Reset abort flag
        service = self._get_fluidics_service()
        if service is not None:
            service.reset_abort()

        self._transition_to(FluidicsControllerState.RUNNING)

        return True

    def stop(self) -> bool:
        """Stop protocol execution.

        Returns:
            True if stop was requested
        """
        if self._is_in_state(
            FluidicsControllerState.IDLE,
            FluidicsControllerState.STOPPED,
            FluidicsControllerState.COMPLETED,
            FluidicsControllerState.FAILED,
        ):
            return False

        _log.info("Stop requested")
        self._stop_event.set()

        # Abort any in-progress operation
        service = self._get_fluidics_service()
        if service is not None:
            service.abort()

        return True

    def skip_to_next_step(self, empty_syringe: bool = True) -> bool:
        """Skip the current step and move to the next one.

        Args:
            empty_syringe: If True, empty syringe to waste before next step

        Returns:
            True if skip was requested
        """
        if not self._is_in_state(
            FluidicsControllerState.RUNNING,
            FluidicsControllerState.PAUSED,
        ):
            return False

        _log.info(f"Skip requested (empty_syringe={empty_syringe})")
        self._empty_syringe_on_skip = empty_syringe
        self._skip_event.set()

        # Abort current operation
        service = self._get_fluidics_service()
        if service is not None:
            service.abort()

        # If paused, resume so we can skip
        if self._is_in_state(FluidicsControllerState.PAUSED):
            self._pause_event.clear()
            if service is not None:
                service.reset_abort()
            self._transition_to(FluidicsControllerState.RUNNING)

        return True

    # =========================================================================
    # Event Handlers
    # =========================================================================

    @handles(LoadFluidicsProtocolsCommand)
    def _on_load_protocols_command(self, cmd: LoadFluidicsProtocolsCommand) -> None:
        """Handle command to load protocols from YAML."""
        if self._is_in_state(FluidicsControllerState.RUNNING, FluidicsControllerState.PAUSED):
            message = "Cannot load protocols while a protocol is running"
            _log.warning(message)
            if self._event_bus:
                self._event_bus.publish(
                    FluidicsProtocolsLoadFailed(path=cmd.path, error_message=message)
                )
            return

        try:
            self.load_protocols(cmd.path)
        except Exception as exc:
            _log.exception("Failed to load fluidics protocols: %s", exc)

    @handles(RunFluidicsProtocolCommand)
    def _on_run_protocol_command(self, cmd: RunFluidicsProtocolCommand) -> None:
        """Handle command to run a protocol."""
        self.run_protocol(cmd.protocol_name)

    @handles(PauseFluidicsCommand)
    def _on_pause_command(self, cmd: PauseFluidicsCommand) -> None:
        """Handle command to pause execution."""
        self.pause()

    @handles(ResumeFluidicsCommand)
    def _on_resume_command(self, cmd: ResumeFluidicsCommand) -> None:
        """Handle command to resume execution."""
        self.resume()

    @handles(StopFluidicsCommand)
    def _on_stop_command(self, cmd: StopFluidicsCommand) -> None:
        """Handle command to stop execution."""
        self.stop()

    @handles(SkipFluidicsStepCommand)
    def _on_skip_step_command(self, cmd: SkipFluidicsStepCommand) -> None:
        """Handle command to skip current step."""
        self.skip_to_next_step(empty_syringe=cmd.empty_syringe)

    # =========================================================================
    # State Machine Callbacks
    # =========================================================================

    def _publish_state_changed(
        self, old_state: FluidicsControllerState, new_state: FluidicsControllerState
    ) -> None:
        """Publish state change event."""
        if self._event_bus:
            self._event_bus.publish(
                FluidicsControllerStateChanged(
                    old_state=old_state.name,
                    new_state=new_state.name,
                    protocol_name=self._current_protocol_name,
                )
            )

    # =========================================================================
    # Worker Thread
    # =========================================================================

    def _run_protocol_worker(self, name: str, protocol: FluidicsProtocol) -> None:
        """Worker thread that executes the protocol steps.

        Args:
            name: Protocol name
            protocol: Protocol to execute
        """
        error_message: Optional[str] = None
        success = False

        try:
            step_index = 0
            estimated_remaining = protocol.estimated_duration_s()

            for step_pos, step in enumerate(protocol.steps):
                repeat = 0
                while repeat < step.repeats:
                    # Check stop signal
                    if self._stop_event.is_set():
                        _log.info("Protocol stopped by user")
                        self._transition_to(FluidicsControllerState.STOPPED)
                        self._publish_completed(name, False, step_index, "Stopped by user")
                        self._reset_to_idle()
                        return

                    # Check pause signal
                    if self._pause_event.is_set():
                        _log.info("Protocol paused")
                        self._transition_to(FluidicsControllerState.PAUSED)
                        # Wait for resume or stop
                        while self._pause_event.is_set() and not self._stop_event.is_set():
                            time.sleep(0.1)
                        if self._stop_event.is_set():
                            _log.info("Protocol stopped while paused")
                            self._transition_to(FluidicsControllerState.STOPPED)
                            self._publish_completed(name, False, step_index, "Stopped by user")
                            self._reset_to_idle()
                            return
                        _log.info("Protocol resumed")

                    # Check skip signal
                    if self._skip_event.is_set():
                        step_index = self._apply_skip(step_index, step, repeat)
                        break  # Skip remaining repeats, move to next step

                    # Update step tracking
                    self._current_step_index = step_index
                    estimated_remaining -= step.estimated_duration_s() / step.repeats

                    # Get next step description for preview
                    next_desc = None
                    if repeat < step.repeats - 1:
                        next_desc = f"{step.get_description()} (repeat {repeat + 2}/{step.repeats})"
                    elif step_pos + 1 < len(protocol.steps):
                        next_step = protocol.steps[step_pos + 1]
                        next_desc = next_step.get_description()

                    # Publish step started event
                    if self._event_bus:
                        self._event_bus.publish(
                            FluidicsProtocolStepStarted(
                                protocol_name=name,
                                step_index=step_index,
                                total_steps=self._total_steps,
                                step_description=step.get_description(),
                                next_step_description=next_desc,
                                estimated_remaining_s=max(0, estimated_remaining),
                            )
                        )

                    # Execute the step
                    step_success = self._execute_step(step)
                    if step_success:
                        step_index += 1
                        repeat += 1
                        continue

                    if self._stop_event.is_set():
                        _log.info("Protocol stopped during step")
                        self._transition_to(FluidicsControllerState.STOPPED)
                        self._publish_completed(name, False, step_index, "Stopped by user")
                        self._reset_to_idle()
                        return

                    if self._skip_event.is_set():
                        step_index = self._apply_skip(step_index, step, repeat)
                        break

                    if self._pause_event.is_set():
                        _log.info("Protocol paused during step")
                        continue

                    error_message = f"Step {step_index} failed: {step.get_description()}"
                    _log.error(error_message)
                    self._transition_to(FluidicsControllerState.FAILED)
                    self._publish_completed(name, False, step_index, error_message)
                    self._reset_to_idle()
                    return

            # All steps completed successfully
            success = True
            self._current_step_index = step_index
            _log.info(f"Protocol '{name}' completed successfully")
            self._transition_to(FluidicsControllerState.COMPLETED)
            self._publish_completed(name, True, step_index, None)
            self._reset_to_idle()

        except Exception as e:
            error_message = str(e)
            _log.exception(f"Protocol execution error: {e}")
            self._transition_to(FluidicsControllerState.FAILED)
            self._publish_completed(name, False, self._current_step_index, error_message)
            self._reset_to_idle()

    def _apply_skip(
        self, step_index: int, step: FluidicsProtocolStep, repeat: int
    ) -> int:
        """Handle skip requests and return updated step index."""
        _log.info("Step skipped by user")
        self._skip_event.clear()

        service = self._get_fluidics_service()
        if service:
            service.reset_abort()
            if self._empty_syringe_on_skip:
                service.empty_syringe()

        remaining = max(0, step.repeats - repeat)
        return step_index + remaining

    def _execute_step(self, step: FluidicsProtocolStep) -> bool:
        """Execute a single protocol step.

        Args:
            step: The step to execute

        Returns:
            True if successful
        """
        # Check for stop before executing
        if self._stop_event.is_set():
            return False

        op = step.operation

        if not self.is_available:
            # Simulation mode - publish synthetic events so GUI updates
            _log.debug(f"[SIMULATED] {step.get_description()}")

            # Publish operation started event
            if self._event_bus:
                self._event_bus.publish(
                    FluidicsOperationStarted(
                        operation=op.value,
                        port=None,
                        solution=step.solution,
                        volume_ul=step.volume_ul or 0.0,
                        flow_rate_ul_per_min=step.flow_rate_ul_per_min or DEFAULT_FLOW_RATE,
                    )
                )

            # Simulate with periodic progress updates
            duration = min(step.estimated_duration_s(), 2.0)  # Cap at 2s per step
            elapsed = 0.0
            interval = 0.2  # Update every 200ms

            while elapsed < duration:
                if self._stop_event.is_set() or self._skip_event.is_set():
                    break
                if self._pause_event.is_set():
                    # Wait for resume or stop
                    while self._pause_event.is_set() and not self._stop_event.is_set():
                        time.sleep(0.1)
                    if self._stop_event.is_set():
                        break

                # Publish progress
                if self._event_bus and duration > 0:
                    progress = min(100.0, (elapsed / duration) * 100.0)
                    self._event_bus.publish(
                        FluidicsOperationProgress(
                            operation=op.value,
                            progress_percent=progress,
                            elapsed_seconds=elapsed,
                            remaining_seconds=max(0, duration - elapsed),
                            syringe_volume_ul=None,
                        )
                    )

                time.sleep(min(interval, duration - elapsed))
                elapsed += interval

            # Publish operation completed event
            if self._event_bus:
                self._event_bus.publish(
                    FluidicsOperationCompleted(
                        operation=op.value,
                        success=True,
                        error_message=None,
                        duration_seconds=elapsed,
                    )
                )

            return True

        # Execute based on operation type
        if op == FluidicsCommand.FLOW:
            return self._execute_flow(step)
        elif op == FluidicsCommand.INCUBATE:
            return self._execute_incubate(step)
        elif op == FluidicsCommand.WASH:
            return self._execute_wash(step)
        elif op == FluidicsCommand.PRIME:
            return self._execute_prime(step)
        elif op == FluidicsCommand.ASPIRATE:
            return self._execute_aspirate(step)
        else:
            _log.warning(f"Unknown operation: {op}")
            return False

    def _execute_flow(self, step: FluidicsProtocolStep) -> bool:
        """Execute a FLOW operation."""
        service = self._get_fluidics_service()
        if service is None:
            return False

        # Ensure abort flag is cleared before starting operation
        # (handles edge cases where previous skip/abort left flag set)
        service.reset_abort()

        solution = step.solution
        if not solution:
            _log.error("FLOW operation requires solution name")
            return False

        volume = step.volume_ul or 0
        flow_rate = step.flow_rate_ul_per_min or DEFAULT_FLOW_RATE

        try:
            return service.flow_solution_by_name(
                solution_name=solution,
                volume_ul=volume,
                flow_rate_ul_per_min=flow_rate,
            )
        except RuntimeError as e:
            if "busy" in str(e).lower():
                _log.warning(f"Fluidics busy: {e}")
                return False
            raise

    def _execute_incubate(self, step: FluidicsProtocolStep) -> bool:
        """Execute an INCUBATE operation."""
        service = self._get_fluidics_service()
        if service is None:
            return False

        # Ensure abort flag is cleared before starting operation
        service.reset_abort()

        duration = step.duration_s or 0
        solution = step.solution

        return service.incubate(
            duration_seconds=duration,
            solution=solution,
            progress_interval=1.0,
            cancel_token=None,  # We handle cancellation via abort()
        )

    def _execute_wash(self, step: FluidicsProtocolStep) -> bool:
        """Execute a WASH operation."""
        service = self._get_fluidics_service()
        if service is None:
            return False

        # Ensure abort flag is cleared before starting operation
        service.reset_abort()

        wash_solution = step.solution or "wash_buffer"
        volume = step.volume_ul or DEFAULT_WASH_VOLUME
        flow_rate = step.flow_rate_ul_per_min or DEFAULT_FLOW_RATE
        try:
            return service.wash(
                wash_solution=wash_solution,
                volume_ul=volume,
                flow_rate_ul_per_min=flow_rate,
                repeats=1,
            )
        except RuntimeError as e:
            if "busy" in str(e).lower():
                _log.warning(f"Fluidics busy: {e}")
                return False
            raise

    def _execute_prime(self, step: FluidicsProtocolStep) -> bool:
        """Execute a PRIME operation."""
        service = self._get_fluidics_service()
        if service is None:
            return False

        # Ensure abort flag is cleared before starting operation
        service.reset_abort()

        solution = step.solution
        volume = step.volume_ul or DEFAULT_PRIME_VOLUME
        flow_rate = step.flow_rate_ul_per_min or DEFAULT_PRIME_FLOW_RATE

        try:
            if solution:
                port = service.get_port_for_solution(solution)
                if port is None:
                    _log.warning(f"Solution '{solution}' not found for priming")
                    return False
                ports = [port]
                final_port = port
            else:
                ports = None
                final_port = None

            return service.prime(
                ports=ports,
                volume_ul=volume,
                flow_rate_ul_per_min=flow_rate,
                final_port=final_port,
            )
        except RuntimeError as e:
            if "busy" in str(e).lower():
                _log.warning(f"Fluidics busy: {e}")
                return False
            raise

    def _execute_aspirate(self, step: FluidicsProtocolStep) -> bool:
        """Execute an ASPIRATE operation."""
        service = self._get_fluidics_service()
        if service is None:
            return False

        # Ensure abort flag is cleared before starting operation
        service.reset_abort()

        try:
            return service.empty_syringe()
        except RuntimeError as e:
            if "busy" in str(e).lower():
                _log.warning(f"Fluidics busy: {e}")
                return False
            raise

    def _wait_with_cancel(self, duration_s: float) -> None:
        """Wait for specified duration with cancel checks.

        Args:
            duration_s: Duration to wait in seconds
        """
        elapsed = 0.0
        interval = 0.1

        while elapsed < duration_s:
            if self._stop_event.is_set() or self._skip_event.is_set():
                return
            if self._pause_event.is_set():
                # Wait for resume
                while self._pause_event.is_set() and not self._stop_event.is_set():
                    time.sleep(0.1)
                if self._stop_event.is_set():
                    return
            time.sleep(min(interval, duration_s - elapsed))
            elapsed += interval

    def _publish_completed(
        self,
        name: str,
        success: bool,
        steps_completed: int,
        error_message: Optional[str],
    ) -> None:
        """Publish protocol completed event."""
        event = FluidicsProtocolCompleted(
            protocol_name=name,
            success=success,
            steps_completed=steps_completed,
            total_steps=self._total_steps,
            error_message=error_message,
        )
        self._last_terminal_state = self.state
        self._last_result = event
        if self._event_bus:
            self._event_bus.publish(event)

    def _reset_to_idle(self) -> None:
        """Reset controller to IDLE state after completion/stop/failure."""
        # Wait a moment then transition to IDLE
        time.sleep(0.1)
        current = self.state
        if current in (
            FluidicsControllerState.STOPPED,
            FluidicsControllerState.COMPLETED,
            FluidicsControllerState.FAILED,
        ):
            self._transition_to(FluidicsControllerState.IDLE)
            self._current_protocol_name = None
