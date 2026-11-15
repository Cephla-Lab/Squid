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

    def test_other_board_in_bootloader_refused(self):
        # -port= only aims the reboot; the Loader programs any board in HalfKay.
        boards = t.apply_events({}, [board("usb:1", 1111111), board("usb:2", 2222222, pid="0478")])
        with self.assertRaisesRegex(t.SelectionError, "SN 2222222 is in the bootloader"):
            t.select_board(boards, "1111111", "Teensy 4.1")

    def test_chosen_board_in_bootloader_allowed(self):
        boards = t.apply_events({}, [board("usb:1", 1111111, pid="0478"), board("usb:2", 2222222)])
        self.assertEqual(t.select_board(boards, "1111111", "Teensy 4.1")["address"], "usb:1")

    def test_single_board_in_bootloader_allowed(self):
        boards = t.apply_events({}, [board("usb:1", 1111111, pid="0478")])
        self.assertEqual(t.select_board(boards, "", "Teensy 4.1")["address"], "usb:1")

    def test_boards_entering_bootloader_during_upload(self):
        history = [
            (0.0, board("usb:2", 2222222, pid="0478")),  # before the upload: ignored
            (5.0, board("usb:1", 1111111, pid="0478")),  # the chosen board: expected
            (5.1, board("usb:3", 3333333, pid="0478")),  # another board: flagged
            (5.2, board("usb:2", 2222222)),  # running, not bootloader
        ]
        self.assertEqual(t.boards_entering_bootloader(history, "usb:1", since=1.0), ["usb:3"])

    def test_no_boards(self):
        with self.assertRaisesRegex(t.SelectionError, "No Teensy"):
            t.select_board({}, "", "Teensy 4.1")


class RebootPortTests(unittest.TestCase):
    class Port:
        def __init__(self, device, serial_number, vid=t.TEENSY_VID):
            self.device, self.serial_number, self.vid = device, serial_number, vid

    def test_usb_string_is_times_ten_below_ten_million(self):
        self.assertEqual(t.usb_serial_string("1682162"), "16821620")
        self.assertEqual(t.usb_serial_string("21315810"), "21315810")

    def test_finds_the_chosen_boards_port_only(self):
        ports = [self.Port("COM8", "20021340"), self.Port("COM5", "16821620"), self.Port("COM15", "21315810")]
        self.assertEqual(t.find_reboot_port(ports, "1682162"), "COM5")
        self.assertEqual(t.find_reboot_port(ports, "21315810"), "COM15")

    def test_exact_usb_string_no_cross_match(self):
        # Board 1234567 shows as "12345670" over USB; board 123456 would show as "1234560".
        ports = [self.Port("COM3", "12345670")]
        self.assertEqual(t.find_reboot_port(ports, "1234567"), "COM3")
        self.assertIsNone(t.find_reboot_port(ports, "123456"))

    def test_non_teensy_port_with_same_serial_ignored(self):
        self.assertIsNone(t.find_reboot_port([self.Port("COM6", "16821620", vid=0x0403)], "1682162"))

    def test_no_serial_port(self):
        self.assertIsNone(t.find_reboot_port([self.Port("COM5", None)], "1682162"))

    def test_several_interfaces_pick_first_by_name(self):
        ports = [self.Port("COM9", "16821620"), self.Port("COM5", "16821620")]
        self.assertEqual(t.find_reboot_port(ports, "1682162"), "COM5")


class ReadyToProgramTests(unittest.TestCase):
    def test_only_chosen_in_bootloader(self):
        boards = t.apply_events({}, [board("usb:1", 1111111, pid="0478"), board("usb:2", 2222222)])
        t.check_ready_to_program(boards, "usb:1")

    def test_other_board_in_bootloader_aborts(self):
        boards = t.apply_events({}, [board("usb:1", 1111111, pid="0478"), board("usb:2", 2222222, pid="0478")])
        with self.assertRaisesRegex(t.UploadError, "SN 2222222 is in the bootloader"):
            t.check_ready_to_program(boards, "usb:1")

    def test_chosen_not_in_bootloader_aborts(self):
        boards = t.apply_events({}, [board("usb:1", 1111111)])
        with self.assertRaisesRegex(t.UploadError, "not in the bootloader"):
            t.check_ready_to_program(boards, "usb:1")


class LoaderGuiTests(unittest.TestCase):
    def test_gui_names(self):
        for n in [
            "teensy.exe",
            "TEENSY.EXE",
            "teensy",
            "/Applications/Teensy.app/Contents/MacOS/Teensy",
            "C:\\Program Files\\Arduino\\hardware\\tools\\teensy.exe",
        ]:
            self.assertTrue(t.is_teensy_loader_gui(n), n)

    def test_cli_tools_are_not_the_gui(self):
        for n in ["teensy_ports.exe", "teensy_loader_cli", "teensy_reboot", "teensy_post_compile.exe", "python.exe"]:
            self.assertFalse(t.is_teensy_loader_gui(n), n)

    def test_parse_tasklist_csv(self):
        text = '"System Idle Process","0","Services","0","8 K"\r\n"teensy.exe","21748","Console","1","20,000 K"\r\n'
        self.assertEqual(t.parse_process_names(text, windows=True), ["System Idle Process", "teensy.exe"])

    def test_parse_ps(self):
        self.assertEqual(t.parse_process_names("bash\n/usr/bin/teensy\n\n", windows=False), ["bash", "/usr/bin/teensy"])


