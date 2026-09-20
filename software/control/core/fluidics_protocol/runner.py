"""ProtocolRunner - executes a resolved protocol step by step on its own thread.

States: IDLE -> RUNNING <-> (PAUSE_REQUESTED ->) PAUSED; RUNNING/PAUSED -> HELD -> RUNNING | ENDED;
RUNNING -> ENDED(finished | stopped | failed). Failure of any kind parks the run in HELD with four
actions (resume from sequence k / restart step / skip / end run). The runner blocks only on Events, is the
only writer of run_manifest.json, and never opens a dialog - the GUI adapts its events (phase 2).
"""

import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import squid.logging
from control.core.fluidics_protocol import manifest as manifest_io
from control.core.fluidics_protocol.events import (
    Hold,
    HoldAction,
    Listener,
    RunFinished,
    RunnerState,
    SequenceProgress,
    StateChanged,
    StepEnded,
    StepStarted,
)
from control.core.fluidics_protocol.ports import (
    FluidicsOutcome,
    FluidicsPort,
    ImagingPort,
    ImagingRequest,
    ImagingResult,
)
from control.core.fluidics_protocol.resolve import ResolvedProtocol
from control.models.fluidics_protocol import (
    FluidicsStep,
    ImagingStep,
    load_protocol,
    protocol_to_dict,
    save_protocol,
    imaging_folder,
    strip_for_library,
)
from control.models.fluidics_run import AttemptRecord, RunCursor, RunManifest, StepRecord, TecState

_OK_IMAGING = ("completed",)


@dataclass(frozen=True)
class RunnerSnapshot:
    state: RunnerState
    step_index: Optional[int]
    attempt: int
    total_steps: int
    hold: Optional[Hold]
    outcome: Optional[str]
    elapsed_s: float
    progress_fraction: Optional[float]  # 0..1 how-far-through, or None when there's no estimate to go on


@dataclass
class _StepResult:
    ok: bool
    outcome: str
    message: Optional[str] = None
    resume_position: Optional[int] = None
    can_accept: bool = False


