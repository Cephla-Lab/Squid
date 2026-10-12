"""FluidicsPort over the Squid-Fluidics library's FluidicsSystem (one per initialized service)."""

import threading
from typing import List, Optional

from fluidics.events import RunEnded, RunStarted, SequenceStarted
from fluidics.sequences import SequenceListAdapter, validate_sequences
from pydantic import ValidationError

import squid.logging
from control.core.fluidics_protocol.ports import FluidicsOutcome
from control.models.fluidics_run import TecState

_log = squid.logging.get_logger(__name__)


class LibraryTicket:
    """One library run. RunEnded is copied on the library's job thread (fields + usage rows) and an Event
    is set; wait() runs on the runner thread and also waits for the session to be free again."""

    def __init__(self, system):
        self._system = system
        self.run_id: Optional[str] = None
        self.position: Optional[int] = None  # plan index of the sequence in flight (relative to the plan handed in)
        self._outcome: Optional[FluidicsOutcome] = None
        self._ended = threading.Event()

    def _on_event(self, event) -> None:
        if isinstance(event, RunStarted) and self.run_id is None:
            self.run_id = event.run_id
        elif isinstance(event, SequenceStarted) and (self.run_id is None or event.run_id == self.run_id):
            self.position = event.position
        elif isinstance(event, RunEnded) and (self.run_id is None or event.run_id == self.run_id):
            try:
                usage = {int(port): float(ul) for port, _name, ul in self._system.usage.rows()}
            except Exception as e:  # never let bookkeeping hide the outcome
                _log.warning(f"Could not read reagent usage: {e}")
                usage = {}
            self._outcome = FluidicsOutcome(
                outcome=event.outcome,
                message=event.message,
                elapsed_seconds=float(event.elapsed_seconds or 0.0),
                position=event.position,
                run_id=event.run_id,
                reagent_used_ul=usage,
            )
            self._ended.set()

    def wait(self, timeout: float) -> Optional[FluidicsOutcome]:
        if not self._ended.wait(timeout):
            return None
        if not self._system.wait(timeout):  # let the job thread unwind before anyone starts the next run
            return None
        return self._outcome

    def pause(self) -> bool:
        return bool(self._system.pause())

    def resume(self) -> bool:
        return bool(self._system.resume())

    def abort(self) -> bool:
        return bool(self._system.abort())

    def at_rest(self) -> bool:
        snapshot = self._system.session.snapshot()
        return bool(snapshot.paused and snapshot.at_rest)


class LibraryFluidicsPort:
    def __init__(self, system):
        self._system = system
        self._config = system.devices.config
        self._current: Optional[LibraryTicket] = None
        self._lock = threading.Lock()
        system.session.events.subscribe(self._dispatch)

    def _dispatch(self, event) -> None:
        with self._lock:
            ticket = self._current
        if ticket is not None:
            ticket._on_event(event)
            if isinstance(event, RunEnded):
                with self._lock:
                    if self._current is ticket:
                        self._current = None

    def validate(self, rows: List[dict]) -> None:
        try:
            SequenceListAdapter.validate_python(rows)
            validate_sequences(rows, self._config)
        except (ValidationError, ValueError) as e:
            raise ValueError(str(e)) from e

    def plan(self, rows: List[dict]) -> tuple:
        return tuple(self._system.plan(rows))

    def start(self, rows: List[dict], plan: Optional[tuple] = None) -> LibraryTicket:
        ticket = LibraryTicket(self._system)
        with self._lock:
            if self._current is not None:
                raise RuntimeError("a fluidics run is already in flight on this port")
            self._current = ticket
        try:
            # RunStarted arrives synchronously inside run(); no runner lock is held here.
            self._system.run(None if plan is not None else rows, plan=plan)
        except BaseException:
            with self._lock:
                if self._current is ticket:
                    self._current = None
            raise
        return ticket

    def make_safe(self) -> List[Exception]:
        return list(self._system.make_safe())

    def tec_state(self) -> Optional[TecState]:
        tc = self._system.devices.temperature_controller
        if tc is None:
            return None
        return TecState(
            targets=[float(t) for t in tc.target_temperatures], output_enabled=[bool(o) for o in tc.output_enabled]
        )

    def restore_tec(self, state: TecState) -> None:
        tc = self._system.devices.temperature_controller
        if tc is None:
            return
        for channel, (target, on) in enumerate(zip(state.targets, state.output_enabled), start=1):
            tc.set_target_temperature(channel, target)
            tc.set_output_enabled(channel, on)

    def disable_tec(self) -> None:
        tc = self._system.devices.temperature_controller
        if tc is None:
            return
        for channel in range(1, tc.channels + 1):
            tc.set_output_enabled(channel, False)
