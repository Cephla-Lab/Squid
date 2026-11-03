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
  * After the upload, the board must leave and come back running with the same serial number
    within UPLOAD_RETURN_TIMEOUT_S, or the upload is reported as failed.

The serial number is the one the Teensy Loader shows, or the USB serial string that pyserial,
`pio device list` and Squid's CONTROLLER_SN show. Teensy firmware multiplies serial numbers below
10,000,000 by 10 in the USB string, so both spellings are accepted.

Boards are found with `teensy_ports -J` from PlatformIO's tool-teensy package (the discovery tool
the Arduino IDE uses). The chosen board's USB location goes to teensy_post_compile as -port=.
"""

import json
import os
import queue
import subprocess
import sys
import threading
import time

BOOTLOADER_PID = "0478"  # HalfKay bootloader, vid 16C0
DISCOVERY_SETTLE_S = 1.0  # stop listening once teensy_ports has been quiet this long
DISCOVERY_MAX_S = 4.0
UPLOAD_RETURN_TIMEOUT_S = 30.0

BOARD_NAMES = {"teensy41": "Teensy 4.1", "teensylc": "Teensy LC"}


class SelectionError(Exception):
    pass


# ---------------------------------------------------------------------------------------------
# Pure logic (unit-tested in test_teensy_select_upload.py, no PlatformIO needed)
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
    return board


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


def find_teensy_ports(tools_dir):
    exe = "teensy_ports.exe" if sys.platform.startswith("win") else "teensy_ports"
    path = os.path.join(tools_dir or "", exe)
    return path if os.path.isfile(path) else None


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
        raise SelectionError(
            f"Teensy SN {wanted_iserial} never rebooted into the bootloader within {timeout_s:.0f} s; "
            "it was probably not programmed."
        )
    raise SelectionError(
        f"Teensy SN {wanted_iserial} did not come back running within {timeout_s:.0f} s after programming. "
        "It may be stuck in the bootloader: check the Teensy Loader window."
    )


# ---------------------------------------------------------------------------------------------
# PlatformIO hooks
# ---------------------------------------------------------------------------------------------


def _install(env):
    from SCons.Script import COMMAND_LINE_TARGETS

    if "upload" not in COMMAND_LINE_TARGETS:
        return

    def fail(msg, boards=None):
        sys.stderr.write(f"\n*** Teensy upload refused: {msg}\n")
        if boards is not None:
            sys.stderr.write("Connected Teensy boards:\n" + describe(boards) + "\n")
        env.Exit(1)

    wanted_sn = env.subst("$UPLOAD_PORT").strip()
    protocol = env.subst("$UPLOAD_PROTOCOL")
    platform = env.PioPlatform()
    teensy_ports = find_teensy_ports(platform.get_package_dir("tool-teensy"))
    expected_name = BOARD_NAMES.get(env.BoardConfig().id, "")

    if protocol != "teensy-gui" or teensy_ports is None:
        # teensy_loader_cli (Linux ARM fallback) cannot target a board; neither can anything
        # without teensy_ports.
        if wanted_sn:
            fail(f"selecting a Teensy by serial number needs the Teensy Loader (protocol is '{protocol}').")
        sys.stderr.write(
            "\nWARNING: cannot list Teensy boards here; the first one found will be flashed. " "Connect only one.\n"
        )
        return

    state = {}

    def before_upload(source, target, env):
        monitor = PortMonitor(teensy_ports)
        boards = monitor.settle()
        try:
            board = select_board(boards, wanted_sn, expected_name)
        except SelectionError as e:
            monitor.close()
            fail(str(e), boards)
        if is_bootloader(board):
            print(f"Note: Teensy SN {board.get('iserial')} is already in the bootloader.")
        print(f"Flashing Teensy SN {board.get('iserial')} ({board.get('boardName', '?')}) at {board['address']}")
        env.Append(UPLOADERFLAGS=[f"-port={board['address']}"])
        state.update(monitor=monitor, board=board, start=time.monotonic())

    def after_upload(source, target, env):
        monitor, board = state["monitor"], state["board"]
        try:
            back = wait_for_return(
                monitor,
                board["address"],
                board.get("iserial"),
                state["start"],
                UPLOAD_RETURN_TIMEOUT_S,
                started_in_bootloader=is_bootloader(board),
            )
        except SelectionError as e:
            sys.stderr.write(f"\n*** Teensy upload FAILED: {e}\n")
            env.Exit(1)
        finally:
            monitor.close()
        print(f"Teensy SN {back.get('iserial')} is running the new firmware.")

    env.AddPreAction("upload", before_upload)
    env.AddPostAction("upload", after_upload)


try:
    Import("env")  # noqa: F821 - provided by SCons when PlatformIO runs this file
except NameError:
    pass  # imported by the unit tests
else:
    _install(env)  # noqa: F821
