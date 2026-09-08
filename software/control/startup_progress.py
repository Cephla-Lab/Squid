"""Headless progress tracking for hardware initialization.

Startup used to be a black hole: `Microscope.build_from_global_config` and
`HighContentScreeningGui.__init__` run back to back with nothing on screen, and
any failure reached the excepthook in `squid.logging` and killed the process
without a dialog.  This module is the transport-neutral half of the fix - it
records what startup is doing, one row per device, and it deliberately imports
no Qt so it can be used from tests and headless scripts.

`StartupReporter` is a no-op sink.  The module singleton `NULL_REPORTER` is the
default argument for every `reporter=` parameter added to the build path, so
existing positional callers keep working unchanged.  The Qt subclass that owns
the actual window lives in `control.startup_progress_window`.
"""

import contextlib
import time
from dataclasses import dataclass
from enum import IntEnum
from typing import Callable, Dict, Iterator, List, Optional, Tuple

import squid.logging

log = squid.logging.get_logger(__name__)


class StepState(IntEnum):
    """State of a single startup step.

    Values are ordered from "not started" to "finished"; do not rely on the
    numbers, they exist only so the enum is comparable and hashable.
    """

    PENDING = 0
    CONNECTING = 1
    WARMING_UP = 2
    READY = 3
    WARNING = 4
    FAILED = 5
    NOT_FOUND = 6
    SKIPPED = 7


# Labels and colors match control/laser_engine_widget.py, which already renders
# "Warming up" / "Ready" / "Error" for the laser engine.  Keeping them identical
# means the startup window and the laser engine tab do not disagree about what
# amber means.
_STATE_LABELS = {
    StepState.PENDING: "Pending",
    StepState.CONNECTING: "Connecting…",
    StepState.WARMING_UP: "Warming up",
    StepState.READY: "Ready",
    StepState.WARNING: "Warning",
    StepState.FAILED: "Failed",
    StepState.NOT_FOUND: "Not found",
    StepState.SKIPPED: "Skipped",
}

_STATE_COLORS = {
    StepState.PENDING: "#888888",
    StepState.CONNECTING: "#daa520",
    StepState.WARMING_UP: "#daa520",
    StepState.READY: "#2e8b57",
    StepState.WARNING: "#daa520",
    StepState.FAILED: "#c0392b",
    StepState.NOT_FOUND: "#c0392b",
    StepState.SKIPPED: "#888888",
}

#: States that mean "this device did not come up".  WARNING is deliberately not
#: in here - it marks the two paths that already swallow failures today
#: (filter wheel homing, confocal mode sync) and that must keep booting.
FAILED_STATES = frozenset({StepState.FAILED, StepState.NOT_FOUND})


def state_label(state: StepState) -> str:
    return _STATE_LABELS.get(state, str(state))


def state_color(state: StepState) -> str:
    return _STATE_COLORS.get(state, "#888888")


@dataclass
class StepResult:
    """One row in the startup window."""

    key: str
    label: str
    state: StepState = StepState.PENDING
    detail: str = ""
    exception: Optional[BaseException] = None
    elapsed_s: float = 0.0
    core: bool = False
    #: False for steps that block in a driver poll loop with no callback hook
    #: (stage homing, objective moves).  The window says so, so a frozen window
    #: reads as honest rather than hung.
    interruptible: bool = True

    @property
    def failed(self) -> bool:
        return self.state in FAILED_STATES

    @property
    def finished(self) -> bool:
        return self.state not in (StepState.PENDING, StepState.CONNECTING, StepState.WARMING_UP)


class StartupError(Exception):
    """Base for the three ways initialization can end early."""


class StartupAborted(StartupError):
    """The user pressed Abort.  Carries the step that was in flight."""

    def __init__(self, step_key: str = "", step_label: str = ""):
        self.step_key = step_key
        self.step_label = step_label
        what = step_label or step_key or "startup"
        super().__init__(f"Initialization aborted while waiting for: {what}")


class StartupCoreDeviceError(StartupError):
    """A device the rest of the system cannot do without failed to come up."""

    def __init__(self, step_key: str, step_label: str, cause: BaseException):
        self.step_key = step_key
        self.step_label = step_label
        self.cause = cause
        super().__init__(f"{step_label or step_key} failed: {cause}")


class StartupFailed(StartupError):
    """One or more independent devices failed; carries the whole checklist."""

    def __init__(self, results: List[StepResult]):
        self.results = list(results)
        self.failures = [r for r in self.results if r.failed]
        names = ", ".join(r.label for r in self.failures) or "unknown"
        super().__init__(f"Initialization failed: {names}")


