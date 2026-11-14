"""PlatformIO extra script: flash one Teensy chosen by its USB serial number.

Without this, `pio run -t upload` hands the image to the Teensy Loader, which programs whichever
Teensy it finds first. With a controller and a joystick panel (or two controllers) on one computer
that can be the wrong board.

    pio run -e teensy41 -t upload                          # exactly one Teensy connected
    pio run -e teensy41 -t upload --upload-port 12345678   # pick the board by serial number

Rules:
  * One Teensy connected and no --upload-port: it is flashed, as before.
  * Two or more connected and no --upload-port: refused; the connected boards and their serial
    numbers are listed.
  * --upload-port given: exactly that board is flashed. Refused if it is not connected.
  * The board must be the model the environment builds for (no joystick image on a controller).
  * Refused while the Teensy Loader application is open: in its automatic mode it programs any
    board that enters the bootloader, including the one this script reboots.
  * Refused while any OTHER board is in the bootloader: the loader programs whichever board is
    there, so only the chosen board may be.
  * After the upload, the board must come back running with the same serial number within
    UPLOAD_RETURN_TIMEOUT_S, and no other board may have entered the bootloader meanwhile, or the
    upload is reported as failed.

How the chosen board is flashed (the Teensy Loader application is not used):
  1. This script asks only that board to reboot into the bootloader, by opening its own USB serial
     port at 134 baud (Teensyduino's USB serial treats that as a reboot request). A board already
     in the bootloader (program button pressed) is not asked.
  2. It waits until that board, and no other, is in the bootloader.
  3. teensy_loader_cli (from PlatformIO's tool-teensy package) programs it and exits. It is run
     without -w, so it never waits for some other board to show up.

Why not the Teensy Loader: teensy_post_compile with only -port= ignored the port when several
boards were connected ("Found 3 Teensy boards, but using auto-search to find board for upload")
and rebooted and programmed a different board, a fluidics controller.

A chosen board with no USB serial port (a non-serial USB type) cannot be asked to reboot. If it is
the only Teensy connected, the Teensy Loader is used as before; otherwise press its program button
and rerun.

The serial number is the one the Teensy Loader shows, or the USB serial string that pyserial,
`pio device list` and Squid's CONTROLLER_SN show. Teensy firmware multiplies serial numbers below
10,000,000 by 10 in the USB string, so both spellings are accepted.

Boards are found with `teensy_ports -J` from PlatformIO's tool-teensy package (the discovery tool
the Arduino IDE uses).

PlatformIO runs this file as an extra script; it replaces the upload command with this same file
run as a program (`python teensy_select_upload.py upload ...`), which does the work above.
"""

import argparse
import inspect
import json
import os
import queue
import subprocess
import sys
import threading
import time

BOOTLOADER_PID = "0478"  # HalfKay bootloader, vid 16C0
TEENSY_VID = 0x16C0
DISCOVERY_SETTLE_S = 1.0  # stop listening once teensy_ports has been quiet this long
DISCOVERY_MAX_S = 4.0
BOOTLOADER_ENTER_TIMEOUT_S = 10.0
UPLOAD_RETURN_TIMEOUT_S = 30.0
REBOOT_BAUD = 134  # Teensyduino USB serial: opening at this rate reboots into the bootloader
PROGRAM_ATTEMPTS = 3  # teensy_loader_cli sometimes fails with "error writing to Teensy"; a retry works

BOARD_NAMES = {"teensy41": "Teensy 4.1", "teensylc": "Teensy LC"}
LOADER_MCU = {"teensy41": "TEENSY41", "teensylc": "TEENSYLC"}


class SelectionError(Exception):
    """Refused before anything was written to any board."""


class UploadError(Exception):
    """Failed after the chosen board was asked to reboot or was programmed."""


# ---------------------------------------------------------------------------------------------
# Pure logic (unit-tested in test_teensy_select_upload.py, no PlatformIO or hardware needed)
# ---------------------------------------------------------------------------------------------


def parse_events(text):
    """Split teensy_ports -J output into JSON objects. Non-JSON debug lines are skipped."""
    decoder = json.JSONDecoder()
    events = []
    i = 0
    while True:
        start = text.find("{", i)
        if start < 0:
            return events
        try:
            obj, end = decoder.raw_decode(text, start)
        except ValueError:
            i = start + 1
            continue
        if isinstance(obj, dict) and "address" in obj:
            events.append(obj)
        i = end


