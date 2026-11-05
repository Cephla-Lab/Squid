"""Sanity-check a Squid install: interpreter, dependencies, camera libraries.

Run from the ``software`` directory (CHECK-DRIVERS.cmd does that for you)::

    python tools/check_drivers.py
    python tools/check_drivers.py --imports-only

The point is to tell a *driver* problem apart from a *Python* problem in a few
seconds, which matters most on a freshly copied portable bundle where a silent
pythonw.exe exit gives you nothing to go on.

Exits 0 if every required check passed, 1 otherwise.
"""

import argparse
import ctypes
import os
import sys
from pathlib import Path

# Dependencies the app imports at startup. Not exhaustive -- enough that a
# half-copied or mis-relocated runtime fails here rather than three screens into
# the GUI.
REQUIRED_IMPORTS = [
    "PyQt5.QtWidgets",
    "qtpy",
    "napari",
    "cv2",
    "numpy",
    "scipy",
    "skimage",
    "tifffile",
    "pandas",
    "matplotlib",
    "serial",
    "filelock",
    "psutil",
    "pydantic_xml",
    "git",
    "crc",
    "lxml",
    "imageio",
    "platformdirs",
    "yaml",
]

# Laid down by the Daheng Galaxy SDK installer. control/gxipy/gxwrapper.py loads
# GxIAPI.dll by bare name, so it resolves off the system PATH -- these entries
# are what make that work, and they are also what a missing post-install reboot
# leaves inactive.
DAHENG_DIRS = [
    r"C:\Program Files\Daheng Imaging\GalaxySDK\APIDll\Win64",
    r"C:\Program Files\Daheng Imaging\GalaxySDK\GenICam\bin\Win64_x64",
]

PASS, FAIL, WARN = "  OK  ", " FAIL ", " WARN "


class Results:
    def __init__(self):
        self.failed = False

    def report(self, status, label, detail=""):
        line = "[{}] {}".format(status, label)
        if detail:
            line += "\n         " + detail.replace("\n", "\n         ")
        print(line)
        if status == FAIL:
            self.failed = True


def section(title):
    print("\n" + title)
    print("-" * len(title))


def check_interpreter(results):
    section("Interpreter")
    print("  executable : {}".format(sys.executable))
    print("  version    : {}".format(sys.version.split()[0]))
    print("  prefix     : {}".format(sys.prefix))
    print("  cwd        : {}".format(os.getcwd()))

    if sys.version_info[:2] != (3, 12):
        results.report(
            WARN,
            "Python 3.12 expected",
            "found {}.{} -- the app is only known to work on 3.12".format(*sys.version_info[:2]),
        )

    # The launcher sets CWD to software\ because control/_def.py globs
    # ./configuration*.ini and reads cache/config_file_path.txt relative to it.
    if not Path("main_hcs.py").exists():
        results.report(
            FAIL,
            "working directory",
            "not the 'software' directory -- run CHECK-DRIVERS.cmd rather than this script directly",
        )
    else:
        results.report(PASS, "working directory is 'software'")


def check_imports(results):
    section("Dependencies")
    missing = []
    for name in REQUIRED_IMPORTS:
        try:
            __import__(name)
        except Exception as exc:  # ImportError, but a broken DLL raises other things
            missing.append("{}: {}".format(name, exc))

    if missing:
        results.report(
            FAIL,
            "{} of {} imports failed".format(len(missing), len(REQUIRED_IMPORTS)),
            "\n".join(missing),
        )
    else:
        results.report(PASS, "all {} imports succeeded".format(len(REQUIRED_IMPORTS)))


