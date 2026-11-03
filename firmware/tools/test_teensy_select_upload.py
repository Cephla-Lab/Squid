"""Host tests for teensy_select_upload.py:  python3 -m unittest discover -s firmware/tools"""

import os
import stat
import sys
import tempfile
import textwrap
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import teensy_select_upload as t  # noqa: E402


def board(address, iserial, name="Teensy 4.1", pid="0483", online=True):
    # Same fields as teensy_ports -J prints (tool-teensy 1.60).
    return {
        "address": address,
        "online": online,
        "label": f"{address} Serial",
        "vid": "16C0",
        "pid": pid,
        "iserial": str(iserial),
        "boardName": name,
        "protocol": "Teensy",
    }


def as_json_stream(*events):
    import json

    return "".join(json.dumps(e, indent=2) + "\n" for e in events)


class ParseTests(unittest.TestCase):
    def test_parses_multiline_objects_and_skips_debug_lines(self):
        text = "usb_remove: x\n" + as_json_stream(board("usb:1", 111), board("usb:2", 222)) + "purge, name=x\n"
        events = t.parse_events(text)
        self.assertEqual([e["address"] for e in events], ["usb:1", "usb:2"])

    def test_offline_event_removes_board(self):
        boards = t.apply_events({}, [board("usb:1", 111), board("usb:2", 222)])
        t.apply_events(boards, [{"address": "usb:1", "online": False}])
        self.assertEqual(list(boards), ["usb:2"])


class SerialMatchTests(unittest.TestCase):
    def test_actual_serial(self):
        self.assertTrue(t.sn_matches("1234567", "1234567"))

    def test_usb_string_times_ten_below_ten_million(self):
        # Teensy firmware reports 1234567 as "12345670" in the USB string (pyserial, CONTROLLER_SN).
        self.assertTrue(t.sn_matches("12345670", "1234567"))

    def test_times_ten_not_applied_at_or_above_ten_million(self):
        self.assertFalse(t.sn_matches("123456780", "12345678"))
        self.assertTrue(t.sn_matches("12345678", "12345678"))

    def test_junk_and_zero(self):
        self.assertFalse(t.sn_matches("abc", "1234567"))
        self.assertFalse(t.sn_matches("0", "0"))


class SelectTests(unittest.TestCase):
    def setUp(self):
        self.two = t.apply_events({}, [board("usb:1", 1111111), board("usb:2", 22222222, name="Teensy LC")])

    def test_single_board_needs_no_serial(self):
        one = t.apply_events({}, [board("usb:1", 1111111)])
        self.assertEqual(t.select_board(one, "", "Teensy 4.1")["address"], "usb:1")

    def test_two_boards_without_serial_refused(self):
        with self.assertRaisesRegex(t.SelectionError, "2 Teensy boards"):
            t.select_board(self.two, "", "Teensy 4.1")

    def test_pick_by_serial(self):
        self.assertEqual(t.select_board(self.two, "11111110", "Teensy 4.1")["address"], "usb:1")

    def test_unknown_serial_refused(self):
        with self.assertRaisesRegex(t.SelectionError, "No connected Teensy has serial number 999"):
            t.select_board(self.two, "999", "Teensy 4.1")

    def test_wrong_model_refused(self):
        with self.assertRaisesRegex(t.SelectionError, "is a Teensy LC"):
            t.select_board(self.two, "22222222", "Teensy 4.1")

    def test_ambiguous_serial_refused(self):
        # 1234567 shows as 12345670 over USB, so it collides with a board whose real SN is 12345670.
        boards = t.apply_events({}, [board("usb:1", 1234567), board("usb:2", 12345670)])
        with self.assertRaisesRegex(t.SelectionError, "more than one"):
            t.select_board(boards, "12345670", "Teensy 4.1")

    def test_no_boards(self):
        with self.assertRaisesRegex(t.SelectionError, "No Teensy"):
            t.select_board({}, "", "Teensy 4.1")


class FakeTeensyPorts:
    """Writes an executable that prints a scripted teensy_ports -J stream: [(delay_s, event), ...]."""

    def __init__(self, script):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "teensy_ports")
        lines = [f"#!{sys.executable}", "import json, sys, time"]
        for delay, ev in script:
            lines.append(f"time.sleep({delay})")
            lines.append(f"print(json.dumps({ev!r}, indent=2)); sys.stdout.flush()")
        lines.append("time.sleep(60)")
        with open(self.path, "w") as f:
            f.write(textwrap.dedent("\n".join(lines)) + "\n")
        os.chmod(self.path, os.stat(self.path).st_mode | stat.S_IEXEC)


@unittest.skipIf(sys.platform.startswith("win"), "fake teensy_ports uses a shebang")
class MonitorTests(unittest.TestCase):
    def test_discovery_then_reboot_cycle(self):
        fake = FakeTeensyPorts(
            [
                (0, board("usb:1", 1111111)),
                (0, board("usb:2", 2222222)),
                (1.5, {"address": "usb:1", "online": False}),  # reboots into the bootloader
                (0.1, board("usb:1", 1111111, pid="0478")),
                (0.3, {"address": "usb:1", "online": False}),
                (0.2, board("usb:1", 1111111)),  # back, running
            ]
        )
        m = t.PortMonitor(fake.path)
        try:
            boards = m.settle()
            self.assertEqual(sorted(boards), ["usb:1", "usb:2"])
            back = t.wait_for_return(m, "usb:1", "1111111", time.monotonic(), timeout_s=5)
            self.assertEqual(back["pid"], "0483")
        finally:
            m.close()

    def test_board_that_never_reboots_fails(self):
        fake = FakeTeensyPorts([(0, board("usb:1", 1111111))])
        m = t.PortMonitor(fake.path)
        try:
            m.settle()
            with self.assertRaisesRegex(t.SelectionError, "never rebooted"):
                t.wait_for_return(m, "usb:1", "1111111", time.monotonic(), timeout_s=1)
        finally:
            m.close()

    def test_other_board_rebooting_does_not_count(self):
        fake = FakeTeensyPorts(
            [
                (0, board("usb:1", 1111111)),
                (0, board("usb:2", 2222222)),
                (1.5, {"address": "usb:2", "online": False}),
                (0.1, board("usb:2", 2222222)),
            ]
        )
        m = t.PortMonitor(fake.path)
        try:
            m.settle()
            with self.assertRaises(t.SelectionError):
                t.wait_for_return(m, "usb:1", "1111111", time.monotonic(), timeout_s=2.5)
        finally:
            m.close()


if __name__ == "__main__":
    unittest.main()