def apply_events(boards, events):
    """Fold add/remove events into {address: board} (later events win)."""
    for ev in events:
        online = ev.get("online", True)
        if online is False or str(online).lower() == "false":
            boards.pop(ev["address"], None)
        else:
            boards[ev["address"]] = ev
    return boards


def sn_matches(wanted, iserial):
    """True if `wanted` names the board whose actual serial number is `iserial`."""
    wanted = str(wanted).strip()
    try:
        actual = int(iserial)
        w = int(wanted)
    except (TypeError, ValueError):
        return False
    if actual <= 0:
        return False
    return w == actual or (actual < 10_000_000 and w == actual * 10)


def usb_serial_string(iserial):
    """The USB serial string Teensy firmware reports for serial number `iserial` (x10 below 10M)."""
    n = int(iserial)
    return str(n * 10 if n < 10_000_000 else n)


def describe(boards):
    if not boards:
        return "  (no Teensy found)"
    lines = []
    for b in sorted(boards.values(), key=lambda b: b["address"]):
        mode = " [bootloader]" if is_bootloader(b) else ""
        lines.append(f"  SN {b.get('iserial', '?'):>10}  {b.get('boardName', '?'):<12} {b['address']}{mode}")
    return "\n".join(lines)


def is_bootloader(board):
    return str(board.get("pid", "")).upper() == BOOTLOADER_PID


def select_board(boards, wanted_sn, expected_name):
    """Return the board to flash, or raise SelectionError explaining why not."""
    if not boards:
        raise SelectionError("No Teensy is connected.")
    if wanted_sn:
        hits = [b for b in boards.values() if sn_matches(wanted_sn, b.get("iserial"))]
        if not hits:
            raise SelectionError(f"No connected Teensy has serial number {wanted_sn}.")
        if len(hits) > 1:
            raise SelectionError(f"Serial number {wanted_sn} matches more than one connected Teensy.")
        board = hits[0]
    else:
        if len(boards) > 1:
            raise SelectionError(
                f"{len(boards)} Teensy boards are connected. Choose one by serial number:\n"
                "    pio run -e <env> -t upload --upload-port <SN>"
            )
        board = next(iter(boards.values()))
    name = board.get("boardName") or ""
    if expected_name and name and name != expected_name:
        raise SelectionError(
            f"Teensy SN {board.get('iserial')} is a {name}, but this environment builds for a {expected_name}."
        )
    stray = other_boards_in_bootloader(boards, board["address"])
    if stray:
        raise SelectionError(
            f"Teensy SN {', '.join(stray)} is in the bootloader. The loader programs any board in the "
            "bootloader, so it could receive this image. Unplug it, or power-cycle it so it runs its "
            "firmware, then retry."
        )
    return board


def other_boards_in_bootloader(boards, chosen_address):
    """Serial numbers of boards other than the chosen one that are in the bootloader now."""
    return sorted(
        str(b.get("iserial", "?")) for b in boards.values() if b["address"] != chosen_address and is_bootloader(b)
    )


def check_ready_to_program(boards, chosen_address):
    """Raise UploadError unless the chosen board, and no other, is in the bootloader."""
    stray = other_boards_in_bootloader(boards, chosen_address)
    if stray:
        raise UploadError(
            f"Teensy SN {', '.join(stray)} is in the bootloader as well as the chosen board. "
            "Nothing was programmed. Power-cycle that board and retry."
        )
    chosen = boards.get(chosen_address)
    if chosen is None or not is_bootloader(chosen):
        raise UploadError("The chosen board is not in the bootloader. Nothing was programmed.")


def boards_entering_bootloader(history, chosen_address, since):
    """Addresses other than the chosen one that entered the bootloader at or after `since`."""
    return sorted(
        {ev["address"] for t, ev in history if t >= since and ev["address"] != chosen_address and is_bootloader(ev)}
    )


def find_reboot_port(ports, iserial):
    """The USB serial port (device name) of the board with serial number `iserial`, or None.

    `ports` are pyserial ListPortInfo-like objects (device, vid, serial_number). Matched on the
    exact USB serial string, so 1234567 (USB "12345670") never matches a board whose own serial
    number is 12345670. A board with several serial interfaces gives several ports; any of them
    belongs to that board, the first by name is used.
    """
    usb_sn = usb_serial_string(iserial)
    hits = sorted(p.device for p in ports if p.vid == TEENSY_VID and str(p.serial_number or "").strip() == usb_sn)
    return hits[0] if hits else None