class CommandTests(unittest.TestCase):
    def test_loader_cli_never_waits_for_a_board(self):
        cmd = t.loader_cli_command("teensy_loader_cli", "teensy41", "fw.hex")
        self.assertEqual(cmd, ["teensy_loader_cli", "--mcu=TEENSY41", "-v", "fw.hex"])
        self.assertNotIn("-w", cmd)
        self.assertNotIn("-s", cmd)
        self.assertEqual(t.loader_cli_command("x", "teensylc", "f.hex")[1], "--mcu=TEENSYLC")

    def test_post_compile_passes_all_three_port_flags(self):
        b = board("Port_#0002.Hub_#0011", 1682162)
        cmd = t.post_compile_command("tools", "teensy41", os.path.join("build", "firmware.hex"), b)
        self.assertIn("-port=Port_#0002.Hub_#0011", cmd)
        self.assertIn("-portlabel=Port_#0002.Hub_#0011 Serial", cmd)
        self.assertIn("-portprotocol=Teensy", cmd)
        self.assertIn("-file=firmware", cmd)
        self.assertIn("-board=TEENSY41", cmd)

    def test_retryable_write_error(self):
        self.assertTrue(t.is_retryable_write_error("Programming...error writing to Teensy\n"))
        self.assertFalse(t.is_retryable_write_error("Unable to open device\n"))


class FakeMonitor:
    """Stands in for PortMonitor: the board set, plus events the fakes below queue for later polls."""

    def __init__(self, boards):
        self.boards = {}
        self.history = []
        self.pending = []
        self.inject(boards, at=-1.0)

    def inject(self, events, at=None):
        now = time.monotonic() if at is None else at
        self.history.extend((now, e) for e in events)
        t.apply_events(self.boards, events)

    def poll(self, wait_s, quiet_s=None):
        if self.pending:
            self.inject(self.pending.pop(0))

    def settle(self):
        return dict(self.boards)

    def close(self):
        pass


