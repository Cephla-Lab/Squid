"""Tests for the Vortran Stradus VersaLase USB HID driver."""

import os
import threading
import time

import psutil
import pytest

import control.illumination_versalase as versalase
import control.versalase_guard as guard
from control.lighting import ShutterControlMode


class FakeStradusHead:
    """Stands in for one hid.device: the 0xA0-0xA3 report handshake over a small laser model."""

    def __init__(self, li, maxp, delay=False, stale_reply=None):
        self.li = li
        self.state = {"MAXP": maxp, "LE": 0, "PUL": 0, "PP": 10.0, "LP": 0.0, "LPS": 10.0, "DELAY": int(delay)}
        self.state.update({"FC": 0, "FD": "LASER EMISSION ACTIVE", "IL": 1, "BPT": 27, "OBT": 25.1})
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
        if name == "LP":
            self.state["LPS"] = float(value)  # the setting ?LPS reports
        return f"{name}={value}"


class FakeGuard:
    """Stands in for the crash guard's Popen, so no test starts a real guard process."""

    def __init__(self, squid_pid):
        self.squid_pid = squid_pid
        self.pid = 4321
        self.terminated = False

    def terminate(self):
        self.terminated = True


@pytest.fixture
def guards(monkeypatch):
    started = []
    monkeypatch.setattr(guard, "start", lambda pid: started.append(FakeGuard(pid)) or started[-1])
    return started


@pytest.fixture
def heads(monkeypatch, guards):
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


def test_smb_ttl_mode_enables_modulation_before_emission(heads):
    laser = versalase.VersaLase(shutter_mode=ShutterControlMode.TTL, ttl_input=versalase.TtlInput.DIGITAL_MODULATION)
    laser.initialize()

    for fake in heads.values():
        assert fake.state["PUL"] == 1 and fake.state["LE"] == 1
        assert fake.sent.index("PUL=1") < fake.sent.index("LE=1")
    # The 488 head has the 5 s emission delay: initialize waits for ?LE to report it on.
    assert heads[b"p488"].sent.count("?LE") >= 2


def test_smb_ttl_intensity_sets_peak_power_as_percent_of_max(heads):
    laser = versalase.VersaLase(ttl_input=versalase.TtlInput.DIGITAL_MODULATION)
    laser.initialize()
    head = laser.channel_mappings[561]

    laser.set_intensity(head, 50)

    assert heads[b"p561"].sent[-1] == "PP=25.4"
    assert laser.get_intensity(head) == pytest.approx(25.4 / 50.7 * 100)
    # In TTL mode the microcontroller decides whether light is out.
    assert laser.get_shutter_state(head) is False


def test_intensity_is_clamped(heads):
    laser = versalase.VersaLase(ttl_input=versalase.TtlInput.DIGITAL_MODULATION)
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
    heads[b"p561"].state.update({"FC": 16, "FD": "INTERLOCK OPEN"})

    laser = versalase.VersaLase()
    with pytest.raises(RuntimeError, match="fault code 16"):
        laser.initialize()
    assert heads[b"p405"].closed and heads[b"p488"].closed  # opened before the faulty head, closed again
    assert laser.heads == []


def test_shut_down_turns_emission_off_and_closes(heads):
    laser = versalase.VersaLase()
    laser.initialize()
    laser.shut_down()
    for fake in heads.values():
        assert fake.state["LE"] == 0 and fake.closed


def test_get_status_reports_every_head_in_wavelength_order(heads):
    laser = versalase.VersaLase(ttl_input=versalase.TtlInput.DIGITAL_MODULATION)
    laser.initialize()
    laser.set_intensity(laser.channel_mappings[488], 40)
    heads[b"p639"].state.update({"FC": 2, "FD": "WARMUP"})

    statuses = laser.get_status()

    assert [s.wavelength for s in statuses] == [405, 488, 561, 639]
    s488 = statuses[1]
    assert s488.emission and s488.modulation and s488.interlock_closed
    assert s488.set_power_mw == pytest.approx(60.0)  # PP in TTL mode
    assert s488.base_plate_temp_c == 27 and s488.optical_block_temp_c == 25.1
    assert s488.fault_description == "" and not s488.faulted
    assert statuses[3].fault_description == "WARMUP" and not statuses[3].faulted  # warm-up is a state, not a fault
    assert "?FD" not in heads[b"p405"].sent  # only asked for when there is something to describe


def test_laser_enable_ttl_runs_cw_with_emission_on(heads):
    laser = versalase.VersaLase()  # TTL through Laser Enable is the default
    laser.initialize()

    for fake in heads.values():
        assert fake.state["PUL"] == 0 and fake.state["LE"] == 1
        assert "PUL=1" not in fake.sent
    head = laser.channel_mappings[488]
    laser.set_intensity(head, 40)
    assert heads[b"p488"].sent[-1] == "LP=60.0"
    assert laser.get_intensity(head) == pytest.approx(40)
    assert laser.get_shutter_state(head) is False  # the Laser Enable line decides whether light is out
    assert not laser.get_status()[1].modulation and laser.get_status()[1].set_power_mw == pytest.approx(60.0)


