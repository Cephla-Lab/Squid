#!/usr/bin/env python3
"""Print the X and Y limit inputs that the Y-axis test firmware reports.

Only for a controller running the temporary image built from
firmware/controller/src/y_axis_test_build.h. Close Squid first: a serial port has one reader.
This script only listens; it sends nothing to the controller.

    python y_switch_monitor.py              list the serial ports
    python y_switch_monitor.py COM5         (Linux: /dev/ttyACM0)
    python y_switch_monitor.py --self-test

A line is printed when an input changes, when a stored setting changes, or when X or Y has moved
0.5 mm. Move each axis to both ends of its travel (joystick) and watch which input goes ACTIVE.
X is the reference for a healthy axis: its left input goes ACTIVE at the end where its position is
lowest, its right input at the other end, and neither is ACTIVE in between.
"""

import argparse
import sys
import time

PACKET_LENGTH = 24
# 200 full steps per revolution x 256 microsteps / 2.54 mm pitch: the controller's power-up setting.
DEFAULT_COUNTS_PER_MM = 200 * 256 / 2.54
REPORT_MOVE_MM = 0.5

LEFT, RIGHT = 0x01, 0x02
CFG_LEFT_ENABLED, CFG_RIGHT_ENABLED, CFG_LEFT_ACTIVE_HIGH, CFG_RIGHT_ACTIVE_HIGH = 0x01, 0x02, 0x04, 0x08


def crc8_ccitt(data: bytes) -> int:
    """CRC-8, polynomial 0x07, initial value 0: the status packet's last byte."""
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


class PacketReader:
    """Finds status packets in a byte stream that can start anywhere."""

    def __init__(self):
        self._buffer = bytearray()
        self.valid_packets = 0  # checksum and firmware version byte good
        self.reports = 0  # of those, the ones that carry this image's report

    def feed(self, data: bytes):
        """Yield (x, y, byte20, byte21) for each packet that carries the test image's report."""
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
            x = int.from_bytes(window[2:6], "big", signed=True)
            y = int.from_bytes(window[6:10], "big", signed=True)
            yield x, y, window[20], window[21]


def list_ports() -> None:
    from serial.tools import list_ports as lp

    ports = list(lp.comports())
    if not ports:
        print("No serial ports found.")
    for port in ports:
        print(f"{port.device}\t{port.description}")
    print(
        "\nRun again with the controller's port, e.g.  python y_switch_monitor.py "
        f"{ports[0].device if ports else 'COM5'}"
    )


def monitor(port: str, counts_per_mm: float) -> int:
    import serial

    reader = PacketReader()
    last_state = None
    last_stored = None
    last_x = last_y = None
    start = time.monotonic()
    warned = False

    print(f"Listening on {port}. Ctrl+C to stop. Positions assume {counts_per_mm:.1f} counts per mm.")
    with serial.Serial(port, 2000000, timeout=0.2) as link:
        while True:
            for x, y, byte20, byte21 in reader.feed(link.read(4096)):
                y_inputs, x_inputs, y_stored, x_stored = decode_status(byte20, byte21)
                if (x_stored, y_stored) != last_stored:
                    last_stored = (x_stored, y_stored)
                    print("\nSettings stored in the controller (what stock firmware runs with):")
                    print(f"  X: {describe_config(x_stored)}")
                    print(f"  Y: {describe_config(y_stored)}")
                    print(
                        f"\n{'time':>7} {'X mm':>9} {'Y mm':>9} | {'X left':>6} {'right':>6} | "
                        f"{'Y left':>6} {'right':>6} | Y as stock firmware reads it"
                    )
                    last_state = None
                moved = last_x is None or max(abs(x - last_x), abs(y - last_y)) >= REPORT_MOVE_MM * counts_per_mm
                if (x_inputs, y_inputs) != last_state or moved:
                    last_state = (x_inputs, y_inputs)
                    last_x, last_y = x, y
                    print(
                        f"{time.monotonic() - start:6.1f}s {x / counts_per_mm:9.2f} {y / counts_per_mm:9.2f} | "
                        f"{show_inputs(x_inputs)} | {show_inputs(y_inputs)} | "
                        f"{show_inputs(as_stock_reads(y_inputs, y_stored))}",
                        flush=True,
                    )
            if not warned and reader.reports == 0 and time.monotonic() - start > 3:
                warned = True
                if reader.valid_packets:
                    print(
                        "Status packets are arriving, but without the test image's report in bytes 20-21: "
                        "this controller is not running the Y-axis test image."
                    )
                else:
                    print("No status packets yet. Is this the controller's port, and is Squid closed?")


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

    def packet(x, y, byte20, byte21, version=0x19):
        body = bytearray(PACKET_LENGTH - 1)
        body[2:6] = x.to_bytes(4, "big", signed=True)
        body[6:10] = y.to_bytes(4, "big", signed=True)
        body[20], body[21], body[22] = byte20, byte21, version
        return bytes(body) + bytes([crc8_ccitt(body)])

    reader = PacketReader()
    stream = b"junk" + packet(1, 2, 0x80, 0x33)[7:]  # text, then the tail of a packet
    stream += packet(1000, -2000, 0x81, 0x33) + packet(5, 6, 0x00, 0x00) + packet(-7, 8, 0xA2, 0x37)
    got = []
    for i in range(0, len(stream), 5):  # in pieces, as a serial port delivers them
        got += list(reader.feed(stream[i : i + 5]))
    assert got == [(1000, -2000, 0x81, 0x33), (-7, 8, 0xA2, 0x37)], got
    assert reader.valid_packets == 3 and reader.reports == 2
    print("self-test passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("port", nargs="?", help="the controller's serial port")
    parser.add_argument(
        "--counts-per-mm",
        type=float,
        default=DEFAULT_COUNTS_PER_MM,
        help="X/Y microsteps per mm, for the positions shown (default: 256 microsteps, 2.54 mm pitch)",
    )
    parser.add_argument("--self-test", action="store_true", help="check the decoding and exit")
    args = parser.parse_args()

    if args.self_test:
        return self_test()
    if args.port is None:
        list_ports()
        return 0
    try:
        return monitor(args.port, args.counts_per_mm)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
