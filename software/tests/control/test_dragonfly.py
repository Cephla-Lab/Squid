"""Tests for the Andor Dragonfly serial driver and its simulation.

The driver is exercised against a fake serial port that emulates the Dragonfly AT protocol:
one CR-terminated command per line, replies ending in ':A' (ok) or ':N' (rejected).
"""

import inspect
from types import SimpleNamespace

import pytest

from control import serial_peripherals
from control.serial_peripherals import Dragonfly, Dragonfly_Simulation

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

    def __init__(self):
        self.is_open = True
        self.commands = []
        self.speed_setpoint = 0
        self.disk_speed = 0
        self.modality = "BF"
        # Wheel positions keyed by the position command's "<kind>,<port>" prefix
        self.positions = {"AT_PS_POS,1": 1, "AT_FW_POS,1": 1, "AT_FW_POS,2": 1, "AT_AP_POS,1": 1}
        self._unread = b""

    @property
    def in_waiting(self):
        return len(self._unread)

    def write(self, data):
        command = data.decode().rstrip("\r")
        self.commands.append(command)
        self._unread += (self._execute(command) + "\r").encode()

    def readline(self):
        line, self._unread = self._unread, b""
        return line

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


def test_simulated_camera_port_follows_the_dichroic():
    sim = Dragonfly_Simulation()
    assert sim.get_camera_port() == 1

    sim.set_port_selection_dichroic(4)

    assert sim.get_camera_port() == 2


def test_simulated_info_lists_match_the_hardware_wheels():
    sim = Dragonfly_Simulation()

    dichroics = sim.get_port_selection_dichroic_info()
    assert len(dichroics) == 4
    assert len(sim.get_emission_filter_info(1)) == 8
    assert len(sim.get_field_aperture_info()) == 10
    # get_camera_port() keys on these two names
    assert dichroics[0].endswith("100% Pass")
    assert dichroics[-1].endswith("100% Reflect")