class StepHandle:
    """Handed to the body of `StartupReporter.step()` to annotate a row."""

    def __init__(self, reporter: "StartupReporter", key: str):
        self._reporter = reporter
        self._key = key

    @property
    def key(self) -> str:
        return self._key

    def detail(self, text: str) -> None:
        """Update the row's detail text without changing its state."""
        current = self._reporter.get(self._key)
        state = current.state if current is not None else StepState.CONNECTING
        self._reporter.set_state(self._key, state, text)

    def warming_up(self, text: str = "") -> None:
        self._reporter.set_state(self._key, StepState.WARMING_UP, text)

    def warning(self, text: str) -> None:
        self._reporter.set_state(self._key, StepState.WARNING, text)

    def not_found(self, text: str) -> None:
        self._reporter.set_state(self._key, StepState.NOT_FOUND, text)

    def on_warmup_retry(self, response: str, elapsed_s: float) -> None:
        """Signature matches `SerialDevice.write_and_check(on_retry=...)`.

        Passing this straight through means a driver's literal reply - e.g.
        'ERR=System in Warmup State' - becomes the row detail with no glue.
        """
        self.warming_up(f"{response} ({elapsed_s:.0f}s)")


def wait_until(
    predicate: Callable[[], bool],
    timeout_s: float,
    poll_interval_s: float = 0.1,
    cancel_fn: Callable[[], bool] = lambda: False,
    on_tick: Optional[Callable[[float], None]] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> bool:
    """Poll `predicate` until it is true, the deadline passes, or cancel fires.

    Generalizes the deadline shape already used by
    `SquidLaserEngineBase.wait_until_ready`.  Returns True only if the predicate
    became true.
    """
    start = time.monotonic()
    deadline = start + timeout_s
    while True:
        if cancel_fn():
            return False
        if predicate():
            return True
        now = time.monotonic()
        if now >= deadline:
            return False
        if on_tick is not None:
            on_tick(now - start)
        sleep_fn(min(poll_interval_s, max(0.0, deadline - now)))


class StartupReporter:
    """Records startup progress.  The base class does nothing visible.

    Subclasses override `_on_changed` to render, and `pump` / `sleep` to keep a
    UI alive.  Everything else is transport-neutral bookkeeping.
    """

    def __init__(self, device_timeout_s: float = 90.0):
        self._results: Dict[str, StepResult] = {}
        self._order: List[str] = []
        self._opened: List[Tuple[str, Callable[[], None]]] = []
        self._abort_requested = False
        self._active_key: Optional[str] = None
        self._device_timeout_s = float(device_timeout_s)
        self._started_at = time.monotonic()

    # ── declaration ────────────────────────────────────────────────────────

    def declare(self, key: str, label: str, *, core: bool = False, interruptible: bool = True) -> None:
        """Register a row up front so the full checklist is visible at t=0."""
        if key in self._results:
            existing = self._results[key]
            existing.label = label
            existing.core = existing.core or core
            existing.interruptible = existing.interruptible and interruptible
            return
        self._results[key] = StepResult(key=key, label=label, core=core, interruptible=interruptible)
        self._order.append(key)
        self._on_changed(self._results[key])

    def get(self, key: str) -> Optional[StepResult]:
        return self._results.get(key)

    @property
    def results(self) -> List[StepResult]:
        return [self._results[k] for k in self._order]

    @property
    def failures(self) -> List[StepResult]:
        return [r for r in self.results if r.failed]

    @property
    def device_timeout_s(self) -> float:
        return self._device_timeout_s

    @property
    def elapsed_s(self) -> float:
        return time.monotonic() - self._started_at

    # ── state changes ──────────────────────────────────────────────────────

    def set_state(self, key: str, state: StepState, detail: str = "") -> None:
        result = self._results.get(key)
        if result is None:
            self.declare(key, key)
            result = self._results[key]
        result.state = state
        result.detail = detail
        self._on_changed(result)

    def skip(self, key: str, reason: str = "") -> None:
        self.set_state(key, StepState.SKIPPED, reason)

    # ── abort ──────────────────────────────────────────────────────────────

    def request_abort(self) -> None:
        """Safe to call from a Qt slot: sets a flag and nothing else.

        Teardown must never run here - the initialization call stack below is
        still live and would be closing devices out from under itself.
        """
        if not self._abort_requested:
            self._abort_requested = True
            log.warning("Startup abort requested by user.")

    def is_abort_requested(self) -> bool:
        return self._abort_requested

    def raise_if_aborted(self, key: str = "") -> None:
        if not self._abort_requested:
            return
        target = key or self._active_key or ""
        result = self._results.get(target)
        raise StartupAborted(target, result.label if result is not None else "")

    def cancel_fn(self) -> Callable[[], bool]:
        """Poll function to hand to blocking waits, mirroring `abort_requested_fn`."""
        return self.is_abort_requested

    # ── opened-device registry ─────────────────────────────────────────────

    def register_opened(self, label: str, closer: Callable[[], None]) -> None:
        """Record something that must be released if startup does not finish.

        `build_from_global_config` holds every opened device in locals until its
        very last statement, so on an abort the caller has no reference to any
        of it.  Registration order equals construction order, so closing in
        reverse reproduces the teardown order documented in tests/conftest.py.

        An explicit `closer` callable is required rather than duck-typing a
        `.close()` method: XLight and SciMicroscopyLEDArray have no `close()` at
        all, and IlluminationController.close() politely shuts down the LDI
        channel by channel, which takes ~55s against a wedged device.
        """
        self._opened.append((label, closer))

    @property
    def opened(self) -> List[Tuple[str, Callable[[], None]]]:
        return list(self._opened)

    def close_all(self) -> List[str]:
        """Close everything registered, newest first.  Never raises."""
        errors: List[str] = []
        for label, closer in reversed(self._opened):
            try:
                closer()
                log.info(f"Startup teardown: released {label}")
            except Exception as e:
                errors.append(f"{label}: {e}")
                log.warning(f"Startup teardown: could not release {label}: {e}", exc_info=True)
        self._opened.clear()
        return errors

    # ── cooperative UI hooks (no-ops here) ─────────────────────────────────

    def pump(self, force: bool = False) -> None:
        """Give the UI a chance to repaint.  No-op without a window."""

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    @property
    def sleep_fn(self) -> Callable[[float], None]:
        """Sleep that keeps the UI alive; handed to SerialDevice wait loops."""
        return self.sleep

    def _on_changed(self, result: StepResult) -> None:
        """Render hook.  Overridden by the Qt reporter."""

    # ── the main entry point ───────────────────────────────────────────────

    @contextlib.contextmanager
    def step(
        self,
        key: str,
        *,
        core: bool = False,
        label: Optional[str] = None,
        interruptible: bool = True,
    ) -> Iterator[StepHandle]:
        """Wrap one initialization step.

        On a clean exit the row becomes READY (unless the body already set a
        terminal state).  An exception is recorded as FAILED and then either
        swallowed - so the sweep continues and the window can show every
        problem at once - or re-raised as `StartupCoreDeviceError` when
        `core=True`.  `StartupAborted` always passes straight through.
        """
        if key not in self._results:
            self.declare(key, label or key, core=core, interruptible=interruptible)
        else:
            existing = self._results[key]
            if label:
                existing.label = label
            existing.core = existing.core or core
            existing.interruptible = existing.interruptible and interruptible

        result = self._results[key]
        self.raise_if_aborted(key)
        self._active_key = key
        self.set_state(key, StepState.CONNECTING)
        started = time.monotonic()
        try:
            yield StepHandle(self, key)
        except StartupAborted:
            result.elapsed_s = time.monotonic() - started
            self._active_key = None
            raise
        except BaseException as e:  # noqa: BLE001 - deliberately broad, see below
            result.elapsed_s = time.monotonic() - started
            result.exception = e
            self._active_key = None
            # A driver may already have classified this more precisely (e.g.
            # NOT_FOUND for an unenumerated port); do not overwrite that.
            if result.state not in FAILED_STATES:
                self.set_state(key, StepState.FAILED, f"{type(e).__name__}: {e}")
            else:
                self._on_changed(result)
            log.error(f"Startup step '{result.label}' failed.", exc_info=True)
            if result.core:
                raise StartupCoreDeviceError(key, result.label, e) from e
            return
        result.elapsed_s = time.monotonic() - started
        self._active_key = None
        if not result.finished:
            # Keep whatever the body chose to say about the device, but drop
            # transient warm-up chatter - a Ready row must not still read
            # "ERR=System in Warmup State".
            detail = "" if result.state == StepState.WARMING_UP else result.detail
            self.set_state(key, StepState.READY, detail)
        else:
            self._on_changed(result)


#: Default for every `reporter=` parameter on the build path.  Shared and
#: stateless enough that existing callers pay nothing for it.
NULL_REPORTER = StartupReporter()


def is_squid_filter_wheel(fw_config) -> bool:
    """True for the MCU-driven filter wheel, whose failure is unsurvivable.

    Zaber and Optospin own their own serial port, so the rest of the sweep can
    continue past them.
    """
    from squid.config import FilterWheelControllerVariant

    return fw_config.controller_type == FilterWheelControllerVariant.SQUID


def declare_expected_steps(reporter: StartupReporter, *, simulated: bool = False, skip_init: bool = False) -> None:
    """Declare every row this machine's configuration will actually visit.

    Reads the same `control._def` flags the builders read, so the checklist is
    complete and correctly ordered before the first device is touched.  Devices
    this machine is not configured for are not declared at all - a row reading
    "Dragonfly - Skipped" on a machine that has never had one is noise, not
    information.  `SKIPPED` is reserved for steps that a real run bypasses
    (--skip-init, simulation).
    """
    import control._def as _def
    import squid.config

    d = reporter.declare

    d("imports", "Loading software modules", core=True)
    d("microcontroller", "Microcontroller", core=True)
    d("stage_xy", "Prior XY stage" if _def.USE_PRIOR_STAGE else "XY stage", core=True)
    if _def.USE_PI_FOCUS_STAGE:
        d("stage_z", "Z focus stage (PI C-414)", core=True, interruptible=False)

    if _def.ENABLE_SPINNING_DISK_CONFOCAL:
        d("spinning_disk", "Spinning disk (Dragonfly)" if _def.USE_DRAGONFLY else "Spinning disk (X-Light/Cicero)")
    if _def.ENABLE_NL5:
        d("nl5", "NL5 confocal scanner")
    if _def.ENABLE_CELLX:
        d("cellx", "CellX laser combiner")

    fw_config = squid.config.get_filter_wheel_config()
    if fw_config:
        # The Squid variant rides on the microcontroller and raises hard on a
        # missing MCU or old firmware; Zaber/Optospin own their own port and a
        # failure there is survivable for the rest of the sweep.
        d("filter_wheel", "Emission filter wheel", core=is_squid_filter_wheel(fw_config))
    if _def.USE_XERYON:
        d("objective_changer", "Objective changer (Xeryon)")
    elif _def.USE_OBJECTIVE_TURRET:
        d("objective_changer", "Objective turret")
    if _def.SUPPORT_LASER_AUTOFOCUS:
        d("camera_focus", "Laser autofocus camera")
    if _def.RUN_FLUIDICS:
        d("fluidics", "Fluidics")
    if _def.HAS_OBJECTIVE_PIEZO:
        d("piezo", "Objective piezo", core=True)
    if _def.SUPPORT_SCIMICROSCOPY_LED_ARRAY:
        d("led_array", "SciMicroscopy LED array")
    if _def.USE_SQUID_LASER_ENGINE:
        d("laser_engine", "Squid laser engine")

    d("camera", "Main camera", core=True)
    # The external light sources are only built when not simulated - mirror the
    # `and not simulated` guards in Microscope.build_from_global_config, or the
    # row would sit at Pending forever in simulation.
    if not simulated:
        if _def.USE_LDI_SERIAL_CONTROL:
            d("light_source", "LDI laser engine")
        elif _def.USE_CELESTA_ETHERNET_CONTROL:
            d("light_source", "CELESTA light engine")
        elif _def.USE_ANDOR_LASER_CONTROL:
            d("light_source", "Andor laser")
    d("illumination_controller", "Illumination controller", core=True)
    d("config_profiles", "Acquisition configuration", core=True)
    if _def.ENABLE_SPINNING_DISK_CONFOCAL:
        d("confocal_sync", "Confocal mode sync")

    if fw_config:
        d("prep_filter_wheel", "Filter wheel homing")
    if _def.HAS_OBJECTIVE_PIEZO:
        d("prep_piezo", "Piezo homing")
    if _def.USE_SQUID_LASER_ENGINE:
        d("prep_laser_engine", "Laser engine startup")
    d("prep_watchdog", "Illumination watchdog")
    d("prep_camera", "Camera configuration", core=True)
    if not skip_init:
        # --skip-init leaves the hardware where it is, so homing never runs.
        d("prep_home_xyz", "Homing stage", core=True, interruptible=False)
    if _def.USE_XERYON or _def.USE_OBJECTIVE_TURRET:
        d("prep_objective", "Objective positioning", interruptible=False)

    d("gui_objects", "Loading controllers", core=True)
    d("gui_hardware", "Starting camera streams", core=True)
    d("gui_widgets", "Building interface", core=True, interruptible=False)
    d("gui_layout", "Arranging layout", core=True, interruptible=False)
    d("gui_connections", "Connecting signals", core=True, interruptible=False)
    d("gui_position", "Restoring stage position", interruptible=False)

    if simulated:
        # Simulated devices return immediately, so nothing is actually stuck in
        # a driver poll loop and every step is effectively interruptible.
        for result in reporter.results:
            result.interruptible = True
