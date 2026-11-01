"""Tests for the Andor Dragonfly serial driver and its simulation.

The driver is exercised against a fake serial port that emulates the Dragonfly AT protocol:
one CR-terminated command per line, replies ending in ':A' (ok) or ':N' (rejected).
"""

import inspect
import time
from types import SimpleNamespace

import pytest

from control import serial_peripherals
from control.serial_peripherals import Dragonfly, Dragonfly_Simulation

REAL_SLEEP = time.sleep  # the port fixture replaces time.sleep
SERIAL_NUMBER = "DRAGONFLY-TEST"

DICHROICS = ["100% Pass", "Dichroic 1", "Dichroic 2", "100% Reflect"]
FILTERS = ["445/45", "525/50", "600/50", "700/75", "Empty 5", "Empty 6", "Empty 7", "Empty 8"]
APERTURES = [f"Aperture {i}" for i in range(1, 11)]


INFO = {"AT_PS_INFO": DICHROICS, "AT_FW_INFO": FILTERS, "AT_AP_INFO": APERTURES}
STATIC_REPLIES = {
    "AT_SERIAL_CSU,?": "DFLY-1024:A",
    "AT_PRODUCT_CSU,?": "CR-DFLY-202-40:A",
    "AT_VER,?": "2.15:A",
    "AT_MS_MAX,?": "6000:A",
    "AT_SYSTEM,?": "CSU=2 EXT=0 FW=2 BF=1 AP=1 EOS=0 PB=0 SH=1:A",
    "AT_STANDBY,0": ":A",
    "AT_DC_SLCT,1": ":A",
    "AT_FW_COMPO,1,?": "1,1:A",
    "AT_FW_COMPO,2,?": "2,1:A",
}


class FakeDragonflyPort:
    """Stands in for serial.Serial, with a Dragonfly on the other end of the cable."""

    def __init__(self, timeout=0.1):
        self.is_open = True
        self._timeout = timeout
        self.timeout_changes = 0  # setting timeout reconfigures a real port (and garbled commands on a Dragonfly)
        self.commands = []
        self.unanswered = set()  # commands the unit never replies to
        self.drop_once = set()  # commands whose first reply is lost
        self.reject_once = set()  # commands the unit rejects (30005:N, garbled) the first time
        self.late = set()  # commands whose reply only arrives during the next exchange, ahead of that one's reply
        self.cut_off = {}  # command -> the part of a reply that arrives, without its CR
        self.chunk = None  # deliver at most this many bytes per read, as a slow reply would arrive
        self._withheld = b""
        self.speed_setpoint = 0
        self.disk_speed = 0
        self.modality = "BF"
        # Wheel positions keyed by the position command's "<kind>,<port>" prefix
        self.positions = {"AT_PS_POS,1": 1, "AT_FW_POS,1": 1, "AT_FW_POS,2": 1, "AT_AP_POS,1": 1}
        self._unread = b""

    @property
    def in_waiting(self):
        return len(self._unread)

    @property
    def timeout(self):
        return self._timeout

    @timeout.setter
    def timeout(self, value):
        self.timeout_changes += 1
        self._timeout = value

    def write(self, data):
        command = data.decode().rstrip("\r")
        self.commands.append(command)
        self._unread += self._withheld  # a late reply to the previous command lands now, ahead of this one's
        self._withheld = b""
        if command in self.reject_once:
            self.reject_once.discard(command)
            self._unread += b"30005:N\r"
        elif command in self.drop_once:
            self.drop_once.discard(command)
        elif command in self.late:
            self.late.discard(command)
            self._withheld = (self._execute(command) + "\r").encode()
        elif command in self.cut_off:
            self._execute(command)
            self._unread += self.cut_off.pop(command)
        elif command not in self.unanswered:
            self._unread += (self._execute(command) + "\r").encode()

    def readline(self):
        line, self._unread = self._unread, b""
        return line

    def read_until(self, expected=b"\n"):
        # Up to and including the terminator; with nothing more coming pyserial returns what it has at its timeout
        idx = self._unread.find(expected)
        end = idx + len(expected) if idx >= 0 else len(self._unread)
        if self.chunk:
            end = min(end, self.chunk)
        line, self._unread = self._unread[:end], self._unread[end:]
        return line

    def reset_input_buffer(self):
        self._unread = b""

    def close(self):
        self.is_open = False

    def _execute(self, command):
        if command in STATIC_REPLIES:
            return STATIC_REPLIES[command]
        if command == "AT_MS_RUN":
            self.disk_speed = self.speed_setpoint
            return ":A"
        if command == "AT_MS_STOP":
            self.disk_speed = 0
            return ":A"
        if command == "AT_MS,?":
            return f"{self.disk_speed}:A"
        if command == "AT_MODALITY,?":
            return f"{self.modality}:A"
        kind, *args = command.split(",")
        if kind == "AT_MS":
            self.speed_setpoint = int(args[0])
            return ":A"
        if kind == "AT_MODALITY":
            self.modality = args[0]
            return ":A"
        if kind in INFO:  # AT_xx_INFO,<port>,<index>,?
            names, index = INFO[kind], int(args[1])
            return f"{names[index - 1]}:A" if 1 <= index <= len(names) else ":N"
        wheel = f"{kind},{args[0]}"  # AT_xx_POS,<port>,<position or ?>
        if wheel in self.positions:
            if args[1] == "?":
                return f"{self.positions[wheel]}:A"
            count = len({"AT_PS_POS": DICHROICS, "AT_FW_POS": FILTERS, "AT_AP_POS": APERTURES}[kind])
            if 1 <= int(args[1]) <= count:
                self.positions[wheel] = int(args[1])
                return ":A"
        return ":N"


