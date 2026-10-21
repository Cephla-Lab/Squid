"""Tests for the Vortran Stradus VersaLase USB HID driver."""

import pytest

import control.illumination_versalase as versalase
from control.lighting import ShutterControlMode


class FakeStradusHead:
    """Stands in for one hid.device: the 0xA0-0xA3 report handshake over a small laser model."""

    def __init__(self, li, maxp, delay=False, stale_reply=None):
        self.li = li
        self.state = {"MAXP": maxp, "LE": 0, "PUL": 0, "PP": 10.0, "LP": 0.0, "LPS": 10.0, "DELAY": int(delay)}
        self.reply = stale_reply  # a head keeps replaying its last response until acknowledged
        self.pending = []
        self.sent = []
        self.closed = False
        self.le_polls_until_on = None

    def open_path(self, path):
        pass

    def set_nonblocking(self, flag):
        pass

    def close(self):
        self.closed = True

    def write(self, report):
        opcode = report[1]
        if opcode == versalase.OP_SEND:
            command = bytes(b for b in report[2:] if b != versalase.PAD).decode().strip()
            self.sent.append(command)
            self.reply = self._execute(command)
        elif opcode == versalase.OP_STATUS:
            self.pending.append([versalase.OP_STATUS, versalase.STATUS_READY])
        elif opcode == versalase.OP_RESPONSE:
            self.pending.append([versalase.OP_RESPONSE] + list((self.reply + "\nStradus:0>").encode()))
        elif opcode == versalase.OP_ACK:
            self.pending.clear()

    def read(self, size, timeout_ms):
        if self.pending:
            frame = self.pending.pop(0)
            return frame + [0] * (size - len(frame))
        return []

    def _execute(self, command):
        if command == "?LI":
            return f"?LI={self.li}"
        if command == "?FC":
            return "?FC=0"
        if command.startswith("?"):
            name = command[1:]
            if name == "LE" and self.le_polls_until_on is not None:
                self.le_polls_until_on -= 1
                if self.le_polls_until_on <= 0:
                    self.state["LE"], self.le_polls_until_on = 1, None
            return f"{command}={self.state[name]}"
        name, value = command.split("=")
        if name == "LE" and value == "1" and self.state["DELAY"]:
            self.le_polls_until_on = 2  # emission comes on after the delay, not on the command
            return "LE=0"
        if name in ("PP", "LP") and not self.state["LE"]:
            return f"{name}=0.00"  # power is only written while emission is on
        self.state[name] = float(value) if name in ("PP", "LP") else int(value)
        return f"{name}={value}"


@pytest.fixture
def heads(monkeypatch):
    fakes = {
        b"p405": FakeStradusHead("VL07241K15, 11070, 405nm, 100mW, C", 100.8),
        b"p488": FakeStradusHead("0106259H19, 11075, 488nm, 150mW, C", 150.0, delay=True, stale_reply="?OP2=1*2"),
        b"p561": FakeStradusHead("VL07241717, 11078, 561nm, 50mW, C", 50.7),
        b"p639": FakeStradusHead("VL07241K14, 11951, 639nm, 160mW, C", 160.0, delay=True),
    }
    opened = iter(fakes.values())
    monkeypatch.setattr(versalase.hid, "enumerate", lambda vid, pid: [{"path": p} for p in fakes])
    monkeypatch.setattr(versalase.hid, "device", lambda: next(opened))
    monkeypatch.setattr(versalase.time, "sleep", lambda s: None)
    return fakes


def test_heads_map_to_squid_channels_by_wavelength(heads):
    laser = versalase.VersaLase()
    assert laser.initialize()

    def wavelength_of(channel):
        return laser.channel_mappings[channel].wavelength if laser.channel_mappings[channel] else None

    assert wavelength_of(405) == 405
    assert wavelength_of(470) == wavelength_of(488) == 488
    assert wavelength_of(545) == wavelength_of(561) == 561
    assert wavelength_of(638) == wavelength_of(640) == 639
    assert wavelength_of(730) is None
    assert laser.channel_mappings[640].serial_number == "VL07241K14"


def test_ttl_mode_enables_modulation_before_emission(heads):
    laser = versalase.VersaLase(shutter_mode=ShutterControlMode.TTL)
    laser.initialize()

    for fake in heads.values():
        assert fake.state["PUL"] == 1 and fake.state["LE"] == 1
        assert fake.sent.index("PUL=1") < fake.sent.index("LE=1")
    # The 488 head has the 5 s emission delay: initialize waits for ?LE to report it on.
    assert heads[b"p488"].sent.count("?LE") >= 2


def test_ttl_intensity_sets_peak_power_as_percent_of_max(heads):
    laser = versalase.VersaLase()
    laser.initialize()
    head = laser.channel_mappings[561]

    laser.set_intensity(head, 50)

    assert heads[b"p561"].sent[-1] == "PP=25.4"
    assert laser.get_intensity(head) == pytest.approx(25.4 / 50.7 * 100)
    # In TTL mode the microcontroller decides whether light is out.
    assert laser.get_shutter_state(head) is False


def test_intensity_is_clamped(heads):
    laser = versalase.VersaLase()
    laser.initialize()
    laser.set_intensity(laser.channel_mappings[405], 150)
    assert heads[b"p405"].sent[-1] == "PP=100.8"


def test_stale_reply_from_a_previous_session_is_ignored(heads):
    laser = versalase.VersaLase()
    laser.initialize()
    assert laser.channel_mappings[488].serial_number == "0106259H19"


def test_software_mode_applies_power_when_shutter_opens(heads):
    laser = versalase.VersaLase(shutter_mode=ShutterControlMode.Software)
    laser.initialize()
    head = laser.channel_mappings[405]
    fake = heads[b"p405"]
    assert fake.state["PUL"] == 0 and fake.state["LE"] == 0

    laser.set_intensity(head, 20)  # emission off: LP would be rejected, so it is held back
    assert not any(c.startswith("LP=") for c in fake.sent)
    assert laser.get_intensity(head) == pytest.approx(20)

    laser.set_shutter_state(head, True)
    assert fake.state["LE"] == 1 and fake.sent[-1] == "LP=20.2"
    assert laser.get_shutter_state(head) is True

    laser.set_shutter_state(head, False)
    assert fake.state["LE"] == 0


def test_no_reply_raises(heads):
    laser = versalase.VersaLase(timeout_s=0.05)
    laser.initialize()
    head = laser.channel_mappings[405]
    heads[b"p405"].write = lambda report: None  # head stops answering

    with pytest.raises(versalase.VortranError):
        head.transact("?LE")


def test_interlock_fault_stops_initialize(heads):
    heads[b"p561"]._execute = lambda command: (
        "?FC=16" if command == "?FC" else FakeStradusHead._execute(heads[b"p561"], command)
    )
    heads[b"p561"].state["FD"] = "INTERLOCK OPEN"

    with pytest.raises(RuntimeError, match="fault code 16"):
        versalase.VersaLase().initialize()


def test_shut_down_turns_emission_off_and_closes(heads):
    laser = versalase.VersaLase()
    laser.initialize()
    laser.shut_down()
    for fake in heads.values():
        assert fake.state["LE"] == 0 and fake.closed
