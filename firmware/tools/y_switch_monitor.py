#!/usr/bin/env python3
"""Print the X and Y limit inputs that the Y-axis test firmware reports, and sweep Y to map them.

Only for a controller running the temporary image built from
firmware/controller/src/y_axis_test_build.h. Close Squid first: a serial port has one reader.

    python y_switch_monitor.py                      list the serial ports
    python y_switch_monitor.py COM5                 listen (Linux: /dev/ttyACM0); sends nothing
    python y_switch_monitor.py COM5 --sweep-y       move Y 20 mm each way and back, recording the inputs
    python y_switch_monitor.py COM5 --sweep-y 80    the same over 80 mm each way (the whole travel)
    python y_switch_monitor.py --self-test

Listening prints a line when an input changes, when a stored setting changes, or when X or Y has
moved 0.5 mm. X is the reference for a healthy axis: its left input goes ACTIVE at the end where its
position is lowest, its right input at the other end, and neither is ACTIVE in between.

--sweep-y is for a machine without a joystick. It MOVES THE STAGE: Y goes down by the distance in
1 mm steps, up by twice the distance, and back down to where it started, slowly. It refuses to run
unless the controller reports the test image, where Y's limit switches are ignored and Y's current is
a tenth of normal: if Y reaches the end of its travel during the sweep it stalls there at that
current until the sweep turns round. Watch the stage: the script cannot tell whether Y really moved.
"""

import argparse
import sys
import time
from collections import namedtuple

PACKET_LENGTH = 24
COMMAND_LENGTH = 8
# 200 full steps per revolution x 256 microsteps / 2.54 mm pitch: the controller's power-up setting.
DEFAULT_COUNTS_PER_MM = 200 * 256 / 2.54
REPORT_MOVE_MM = 0.5

LEFT, RIGHT = 0x01, 0x02
CFG_LEFT_ENABLED, CFG_RIGHT_ENABLED, CFG_LEFT_ACTIVE_HIGH, CFG_RIGHT_ACTIVE_HIGH = 0x01, 0x02, 0x04, 0x08

# firmware/controller/src/constants_protocol.h
MOVE_Y, SET_LIM, SET_MAX_VELOCITY_ACCELERATION = 1, 9, 22
AXIS_Y = 1
LIM_CODE_Y_POSITIVE, LIM_CODE_Y_NEGATIVE = 2, 3
COMPLETED_WITHOUT_ERRORS, IN_PROGRESS = 0, 1

SWEEP_STEP_MM = 1.0
SWEEP_VELOCITY_MM_S = 10.0
SWEEP_ACCELERATION_MM_S2 = 100.0
SWEEP_LIMIT_MARGIN_MM = 300.0

Packet = namedtuple("Packet", "cmd_id status x y byte20 byte21")


def crc8_ccitt(data: bytes) -> int:
    """CRC-8, polynomial 0x07, initial value 0: the last byte of a status packet and of a command."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def decode_status(byte20: int, byte21: int):
    """(y_inputs, x_inputs, y_stored, x_stored), or None if byte 20 is not this image's report.

    Mirrors y_axis_test_pack_status() in y_axis_test_build.h.
    """
    if not byte20 & 0x80:
        return None
    return byte20 & 0x03, (byte20 >> 4) & 0x03, byte21 & 0x0F, (byte21 >> 4) & 0x0F


def as_stock_reads(inputs_low: int, stored: int) -> int:
    """Y's inputs as stock firmware would read them: the test image reads Y as active-low, stock
    reads each input with its stored polarity."""
    seen = 0
    if bool(inputs_low & LEFT) != bool(stored & CFG_LEFT_ACTIVE_HIGH):
        seen |= LEFT
    if bool(inputs_low & RIGHT) != bool(stored & CFG_RIGHT_ACTIVE_HIGH):
        seen |= RIGHT
    return seen


def describe_config(stored: int) -> str:
    def side(enabled_bit, active_high_bit):
        if not stored & enabled_bit:
            return "DISABLED"
        return "enabled, active-HIGH" if stored & active_high_bit else "enabled, active-low"

    text = (
        f"left {side(CFG_LEFT_ENABLED, CFG_LEFT_ACTIVE_HIGH)}; right {side(CFG_RIGHT_ENABLED, CFG_RIGHT_ACTIVE_HIGH)}"
    )
    return f"{text}  (0x{stored:X}{', the stock setting' if stored == 0x03 else ', NOT the stock setting 0x3'})"


def show_inputs(inputs: int) -> str:
    return f"{'ACTIVE' if inputs & LEFT else '-':>6} {'ACTIVE' if inputs & RIGHT else '-':>6}"


def build_command(cmd_id: int, command: int, payload: bytes) -> bytes:
    """An 8-byte command: id, command, payload, zero padding, CRC-8."""
    body = bytearray(COMMAND_LENGTH - 1)
    body[0], body[1] = cmd_id, command
    body[2 : 2 + len(payload)] = payload
    return bytes(body) + bytes([crc8_ccitt(body)])


class PacketReader:
    """Finds status packets in a byte stream that can start anywhere."""

    def __init__(self):
        self._buffer = bytearray()
        self.valid_packets = 0  # checksum and firmware version byte good
        self.reports = 0  # of those, the ones that carry this image's report

    def feed(self, data: bytes):
        """Yield a Packet for each status packet that carries the test image's report."""
        self._buffer += data
        while len(self._buffer) >= PACKET_LENGTH:
            window = bytes(self._buffer[:PACKET_LENGTH])
            # The version byte (22) is non-zero from firmware 1.0 on: a window that starts mid-packet
            # can match the checksum by chance, but not with the version byte in place as well.
            if window[22] == 0 or crc8_ccitt(window[:-1]) != window[-1]:
                del self._buffer[0]
                continue
            del self._buffer[:PACKET_LENGTH]
            self.valid_packets += 1
            if not window[20] & 0x80:
                continue
            self.reports += 1
            yield Packet(
                window[0],
                window[1],
                int.from_bytes(window[2:6], "big", signed=True),
                int.from_bytes(window[6:10], "big", signed=True),
                window[20],
                window[21],
            )