@pytest.fixture
def port(monkeypatch):
    fake_port = FakeDragonflyPort()
    monkeypatch.setattr(
        serial_peripherals.list_ports,
        "comports",
        lambda: [SimpleNamespace(serial_number=SERIAL_NUMBER, device="/dev/fake-dragonfly", vid=None, pid=None)],
    )
    monkeypatch.setattr(serial_peripherals.serial, "Serial", lambda *args, **kwargs: fake_port)
    # The driver sleeps for seconds while the unit leaves standby and switches modality
    monkeypatch.setattr(serial_peripherals.time, "sleep", lambda seconds: None)
    return fake_port


@pytest.fixture
def dragonfly(port):
    return Dragonfly(SERIAL_NUMBER)


def test_bring_up_leaves_standby_and_arms_the_disk_at_full_speed(dragonfly, port):
    assert "AT_STANDBY,0" in port.commands
    assert port.speed_setpoint == 6000


def test_camera_port_follows_the_port_selection_dichroic(dragonfly, port):
    assert dragonfly.get_camera_port() == 1

    dragonfly.set_port_selection_dichroic(4)

    assert port.positions["AT_PS_POS,1"] == 4
    assert dragonfly.get_camera_port() == 2


def test_emission_filter_info_covers_all_eight_positions(dragonfly):
    assert dragonfly.get_emission_filter_info(1) == [f"{i}:{name}" for i, name in enumerate(FILTERS, start=1)]


def test_emission_filter_position_round_trips(dragonfly, port):
    dragonfly.set_emission_filter(2, 5)

    assert port.positions["AT_FW_POS,2"] == 5
    assert dragonfly.get_emission_filter(2) == 5


def test_field_aperture_position_round_trips(dragonfly, port):
    dragonfly.set_field_aperture_wheel_position(7)

    assert port.positions["AT_AP_POS,1"] == 7
    assert dragonfly.get_field_aperture_wheel_position() == 7


def test_disk_motor_state_is_read_back_from_the_speed(dragonfly):
    assert dragonfly.get_disk_motor_state() is False

    dragonfly.set_disk_motor_state(True)
    assert dragonfly.get_disk_motor_state() is True

    dragonfly.set_disk_motor_state(False)
    assert dragonfly.get_disk_motor_state() is False


def test_close_releases_the_serial_port(dragonfly, port):
    dragonfly.close()

    assert not port.is_open


# ------------------------------------------------------------------ reading replies


def test_a_command_waits_for_its_reply_not_a_fixed_delay(dragonfly, port, monkeypatch):
    monkeypatch.setattr(serial_peripherals.time, "sleep", REAL_SLEEP)  # the fixture's no-op would hide a fixed wait
    started = time.perf_counter()

    dragonfly.set_emission_filter(1, 3)

    assert time.perf_counter() - started < 0.05  # the old driver slept 0.1 s before reading
    assert port.positions["AT_FW_POS,1"] == 3


def test_a_reply_arriving_in_pieces_is_put_together(dragonfly, port):
    port.chunk = 2

    dragonfly.set_emission_filter(1, 5)
    assert dragonfly.get_emission_filter(1) == 5
    assert dragonfly.get_port_selection_dichroic_info()[-1] == "100% Reflect"