def check_toupcam(results):
    """Main camera (ITR3CMOS26000KMA).

    Mirrors the resolution in control/toupcam.py: the DLL is found relative to
    __file__, so it travels inside the bundle and cannot be broken by a move.
    """
    section("Toupcam -- main camera")

    dll = Path("drivers and libraries") / "toupcam" / "windows" / "x64" / "toupcam.dll"
    if not dll.exists():
        results.report(FAIL, "toupcam.dll missing from the bundle", str(dll.resolve()))
        return

    try:
        ctypes.windll.LoadLibrary(str(dll.resolve()))
    except OSError as exc:
        results.report(FAIL, "toupcam.dll present but would not load", str(exc))
        return

    results.report(PASS, "toupcam.dll loaded", str(dll.resolve()))

    # Enumeration needs the USB driver, not just the DLL, so this distinguishes
    # "library fine, camera not plugged in / driver missing" from "library broken".
    try:
        sys.path.insert(0, os.getcwd())
        import control.toupcam as toupcam  # noqa: E402

        devices = toupcam.Toupcam.EnumV2()
        if devices:
            results.report(
                PASS,
                "{} Toupcam device(s) detected".format(len(devices)),
                "\n".join(d.displayname for d in devices),
            )
        else:
            results.report(
                WARN,
                "no Toupcam device detected",
                "library is fine; camera unplugged, powered off, or USB driver not installed",
            )
    except Exception as exc:
        results.report(WARN, "could not enumerate Toupcam devices", str(exc))


def check_daheng(results):
    """Laser-AF camera (MER2-630-60U3M).

    This is the one piece that cannot be bundled: GxIAPI.dll is loaded by bare
    name off the system PATH and ships with a kernel-mode USB driver.
    """
    section("Daheng Galaxy -- laser-AF camera")

    present = [d for d in DAHENG_DIRS if os.path.isdir(d)]
    if not present:
        results.report(
            FAIL,
            "Daheng Galaxy SDK is not installed",
            "expected " + DAHENG_DIRS[0] + "\ninstall the Galaxy SDK for Windows, then reboot",
        )
        return

    # Match what the launcher does, so this script also works when invoked directly.
    os.environ["PATH"] = os.pathsep.join(present) + os.pathsep + os.environ.get("PATH", "")

    try:
        ctypes.WinDLL("GxIAPI.dll", winmode=0)
    except OSError as exc:
        results.report(
            FAIL,
            "GxIAPI.dll would not load",
            "{}\nSDK directory exists, so this is usually a missing reboot after install".format(exc),
        )
        return

    results.report(PASS, "GxIAPI.dll loaded")

    try:
        sys.path.insert(0, os.getcwd())
        import control.gxipy as gx  # noqa: E402

        # DeviceManager.__new__ calls gx_init_lib itself, and __del__ closes it,
        # which is how control/camera.py drives it too.
        count = gx.DeviceManager().update_device_list()[0]

        if count:
            results.report(PASS, "{} Daheng device(s) detected".format(count))
        else:
            results.report(
                WARN,
                "no Daheng device detected",
                "library is fine; camera unplugged or USB driver not bound",
            )
    except Exception as exc:
        results.report(WARN, "could not enumerate Daheng devices", str(exc))


def check_serial(results):
    """The microcontroller arrives as a USB CDC serial port."""
    section("Serial ports")
    try:
        from serial.tools import list_ports

        ports = list(list_ports.comports())
    except Exception as exc:
        results.report(WARN, "could not list serial ports", str(exc))
        return

    if ports:
        results.report(
            PASS,
            "{} serial port(s)".format(len(ports)),
            "\n".join("{}: {}".format(p.device, p.description) for p in ports),
        )
    else:
        results.report(WARN, "no serial ports found", "the microcontroller may be unplugged")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--imports-only",
        action="store_true",
        help="check the interpreter and dependencies only; skip hardware. Used by the build script, "
        "which runs on a machine that may not have the cameras attached.",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Squid environment check")
    print("=" * 60)

    results = Results()
    check_interpreter(results)
    check_imports(results)

    if not args.imports_only:
        check_toupcam(results)
        check_daheng(results)
        check_serial(results)

    print("\n" + "=" * 60)
    if results.failed:
        print("RESULT: problems found -- see the FAIL lines above.")
    else:
        print("RESULT: all required checks passed.")
    print("=" * 60)

    return 1 if results.failed else 0


if __name__ == "__main__":
    sys.exit(main())