def list_ports() -> None:
    from serial.tools import list_ports as lp

    ports = list(lp.comports())
    if not ports:
        print("No serial ports found.")
    for port in ports:
        print(f"{port.device}\t{port.description}")
    print(
        f"\nRun again with the controller's port, e.g.  python y_switch_monitor.py {ports[0].device if ports else 'COM5'}"
    )


def print_stored(x_stored: int, y_stored: int) -> None:
    print("\nSettings stored in the controller (what stock firmware runs with):")
    print(f"  X: {describe_config(x_stored)}")
    print(f"  Y: {describe_config(y_stored)}")


def monitor(link, counts_per_mm: float) -> int:
    reader = PacketReader()
    last_state = None
    last_stored = None
    last_x = last_y = None
    start = time.monotonic()
    warned = False

    while True:
        for packet in reader.feed(link.read(4096)):
            y_inputs, x_inputs, y_stored, x_stored = decode_status(packet.byte20, packet.byte21)
            if (x_stored, y_stored) != last_stored:
                last_stored = (x_stored, y_stored)
                print_stored(x_stored, y_stored)
                print(
                    f"\n{'time':>7} {'X mm':>9} {'Y mm':>9} | {'X left':>6} {'right':>6} | "
                    f"{'Y left':>6} {'right':>6} | Y as stock firmware reads it"
                )
                last_state = None
            moved = last_x is None or (
                max(abs(packet.x - last_x), abs(packet.y - last_y)) >= REPORT_MOVE_MM * counts_per_mm
            )
            if (x_inputs, y_inputs) != last_state or moved:
                last_state = (x_inputs, y_inputs)
                last_x, last_y = packet.x, packet.y
                print(
                    f"{time.monotonic() - start:6.1f}s {packet.x / counts_per_mm:9.2f} {packet.y / counts_per_mm:9.2f} | "
                    f"{show_inputs(x_inputs)} | {show_inputs(y_inputs)} | "
                    f"{show_inputs(as_stock_reads(y_inputs, y_stored))}",
                    flush=True,
                )
        if not warned and reader.reports == 0 and time.monotonic() - start > 3:
            warned = True
            print(no_report_message(reader))


def no_report_message(reader: PacketReader) -> str:
    if reader.valid_packets:
        return (
            "Status packets are arriving, but without the test image's report in bytes 20-21: "
            "this controller is not running the Y-axis test image."
        )
    return "No status packets. Is this the controller's port, and is Squid closed?"


