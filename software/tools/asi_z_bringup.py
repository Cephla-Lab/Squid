"""ASI LS50 Z-stage controller bring-up test.

Exercises the production driver (squid.stage.asi.LS50Controller) against real hardware:
port discovery, comms sanity, position/status readout, and -- only with --allow-motion --
a small jog AWAY from the sample and back, plus an optional soft-limit fence check.

Read-only by default: without --allow-motion the stage never moves.

Frame reminder: native 0 is the power-on position (power on with the stage retracted!),
native POSITIVE is away from the sample. Squid displays the negation (asi_z_invert = True),
so squid + is toward the sample.

Usage:
    python3 tools/asi_z_bringup.py --sn <USB serial number>            # read-only checks
    python3 tools/asi_z_bringup.py --sn <SN> --allow-motion            # + jog test
    python3 tools/asi_z_bringup.py --sn <SN> --allow-motion --fence-mm 50   # + fence test
    python3 tools/asi_z_bringup.py --simulate --allow-motion           # dry run, no hardware
"""

import argparse
import sys
import time

sys.path.insert(0, ".")  # run from the software/ directory

import squid.logging
from squid.stage.asi import LS50Controller, MS2000Serial, _SimulatedLS50

log = squid.logging.get_logger("asi-z-bringup")


def report_position(backend, label: str) -> float:
    native = backend.get_position_mm()
    squid_mm = -native + 0.0  # +0.0 avoids the confusing '-0.0000' rendering
    log.info(f"{label}: native = {native:+.4f} mm  (squid would display {squid_mm:+.4f} mm)")
    return native


def connect(args):
    if args.simulate:
        log.info("SIMULATED backend (no hardware).")
        backend = _SimulatedLS50()
        backend.initialize()
        return backend

    if args.port:
        port = args.port
    elif args.sn:
        import squid.stage.utils

        try:
            port = squid.stage.utils.resolve_serial_port_by_sn(args.sn)
        except RuntimeError as e:
            import serial.tools.list_ports

            log.error(str(e))
            log.error("Available ports:")
            for p in serial.tools.list_ports.comports():
                log.error(f"  {p.device}  serial_number={p.serial_number}  {p.description}")
            sys.exit(1)
    else:
        log.error("Pass --sn <USB serial number>, --port </dev/ttyUSB*>, or --simulate.")
        sys.exit(1)

    log.info(f"Connecting to {port} at {args.baud} baud ...")
    backend = LS50Controller()
    backend.connect_serial(port, baudrate=args.baud)
    backend.initialize()  # one W Z round-trip; no motion
    return backend


def read_only_checks(backend):
    log.info("--- Read-only checks ---")
    report_position(backend, "Position")
    log.info(f"Busy: {backend.is_moving()}  (expect False on an idle stage)")
    if isinstance(backend, LS50Controller):
        with_serial: MS2000Serial = backend._serial
        for cmd in ("BU", "N"):  # build info / who
            try:
                log.info(f"{cmd!r} -> {with_serial.command(cmd)!r}")
            except Exception as e:
                log.warning(f"{cmd!r} failed: {e} (informational only)")


def jog_test(backend, jog_mm: float):
    log.info("--- Jog test ---")
    start = report_position(backend, "Start")
    log.info(f"Jogging native +{jog_mm} mm. WATCH THE STAGE: it must move AWAY from the sample.")
    t0 = time.monotonic()
    backend.move_relative(+jog_mm, wait=True)
    log.info(f"Move + settle took {time.monotonic() - t0:.2f} s")
    after = report_position(backend, "After jog")
    if abs((after - start) - jog_mm) > 0.01:
        log.warning(f"Jog moved {after - start:+.4f} mm, expected {jog_mm:+.4f} mm -- check units/backlash.")
    log.info("Returning to the start position ...")
    backend.move_to(start, wait=True)
    end = report_position(backend, "Back at start")
    if abs(end - start) > 0.005:
        log.warning(f"Did not return exactly to start (off by {end - start:+.4f} mm).")
    log.info("If the stage moved TOWARD the sample on the jog, the wiring/direction is flipped:")
    log.info("do NOT set asi_z_invert = False blindly -- re-check the controller axis polarity first.")


def fence_test(backend, fence_mm: float):
    log.info(f"--- Soft-limit fence test (native +/-{fence_mm} mm) ---")
    backend.set_travel_limits(-fence_mm, fence_mm)
    log.info(f"Fence set; controller SL/SU written. hardware_limits_mm = {backend.hardware_limits_mm()}")
    start = backend.get_position_mm()
    target = fence_mm + 5.0  # beyond the fence, in the AWAY direction
    log.info(f"Commanding native {target:+.1f} mm (beyond the fence) -- the driver must clamp it.")
    reached = backend.move_to(target, wait=True)
    if reached <= fence_mm + 0.01:
        log.info(f"OK: clamped to {reached:+.4f} mm.")
    else:
        log.warning(f"NOT clamped: reached {reached:+.4f} mm!")
    log.info("Returning to the start position ...")
    backend.move_to(start, wait=True)
    report_position(backend, "Back at start")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sn", help="Controller USB serial number (resolved to a port)")
    parser.add_argument("--port", help="Explicit serial port (overrides --sn)")
    parser.add_argument("--baud", type=int, default=115200, help="Baud rate (default 115200)")
    parser.add_argument("--simulate", action="store_true", help="Use the simulated backend (no hardware)")
    parser.add_argument("--allow-motion", action="store_true", help="Enable the jog / fence tests (MOVES the stage)")
    parser.add_argument("--jog-mm", type=float, default=0.5, help="Jog distance in native mm (default 0.5, away)")
    parser.add_argument("--fence-mm", type=float, default=0.0, help="Also test the +/- soft-limit fence (0 = skip)")
    args = parser.parse_args()

    backend = connect(args)
    try:
        read_only_checks(backend)

        if not args.allow_motion:
            log.info("Read-only run complete. Re-run with --allow-motion for the jog test.")
            return

        if not args.simulate:
            log.warning(f"About to MOVE the stage: +{args.jog_mm} mm native (away from the sample) and back.")
            if input("Type 'move' to continue: ").strip().lower() != "move":
                log.info("Aborted before motion.")
                return

        jog_test(backend, args.jog_mm)
        if args.fence_mm > 0:
            fence_test(backend, args.fence_mm)

        log.info("--- Done. Next steps for the machine ini ---")
        log.info("[GENERAL]: use_asi_z_stage = True, asi_z_stage_sn = <SN>, asi_z_travel_mm = 50")
    finally:
        backend.close()


if __name__ == "__main__":
    main()