def is_teensy_loader_gui(process_name):
    """True for the Teensy Loader application (teensy.exe, teensy, Teensy.app), not its CLI tools."""
    base = os.path.basename(str(process_name).strip().strip('"').replace("\\", "/"))
    if base.lower().endswith(".exe"):
        base = base[:-4]
    return base.lower() == "teensy"


def parse_process_names(text, windows):
    """Process names from `tasklist /FO CSV /NH` (Windows) or `ps -A -o comm=` output."""
    names = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if windows:
            if line.startswith('"'):
                names.append(line[1:].split('"', 1)[0])
        else:
            names.append(line)
    return names


def is_retryable_write_error(output):
    return "error writing to teensy" in output.lower()


def loader_cli_command(loader_cli, board_id, hex_path):
    """teensy_loader_cli arguments. No -w: it must not wait for whichever board appears next."""
    return [loader_cli, f"--mcu={LOADER_MCU[board_id]}", "-v", hex_path]


def post_compile_command(tools_dir, board_id, hex_path, board):
    """The Teensy Loader path (only for a lone board with no serial port), with all three port flags."""
    exe = "teensy_post_compile.exe" if sys.platform.startswith("win") else "teensy_post_compile"
    return [
        os.path.join(tools_dir, exe),
        f"-file={os.path.splitext(os.path.basename(hex_path))[0]}",
        f"-path={os.path.dirname(os.path.abspath(hex_path))}",
        f"-tools={tools_dir}",
        f"-board={board_id.upper()}",
        "-reboot",
        f"-port={board['address']}",
        f"-portlabel={board.get('label', board['address'])}",
        f"-portprotocol={board.get('protocol', 'Teensy')}",
    ]


# ---------------------------------------------------------------------------------------------
# teensy_ports process
# ---------------------------------------------------------------------------------------------


