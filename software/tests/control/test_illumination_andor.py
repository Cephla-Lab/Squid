"""Tests for the Andor HLE laser engine driver (USB HID).

Only the HID boundary is faked: FakeHleUnit answers the HLE's one-byte-opcode protocol in 64-byte
reports, the way pyhidapi delivers them (report id 0 first), over a small model of a laser unit.
"""

import struct
import threading
import time

import pytest

import control.illumination_andor as andor
from control.illumination_andor import LaserCommands as Op
from control.lighting import IlluminationController, IntensityControlMode, LightSourceType, ShutterControlMode
from control.microcontroller import Microcontroller, SimSerial


class FakeHleUnit:
    """One HLE unit: lines (index -> nm), per-line transmission in 0.1 %, one shutter byte."""

    def __init__(self, serial, lines):
        self.serial_number = serial
        self.lines = dict(lines)
        self.transmission = {line: 0 for line in lines}
        self.shutter_mask = 0xFF  # power-on state is unknown, so the driver has to close them
        self.sent = []  # command bytes, without the report id
        self.pending = []
        self.opened = False
        self.closed = False
        self.fail_line_setup = False
        self.in_transaction = False
        self.interleaved = 0  # writes that arrived while another transaction was still unanswered

    def open(self, vid, pid, serial):
        assert serial == self.serial_number
        self.opened = True

    def set_nonblocking(self, flag):
        pass

    def close(self):
        self.closed = True

    def write(self, data):
        assert data[0] == 0x00, "HID report id"
        if self.in_transaction:
            self.interleaved += 1
        self.in_transaction = True
        time.sleep(0.0005)  # give another thread a chance to barge in
        command = bytes(data[1:])
        self.sent.append(command)
        self.pending.append([0x00] + self._execute(command))

    def read(self, size, timeout_ms=0):
        if self.pending:
            self.in_transaction = False
            frame = self.pending.pop(0)
            return frame + [0] * (size - len(frame))
        return []

    def _execute(self, command):
        op = command[0]
        if op == Op.SET_SHUTTER_STATE:
            self.shutter_mask = command[1]
            return [op]
        if op == Op.READ_SHUTTER_STATE:
            return [op, self.shutter_mask]
        if op == Op.SET_TRANSMISSION:
            line, value = command[1], struct.unpack(">H", command[2:4])[0]
            if line not in self.lines:
                return [Op.ERROR_RESPONSE]
            self.transmission[line] = value
            return [op]
        if op == Op.READ_TRANSMISSION:
            line = command[1]
            if line not in self.lines:
                return [Op.ERROR_RESPONSE]
            return [op] + list(struct.pack(">H", self.transmission[line]))
        if op == Op.GET_LASER_LINE_SETUP:
            if self.fail_line_setup:
                return [Op.ERROR_RESPONSE]
            pairs = []
            for index in range(8):
                pairs += list(struct.pack(">H", self.lines.get(index, 0) * 10))  # nm * 10, 0 = no line
            return [op] + pairs
        return [Op.ERROR_RESPONSE]

    def shutter_commands(self):
        return [c for c in self.sent if c[0] == Op.SET_SHUTTER_STATE]


class FakeHidDevice:
    """What hid.device() returns: bound to a unit by the serial number passed to open()."""

    def __init__(self, units_by_serial):
        self._units = units_by_serial
        self._unit = None

    def open(self, vid, pid, serial):
        self._unit = self._units[serial]
        self._unit.open(vid, pid, serial)

    def __getattr__(self, name):
        return getattr(self._unit, name)


@pytest.fixture
def hle(monkeypatch):
    """Install fake HLE units behind the hid module the driver uses."""

    def install(*units):
        by_serial = {u.serial_number: u for u in units}
        monkeypatch.setattr(andor.hid, "enumerate", lambda vid, pid: [{"serial_number": s} for s in by_serial])
        monkeypatch.setattr(andor.hid, "device", lambda: FakeHidDevice(by_serial))
        return units

    return install


def make_laser(active_blanking=False):
    laser = andor.AndorLaser(active_blanking=active_blanking)
    laser.initialize()
    return laser


FOUR_LINE_UNIT = {0: 405, 1: 488, 2: 561, 3: 639}


# ------------------------------------------------------------------ bring-up and mapping


def test_lines_map_to_the_shared_channel_groups(hle):
    (unit,) = hle(FakeHleUnit("HLE-1", FOUR_LINE_UNIT))

    laser = make_laser()

    sn = unit.serial_number
    assert laser.channel_mappings[405] == (sn, 0)
    assert laser.channel_mappings[470] == laser.channel_mappings[488] == (sn, 1)
    assert laser.channel_mappings[561] == (sn, 2)
    assert laser.channel_mappings[638] == laser.channel_mappings[640] == (sn, 3)  # 639 nm serves the 640 group
    assert laser.channel_mappings[730] is None


def test_initialize_closes_every_shutter(hle):
    (unit,) = hle(FakeHleUnit("HLE-1", FOUR_LINE_UNIT))

    make_laser()

    assert unit.opened
    assert unit.shutter_mask == 0


def test_no_units_found_raises(hle):
    hle()

    with pytest.raises(RuntimeError, match="No laser devices found"):
        make_laser()


def test_a_unit_that_fails_line_setup_is_released(hle):
    (unit,) = hle(FakeHleUnit("HLE-1", FOUR_LINE_UNIT))
    unit.fail_line_setup = True

    with pytest.raises(RuntimeError):
        make_laser()

    assert unit.closed


