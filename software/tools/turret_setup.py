#!/usr/bin/env python3
"""One-time setup of the NiMotion objective-turret drive: factory parameters + EEPROM.

Writes the register values ObjectiveTurret4PosController expects (INIT_PARAMS) plus the
power-cycle-only microstep register (POWER_CYCLE_PARAMS), saves them to the drive's
EEPROM, reads everything back, and tells you to power-cycle the drive. Run it once per
new drive, or whenever the GUI refuses to start with "Turret microstep register reads N
... 16 microsteps is required".

Why a separate tool: the microstep register (0x001A) reads back the *pending* value but
only takes effect after a power cycle. The controller therefore never writes it (a
write-then-continue would let the next start pass the check while the drive still runs
the old scale and every move rotates the wrong angle). The write, the EEPROM save and the
power cycle have to happen together, with a person at the bench. That is this script.

Usage (from software/, with the machine .ini in place so the serial number, slave id and
baud rate come from OBJECTIVE_TURRET_* — or pass --port explicitly):

    python3 tools/turret_setup.py            # show current vs expected, confirm, write, save
    python3 tools/turret_setup.py --check    # read-only: report, exit 1 on any mismatch
    python3 tools/turret_setup.py --port /dev/ttyUSB0 --yes

Workflow:  turret_setup.py  ->  power-cycle the drive  ->  turret_setup.py --check  ->  GUI.
The direction register (0x0052) is runtime-only and intentionally not written here; the
controller sets it on every start and the manual forbids persisting it.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Callable, NamedTuple, Optional

# Tools are run as `python3 tools/turret_setup.py` from software/. Put software/ on the path
# and make it the working directory: control._def finds the machine .ini relative to cwd.
_SOFTWARE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SOFTWARE_DIR not in sys.path:
    sys.path.insert(0, _SOFTWARE_DIR)
os.chdir(_SOFTWARE_DIR)

import control._def  # noqa: E402
import control.objective_turret_controller as otc  # noqa: E402
from control.modbus_rtu import ModbusError, ModbusRTUClient  # noqa: E402

SETUP_PARAMS = otc.POWER_CYCLE_PARAMS + otc.INIT_PARAMS


class Row(NamedTuple):
    label: str
    addr: int
    current: int
    desired: int
    is_32bit: bool
    mask: Optional[int]

    @property
    def ok(self) -> bool:
        return self.current == self.desired

    def fmt(self, value: int) -> str:
        return otc.format_register_value(value, self.mask)


def _read_rows(modbus, slave_id: int) -> list:
    rows = []
    for addr, expected, label, kwargs in SETUP_PARAMS:
        current, desired, _ = otc.calibrate_register(modbus, slave_id, addr, expected, label, write=False, **kwargs)
        rows.append(Row(label, addr, current, desired, kwargs.get("is_32bit", False), kwargs.get("mask")))
    return rows


def _print_rows(rows, out: Callable[[str], None]) -> None:
    out(f"  {'register':<18} {'addr':<7} {'device':>12} {'expected':>12}  status")
    for r in rows:
        out(
            f"  {r.label:<18} 0x{r.addr:04X}  {r.fmt(r.current):>12} {r.fmt(r.desired):>12}  {'ok' if r.ok else 'MISMATCH'}"
        )


def run(
    modbus,
    slave_id: int,
    *,
    check_only: bool,
    out: Callable[[str], None] = print,
    confirm: Callable[[], bool] = lambda: True
) -> int:
    """Check or apply the factory parameters. Returns a process exit code (0 = drive matches).

    `confirm` is asked once, after the report and before anything is written."""
    out("Reading turret drive parameters...")
    rows = _read_rows(modbus, slave_id)
    _print_rows(rows, out)
    mismatched = [r for r in rows if not r.ok]

    if check_only:
        if mismatched:
            out(f"\n{len(mismatched)} register(s) differ from the controller's expectations.")
            out("Run without --check to write them and save to EEPROM.")
            return 1
        out("\nAll registers match. The drive is set up for ObjectiveTurret4PosController.")
        return 0

    if not confirm():
        out("Aborted; nothing written.")
        return 2

    otc.prepare_for_parameter_writes(modbus, slave_id)
    for r in mismatched:
        otc.write_register_value(modbus, slave_id, r.addr, r.desired, is_32bit=r.is_32bit)
        out(f"  wrote {r.label} @ 0x{r.addr:04X}: {r.fmt(r.current)} -> {r.fmt(r.desired)}")
    # Always persist: RAM matching does not prove EEPROM does, and this tool exists to make
    # the drive come up right after the power cycle. Direction (0x52) is left out on purpose.
    otc.save_to_eeprom(modbus, slave_id)

    out("\nRead-back verification:")
    verify = _read_rows(modbus, slave_id)
    _print_rows(verify, out)
    failed = [r for r in verify if not r.ok]
    if failed:
        out(f"\nFAILED: {len(failed)} register(s) did not take the written value.")
        out("Check that the motor is disabled (not in fault) and that the register is writable on this drive.")
        return 1

    out("\nDone. Now POWER-CYCLE the turret drive (switch its 24 V supply off and on).")
    if any(r.addr == otc.REG_MICROSTEP for r in mismatched):
        out("The microstep register was changed: the drive keeps running at the OLD microstep until")
        out("it is power-cycled, even though it already reads back the new value.")
    out("After the power cycle, run `python3 tools/turret_setup.py --check` to confirm the values")
    out("persisted, then start the GUI.")
    return 0


def _resolve_port(args) -> str:
    if args.port:
        return args.port
    serial_number = args.serial_number or control._def.OBJECTIVE_TURRET_SERIAL_NUMBER
    if not serial_number:
        sys.exit(
            "No --port given and OBJECTIVE_TURRET_SERIAL_NUMBER is empty in the machine .ini "
            "(or no configuration*.ini was found in software/). Pass --port or --serial-number."
        )
    return otc.find_port(serial_number)


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", help="serial device (e.g. /dev/ttyUSB0 or COM3); overrides --serial-number")
    parser.add_argument(
        "--serial-number", help="USB serial number of the RS-485 adapter (default: OBJECTIVE_TURRET_SERIAL_NUMBER)"
    )
    parser.add_argument(
        "--slave-id", type=int, default=control._def.OBJECTIVE_TURRET_SLAVE_ID, help="Modbus slave id (default: .ini)"
    )
    parser.add_argument(
        "--baudrate", type=int, default=control._def.OBJECTIVE_TURRET_BAUDRATE, help="serial baud rate (default: .ini)"
    )
    parser.add_argument("--check", action="store_true", help="read-only: report mismatches, write nothing")
    parser.add_argument("--yes", action="store_true", help="write without asking for confirmation")
    args = parser.parse_args(argv)

    port = _resolve_port(args)
    print(f"Connecting to turret drive on {port} (slave {args.slave_id}, {args.baudrate} baud)...")
    modbus = ModbusRTUClient(port=port, baudrate=args.baudrate, timeout=0.5)
    try:
        modbus.connect()
    except ModbusError as exc:
        sys.exit(f"Could not open {port}: {exc}")

    def confirm() -> bool:
        if args.yes:
            return True
        answer = input("\nWrite the differing registers and save ALL parameters to EEPROM? [y/N] ")
        return answer.strip().lower() in ("y", "yes")

    try:
        # The apply path leaves the motor disabled itself; --check must not touch the drive.
        return run(modbus, args.slave_id, check_only=args.check, confirm=confirm)
    finally:
        modbus.disconnect()


if __name__ == "__main__":
    sys.exit(main())
