"""FluidicsService — Squid's one import site of the Squid-Fluidics library (the ``fluidics`` package).

``MicroscopeAddons`` creates the service uninitialized when ``RUN_FLUIDICS`` is on. ``initialize()``
loads the library's ``FluidicsConfig`` YAML (the same file the standalone fluidics GUI uses) and brings
the ``FluidicsSystem`` up. That call blocks — about 3 s simulated, 5–30 s on hardware — so the GUI
calls it off the GUI thread. ``system`` is ``None`` until then.

Logging: the library logs under ``fluidics`` (the Tecan driver under ``XCaliburD``) while Squid attaches
handlers only to the ``squid`` logger, so a forwarding handler re-dispatches every library record through
the ``squid`` logger at emit time — console, main_hcs.log, per-acquisition logs, future run logs.
"""

import logging
import threading
from typing import List, Optional, Tuple

import squid.logging

try:
    from fluidics.control.config import load_config
    from fluidics.devices import build_devices
    from fluidics.events import RunEnded
    from fluidics.run_session import RunSession
    from fluidics.system import FluidicsSystem
except ImportError as e:  # pragma: no cover - environment problem, not logic
    raise ImportError(
        "The fluidics library is not installed. From software/ run: "
        "git submodule update --init fluidics_v2 && pip3 install --no-deps -e fluidics_v2/software"
    ) from e

_log = squid.logging.get_logger(__name__)

LIBRARY_LOGGER_NAMES = ("fluidics", "XCaliburD")

_REQUIRED_SYSTEM_API = (
    "build",
    "plan",
    "run",
    "run_manual",
    "wait",
    "abort",
    "pause",
    "resume",
    "busy",
    "make_safe",
    "close",
)
_REQUIRED_SESSION_API = ("start", "snapshot", "abort", "pause", "resume", "wait", "busy")
_REQUIRED_RUN_ENDED_FIELDS = ("run_id", "outcome", "message", "elapsed_seconds", "position")


class FluidicsLibraryError(RuntimeError):
    """The installed fluidics library does not expose the API Squid was written against."""


def check_library_surface() -> None:
    missing = [f"FluidicsSystem.{name}" for name in _REQUIRED_SYSTEM_API if not hasattr(FluidicsSystem, name)]
    missing += [f"RunSession.{name}" for name in _REQUIRED_SESSION_API if not hasattr(RunSession, name)]
    if tuple(RunEnded._fields) != _REQUIRED_RUN_ENDED_FIELDS:
        missing.append(f"RunEnded fields {RunEnded._fields} != {_REQUIRED_RUN_ENDED_FIELDS}")
    if missing:
        raise FluidicsLibraryError(
            "The installed fluidics library does not match what Squid expects (missing: "
            + ", ".join(missing)
            + "). Update the submodule (git submodule update --init fluidics_v2) and reinstall it "
            "(pip3 install --no-deps -e fluidics_v2/software)."
        )


check_library_surface()


class _ForwardToSquidHandler(logging.Handler):
    """Re-dispatches a library record through the ``squid`` logger's handlers, whatever they are right now."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            squid.logging.get_logger().handle(record)
        except Exception:
            self.handleError(record)


_bridge_lock = threading.Lock()
_bridge_handler: Optional[logging.Handler] = None


def install_logging_bridge() -> logging.Handler:
    """Attach the forwarding handler to the library loggers once; returns it."""
    global _bridge_handler
    with _bridge_lock:
        if _bridge_handler is None:
            handler = _ForwardToSquidHandler(level=logging.DEBUG)
            for name in LIBRARY_LOGGER_NAMES:
                library_logger = logging.getLogger(name)
                library_logger.setLevel(logging.DEBUG)
                library_logger.addHandler(handler)
            _bridge_handler = handler
        return _bridge_handler


class FluidicsService:
    def __init__(self, default_config_path: str, simulated: bool = False):
        self.default_config_path = default_config_path
        self.simulated = simulated
        self.system: Optional[FluidicsSystem] = None
        self.config = None
        self.config_path: Optional[str] = None
        self.issues: List[Tuple[str, str]] = []
        self._lock = threading.Lock()
        install_logging_bridge()

    @property
    def initialized(self) -> bool:
        return self.system is not None

    def initialize(
        self, config_path: Optional[str] = None, report_dir: Optional[str] = None, instant: bool = False
    ) -> None:
        """Load the config and bring the system up. Blocking; call off the GUI thread.

        instant: skip the simulated devices' pacing (tests only; refused for real hardware).
        Degraded bring-up (no TEC, no flow sensors) is recorded in ``issues`` and logged; a missing
        controller or pump raises the library's exception and leaves the service uninitialized.
        """
        with self._lock:
            if self.system is not None:
                raise RuntimeError("The fluidics system is already initialized")
            if instant and not self.simulated:
                raise ValueError("instant bring-up is only for the simulated fluidics system")
            path = config_path or self.default_config_path
            config = load_config(path)
            issues: List[Tuple[str, str]] = []

            def on_issue(kind: str, message: str) -> None:
                issues.append((kind, message))
                _log.warning(f"Fluidics bring-up issue ({kind}): {message}")

            _log.info(f"Initializing the fluidics system from {config.source_path} (simulated={self.simulated})")
            if instant:
                devices = build_devices(config, simulation=True, on_issue=on_issue, instant=True)
                system = FluidicsSystem(config, devices, report_dir=report_dir)
            else:
                system = FluidicsSystem.build(
                    config, simulation=self.simulated, on_issue=on_issue, report_dir=report_dir
                )
            self.config = config
            self.config_path = config.source_path
            self.issues = issues
            self.system = system
            _log.info("Fluidics system initialized")

    def close(self, timeout: float = 10.0) -> List[Exception]:
        """Abort any job, close the devices and let the library finish writing its reports. Safe when uninitialized."""
        with self._lock:
            system, self.system = self.system, None
        if system is None:
            return []
        errors = list(system.close(timeout=timeout))
        try:
            system.reports.wait(timeout)
        except Exception as e:
            errors.append(e)
        for error in errors:
            _log.warning(f"Error closing the fluidics system: {error}")
        return errors