def test_head_without_smb_modulation_points_at_laser_enable(heads):
    original = heads[b"p405"]._execute
    heads[b"p405"]._execute = lambda command: "PUL=0" if command == "PUL=1" else original(command)

    with pytest.raises(RuntimeError, match="LASER_ENABLE"):
        versalase.VersaLase(ttl_input=versalase.TtlInput.DIGITAL_MODULATION).initialize()
    assert heads[b"p405"].state["LE"] == 0  # emission is never enabled on a head left in CW


def test_set_commands_wait_longer_than_queries(heads, monkeypatch):
    """A head replies to LP/PP only after regulating (~1.2 s on AION), longer than a query may take."""
    laser = versalase.VersaLase(timeout_s=0.05)
    laser.initialize()
    head = laser.channel_mappings[405]
    fake = heads[b"p405"]
    clock = {"now": 0.0}
    monkeypatch.setattr(versalase.time, "monotonic", lambda: clock["now"])
    real_read = fake.read

    def slow_read(size, timeout_ms):
        clock["now"] += 0.01  # every read takes 10 ms
        if fake.sent[-1].startswith("LP=") and clock["now"] < 1.2:
            return []  # still regulating
        return real_read(size, timeout_ms)

    fake.read = slow_read
    laser.set_intensity(head, 20)  # would time out after 0.05 s if commands used the query timeout
    assert fake.state["LP"] == pytest.approx(20.2)

    clock["now"] = 0.0
    fake.read = lambda size, timeout_ms: (clock.__setitem__("now", clock["now"] + 0.01), [])[1]
    with pytest.raises(versalase.VortranError):
        head.transact("?LE")
    assert clock["now"] < 0.2  # a query still gives up after its own short timeout


def test_failed_initialize_turns_earlier_heads_off_and_closes_them(heads):
    """The fourth head refusing PUL=1 must not leave the first three emitting or open."""
    original = heads[b"p639"]._execute
    heads[b"p639"]._execute = lambda command: "PUL=0" if command == "PUL=1" else original(command)
    laser = versalase.VersaLase(ttl_input=versalase.TtlInput.DIGITAL_MODULATION)

    with pytest.raises(RuntimeError, match="refused digital modulation"):
        laser.initialize()

    for name in (b"p405", b"p488", b"p561"):
        assert "LE=1" in heads[name].sent  # these had emission enabled before the failure
    for fake in heads.values():
        assert fake.state["LE"] == 0 and fake.closed
    assert laser.heads == []
    assert all(head is None for head in laser.channel_mappings.values())


def test_crash_guard_starts_before_any_head_is_touched(heads, guards, monkeypatch):
    started_before_open = []
    real_open = versalase.VersaLase._open_heads
    monkeypatch.setattr(
        versalase.VersaLase, "_open_heads", lambda self: (started_before_open.append(len(guards)), real_open(self))
    )
    laser = versalase.VersaLase()
    laser.initialize()

    assert started_before_open == [1]
    assert [g.squid_pid for g in guards] == [os.getpid()]
    assert not guards[0].terminated


def test_clean_shut_down_turns_heads_off_then_stops_the_guard(heads, guards):
    laser = versalase.VersaLase()
    laser.initialize()
    le_when_stopped = []
    guards[0].terminate = lambda: le_when_stopped.append([f.state["LE"] for f in heads.values()])

    laser.shut_down()

    assert le_when_stopped == [[0, 0, 0, 0]]  # stopped only once every head was off


def test_failed_initialize_also_stops_the_guard(heads, guards):
    heads[b"p561"].state.update({"FC": 16, "FD": "INTERLOCK OPEN"})
    with pytest.raises(RuntimeError):
        versalase.VersaLase().initialize()
    assert guards[0].terminated


def test_no_crash_guard_when_disabled(heads, guards):
    versalase.VersaLase(crash_guard=False).initialize()
    assert guards == []


def test_a_guard_that_cannot_start_does_not_block_initialize(heads, monkeypatch):
    def fail(pid):
        raise OSError("no python")

    monkeypatch.setattr(guard, "start", fail)
    laser = versalase.VersaLase()
    assert laser.initialize()
    laser.shut_down()


class ReopenableDevice:
    """hid.device() for the guard, which opens a fresh device per attempt: open_path() picks the fake head."""

    def __init__(self, fakes):
        self._fakes = fakes
        self._fake = None

    def open_path(self, path):
        self._fake = self._fakes[path]

    def __getattr__(self, name):
        return getattr(self._fake, name)

    def close(self):
        if self._fake is not None:
            self._fake.close()