def test_a_move_is_sent_once_and_a_missing_reply_raises(dragonfly, port, monkeypatch):
    monkeypatch.setattr(Dragonfly, "REPLY_TIMEOUT_S", 0.05)
    port.unanswered.add("AT_FW_POS,1,4")

    with pytest.raises(serial_peripherals.SerialDeviceError, match="no reply"):
        dragonfly.set_emission_filter(1, 4)

    assert port.commands.count("AT_FW_POS,1,4") == 1  # not re-sent while the wheel may still be moving


def test_a_stale_reply_is_not_taken_as_the_answer(dragonfly, port):
    port._unread = b"7:A\r"  # e.g. a late reply to an earlier command

    assert dragonfly.get_emission_filter(1) == 1


def test_a_rejected_command_raises(dragonfly):
    with pytest.raises(serial_peripherals.SerialDeviceError, match="failed"):
        dragonfly.set_emission_filter(1, 9)


def test_a_query_rejected_once_is_asked_again(dragonfly, port):
    port.reject_once.add("AT_FW_POS,1,?")

    assert dragonfly.get_emission_filter(1) == 1
    assert port.commands.count("AT_FW_POS,1,?") == 2


def test_a_rejected_move_is_not_sent_again(dragonfly, port):
    port.reject_once.add("AT_FW_POS,1,3")

    with pytest.raises(serial_peripherals.SerialDeviceError, match="failed"):
        dragonfly.set_emission_filter(1, 3)
    assert port.commands.count("AT_FW_POS,1,3") == 1


def test_a_query_rejected_for_good_is_asked_once(dragonfly, port):
    with pytest.raises(serial_peripherals.SerialDeviceError, match="failed"):
        dragonfly.get_emission_filter(3)  # no wheel on port 3: ':N', not the garbled-command 30005
    assert port.commands.count("AT_FW_POS,3,?") == 1


def test_an_unanswered_query_is_asked_again(dragonfly, port, monkeypatch):
    monkeypatch.setattr(Dragonfly, "QUERY_TIMEOUT_S", 0.05)
    port.drop_once.add("AT_FW_POS,1,?")

    assert dragonfly.get_emission_filter(1) == 1
    assert port.commands.count("AT_FW_POS,1,?") == 2


def test_a_late_reply_is_not_taken_as_the_next_commands_answer(dragonfly, port, monkeypatch):
    monkeypatch.setattr(Dragonfly, "REPLY_TIMEOUT_S", 0.05)
    port.late.add("AT_FW_POS,1,7")  # its ':A' only comes once the next command has been sent

    with pytest.raises(serial_peripherals.SerialDeviceError, match="no reply"):
        dragonfly.set_emission_filter(1, 7)
    dragonfly.set_emission_filter(1, 2)

    # The late ':A' was dropped by a resync query, so the second move waited for its own reply
    assert port.commands[-2:] == ["AT_SERIAL_CSU,?", "AT_FW_POS,1,2"]
    assert port._unread == b""
    assert port.positions["AT_FW_POS,1"] == 2


def test_a_reply_cut_off_before_its_cr_counts_as_no_reply(dragonfly, port, monkeypatch):
    monkeypatch.setattr(Dragonfly, "REPLY_TIMEOUT_S", 0.05)
    port.cut_off["AT_FW_POS,1,4"] = b":A"  # the CR never arrives

    with pytest.raises(serial_peripherals.SerialDeviceError, match="no reply"):
        dragonfly.set_emission_filter(1, 4)
    assert dragonfly.get_emission_filter(1) == 4  # the next exchange resyncs first and is answered normally


def test_slow_commands_get_long_timeouts(dragonfly, monkeypatch):
    timeouts = {}
    real_exchange = Dragonfly._exchange

    def spy(self, command, timeout_s):
        timeouts[command] = timeout_s
        return real_exchange(self, command, timeout_s)

    monkeypatch.setattr(Dragonfly, "_exchange", spy)
    dragonfly.set_disk_motor_state(True)
    dragonfly.set_disk_speed(4000)
    dragonfly.set_modality("CONFOCAL")
    dragonfly.set_emission_filter(1, 2)
    dragonfly.get_emission_filter(1)

    assert timeouts["AT_MS_RUN"] >= 10  # the disk takes ~6 s to come up to speed
    assert timeouts["AT_MS,4000"] >= 10
    assert timeouts["AT_MODALITY,CONFOCAL"] >= 10
    assert timeouts["AT_FW_POS,1,2"] == Dragonfly.REPLY_TIMEOUT_S
    assert timeouts["AT_FW_POS,1,?"] == Dragonfly.QUERY_TIMEOUT_S