def test_two_units_serving_one_channel_group_is_an_error(hle):
    hle(FakeHleUnit("HLE-1", {0: 488}), FakeHleUnit("HLE-2", {0: 488}))

    with pytest.raises(RuntimeError, match="488"):
        make_laser()


# ------------------------------------------------------------------ channels the engine does not have


def test_unmapped_channels_are_harmless(hle):
    (unit,) = hle(FakeHleUnit("HLE-1", {1: 488}))
    laser = make_laser()
    sent_before = len(unit.sent)

    laser.set_intensity(None, 5.0)

    assert laser.get_intensity(None) == 0.0
    assert laser.get_shutter_state(None) is False
    assert len(unit.sent) == sent_before


def test_an_engine_without_every_group_still_builds_the_illumination_controller(hle):
    hle(FakeHleUnit("HLE-1", {1: 488}))
    laser = andor.AndorLaser()
    mcu = Microcontroller(SimSerial(), reset_and_initialize=False)

    controller = IlluminationController(
        mcu, IntensityControlMode.Software, ShutterControlMode.TTL, LightSourceType.AndorLaser, laser
    )

    assert controller.intensity_settings[405] == 0.0
    assert controller.is_on[405] is False
    assert controller.channel_mappings_software[488] == ("HLE-1", 1)
    controller.close()
    mcu.close()


# ------------------------------------------------------------------ intensity


def test_intensity_is_sent_as_tenths_of_a_percent(hle):
    (unit,) = hle(FakeHleUnit("HLE-1", FOUR_LINE_UNIT))
    laser = make_laser()

    laser.set_intensity(("HLE-1", 1), 12.5)

    assert unit.transmission[1] == 125
    assert laser.get_intensity(("HLE-1", 1)) == 12.5


def test_an_error_reply_raises(hle):
    (unit,) = hle(FakeHleUnit("HLE-1", {1: 488}))
    laser = make_laser()

    with pytest.raises(RuntimeError):
        laser.set_intensity(("HLE-1", 5), 10.0)  # no such line on the unit


def test_hid_transactions_are_serialised_across_threads(hle):
    (unit,) = hle(FakeHleUnit("HLE-1", FOUR_LINE_UNIT))
    laser = make_laser()

    def hammer():
        for _ in range(30):
            laser.get_intensity(("HLE-1", 1))

    threads = [threading.Thread(target=hammer) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert unit.interleaved == 0


# ------------------------------------------------------------------ active blanking


def test_without_active_blanking_set_intensity_leaves_the_shutters_alone(hle):
    (unit,) = hle(FakeHleUnit("HLE-1", FOUR_LINE_UNIT))
    laser = make_laser()
    shutter_commands_after_init = len(unit.shutter_commands())

    laser.set_intensity(("HLE-1", 1), 10.0)

    assert len(unit.shutter_commands()) == shutter_commands_after_init
    assert unit.shutter_mask == 0


def test_active_blanking_opens_only_the_active_line_across_units(hle):
    a, b = hle(FakeHleUnit("HLE-A", {0: 405, 1: 488}), FakeHleUnit("HLE-B", {0: 561}))
    laser = make_laser(active_blanking=True)

    laser.set_intensity(("HLE-A", 1), 10.0)
    assert (a.shutter_mask, b.shutter_mask) == (0b10, 0)

    laser.set_intensity(("HLE-B", 0), 10.0)
    assert (a.shutter_mask, b.shutter_mask) == (0, 0b1)


def test_active_blanking_does_not_resend_an_unchanged_shutter_byte(hle):
    (unit,) = hle(FakeHleUnit("HLE-1", FOUR_LINE_UNIT))
    laser = make_laser(active_blanking=True)
    laser.set_intensity(("HLE-1", 1), 10.0)
    shutter_commands = len(unit.shutter_commands())

    laser.set_intensity(("HLE-1", 1), 20.0)  # same line again

    assert len(unit.shutter_commands()) == shutter_commands


# ------------------------------------------------------------------ shutdown


def test_shut_down_zeroes_every_line_closes_the_shutters_and_releases_the_units(hle):
    a, b = hle(FakeHleUnit("HLE-A", {0: 405, 1: 488}), FakeHleUnit("HLE-B", {0: 561}))
    laser = make_laser(active_blanking=True)
    laser.set_intensity(("HLE-A", 1), 30.0)
    laser.set_intensity(("HLE-B", 0), 40.0)

    laser.shut_down()

    assert all(value == 0 for unit in (a, b) for value in unit.transmission.values())
    assert (a.shutter_mask, b.shutter_mask) == (0, 0)
    assert a.closed and b.closed


def test_shut_down_twice_is_harmless(hle):
    hle(FakeHleUnit("HLE-1", FOUR_LINE_UNIT))
    laser = make_laser()

    laser.shut_down()
    laser.shut_down()


def test_shut_down_releases_the_other_units_when_one_stops_answering(hle):
    a, b = hle(FakeHleUnit("HLE-A", {0: 405}), FakeHleUnit("HLE-B", {0: 561}))
    laser = make_laser()
    a.write = lambda data: (_ for _ in ()).throw(OSError("device unplugged"))

    laser.shut_down()

    assert b.transmission[0] == 0 and b.shutter_mask == 0
    assert a.closed and b.closed
