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


class FakeDragonflyPort:
    """Stands in for serial.Serial, with a Dragonfly on the other end of the cable."""

    def __init__(self):
        self.is_open = True
        self.commands = []
        self.speed_setpoint = 0
        self.disk_speed = 0
        self.modality = "BF"
        self.dichroic = 1
        self.filters = {1: 1, 2: 1}
        self.aperture = 1
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
        fixed = {
            "AT_SERIAL_CSU,?": "DFLY-1024:A",
            "AT_PRODUCT_CSU,?": "CR-DFLY-202-40:A",
            "AT_VER,?": "2.15:A",
            "AT_MS_MAX,?": "6000:A",
            "AT_SYSTEM,?": "CSU=2 EXT=0 FW=2 BF=1 AP=1 EOS=0 PB=0 SH=1:A",
            "AT_STANDBY,0": ":A",
            "AT_DC_SLCT,1": ":A",
            "AT_MS_RUN": ":A",
            "AT_MS_STOP": ":A",
            "AT_MS,?": f"{self.disk_speed}:A",
            "AT_MODALITY,?": f"{self.modality}:A",
            "AT_PS_POS,1,?": f"{self.dichroic}:A",
            "AT_AP_POS,1,?": f"{self.aperture}:A",
            "AT_FW_COMPO,1,?": "1,1:A",
            "AT_FW_COMPO,2,?": "2,1:A",
        }
        if command in fixed:
            if command == "AT_MS_RUN":
                self.disk_speed = self.speed_setpoint
            elif command == "AT_MS_STOP":
                self.disk_speed = 0
            return fixed[command]
        parts = command.split(",")
        if parts[0] == "AT_MS":
            self.speed_setpoint = int(parts[1])
            return ":A"
        if parts[0] == "AT_MODALITY":
            self.modality = parts[1]
            return ":A"
        if parts[0] == "AT_PS_POS":
            return self._move("dichroic", parts[2], len(DICHROICS))
        if parts[0] == "AT_AP_POS":
            return self._move("aperture", parts[2], len(APERTURES))
        if parts[0] == "AT_FW_POS":
            port = int(parts[1])
            if parts[2] == "?":
                return f"{self.filters[port]}:A"
            if 1 <= int(parts[2]) <= len(FILTERS):
                self.filters[port] = int(parts[2])
                return ":A"
            return ":N"
        if parts[0] in ("AT_PS_INFO", "AT_FW_INFO", "AT_AP_INFO"):
            names = {"AT_PS_INFO": DICHROICS, "AT_FW_INFO": FILTERS, "AT_AP_INFO": APERTURES}[parts[0]]
            index = int(parts[2])
            return f"{names[index - 1]}:A" if 1 <= index <= len(names) else ":N"
        return ":N"

    def _move(self, attribute, value, count):
        if 1 <= int(value) <= count:
            setattr(self, attribute, int(value))
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

    assert port.dichroic == 4
    assert dragonfly.get_camera_port() == 2


def test_emission_filter_info_covers_all_eight_positions(dragonfly):
    assert dragonfly.get_emission_filter_info(1) == [f"{i}:{name}" for i, name in enumerate(FILTERS, start=1)]


def test_emission_filter_position_round_trips(dragonfly, port):
    dragonfly.set_emission_filter(2, 5)

    assert port.filters[2] == 5
    assert dragonfly.get_emission_filter(2) == 5


def test_field_aperture_position_round_trips(dragonfly, port):
    dragonfly.set_field_aperture_wheel_position(7)

    assert port.aperture == 7
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


def test_simulated_info_lists_match_the_hardware_wheel_sizes():
    sim = Dragonfly_Simulation()

    assert len(sim.get_port_selection_dichroic_info()) == 4
    assert len(sim.get_emission_filter_info(1)) == 8
    assert len(sim.get_field_aperture_info()) == 10


def test_simulated_dichroic_names_carry_the_port_markers_the_driver_keys_on():
    names = Dragonfly_Simulation().get_port_selection_dichroic_info()

    assert names[0].endswith("100% Pass")
    assert names[-1].endswith("100% Reflect")
    assert not any(name.isdigit() for name in names)
