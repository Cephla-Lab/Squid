"""Tests for the CrestOptics X-Light serial driver.

The driver is exercised against a fake serial port that emulates the X-Light V3 protocol:
commands and replies are terminated by a carriage return, set commands are echoed back, and
"r<prefix>" / "r<prefix>N" query a device's position / number of positions.
"""

from types import SimpleNamespace

import pytest

from control import serial_peripherals
from control.serial_peripherals import SerialDeviceError, XLight

SERIAL_NUMBER = "XLIGHT-TEST"

# idc bits: disk motor, disk slider, dichroic wheel, emission wheel, both irises, dichroic slider
V3_CONFIG_WORD = 0x001 | 0x002 | 0x004 | 0x008 | 0x200 | 0x400 | 0x800


class FakeXLightV3Port:
    """Stands in for serial.Serial, with an X-Light V3 on the other end of the cable."""

    def __init__(self):
        self.is_open = True
        self.commands = []
        # When True the device ignores everything it is sent (busy / disconnected)
        self.unresponsive = False
        self.positions = {"B": 1, "C": 1, "D": 0, "N": 0, "P": 0, "J": 350, "V": 1000}
        self.position_counts = {"B": 8, "C": 3, "P": 3}
        self._unread = b""

    # --- serial.Serial interface used by SerialDevice ---

    def open(self):
        self.is_open = True

    def close(self):
        self.is_open = False

    @property
    def in_waiting(self):
        return len(self._unread)

    def reset_input_buffer(self):
        self._unread = b""

    def write(self, data):
        command = data.decode().rstrip("\r")
        self.commands.append(command)
        if self.unresponsive:
            return
        reply = self._execute(command)
        if reply is not None:
            self._unread += (reply + "\r").encode()

    def readline(self):
        # Replies end in "\r", so a real readline() returns everything received before the timeout
        newline = self._unread.find(b"\n")
        end = len(self._unread) if newline < 0 else newline + 1
        line, self._unread = self._unread[:end], self._unread[end:]
        return line

    # --- test helpers ---

    def leave_unread_reply(self, reply):
        """Queue a reply that the host never read (e.g. from an unvalidated wheel move)."""
        self._unread += (reply + "\r").encode()

    def commands_sent(self, prefix):
        return [c for c in self.commands if c.startswith(prefix)]

    # --- device emulation ---

    def _execute(self, command):
        if command == "idc":
            return format(V3_CONFIG_WORD, "08X")
        if command.startswith("r"):
            target = command[1:]
            if len(target) == 2 and target[1] == "N" and target[0] in self.position_counts:
                return command + str(self.position_counts[target[0]])
            if target in self.positions:
                return command + str(self.positions[target])
            return None
        prefix, value = command[0], command[1:]
        if prefix in self.positions and value.isdigit():
            self.positions[prefix] = int(value)
            return command
        return None


@pytest.fixture
def port(monkeypatch):
    fake_port = FakeXLightV3Port()
    monkeypatch.setattr(
        serial_peripherals.list_ports,
        "comports",
        lambda: [SimpleNamespace(serial_number=SERIAL_NUMBER, device="/dev/fake-xlight", vid=None, pid=None)],
    )
    monkeypatch.setattr(serial_peripherals.serial, "Serial", lambda *args, **kwargs: fake_port)
    # The driver sleeps for seconds while wheels and irises move
    monkeypatch.setattr(serial_peripherals.time, "sleep", lambda seconds: None)
    return fake_port


@pytest.fixture
def xlight(port):
    return XLight(SERIAL_NUMBER)


class TestIris:
    def test_closing_the_iris_reaches_hardware_that_is_not_already_closed(self, xlight, port):
        # The device starts with the illumination iris at 35%
        xlight.set_illumination_iris(0)

        assert port.positions["J"] == 0

    def test_command_is_sent_in_tenths_of_a_percent(self, xlight, port):
        xlight.set_illumination_iris(80)
        xlight.set_emission_iris(45)

        assert port.positions["J"] == 800
        assert port.positions["V"] == 450

    def test_repeating_an_acknowledged_value_is_not_resent(self, xlight, port):
        xlight.set_illumination_iris(80)
        xlight.set_illumination_iris(80)

        assert port.commands_sent("J") == ["J800"]

    def test_failed_command_is_retried_on_the_next_call(self, xlight, port):
        port.unresponsive = True
        with pytest.raises(SerialDeviceError):
            xlight.set_illumination_iris(80)

        port.unresponsive = False
        xlight.set_illumination_iris(80)

        assert port.positions["J"] == 800

    def test_failed_emission_command_is_retried_on_the_next_call(self, xlight, port):
        port.unresponsive = True
        with pytest.raises(SerialDeviceError):
            xlight.set_emission_iris(45)

        port.unresponsive = False
        xlight.set_emission_iris(45)

        assert port.positions["V"] == 450

    def test_unrelated_reply_does_not_count_as_acknowledgement(self, xlight, port):
        # An emission wheel reply is still waiting to be read, and the iris command is ignored
        port.leave_unread_reply("B1")
        port.unresponsive = True

        with pytest.raises(SerialDeviceError):
            xlight.set_illumination_iris(80)

    def test_acknowledgement_behind_an_unread_reply_is_accepted(self, xlight, port):
        port.leave_unread_reply("B1")

        xlight.set_illumination_iris(80)

        assert port.positions["J"] == 800
        assert port.commands_sent("J") == ["J800"]


class TestDiskMotor:
    def test_running_motor_is_reported_as_on(self, xlight, port):
        port.positions["N"] = 1

        assert xlight.get_disk_motor_state() is True

    def test_stopped_motor_is_reported_as_off(self, xlight, port):
        port.positions["N"] = 0

        assert xlight.get_disk_motor_state() is False

    def test_starting_the_motor_reaches_hardware(self, xlight, port):
        xlight.set_disk_motor_state(True)

        assert port.positions["N"] == 1

    def test_stopping_the_motor_reaches_hardware(self, xlight, port):
        port.positions["N"] = 1

        xlight.set_disk_motor_state(False)

        assert port.positions["N"] == 0

    def test_unconfirmed_motor_command_raises(self, xlight, port):
        port.unresponsive = True

        with pytest.raises(SerialDeviceError):
            xlight.set_disk_motor_state(True)


class TestPositionCounts:
    def test_dichroic_position_beyond_the_wheel_is_rejected(self, xlight, port):
        # The wheel reports 3 positions
        with pytest.raises(ValueError):
            xlight.set_dichroic(4)

        assert port.commands_sent("C") == []

    def test_last_dichroic_position_is_accepted(self, xlight, port):
        assert xlight.set_dichroic(3) == 3
        assert port.positions["C"] == 3

    def test_five_position_dichroic_wheel_accepts_position_five(self, port):
        port.position_counts["C"] = 5
        xlight = XLight(SERIAL_NUMBER)

        assert xlight.set_dichroic(5) == 5

    def test_filter_slider_position_beyond_the_slider_is_rejected(self, xlight, port):
        # The slider reports 3 positions, numbered from 0
        with pytest.raises(ValueError):
            xlight.set_filter_slider(3)

        assert port.commands_sent("P") == []

    def test_last_filter_slider_position_is_accepted(self, xlight, port):
        xlight.set_filter_slider(2)

        assert port.positions["P"] == 2

    def test_v3_defaults_apply_when_the_device_does_not_report_counts(self, port):
        port.position_counts = {}
        xlight = XLight(SERIAL_NUMBER)

        assert xlight.dichroic_positions == 3
        assert xlight.filter_slider_positions == 3