@pytest.fixture
def guard_heads(monkeypatch):
    fakes = {
        b"p405": FakeStradusHead("VL07241K15, 11070, 405nm, 100mW, C", 100.8),
        b"p639": FakeStradusHead("VL07241K14, 11951, 639nm, 160mW, C", 160.0),
    }
    for fake in fakes.values():
        fake.state.update({"LE": 1, "LW": 405 if fake is fakes[b"p405"] else 639})
    monkeypatch.setattr(guard.hid, "enumerate", lambda vid, pid: [{"path": p} for p in fakes])
    monkeypatch.setattr(guard.hid, "device", lambda: ReopenableDevice(fakes))
    monkeypatch.setattr(guard.time, "sleep", lambda s: None)
    return fakes


def test_guard_turns_every_head_off(guard_heads):
    assert guard.turn_off_all_heads() == ["405 nm", "639 nm"]
    assert all(f.state["LE"] == 0 and f.closed for f in guard_heads.values())


def test_guard_retries_a_head_that_fails(guard_heads):
    fake = guard_heads[b"p639"]
    original = fake._execute
    failures = iter([True])
    fake._execute = lambda command: "LE=1" if command == "LE=0" and next(failures, False) else original(command)

    assert guard.turn_off_all_heads() == ["405 nm", "639 nm"]
    assert fake.sent.count("LE=0") == 2 and fake.state["LE"] == 0


def test_guard_main_waits_for_squid_then_turns_emission_off(monkeypatch):
    events = []

    class Process:
        def __init__(self, pid):
            events.append(("watch", pid))

        def wait(self):
            events.append("squid ended")

    monkeypatch.setattr(guard.psutil, "Process", Process)
    monkeypatch.setattr(guard.squid.logging, "add_file_logging", lambda path: True)
    monkeypatch.setattr(guard, "turn_off_all_heads", lambda: events.append("off") or ["405 nm"])

    assert guard.main(["1234"]) == 0
    assert events == [("watch", 1234), "squid ended", "off"]


def test_guard_main_acts_when_squid_is_already_gone(monkeypatch):
    def gone(pid):
        raise psutil.NoSuchProcess(pid)

    monkeypatch.setattr(guard.psutil, "Process", gone)
    monkeypatch.setattr(guard.squid.logging, "add_file_logging", lambda path: True)
    monkeypatch.setattr(guard, "turn_off_all_heads", lambda: ["405 nm"])

    assert guard.main(["1234"]) == 0


def test_shut_down_keeps_the_guard_when_a_head_stays_on(heads, guards):
    """A head that never confirms LE=0 is retried, and the guard stays as the fallback."""
    laser = versalase.VersaLase()
    laser.initialize()
    stuck = heads[b"p561"]
    original = stuck._execute
    stuck._execute = lambda command: "LE=1" if command == "LE=0" else original(command)

    laser.shut_down()

    assert stuck.sent.count("LE=0") == versalase.SHUTDOWN_ATTEMPTS
    assert all(f.closed for f in heads.values())  # every handle is released for the guard
    assert all(f.state["LE"] == 0 for name, f in heads.items() if name != b"p561")
    assert not guards[0].terminated


def test_shut_down_retries_a_head_and_then_stops_the_guard(heads, guards):
    laser = versalase.VersaLase()
    laser.initialize()
    flaky = heads[b"p405"]
    original = flaky._execute
    replies = iter(["LE=1"])  # still on after the first LE=0
    flaky._execute = lambda command: (
        next(replies, None) or original(command) if command == "LE=0" else original(command)
    )

    laser.shut_down()

    assert flaky.sent.count("LE=0") == 2 and flaky.state["LE"] == 0
    assert guards[0].terminated


def test_close_waits_for_a_transaction_in_flight(heads):
    """close() from one thread while another is mid-read must not close the device under it."""
    laser = versalase.VersaLase(crash_guard=False)
    laser.initialize()
    head = laser.channel_mappings[405]
    fake = heads[b"p405"]
    reading = threading.Event()
    release = threading.Event()
    real_read = fake.read

    def slow_read(size, timeout_ms):
        if fake.sent[-1] == "?LE" and not release.is_set():
            reading.set()
            release.wait(5)
        return real_read(size, timeout_ms)

    fake.read = slow_read
    result = {}
    reader = threading.Thread(target=lambda: result.update(le=head.transact("?LE")))
    reader.start()
    assert reading.wait(5)

    closer = threading.Thread(target=head.close)
    closer.start()
    time.sleep(0.2)
    assert closer.is_alive() and not fake.closed  # waiting for the read

    release.set()
    reader.join(5)
    closer.join(5)
    assert result == {"le": "1"} and fake.closed
    with pytest.raises(versalase.VortranError, match="closed"):
        head.transact("?LE")