class Controller:
    """Sends commands and follows the status packets. Used by the sweep only."""

    def __init__(self, link):
        self._link = link
        self._reader = PacketReader()
        self._cmd_id = 0
        self.last = None  # the newest packet that carries the test image's report

    @property
    def reader(self) -> PacketReader:
        return self._reader

    def listen(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            for packet in self._reader.feed(self._link.read(4096)):
                self.last = packet

    def command(self, command: int, payload: bytes, timeout_s: float = 10.0) -> None:
        """Send one command and wait until the controller reports it finished."""
        # Never the id the controller is echoing now: a packet from before this command must not
        # pass for its answer.
        previous = self.last.cmd_id if self.last is not None else self._cmd_id
        self._cmd_id = (max(previous, self._cmd_id) + 1) % 256
        self._link.write(build_command(self._cmd_id, command, payload))
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            for packet in self._reader.feed(self._link.read(4096)):
                self.last = packet
                if packet.cmd_id != self._cmd_id or packet.status == IN_PROGRESS:
                    continue
                if packet.status != COMPLETED_WITHOUT_ERRORS:
                    raise RuntimeError(f"The controller refused command {command}: status {packet.status}.")
                return
        raise TimeoutError(f"Command {command} did not finish within {timeout_s:.0f} s.")


def describe_sweep(samples) -> list:
    """One line per Y input over the sweep's samples [(offset_mm, y_inputs), ...]."""
    lines = []
    low = min(offset for offset, _ in samples)
    high = max(offset for offset, _ in samples)
    for name, bit in (("left", LEFT), ("right", RIGHT)):
        active = sorted({offset for offset, inputs in samples if inputs & bit})
        clear = sorted({offset for offset, inputs in samples if not inputs & bit})
        if not active:
            lines.append(f"Y {name}: never ACTIVE between {low:+.0f} and {high:+.0f} mm.")
        elif not clear:
            lines.append(f"Y {name}: ACTIVE at every position between {low:+.0f} and {high:+.0f} mm.")
        else:
            lines.append(
                f"Y {name}: ACTIVE at {len(active)} positions from {active[0]:+.0f} to {active[-1]:+.0f} mm, "
                f"clear at {len(clear)} from {clear[0]:+.0f} to {clear[-1]:+.0f} mm."
            )
    return lines


def sweep_y(link, counts_per_mm: float, distance_mm: float, settle_s: float = 0.05) -> int:
    controller = Controller(link)
    controller.listen(1.0)
    if controller.last is None or controller.reader.reports < 5:
        print(no_report_message(controller.reader))
        print("Nothing was sent to the controller and the stage was not moved.")
        return 1

    y_inputs, _, y_stored, x_stored = decode_status(controller.last.byte20, controller.last.byte21)
    print_stored(x_stored, y_stored)
    steps = int(round(distance_mm / SWEEP_STEP_MM))
    print(
        f"\nSweeping Y: down {steps} mm, up {2 * steps} mm, down {steps} mm, in {SWEEP_STEP_MM:.0f} mm steps at "
        f"{SWEEP_VELOCITY_MM_S:.0f} mm/s. Ctrl+C stops after the step under way."
    )

    def int32(value: float) -> bytes:
        return int(round(value)).to_bytes(4, "big", signed=True)

    # Slow, and with travel limits out of the way: Squid leaves its own in the controller, counted
    # from wherever Y stood when Squid "homed" it.
    start_counts = controller.last.y
    controller.command(
        SET_MAX_VELOCITY_ACCELERATION,
        bytes([AXIS_Y])
        + int(SWEEP_VELOCITY_MM_S * 100).to_bytes(2, "big")
        + int(SWEEP_ACCELERATION_MM_S2 * 10).to_bytes(2, "big"),
    )
    margin = (distance_mm + SWEEP_LIMIT_MARGIN_MM) * counts_per_mm
    controller.command(SET_LIM, bytes([LIM_CODE_Y_NEGATIVE]) + int32(start_counts - margin))
    controller.command(SET_LIM, bytes([LIM_CODE_Y_POSITIVE]) + int32(start_counts + margin))

    print(f"\n{'offset':>9} {'Y mm':>9} | {'Y left':>6} {'right':>6}")
    samples = [(0.0, y_inputs)]
    print(f"{0.0:+8.0f}  {start_counts / counts_per_mm:9.2f} | {show_inputs(y_inputs)}", flush=True)
    offset = 0.0
    last_inputs = y_inputs
    try:
        for direction in [-1] * steps + [1] * (2 * steps) + [-1] * steps:
            controller.command(MOVE_Y, int32(direction * SWEEP_STEP_MM * counts_per_mm))
            controller.listen(settle_s)  # a packet from after the stop
            offset += direction * SWEEP_STEP_MM
            inputs = decode_status(controller.last.byte20, controller.last.byte21)[0]
            samples.append((offset, inputs))
            turning = abs(offset) == steps * SWEEP_STEP_MM or offset == 0
            if inputs != last_inputs or turning or offset % 5 == 0:
                changed = "   <-- changed" if inputs != last_inputs else ""
                print(
                    f"{offset:+8.0f}  {controller.last.y / counts_per_mm:9.2f} | {show_inputs(inputs)}{changed}",
                    flush=True,
                )
            last_inputs = inputs
    except KeyboardInterrupt:
        print("Stopped. Y is not back where it started.")

    print()
    for line in describe_sweep(samples):
        print(line)
    print("Offsets are from where Y stood when the sweep began. They are commanded positions: if the stage")
    print("stalled at an end of its travel, it moved less than the numbers say.")
    return 0


def self_test() -> int:
    # The vectors of firmware/controller/test/test_y_axis_test_build
    assert decode_status(0x80, 0x33) == (0x0, 0x0, 0x3, 0x3)
    assert decode_status(0xA1, 0x37) == (0x1, 0x2, 0x7, 0x3)
    assert decode_status(0x83, 0xF0) == (0x3, 0x0, 0x0, 0xF)
    assert decode_status(0x00, 0x00) is None

    # Stock polarity: stock reads what the image reads. Left stored active-high: left is inverted.
    assert as_stock_reads(0x0, 0x3) == 0x0 and as_stock_reads(0x3, 0x3) == 0x3
    assert as_stock_reads(0x0, 0x7) == LEFT and as_stock_reads(0x1, 0x7) == 0x0
    assert as_stock_reads(0x0, 0xF) == 0x3

    assert crc8_ccitt(b"123456789") == 0xF4  # the CRC-8 check value

    def packet(x, y, byte20, byte21, version=0x19, cmd_id=0, status=0):
        body = bytearray(PACKET_LENGTH - 1)
        body[0], body[1] = cmd_id, status
        body[2:6] = x.to_bytes(4, "big", signed=True)
        body[6:10] = y.to_bytes(4, "big", signed=True)
        body[20], body[21], body[22] = byte20, byte21, version
        return bytes(body) + bytes([crc8_ccitt(body)])

    reader = PacketReader()
    stream = b"junk" + packet(1, 2, 0x80, 0x33)[7:]  # text, then the tail of a packet
    stream += packet(1000, -2000, 0x81, 0x33, cmd_id=7, status=1) + packet(5, 6, 0x00, 0x00) + packet(-7, 8, 0xA2, 0x37)
    got = []
    for i in range(0, len(stream), 5):  # in pieces, as a serial port delivers them
        got += list(reader.feed(stream[i : i + 5]))
    assert got == [Packet(7, 1, 1000, -2000, 0x81, 0x33), Packet(0, 0, -7, 8, 0xA2, 0x37)], got
    assert reader.valid_packets == 3 and reader.reports == 2

    # MOVE_Y by -20157 counts with id 5: the bytes Squid's Microcontroller sends for the same move
    command = build_command(5, MOVE_Y, (-20157).to_bytes(4, "big", signed=True))
    assert command[:7] == bytes([5, 1, 0xFF, 0xFF, 0xB1, 0x43, 0]) and command[7] == crc8_ccitt(command[:7])

    assert describe_sweep([(0, RIGHT), (-1, RIGHT), (1, 0), (2, 0)]) == [
        "Y left: never ACTIVE between -1 and +2 mm.",
        "Y right: ACTIVE at 2 positions from -1 to +0 mm, clear at 2 from +1 to +2 mm.",
    ]
    assert describe_sweep([(0, RIGHT), (1, RIGHT)])[1] == "Y right: ACTIVE at every position between +0 and +1 mm."
    print("self-test passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("port", nargs="?", help="the controller's serial port")
    parser.add_argument(
        "--counts-per-mm",
        type=float,
        default=DEFAULT_COUNTS_PER_MM,
        help="X/Y microsteps per mm (default: 256 microsteps, 2.54 mm pitch)",
    )
    parser.add_argument(
        "--sweep-y",
        nargs="?",
        type=float,
        const=20.0,
        metavar="MM",
        help="MOVE Y this far each way (default 20 mm) and back, recording its limit inputs",
    )
    parser.add_argument("--self-test", action="store_true", help="check the decoding and exit")
    args = parser.parse_args()

    if args.self_test:
        return self_test()
    if args.port is None:
        list_ports()
        return 0
    if args.sweep_y is not None and not 1 <= args.sweep_y <= 100:
        parser.error("--sweep-y takes a distance from 1 to 100 mm")

    import serial

    try:
        with serial.Serial(args.port, 2000000, timeout=0.05) as link:
            print(f"Connected to {args.port}. Positions assume {args.counts_per_mm:.1f} counts per mm.")
            if args.sweep_y is not None:
                return sweep_y(link, args.counts_per_mm, args.sweep_y)
            print("Listening. Ctrl+C to stop.")
            return monitor(link, args.counts_per_mm)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