class PortMonitor:
    """Runs `teensy_ports -J` and keeps the live set of connected Teensy boards."""

    def __init__(self, teensy_ports):
        self._proc = subprocess.Popen(
            [teensy_ports, "-J"],
            stdin=subprocess.PIPE,  # keep stdin open: some discovery tools exit on stdin EOF
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self._chunks = queue.Queue()
        self._buffer = ""
        self.boards = {}
        self.history = []  # (time, event) for every event seen
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self._proc.stdout:
            self._chunks.put(line)

    def poll(self, wait_s, quiet_s=None):
        """Read output for up to wait_s, stopping early after quiet_s without a new line."""
        deadline = time.monotonic() + wait_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                timeout = remaining if quiet_s is None else min(remaining, quiet_s)
                self._buffer += self._chunks.get(timeout=timeout)
            except queue.Empty:
                break
        # Only complete objects are consumed; a half-received one stays in the buffer.
        last_close = self._buffer.rfind("}")
        if last_close >= 0:
            events = parse_events(self._buffer[: last_close + 1])
            self._buffer = self._buffer[last_close + 1 :]
            now = time.monotonic()
            self.history.extend((now, e) for e in events)
            apply_events(self.boards, events)

    def settle(self):
        """Collect the boards that are connected now."""
        self.poll(DISCOVERY_MAX_S, quiet_s=DISCOVERY_SETTLE_S)
        return dict(self.boards)

    def close(self):
        try:
            self._proc.terminate()
            self._proc.wait(timeout=2)
        except Exception:
            self._proc.kill()


def tool_path(tools_dir, name):
    exe = name + ".exe" if sys.platform.startswith("win") else name
    path = os.path.join(tools_dir or "", exe)
    return path if os.path.isfile(path) else None


def find_teensy_ports(tools_dir):
    return tool_path(tools_dir, "teensy_ports")


def wait_for_bootloader(monitor, address, timeout_s):
    """Wait until the board at `address` is in the bootloader. Raises UploadError on timeout."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        monitor.poll(0.25)
        board = monitor.boards.get(address)
        if board is not None and is_bootloader(board):
            return board
    raise UploadError(
        f"The chosen board did not enter the bootloader within {timeout_s:.0f} s. Nothing was programmed."
    )


def wait_for_return(monitor, address, wanted_iserial, since, timeout_s, started_in_bootloader=False):
    """Wait until the board at `address` has left (or entered the bootloader) after `since`,
    then is back running with the same serial number. Returns the returned board or raises."""
    left = started_in_bootloader
    deadline = time.monotonic() + timeout_s
    seen = 0
    while time.monotonic() < deadline:
        monitor.poll(0.25)
        for t, ev in monitor.history[seen:]:
            if ev.get("address") != address or t < since:
                continue
            online = ev.get("online", True)
            gone = online is False or str(online).lower() == "false"
            if gone or is_bootloader(ev):
                left = True
            elif left and str(ev.get("iserial")) == str(wanted_iserial):
                return ev
        seen = len(monitor.history)
    if not left:
        raise UploadError(
            f"Teensy SN {wanted_iserial} never rebooted into the bootloader within {timeout_s:.0f} s; "
            "it was probably not programmed."
        )
    raise UploadError(
        f"Teensy SN {wanted_iserial} did not come back running within {timeout_s:.0f} s after programming. "
        "It may be stuck in the bootloader."
    )


# ---------------------------------------------------------------------------------------------
# Side effects: process list, serial reboot request, loaders
# ---------------------------------------------------------------------------------------------


def running_process_names():
    """Names of running processes, or None if they cannot be listed."""
    windows = sys.platform.startswith("win")
    cmd = ["tasklist", "/FO", "CSV", "/NH"] if windows else ["ps", "-A", "-o", "comm="]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=15).stdout
    except Exception:
        return None
    return parse_process_names(out, windows)


def request_bootloader(device):
    """Ask the board behind serial port `device` to reboot into the bootloader."""
    import serial  # pyserial: a PlatformIO dependency

    try:
        port = serial.Serial(device, REBOOT_BAUD)
        time.sleep(0.2)
        port.close()
    except serial.SerialException as e:
        raise SelectionError(
            f"Could not open {device} to reboot the board ({e}). Close any program using it (Squid, a serial "
            "monitor) and retry."
        )


def list_serial_ports():
    import serial.tools.list_ports  # pyserial: a PlatformIO dependency

    return list(serial.tools.list_ports.comports())


def run_loader(cmd):
    """Run a loader command, echoing its output. Returns (returncode, output)."""
    print(" ".join(f'"{c}"' if " " in c else c for c in cmd))
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    sys.stdout.write(proc.stdout)
    sys.stdout.flush()
    return proc.returncode, proc.stdout


def upload(tools_dir, board_id, wanted_sn, hex_path):
    """Flash `hex_path` to the chosen board. Raises SelectionError or UploadError."""
    teensy_ports = find_teensy_ports(tools_dir)
    loader_cli = tool_path(tools_dir, "teensy_loader_cli")
    if teensy_ports is None or loader_cli is None:
        raise SelectionError(f"teensy_ports and teensy_loader_cli are needed in {tools_dir}.")
    if board_id not in LOADER_MCU:
        raise SelectionError(f"Unknown board '{board_id}'.")

    names = running_process_names()
    if names is None:
        sys.stderr.write("WARNING: could not list running programs to check for the Teensy Loader.\n")
    elif any(is_teensy_loader_gui(n) for n in names):
        raise SelectionError(
            "The Teensy Loader application is open. In its automatic mode it programs any board that "
            "enters the bootloader, so it could flash the board this upload reboots. Close it and retry."
        )

    monitor = PortMonitor(teensy_ports)
    try:
        boards = monitor.settle()
        try:
            board = select_board(boards, wanted_sn, BOARD_NAMES[board_id])
        except SelectionError as e:
            raise SelectionError(f"{e}\nConnected Teensy boards:\n{describe(boards)}")
        address, iserial = board["address"], board.get("iserial")
        start = time.monotonic()

        if is_bootloader(board):
            print(f"Teensy SN {iserial} is already in the bootloader.")
        else:
            device = find_reboot_port(list_serial_ports(), iserial)
            if device is None:
                if len(boards) == 1:
                    return _upload_with_teensy_loader(monitor, tools_dir, board_id, hex_path, board, start)
                raise SelectionError(
                    f"Teensy SN {iserial} has no USB serial port to ask it to reboot through. Press its program "
                    "button (it then shows as in the bootloader) and rerun, or connect only that board."
                )
            print(f"Rebooting Teensy SN {iserial} ({board.get('boardName', '?')}) at {address} through {device}")
            request_bootloader(device)
            wait_for_bootloader(monitor, address, BOOTLOADER_ENTER_TIMEOUT_S)

        for attempt in range(1, PROGRAM_ATTEMPTS + 1):
            monitor.poll(0.5)
            check_ready_to_program(monitor.boards, address)
            print(f"Programming Teensy SN {iserial} (attempt {attempt} of {PROGRAM_ATTEMPTS})")
            rc, out = run_loader(loader_cli_command(loader_cli, board_id, hex_path))
            if rc == 0:
                break
            if not (is_retryable_write_error(out) and attempt < PROGRAM_ATTEMPTS):
                raise UploadError(f"teensy_loader_cli failed (exit code {rc}).")
            time.sleep(1.0)

        _verify_return(monitor, address, iserial, start)
    finally:
        monitor.close()


def _upload_with_teensy_loader(monitor, tools_dir, board_id, hex_path, board, start):
    """A lone board without a serial port: the Teensy Loader finds it, as the old upload did."""
    print(
        f"Teensy SN {board.get('iserial')} has no USB serial port and is the only Teensy connected: "
        "using the Teensy Loader. Close its window when the upload is done."
    )
    rc, _ = run_loader(post_compile_command(tools_dir, board_id, hex_path, board))
    if rc != 0:
        raise UploadError(f"teensy_post_compile failed (exit code {rc}).")
    _verify_return(monitor, board["address"], board.get("iserial"), start)


def _verify_return(monitor, address, iserial, start):
    errors = []
    try:
        wait_for_return(monitor, address, iserial, start, UPLOAD_RETURN_TIMEOUT_S)
    except UploadError as e:
        errors.append(str(e))
    # A board that reached the bootloader during the upload (someone pressed its button) may have
    # been programmed. Too late to stop, but never silent.
    others = boards_entering_bootloader(monitor.history, address, start)
    if others:
        sns = ", ".join(
            str(next((ev.get("iserial") for _, ev in monitor.history if ev["address"] == a), a)) for a in others
        )
        errors.append(
            f"Teensy SN {sns} entered the bootloader during this upload and may have been programmed "
            "with this image. Check it before use."
        )
    if errors:
        raise UploadError("\n".join(errors))
    print(f"Teensy SN {iserial} is running the new firmware.")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Flash one Teensy chosen by serial number.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    up = sub.add_parser("upload")
    up.add_argument("--tools", required=True, help="PlatformIO tool-teensy directory")
    up.add_argument("--board", required=True, choices=sorted(LOADER_MCU), help="PlatformIO board id")
    up.add_argument("--sn", default="", help="serial number of the board to flash (empty: the only one)")
    up.add_argument("hex")
    args = parser.parse_args(argv)
    try:
        upload(args.tools, args.board, args.sn.strip(), args.hex)
    except SelectionError as e:
        sys.stderr.write(f"\n*** Teensy upload refused: {e}\n")
        return 1
    except UploadError as e:
        sys.stderr.write("".join(f"\n*** Teensy upload FAILED: {line}\n" for line in str(e).splitlines() if line))
        return 1
    return 0


# ---------------------------------------------------------------------------------------------
# PlatformIO hook
# ---------------------------------------------------------------------------------------------


def _this_script():
    # SCons runs extra scripts without __file__, but compiles them under their own file name.
    return os.path.abspath(inspect.currentframe().f_code.co_filename)


def _install(env):
    from SCons.Script import COMMAND_LINE_TARGETS

    if "upload" not in COMMAND_LINE_TARGETS:
        return

    wanted_sn = env.subst("$UPLOAD_PORT").strip()
    protocol = env.subst("$UPLOAD_PROTOCOL")
    tools_dir = env.PioPlatform().get_package_dir("tool-teensy") or ""
    board_id = env.BoardConfig().id
    usable = (
        protocol == "teensy-gui"
        and board_id in LOADER_MCU
        and find_teensy_ports(tools_dir) is not None
        and tool_path(tools_dir, "teensy_loader_cli") is not None
    )

    if not usable:
        # teensy-cli (Linux ARM fallback) reboots with teensy_reboot -s, which cannot target a board.
        if wanted_sn:
            sys.stderr.write(
                f"\n*** Teensy upload refused: selecting a Teensy by serial number needs teensy_ports and "
                f"teensy_loader_cli in tool-teensy and the teensy-gui protocol (protocol is '{protocol}').\n"
            )
            env.Exit(1)
        sys.stderr.write(
            "\nWARNING: cannot list Teensy boards here; the first one found will be flashed. Connect only one.\n"
        )
        return

    env.Replace(
        UPLOADCMD=" ".join(
            [
                '"$PYTHONEXE"',
                f'"{_this_script()}"',
                "upload",
                f'--tools "{tools_dir}"',
                f"--board {board_id}",
                f'--sn "{wanted_sn}"',
                '"$SOURCE"',
            ]
        )
    )


try:
    Import("env")  # noqa: F821 - provided by SCons when PlatformIO runs this file
except NameError:
    if __name__ == "__main__":
        sys.exit(main())
    # otherwise imported by the unit tests
else:
    _install(env)  # noqa: F821
