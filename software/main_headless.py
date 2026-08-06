"""Run the Squid core service (REST + SSE API) without the GUI.

Serves the same API as the GUI-embedded server (see main_hcs.py), but uvicorn
runs in the main thread of a QApplication-free process — intended for
scheduler-driven instruments and remote operation. Host/port/auth come from the
[CORE_SERVICE] INI section, same as the GUI. Ctrl+C or SIGTERM stops the server,
aborts any in-flight acquisition, and closes the hardware.
"""

import argparse
import logging
import multiprocessing
import signal
import threading
from pathlib import Path

import squid.logging

squid.logging.setup_uncaught_exception_logging()

import control._def
import control.microscope
import control.utils
from control.single_instance import acquire_single_instance_lock
from tools.migrate_acquisition_configs import run_auto_migration


def _finish_active_acquisition(service, log) -> None:
    """Abort an in-flight acquisition so the hardware lands in a safe state."""
    active = service.jobs.active
    if active is None:
        return
    log.info(f"Shutdown requested while job {active.job_id} is running; aborting it first...")
    try:
        result = service.abort_job(active.job_id, timeout_s=60.0)
        if result["timed_out"]:
            log.warning("Acquisition did not stop within 60s; hardware may not be in a safe state")
        else:
            log.info(f"Job {active.job_id} stopped (outcome: {result['job'].get('outcome')})")
    except Exception as e:
        log.error(f"Abort on shutdown failed: {e}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Squid core service REST API, no GUI.")
    parser.add_argument("--simulation", help="Run with simulated hardware.", action="store_true")
    parser.add_argument("--verbose", help="Turn on verbose logging (DEBUG level)", action="store_true")
    parser.add_argument(
        "--skip-init",
        help="Skip hardware initialization and homing (for restart after settings change)",
        action="store_true",
    )
    args = parser.parse_args()

    log = squid.logging.get_logger("main_headless")
    if args.verbose:
        log.info("Turning on debug logging.")
        squid.logging.set_stdout_log_level(logging.DEBUG)
    if not squid.logging.add_file_logging(f"{squid.logging.get_default_log_directory()}/main_headless.log"):
        log.error("Couldn't setup logging to file!")
        return 1

    # Same hardware-exclusivity lock as the GUI (QLockFile needs no QApplication).
    lock_result = acquire_single_instance_lock()
    if lock_result.lock is None:
        if lock_result.busy:
            log.error("Another instance of Squid is already running on this computer.")
        else:
            log.error(f"Failed to create the lock file at: {lock_result.path}")
        return 1

    try:
        log.info(f"Squid Repository State: {control.utils.get_squid_repo_state_description()}")
        run_auto_migration()

        from squid_service.config import ServiceConfig
        from squid_service.headless import create_headless_service
        from squid_service.rest.app import create_app
        from squid_service.rest.server import CoreServiceServer

        # Fail fast on a bad [CORE_SERVICE] config before touching hardware.
        service_config = ServiceConfig.from_def()

        microscope = control.microscope.Microscope.build_from_global_config(args.simulation, skip_init=args.skip_init)
        try:
            service = create_headless_service(
                microscope,
                simulation=args.simulation,
                job_persist_path=Path("cache/last_job.json"),
                methods_dir=Path(service_config.methods_dir),
            )
            # Run uvicorn in a thread (same as the GUI) and own the signals here.
            # uvicorn's main-thread signal capture replays SIGTERM after serve(),
            # which would kill the process before any teardown below could run.
            server = CoreServiceServer(create_app(service, service_config), service_config.host, service_config.port)
            shutdown_requested = threading.Event()
            signal.signal(signal.SIGINT, lambda *_: shutdown_requested.set())
            signal.signal(signal.SIGTERM, lambda *_: shutdown_requested.set())
            server.start()
            log.info("Headless mode: Ctrl+C or SIGTERM to stop.")
            # Wait with a timeout so the main thread returns to the interpreter
            # regularly; a bare wait() can delay signal-handler delivery.
            while not shutdown_requested.wait(timeout=1.0):
                pass
            log.info("Shutdown requested.")
            _finish_active_acquisition(service, log)
            server.stop()
        finally:
            microscope.close()
            # JobRunner subprocesses have no teardown path yet (issue tracked in
            # the core-service follow-ups); reap them so an unattended instrument
            # does not accumulate PPID-1 orphans across restarts.
            for child in multiprocessing.active_children():
                child.join(timeout=2.0)
                if child.is_alive():
                    log.warning(f"Terminating lingering subprocess {child.pid}")
                    child.terminate()
    finally:
        lock_result.lock.unlock()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