class UploadFlowTests(unittest.TestCase):
    """upload() end to end with every side effect faked: which board is rebooted, what is programmed."""

    CONTROLLER = ("Port_#0002.Hub_#0011", 1682162, "COM5")
    LASER = ("Port_#0002.Hub_#0009", 2002134, "COM8")
    FLUIDICS = ("Port_#0002.Hub_#0012", 2131581, "COM15")
    PATCHED = ["PortMonitor", "list_serial_ports", "request_bootloader", "run_loader", "running_process_names", "time"]

    def setUp(self):
        self.tools = tempfile.mkdtemp()
        for name in ["teensy_ports", "teensy_loader_cli", "teensy_post_compile"]:
            exe = name + (".exe" if sys.platform.startswith("win") else "")
            open(os.path.join(self.tools, exe), "w").close()
        self.rebooted = []
        self.loader_runs = []
        self.loader_results = []  # (rc, output) per run; success when empty
        self.processes = ["explorer.exe"]
        self.no_serial = set()
        self.extra_on_reboot = []  # other boards that (wrongly) enter the bootloader with the chosen one
        saved = {k: getattr(t, k) for k in self.PATCHED}
        self.addCleanup(lambda: [setattr(t, k, v) for k, v in saved.items()])

        class FastTime:
            monotonic = staticmethod(time.monotonic)

            @staticmethod
            def sleep(_):
                pass

        t.time = FastTime

    def rig(self, *specs, bootloader=()):
        evs = [board(a, sn, pid="0478" if sn in bootloader else "0483") for a, sn, _ in specs]
        self.monitor = FakeMonitor(evs)
        t.PortMonitor = lambda path: self.monitor
        t.running_process_names = lambda: self.processes
        port = RebootPortTests.Port
        t.list_serial_ports = lambda: [
            port(dev, t.usb_serial_string(sn)) for a, sn, dev in specs if sn not in self.no_serial
        ]

        def request_bootloader(device):
            self.rebooted.append(device)
            a, sn = next((a, sn) for a, sn, dev in specs if dev == device)
            self.monitor.pending.append([{"address": a, "online": False}])
            self.monitor.pending.append(
                [board(a, sn, pid="0478")] + [board(oa, osn, pid="0478") for oa, osn in self.extra_on_reboot]
            )

        def run_loader(cmd):
            self.loader_runs.append(cmd)
            rc, out = self.loader_results.pop(0) if self.loader_results else (0, "Booting\n")
            if rc == 0:
                for a, b in list(self.monitor.boards.items()):
                    if t.is_bootloader(b):
                        self.monitor.pending.append([{"address": a, "online": False}])
                        self.monitor.pending.append([board(a, b["iserial"])])
            return rc, out

        t.request_bootloader = request_bootloader
        t.run_loader = run_loader

    def test_reboots_and_programs_only_the_chosen_board_among_three(self):
        self.rig(self.CONTROLLER, self.LASER, self.FLUIDICS)
        t.upload(self.tools, "teensy41", "16821620", "fw.hex")
        self.assertEqual(self.rebooted, ["COM5"])
        self.assertEqual(len(self.loader_runs), 1)
        self.assertIn("--mcu=TEENSY41", self.loader_runs[0])

    def test_refused_while_teensy_loader_gui_open(self):
        self.rig(self.CONTROLLER, self.FLUIDICS)
        self.processes = ["explorer.exe", "teensy.exe"]
        with self.assertRaisesRegex(t.SelectionError, "Teensy Loader application is open"):
            t.upload(self.tools, "teensy41", "16821620", "fw.hex")
        self.assertEqual((self.rebooted, self.loader_runs), ([], []))

    def test_other_board_entering_bootloader_aborts_before_programming(self):
        self.rig(self.CONTROLLER, self.FLUIDICS)
        self.extra_on_reboot = [(self.FLUIDICS[0], self.FLUIDICS[1])]
        with self.assertRaisesRegex(t.UploadError, "SN 2131581 is in the bootloader"):
            t.upload(self.tools, "teensy41", "16821620", "fw.hex")
        self.assertEqual(self.loader_runs, [])

    def test_retries_error_writing_to_teensy(self):
        self.rig(self.CONTROLLER, self.FLUIDICS)
        self.loader_results = [(1, "Programming...error writing to Teensy\n")]
        t.upload(self.tools, "teensy41", "16821620", "fw.hex")
        self.assertEqual(len(self.loader_runs), 2)

    def test_other_loader_error_is_not_retried(self):
        self.rig(self.CONTROLLER, self.FLUIDICS)
        self.loader_results = [(1, "Unable to open device\n")]
        with self.assertRaisesRegex(t.UploadError, "exit code 1"):
            t.upload(self.tools, "teensy41", "16821620", "fw.hex")
        self.assertEqual(len(self.loader_runs), 1)

    def test_board_already_in_bootloader_is_not_rebooted(self):
        self.rig(self.CONTROLLER, self.FLUIDICS, bootloader=(1682162,))
        t.upload(self.tools, "teensy41", "16821620", "fw.hex")
        self.assertEqual(self.rebooted, [])
        self.assertEqual(len(self.loader_runs), 1)

    def test_no_serial_port_with_other_boards_refused(self):
        self.no_serial = {1682162}
        self.rig(self.CONTROLLER, self.FLUIDICS)
        with self.assertRaisesRegex(t.SelectionError, "no USB serial port"):
            t.upload(self.tools, "teensy41", "16821620", "fw.hex")
        self.assertEqual((self.rebooted, self.loader_runs), ([], []))

    def test_no_serial_port_lone_board_uses_teensy_loader_with_port_flags(self):
        self.no_serial = {1682162}
        self.rig(self.CONTROLLER)
        fake_run = t.run_loader

        def run_loader(cmd):
            # The Teensy Loader reboots the lone board itself.
            self.monitor.inject([board(self.CONTROLLER[0], self.CONTROLLER[1], pid="0478")])
            return fake_run(cmd)

        t.run_loader = run_loader
        t.upload(self.tools, "teensy41", "", "fw.hex")
        self.assertTrue(os.path.basename(self.loader_runs[0][0]).startswith("teensy_post_compile"))
        self.assertTrue(any(a.startswith("-portprotocol=") for a in self.loader_runs[0]))

    def test_wrong_serial_refused_without_touching_anything(self):
        self.rig(self.CONTROLLER, self.FLUIDICS)
        with self.assertRaisesRegex(t.SelectionError, "No connected Teensy has serial number 999"):
            t.upload(self.tools, "teensy41", "999", "fw.hex")
        self.assertEqual((self.rebooted, self.loader_runs), ([], []))

    def test_board_that_does_not_return_fails(self):
        self.rig(self.CONTROLLER, self.FLUIDICS)
        t.UPLOAD_RETURN_TIMEOUT_S, saved = 0.3, t.UPLOAD_RETURN_TIMEOUT_S
        self.addCleanup(setattr, t, "UPLOAD_RETURN_TIMEOUT_S", saved)
        self.loader_results = [(0, "Booting\n")]
        fake_run = t.run_loader

        def run_loader(cmd):
            rc, out = fake_run(cmd)
            self.monitor.pending.clear()  # the board never comes back
            return rc, out

        t.run_loader = run_loader
        with self.assertRaisesRegex(t.UploadError, "did not come back running"):
            t.upload(self.tools, "teensy41", "16821620", "fw.hex")


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
            with self.assertRaisesRegex(t.UploadError, "never rebooted"):
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
            with self.assertRaises(t.UploadError):
                t.wait_for_return(m, "usb:1", "1111111", time.monotonic(), timeout_s=2.5)
        finally:
            m.close()


if __name__ == "__main__":
    unittest.main()