class ProtocolRunner:
    def __init__(
        self,
        resolved: ResolvedProtocol,
        run_dir,
        imaging: ImagingPort,
        fluidics: FluidicsPort,
        run_name: str,
        listener: Optional[Listener] = None,
        manifest: Optional[RunManifest] = None,
        heartbeat_s: float = 5.0,
        poll_s: float = 0.05,
    ):
        self._log = squid.logging.get_logger(__name__)
        self._resolved = resolved
        self._steps = resolved.steps
        self.run_dir = Path(run_dir)
        self._imaging = imaging
        self._fluidics = fluidics
        self._listener: Listener = listener or (lambda event: None)
        self._heartbeat_s = heartbeat_s
        self._poll_s = poll_s
        self._recovering = manifest is not None
        if manifest is not None:
            self._check_recovery_manifest(manifest, resolved, self.run_dir)
        now = time.time()
        self._manifest = manifest or RunManifest(
            run_name=run_name,
            run_dir=str(self.run_dir),
            protocol_name=resolved.protocol.name,
            status="running",
            steps=[
                StepRecord(index=s.index, kind=s.kind, round=s.round, label=s.label, row_indices=self._row_indices(s))
                for s in self._steps
            ],
            pid=os.getpid(),
            heartbeat_at=now,
            started_at=now,
        )
        self._manifest.pid = os.getpid()
        self._lock = threading.RLock()
        self._state = RunnerState.IDLE
        self._hold: Optional[Hold] = None
        self._outcome: Optional[str] = None
        self._pause_requested = threading.Event()
        self._resume_requested = threading.Event()
        self._abort_step_requested = threading.Event()
        self._abort_run_requested = threading.Event()
        self._hold_decision: Optional[Tuple[HoldAction, bool]] = None
        self._hold_event = threading.Event()
        self._done = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_heartbeat = 0.0
        self._log_handler = None
        self._current_step: Optional[int] = None
        self._current_attempt = 0
        self._active = None  # the ticket/handle being driven, aborted if the runner itself crashes
        self._tec_before: Optional[TecState] = None

    # ---- public API (any thread) ----

    @property
    def state(self) -> RunnerState:
        return self._state

    @property
    def hold(self) -> Optional[Hold]:
        return self._hold

    @property
    def outcome(self) -> Optional[str]:
        return self._outcome

    @property
    def manifest(self) -> RunManifest:
        with self._lock:
            return self._manifest.model_copy(deep=True)

    def snapshot(self) -> RunnerSnapshot:
        with self._lock:
            elapsed = time.time() - self._manifest.started_at
            return RunnerSnapshot(
                state=self._state,
                step_index=self._current_step,
                attempt=self._current_attempt,
                total_steps=len(self._steps),
                hold=self._hold,
                outcome=self._outcome,
                elapsed_s=elapsed,
                progress_fraction=self._progress_fraction(elapsed),
            )

    def _progress_fraction(self, elapsed_s: float) -> Optional[float]:
        """How far through the run, 0..1, or None when there's no time estimate to go on (the
        view then falls back to counting completed steps). Priced off the rough total estimate,
        so hold just under full until the run actually ends."""
        if self._state is RunnerState.ENDED:
            return 1.0
        estimate = self._resolved.total_estimate_s
        if estimate and estimate > 0:
            return min(0.99, elapsed_s / estimate)
        return None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("ProtocolRunner.start() called twice")
        self._thread = threading.Thread(target=self._run, name="fluidics-protocol", daemon=True)
        self._thread.start()

    def pause(self) -> None:
        self._resume_requested.clear()
        self._pause_requested.set()

    def resume(self) -> None:
        self._pause_requested.clear()
        self._resume_requested.set()

    def abort_step(self) -> None:
        self._abort_step_requested.set()

    def abort_run(self) -> None:
        self._abort_run_requested.set()
        self._resume_requested.set()  # wake a paused run
        self._hold_decision = (HoldAction.END, False)
        self._hold_event.set()

    def hold_action(self, action: HoldAction, restore_tec: bool = False) -> None:
        if self._state != RunnerState.HELD:
            raise RuntimeError(f"No hold to act on (state {self._state.value})")
        self._hold_decision = (action, restore_tec)
        self._hold_event.set()

    def wait(self, timeout: Optional[float] = None) -> bool:
        if not self._done.wait(timeout):
            return False
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
            return not thread.is_alive()
        return True

    # ---- runner thread ----

    def _run(self) -> None:
        outcome = "failed"
        try:
            self._open_run()
            outcome = self._loop()
        except Exception as e:  # a bug in the runner itself must still leave a readable manifest and a safe system
            self._log.exception("Protocol runner crashed")
            outcome = "failed"
            self._manifest.hold_message = str(e)
            active, self._active = self._active, None
            if active is not None:
                self._safe(active.abort, "aborting the interrupted step")
            self._safe(self._fluidics.make_safe, "making the fluidics system safe")
        finally:
            self._close_run(outcome)

    def _open_run(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._log_handler = squid.logging.add_file_handler(
                str(self.run_dir / manifest_io.RUN_LOG_NAME), replace_existing=True
            )
        except Exception:
            self._log.exception("Could not open run.log")
        protocol_copy = self.run_dir / manifest_io.PROTOCOL_COPY_NAME
        if not self._recovering or not protocol_copy.exists():
            save_protocol(self._resolved.protocol, str(protocol_copy))
        if not self._recovering or not self._manifest.protocol_sha256:
            self._manifest.protocol_sha256 = manifest_io.sha256_of_file(protocol_copy)
        self._set_state(RunnerState.RUNNING, status="running")
        self._log.info(f"Protocol run '{self._manifest.run_name}' started: {len(self._steps)} steps in {self.run_dir}")

    def _close_run(self, outcome: str) -> None:
        with self._lock:
            self._outcome = outcome
            self._manifest.status = outcome
            self._manifest.ended_at = time.time()
            self._manifest.cursor = RunCursor(step=self._current_step, attempt=self._current_attempt, sequence=None)
            self._save()
        self._state = RunnerState.ENDED
        self._emit(StateChanged(RunnerState.ENDED, None))
        self._emit(RunFinished(outcome))
        self._log.info(f"Protocol run ended: {outcome}")
        if self._log_handler is not None:
            squid.logging.remove_handler(self._log_handler)
            self._log_handler = None
        self._done.set()

    def _loop(self) -> str:
        index = 0
        resume_position: Optional[int] = None
        pending_hold: Optional[Hold] = None
        if self._recovering and self._manifest.cursor.step is not None and not self._manifest.is_terminal:
            index = self._manifest.cursor.step
            pending_hold = self._recovery_hold(index)

        while index < len(self._steps):
            step = self._steps[index]
            if pending_hold is None:
                if self._abort_run_requested.is_set():
                    return "stopped"
                self._pause_gate()
                if self._abort_run_requested.is_set():
                    return "stopped"
                attempt = self._begin_attempt(step)
                if isinstance(step, FluidicsStep):
                    result = self._run_fluidics_step(step, attempt, resume_position)
                else:
                    result = self._run_imaging_step(step, attempt)
                resume_position = None
                if result.ok:
                    index += 1
                    self._advance_cursor(index)
                    continue
                if self._abort_run_requested.is_set():
                    return "stopped"
                pending_hold = Hold(
                    step_index=step.index,
                    attempt=attempt,
                    kind=step.kind,
                    reason=result.outcome,
                    message=result.message,
                    resume_position=result.resume_position,
                    tec_before=self._tec_before,
                    can_resume=isinstance(step, FluidicsStep) and result.resume_position is not None,
                    can_accept=result.can_accept,
                )
                if isinstance(step, ImagingStep):
                    # The library makes itself safe when a fluidics run ends early; imaging-side holds must do it here.
                    self._safe(self._fluidics.make_safe, "making the fluidics system safe")

            action, restore_tec = self._enter_hold(pending_hold)
            hold, pending_hold = pending_hold, None
            if restore_tec and hold.tec_before is not None:
                self._safe(lambda: self._fluidics.restore_tec(hold.tec_before), "restoring the TEC")
            if action is HoldAction.END:
                return "stopped"
            if action in (HoldAction.SKIP, HoldAction.ACCEPT):
                if action is HoldAction.SKIP:
                    with self._lock:
                        self._manifest.steps[step.index].skipped = True
                index += 1
                self._advance_cursor(index)
                continue
            if action is HoldAction.RESUME and hold.can_resume:
                resume_position = hold.resume_position
            else:
                resume_position = None
            # RESUME / RESTART: run the same step again as a new attempt
        return "finished"

    # ---- steps ----

    def _run_fluidics_step(self, step: FluidicsStep, attempt: int, resume_position: Optional[int]) -> _StepResult:
        rows = strip_for_library(step.rows)
        offset = int(resume_position or 0)
        try:
            plan = self._fluidics.plan(rows)
            tail = plan[offset:] if offset else plan
            self._set_cursor_sequence(offset)
            ticket = self._fluidics.start(rows, plan=tail)
        except Exception as e:  # a refused/failed launch holds like everything else
            self._log.error(f"Fluidics step {step.label} could not start: {e}")
            self._end_attempt(step, attempt, "failed_to_start", str(e))
            return _StepResult(ok=False, outcome="failed_to_start", message=str(e))
        # _active stays set if the drive raises, so _run's crash handler can abort it; cleared on a clean return.
        self._active = ticket
        outcome = self._drive_fluidics(ticket, step, offset, len(plan))
        self._active = None
        position = None if outcome.position is None else offset + int(outcome.position)
        self._end_attempt(
            step,
            attempt,
            outcome.outcome,
            outcome.message,
            elapsed_s=outcome.elapsed_seconds,
            ended_position=position,
            fluidics_run_id=outcome.run_id or getattr(ticket, "run_id", None),
            reagent_used_ul={str(k): float(v) for k, v in outcome.reagent_used_ul.items()},
        )
        if outcome.outcome == "finished":
            return _StepResult(ok=True, outcome="finished")
        return _StepResult(
            ok=False,
            outcome=outcome.outcome,
            message=outcome.message,
            resume_position=position if position is not None else 0,
        )

    def _drive_fluidics(self, ticket, step: FluidicsStep, offset: int, total: int) -> FluidicsOutcome:
        aborted = False
        pause_deferred = False  # the library refused the pause (no gate left): park at the step boundary
        last_position = None
        while True:
            result = ticket.wait(self._poll_s)
            if result is not None:
                self._abort_step_requested.clear()
                if pause_deferred or self._state in (RunnerState.PAUSE_REQUESTED, RunnerState.PAUSED):
                    # The operator asked to pause and the step ended before/while holding: honour it at the boundary.
                    self._pause_requested.set()
                if self._state is not RunnerState.RUNNING:
                    self._set_state(RunnerState.RUNNING, status="running")
                return result
            position = getattr(ticket, "position", None)
            if position is not None and position != last_position:
                last_position = position
                self._set_cursor_sequence(offset + int(position))
                self._emit(SequenceProgress(step.index, offset + int(position), total, step.label))
            self._heartbeat()
            if (self._abort_step_requested.is_set() or self._abort_run_requested.is_set()) and not aborted:
                aborted = True
                ticket.abort()
            if self._pause_requested.is_set() and self._state is RunnerState.RUNNING and not pause_deferred:
                self._pause_requested.clear()
                if ticket.pause():
                    self._set_state(RunnerState.PAUSE_REQUESTED, status="paused")
                else:
                    pause_deferred = True
            if self._state is RunnerState.PAUSE_REQUESTED and ticket.at_rest():
                self._set_state(RunnerState.PAUSED, status="paused")
            if self._resume_requested.is_set():
                if self._state in (RunnerState.PAUSE_REQUESTED, RunnerState.PAUSED):
                    self._resume_requested.clear()
                    ticket.resume()
                    self._set_state(RunnerState.RUNNING, status="running")
                elif pause_deferred:
                    self._resume_requested.clear()
                    pause_deferred = False

    def _run_imaging_step(self, step: ImagingStep, attempt: int) -> _StepResult:
        resolved = self._resolved.imaging[step.row_index]
        # the output folder is {round}_{folder_name}; the folder_name field is a stable base
        base = imaging_folder(step.round, step.row.folder)
        folder = base if attempt == 1 else f"{base}_attempt{attempt}"
        info = {
            "protocol": self._resolved.protocol.name,
            "run_name": self._manifest.run_name,
            "round": step.round,
            "step": step.row.name,
            "row_index": step.row_index,
            "step_index": step.index,
            "attempt": attempt,
        }
        request = ImagingRequest(
            folder=folder,
            run_dir=str(self.run_dir),
            settings=resolved.settings,
            coordinates=resolved.coordinates,
            step_index=step.index,
            attempt=attempt,
            protocol=info,
        )
        with self._lock:
            self._manifest.steps[step.index].attempts[-1].folder = folder
            self._save()
        try:
            handle = self._imaging.start(request)
        except Exception as e:
            self._log.error(f"Imaging step {step.label} could not start: {e}")
            self._end_attempt(step, attempt, "failed_to_start", str(e), folder=folder)
            return _StepResult(ok=False, outcome="failed_to_start", message=str(e))
        session_dir = self.run_dir / folder
        if session_dir.is_dir():
            self._safe(lambda: manifest_io.write_step_info(session_dir, info), "writing protocol_step.json")
        self._active = handle
        result = self._drive_imaging(handle)
        self._active = None
        self._end_attempt(step, attempt, result.end_reason, None, folder=folder, images=result.image_count)
        if result.end_reason in _OK_IMAGING:
            return _StepResult(ok=True, outcome=result.end_reason)
        if result.end_reason == "completed_with_errors":
            return _StepResult(
                ok=False, outcome=result.end_reason, message="Some images failed to save", can_accept=True
            )
        if session_dir.is_dir():
            marker = {"reason": result.end_reason, "attempt": attempt, "ended_at": time.time(), **info}
            self._safe(lambda: manifest_io.write_aborted_marker(session_dir, marker), "writing aborted.json")
        return _StepResult(ok=False, outcome=result.end_reason, message=f"Acquisition ended with {result.end_reason}")

    def _drive_imaging(self, handle) -> ImagingResult:
        aborted = False
        while True:
            result = handle.wait(self._poll_s)
            if result is not None:
                self._abort_step_requested.clear()
                return result
            self._heartbeat()
            if (self._abort_step_requested.is_set() or self._abort_run_requested.is_set()) and not aborted:
                aborted = True
                handle.abort()
            # pause: nothing to do here - the run parks at the step boundary (_pause_gate)

    # ---- pause / hold ----

    def _pause_gate(self) -> None:
        if not self._pause_requested.is_set():
            return
        self._pause_requested.clear()
        self._set_state(RunnerState.PAUSED, status="paused")
        while not self._resume_requested.wait(self._poll_s):
            self._heartbeat()
            if self._abort_run_requested.is_set():
                return
        self._resume_requested.clear()
        if not self._abort_run_requested.is_set():
            self._set_state(RunnerState.RUNNING, status="running")

    def _enter_hold(self, hold: Hold) -> Tuple[HoldAction, bool]:
        self._hold_decision = None
        self._hold_event.clear()
        with self._lock:
            self._hold = hold
            self._manifest.hold_reason = hold.reason
            self._manifest.hold_message = hold.message
        self._set_state(RunnerState.HELD, status="held")
        self._log.warning(
            f"Protocol held at step {hold.step_index + 1} ({hold.kind}, attempt {hold.attempt}): "
            f"{hold.reason} {hold.message or ''}"
        )
        while not self._hold_event.wait(self._poll_s):
            self._heartbeat()
            if self._abort_run_requested.is_set():
                break
        if self._abort_run_requested.is_set():
            decision = (HoldAction.END, False)
        else:
            decision = self._hold_decision or (HoldAction.END, False)
        with self._lock:
            self._hold = None
            self._manifest.hold_reason = None
            self._manifest.hold_message = None
        self._pause_requested.clear()
        self._abort_step_requested.clear()
        if decision[0] is not HoldAction.END:
            self._set_state(RunnerState.RUNNING, status="running")
        return decision

    def _recovery_hold(self, index: int) -> Hold:
        step = self._steps[index]
        cursor = self._manifest.cursor
        record = self._manifest.steps[step.index]
        last = record.attempts[-1] if record.attempts else None
        if last is not None and last.outcome is None:
            last.outcome = "error"
            last.message = "The GUI stopped while this attempt was running"
            last.ended_at = time.time()
            if isinstance(step, ImagingStep) and last.folder and (self.run_dir / last.folder).is_dir():
                marker_dir = self.run_dir / last.folder
                marker = {"reason": "crash", "attempt": last.attempt}
                self._safe(lambda: manifest_io.write_aborted_marker(marker_dir, marker), "writing aborted.json")
        self._safe(self._fluidics.make_safe, "making the fluidics system safe")
        self._current_step, self._current_attempt = step.index, cursor.attempt
        return Hold(
            step_index=step.index,
            attempt=cursor.attempt,
            kind=step.kind,
            reason="recovered",
            message=(
                f"The GUI stopped while step {step.index + 1} ({step.label}) was running. "
                "Pump and valve state is unknown - check before continuing."
            ),
            resume_position=cursor.sequence if isinstance(step, FluidicsStep) else None,
            tec_before=self._manifest.tec,
            can_resume=isinstance(step, FluidicsStep) and cursor.sequence is not None,
            can_accept=False,
        )

    # ---- manifest bookkeeping (runner thread only) ----

    @staticmethod
    def _check_recovery_manifest(manifest: RunManifest, resolved: ResolvedProtocol, run_dir: Path) -> None:
        """A manifest may only resume the protocol it recorded: same steps, same protocol.yaml."""
        recorded = [(s.index, s.kind) for s in manifest.steps]
        current = [(s.index, s.kind) for s in resolved.steps]
        if recorded != current:
            raise ValueError("The run manifest does not match this protocol's steps; it cannot be resumed")
        protocol_copy = run_dir / manifest_io.PROTOCOL_COPY_NAME
        if not protocol_copy.exists():
            raise ValueError("protocol.yaml is missing from the run folder; the run cannot be resumed")
        if manifest.protocol_sha256 and manifest_io.sha256_of_file(protocol_copy) != manifest.protocol_sha256:
            raise ValueError("protocol.yaml in the run folder differs from the one the manifest recorded")
        if protocol_to_dict(load_protocol(str(protocol_copy))) != protocol_to_dict(resolved.protocol):
            raise ValueError(
                "The protocol being resumed differs from protocol.yaml in the run folder; resume from that file"
            )

    @staticmethod
    def _row_indices(step) -> list:
        return list(step.row_indices) if isinstance(step, FluidicsStep) else [step.row_index]

    def _begin_attempt(self, step) -> int:
        with self._lock:
            record = self._manifest.steps[step.index]
            attempt = len(record.attempts) + 1
            record.attempts.append(AttemptRecord(attempt=attempt, started_at=time.time()))
            self._current_step, self._current_attempt = step.index, attempt
            self._manifest.cursor = RunCursor(step=step.index, attempt=attempt, sequence=None)
            self._tec_before = self._safe(self._fluidics.tec_state, "reading the TEC state")
            self._manifest.tec = self._tec_before
            self._save()
        self._emit(StepStarted(step.index, attempt, step.kind, step.label))
        self._log.info(f"Step {step.index + 1}/{len(self._steps)} ({step.kind} {step.label}) attempt {attempt} started")
        return attempt

    def _advance_cursor(self, next_index: int) -> None:
        """A finished/skipped step must never be offered for resume: point the cursor at the next one, on disk."""
        with self._lock:
            step = next_index if next_index < len(self._steps) else None
            self._manifest.cursor = RunCursor(step=step, attempt=0, sequence=None)
            self._save()

    def _set_cursor_sequence(self, position: int) -> None:
        with self._lock:
            self._manifest.cursor.sequence = position
            self._save()

    def _end_attempt(self, step, attempt: int, outcome: str, message: Optional[str], **fields) -> None:
        with self._lock:
            record = self._manifest.steps[step.index].attempts[-1]
            record.outcome = outcome
            record.message = message
            record.ended_at = time.time()
            for key, value in fields.items():
                setattr(record, key, value)
            if record.elapsed_s is None and record.started_at is not None:
                record.elapsed_s = record.ended_at - record.started_at
            self._save()
        self._emit(StepEnded(step.index, attempt, outcome, message))
        self._log.info(f"Step {step.index + 1} attempt {attempt} ended: {outcome} {message or ''}")

    def _set_state(self, state: RunnerState, status: Optional[str] = None) -> None:
        # Write the manifest before publishing the state, so anyone who sees the new state on `runner.state`
        # also finds it on disk (the GUI reads one, the recovery path the other).
        with self._lock:
            if status is not None:
                self._manifest.status = status
            self._save()
            self._state = state
        self._emit(StateChanged(state, self._hold if state is RunnerState.HELD else None))

    def _heartbeat(self) -> None:
        now = time.time()
        if now - self._last_heartbeat < self._heartbeat_s:
            return
        self._last_heartbeat = now
        with self._lock:
            self._save()

    def _save(self) -> None:
        self._manifest.heartbeat_at = time.time()
        try:
            manifest_io.write_manifest(self.run_dir, self._manifest)
        except OSError as e:
            self._log.warning(f"Could not write the run manifest: {e}")

    def _emit(self, event) -> None:
        try:
            self._listener(event)
        except Exception:
            self._log.exception("Runner listener failed")

    def _safe(self, fn, doing: str):
        try:
            return fn()
        except Exception as e:
            self._log.warning(f"Error {doing}: {e}")
            return None
