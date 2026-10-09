"""Headless illumination power calibration: Utils > Illumination Power Calibration... without the GUI.

Opens the controller itself, so run it with the Squid GUI closed. Calibrates every DAC-driven epi channel (or the ones
named with --channels), prints each channel's verification, and with --save writes what the GUI would write
(machine_configs/intensity_calibrations/<λ>nm_<port>.csv + .png) and points the illumination config at it.

    python tools/generate_intensity_calibrations.py --measured-in widefield
    python tools/generate_intensity_calibrations.py --channels "Fluorescence 405 nm Ex" --save
    python tools/generate_intensity_calibrations.py --simulation --settle-s 0 --hold-s 0   # dry run, simulated
"""

import argparse
import os
import sys
from pathlib import Path
from typing import Optional, Sequence

SOFTWARE_DIR = Path(__file__).resolve().parents[1]


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--channels", nargs="*", default=None, help="illumination channel names (default: all)")
    parser.add_argument(
        "--measured-in",
        choices=["widefield", "confocal", "n/a"],
        default="n/a",
        help="imaging mode the light path is in; recorded in each file",
    )
    parser.add_argument(
        "--sensor-limit-mw",
        type=float,
        default=None,
        help="stop at the first reading above this (default and maximum: the sensor's own maximum power)",
    )
    parser.add_argument("--settle-s", type=float, default=None, help="wait after turning the light on (s)")
    parser.add_argument("--hold-s", type=float, default=None, help="continuous-light check length (s); 0 skips it")
    parser.add_argument("--resource", default=None, help="VISA resource (default: the first Thorlabs meter)")
    parser.add_argument("--save", action="store_true", help="write the files and point the config at them")
    parser.add_argument("--simulation", action="store_true", help="simulated controller and meter")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None, config_repo=None) -> int:
    args = parse_args(argv)
    sys.path.insert(0, str(SOFTWARE_DIR))
    os.chdir(SOFTWARE_DIR)
    import control._def
    import control.microcontroller as microcontroller
    from control.core.config import ConfigRepository
    from squid.intensity_calibration_run import CalibrationSession, ChannelResult

    if args.simulation:
        serial_device = microcontroller.SimSerial()
    else:
        serial_device = microcontroller.get_microcontroller_serial_device(
            version=control._def.CONTROLLER_VERSION, sn=control._def.CONTROLLER_SN
        )
    mcu = microcontroller.Microcontroller(serial_device=serial_device)
    session = CalibrationSession(mcu, config_repo or ConfigRepository())
    if args.settle_s is not None:
        session.settle_s = args.settle_s
    if args.hold_s is not None:
        session.hold_s = args.hold_s
    targets = [t for t in session.targets() if args.channels is None or t.name in args.channels]
    if not targets:
        print("No matching DAC-driven epi-illumination channel in the illumination config.")
        return 2
    info = session.connect(resource=args.resource)
    print(f"Power meter: {info.meter}, sensor {info.sensor}" + ("" if info.validated else " (model not validated)"))
    sensor_limit_mw = args.sensor_limit_mw if args.sensor_limit_mw is not None else info.max_power_mw
    if sensor_limit_mw is None or sensor_limit_mw <= 0:
        print("The meter did not report the sensor's maximum power: pass --sensor-limit-mw.")
        session.disconnect()
        return 2
    if info.max_power_mw is not None and sensor_limit_mw > info.max_power_mw:
        print(f"--sensor-limit-mw {sensor_limit_mw:g} is above the sensor's maximum ({info.max_power_mw:g} mW).")
        session.disconnect()
        return 2
    calibrations = []
    # Arm the illumination watchdog as the GUI does at startup (Microscope._prepare_for_use); the session owns it while
    # the light is on, so a hung run turns the light off within the timeout.
    if mcu.firmware_version >= (1, 1):
        mcu.set_watchdog_timeout(control._def.WATCHDOG_TIMEOUT_S)
        mcu.wait_till_operation_is_completed()
        mcu.start_heartbeat(interval_s=control._def.WATCHDOG_TIMEOUT_S / 2)
        print(f"Illumination watchdog armed ({control._def.WATCHDOG_TIMEOUT_S:g} s).")
    else:
        print(
            "This controller has no illumination watchdog (firmware before 1.1): if this tool stops responding, "
            "turn the light off at the controller."
        )
    try:
        results = session.run(
            targets,
            measured_in=args.measured_in,
            sensor_limit_mw=sensor_limit_mw,
            progress=lambda message, done, total: print(f"\r{message}", end="", flush=True),
        )
        print()
        for name, result in results.items():
            if isinstance(result, ChannelResult):
                c = result.calibration
                calibrations.append(c)
                extras = list(result.warnings) + ([c.rollover] if c.rollover else [])
                print(f"{name}: {c.p_max_mw:.4g} mW, {c.verification_summary()}" + "".join(f"; {e}" for e in extras))
            else:
                print(f"{name}: not calibrated ({result})")
        if args.save and calibrations:
            for path, backup in session.save(calibrations):
                print(f"saved {path}" + (f" (previous file moved to {backup})" if backup else ""))
    finally:
        session.disconnect()
        mcu.stop_heartbeat()
    all_pass = len(calibrations) == len(targets) and all(c.verification == "pass" for c in calibrations)
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