def test_a_modality_change_and_a_disk_stop_keep_their_minimum_time(dragonfly, monkeypatch):
    sleeps = []
    monkeypatch.setattr(serial_peripherals.time, "sleep", lambda seconds: sleeps.append(seconds))

    dragonfly.set_modality("CONFOCAL")
    dragonfly.set_disk_motor_state(False)
    dragonfly.set_emission_filter(1, 2)

    assert len(sleeps) == 2
    assert sleeps[0] == pytest.approx(Dragonfly.MODALITY_SETTLE_S, abs=0.05)
    assert sleeps[1] == pytest.approx(Dragonfly.DISK_STOP_SETTLE_S, abs=0.05)


def test_a_port_opened_without_a_read_timeout_gets_one(monkeypatch):
    blocking_port = FakeDragonflyPort(timeout=None)
    monkeypatch.setattr(
        serial_peripherals.list_ports,
        "comports",
        lambda: [SimpleNamespace(serial_number=SERIAL_NUMBER, device="/dev/fake-dragonfly", vid=None, pid=None)],
    )
    monkeypatch.setattr(serial_peripherals.serial, "Serial", lambda *args, **kwargs: blocking_port)

    Dragonfly(SERIAL_NUMBER)

    assert blocking_port.timeout == 0.1  # set once, at bring-up: the reply deadlines need reads to return


def test_the_port_is_never_reconfigured_around_a_command(dragonfly, port):
    dragonfly.set_emission_filter(1, 2)
    dragonfly.get_emission_filter(1)
    dragonfly.set_disk_motor_state(True)

    assert port.timeout_changes == 0


def test_bring_up_checks_the_ftdi_latency_timer(port, monkeypatch):
    checks = []
    monkeypatch.setattr(
        serial_peripherals.squid.ftdi, "warn_if_latency_timer_high", lambda p, name, log: checks.append((p, name))
    )

    Dragonfly(SERIAL_NUMBER)

    assert checks == [("/dev/fake-dragonfly", "Dragonfly")]


# ------------------------------------------------------------------ simulation


def _public_methods(cls):
    return {name for name, member in inspect.getmembers(cls, inspect.isfunction) if not name.startswith("_")}


def test_simulation_offers_every_hardware_method_with_the_same_signature():
    missing = _public_methods(Dragonfly) - _public_methods(Dragonfly_Simulation)
    assert not missing, f"Dragonfly_Simulation lacks: {sorted(missing)}"

    for name in _public_methods(Dragonfly):
        real = list(inspect.signature(getattr(Dragonfly, name)).parameters)
        simulated = list(inspect.signature(getattr(Dragonfly_Simulation, name)).parameters)
        assert simulated == real, f"{name}: simulation takes {simulated}, hardware takes {real}"


def test_simulated_field_aperture_round_trips():
    sim = Dragonfly_Simulation()

    sim.set_field_aperture_wheel_position(5)

    assert sim.get_field_aperture_wheel_position() == 5


@pytest.mark.parametrize("position", [2, 3])
def test_an_intermediate_dichroic_does_not_identify_a_camera_port(dragonfly, position):
    dragonfly.set_port_selection_dichroic(position)

    with pytest.raises(ValueError, match="Unknown camera port"):
        dragonfly.get_camera_port()


@pytest.mark.parametrize("position, expected", [(1, 1), (4, 2)])
def test_simulated_camera_port_follows_the_dichroic(position, expected):
    sim = Dragonfly_Simulation()

    sim.set_port_selection_dichroic(position)

    assert sim.get_camera_port() == expected


@pytest.mark.parametrize("position", [2, 3])
def test_simulated_intermediate_dichroic_does_not_identify_a_camera_port(position):
    sim = Dragonfly_Simulation()

    sim.set_port_selection_dichroic(position)

    with pytest.raises(ValueError, match="Unknown camera port"):
        sim.get_camera_port()


def test_simulated_info_lists_match_the_hardware_wheels():
    sim = Dragonfly_Simulation()

    dichroics = sim.get_port_selection_dichroic_info()
    assert len(dichroics) == 4
    assert len(sim.get_emission_filter_info(1)) == 8
    assert len(sim.get_field_aperture_info()) == 10
    # get_camera_port() keys on these two names
    assert dichroics[0].endswith("100% Pass")
    assert dichroics[-1].endswith("100% Reflect")
