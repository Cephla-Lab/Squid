"""PriorStage against a fake ProScan that, like the real controller, ends every reply with a bare CR."""

import threading
import time
from types import SimpleNamespace

import pytest
import serial.tools.list_ports  # prior.py relies on the app having imported it already

import squid.exceptions
import squid.stage.prior


class FakeProScan:
    """Just enough of the ProScan ASCII protocol for PriorStage: CR-terminated replies, multi-line STAGE."""

    def __init__(self, *args, timeout=0.1, **kwargs):
        self.timeout = timeout
        self.baudrate = kwargs.get("baudrate", 115200)
        self._buf = b""
        self._lock = threading.Lock()
        self.x = 0
        self.y = 0
        self.busy_polls = 0
        self.commands = []
        self.sms = None
        self.sas = None

    # --- pyserial surface ---
    def write(self, data: bytes):
        cmd = data.decode().strip()
        with self._lock:
            if cmd:
                self.commands.append(cmd)
                self._buf += self._reply(cmd)

    def read_until(self, expected=b"\n"):
        with self._lock:
            idx = self._buf.find(expected)
            if idx >= 0:
                line, self._buf = self._buf[: idx + 1], self._buf[idx + 1 :]
                return line
            line, self._buf = self._buf, b""
        time.sleep(self.timeout)  # no terminator: pyserial runs out the timeout
        return line

    def readline(self):
        return self.read_until(b"\n")

    def reset_input_buffer(self):
        with self._lock:
            self._buf = b""

    flushInput = reset_input_buffer

    @property
    def in_waiting(self):
        return len(self._buf)

    def inject(self, data: bytes):
        with self._lock:
            self._buf += data

    # --- controller ---
    def _reply(self, cmd: str) -> bytes:
        if cmd == "STAGE":
            lines = ["STAGE = H117(1MM)4", "TYPE = 25", "SIZE_X = 114 MM", "SIZE_Y = 75 MM", "END"]
            return b"".join(f"{line}\r".encode() for line in lines)
        if cmd == "$,S":
            if self.busy_polls > 0:
                self.busy_polls -= 1
                return b"3\r"
            return b"0\r"
        if cmd == "P":
            return f"{self.x},{self.y},0\r".encode()
        if cmd.startswith("GX "):
            self.x = int(cmd.split()[1])
            self.busy_polls = 2
            return b"R\r"
        if cmd.startswith("GY "):
            self.y = int(cmd.split()[1])
            self.busy_polls = 2
            return b"R\r"
        if cmd.startswith("GR "):
            dx, dy = (int(v) for v in cmd.split()[1].split(","))
            self.x += dx
            self.y += dy
            self.busy_polls = 2
            return b"R\r"
        # Physical units as an H117(1MM) reports them: 60 um/s and 285.5 um/s^2 per SMS/SAS step.
        if cmd == "SMS,u":
            return f"{self.sms * 60:.2f}\r".encode()
        if cmd == "SAS,u":
            return f"{self.sas * 285.5:.2f}\r".encode()
        if cmd.startswith("SMS "):
            self.sms = int(cmd.split()[1])
        if cmd.startswith("SAS "):
            self.sas = int(cmd.split()[1])
        return b"0\r"


@pytest.fixture
def fake(monkeypatch):
    instance = {}

    def make_serial(*args, **kwargs):
        instance["fake"] = FakeProScan(*args, **kwargs)
        return instance["fake"]

    monkeypatch.setattr(squid.stage.prior.serial, "Serial", make_serial)
    monkeypatch.setattr(
        squid.stage.prior.serial.tools.list_ports,
        "comports",
        lambda: [SimpleNamespace(device="COM_FAKE", serial_number="FAKE_SN")],
    )
    return instance


def make_stage(fake, **kwargs):
    stage = squid.stage.prior.PriorStage(sn="FAKE_SN", **kwargs)
    return stage, fake["fake"]


def test_init_reads_multiline_stage_reply_without_misaligning(fake):
    stage, ser = make_stage(fake)
    assert stage.stage_model == "H117(1MM)4"
    # If the STAGE trailer lines were left in the buffer, the init "P" would have parsed "TYPE = 25" and raised.
    assert stage.get_pos().x_mm == 0


def test_speed_and_acceleration_are_configurable(fake):
    stage, ser = make_stage(fake, max_speed=500, acceleration=400)
    assert ser.sms == 500
    assert ser.sas == 400


def test_default_speed_and_acceleration_unchanged(fake):
    stage, ser = make_stage(fake)
    assert ser.sms == 100
    assert ser.sas == 100


def test_command_returns_on_cr_not_serial_timeout(fake):
    stage, ser = make_stage(fake)
    ser.timeout = 0.5
    t0 = time.perf_counter()
    for _ in range(5):
        assert stage._send_command("$,S") == "0"
    # readline() would have waited 5 x 0.5 s for an LF that the controller never sends.
    assert time.perf_counter() - t0 < 0.25


def test_move_to_waits_for_stop_and_updates_position(fake):
    stage, ser = make_stage(fake)
    t0 = time.perf_counter()
    stage.move_x_to(1.5)
    stage.move_y_to(-0.25)
    elapsed = time.perf_counter() - t0
    pos = stage.get_pos()
    assert pos.x_mm == pytest.approx(1.5)
    assert pos.y_mm == pytest.approx(-0.25)
    assert not stage.get_state().busy
    # Two axes, each: move + 3 status polls + position read. Was ~0.9 s with readline() timeouts.
    assert elapsed < 0.2


def test_relative_move(fake):
    stage, ser = make_stage(fake)
    stage.move_x(0.1)
    stage.move_y(0.2)
    pos = stage.get_pos()
    assert pos.x_mm == pytest.approx(0.1)
    assert pos.y_mm == pytest.approx(0.2)


def test_stale_reply_is_not_taken_as_answer(fake):
    stage, ser = make_stage(fake)
    ser.inject(b"R\r")  # a late reply from an earlier command
    assert stage._send_command("$,S") == "0"


def test_checks_ftdi_latency_timer_of_its_port(fake, monkeypatch):
    checks = []
    monkeypatch.setattr(
        squid.stage.prior.squid.ftdi, "warn_if_latency_timer_high", lambda port, name, log: checks.append((port, name))
    )
    make_stage(fake)
    assert checks == [("COM_FAKE", "Prior stage")]


def test_microscope_build_passes_ini_speed_to_prior_stage(fake, monkeypatch):
    import control._def
    import control.microscope

    monkeypatch.setattr(control._def, "USE_PRIOR_STAGE", True)
    monkeypatch.setattr(control._def, "PRIOR_STAGE_SN", "FAKE_SN")
    monkeypatch.setattr(control._def, "PRIOR_STAGE_MAX_SPEED", 500)
    monkeypatch.setattr(control._def, "PRIOR_STAGE_ACCELERATION", 300)
    scope = control.microscope.Microscope.build_from_global_config(simulated=True)
    try:
        assert isinstance(scope.stage, squid.stage.prior.PriorStage)
        assert fake["fake"].sms == 500
        assert fake["fake"].sas == 300
    finally:
        scope.close()


def test_controller_error_raises(fake):
    stage, ser = make_stage(fake)
    normal = ser._reply
    # Only the command under test: the background position poller's "P" must keep getting real replies.
    ser._reply = lambda cmd: b"E,4\r" if cmd.startswith("SMS") else normal(cmd)
    with pytest.raises(Exception, match="E,4"):
        stage._send_command("SMS 5000")


def _status_replies(ser, replies):
    """Answer "$,S" from `replies` (bytes, or None = no reply at all) and fall back to the normal fake after."""
    normal = ser._reply
    pending = iter(replies)

    def reply(cmd):
        if cmd == "$,S":
            nxt = next(pending, "normal")
            if nxt != "normal":
                return nxt or b""
        return normal(cmd)

    ser._reply = reply


def test_wait_for_stop_raises_when_controller_stops_answering(fake, monkeypatch):
    monkeypatch.setattr(squid.stage.prior.PriorStage, "STATUS_REPLY_TIMEOUT_S", 0.3)
    stage, ser = make_stage(fake)
    _status_replies(ser, [None] * 1000)
    t0 = time.perf_counter()
    with pytest.raises(squid.exceptions.SquidTimeout, match=r"\$,S"):
        stage.wait_for_stop()
    # Each unanswered poll costs the 0.1 s read timeout, so it gives up a poll or two after the 0.3 s budget.
    assert time.perf_counter() - t0 < 1.0


def test_long_move_reporting_busy_does_not_time_out(fake, monkeypatch):
    # The budget is for silence, not for move length: busy statuses keep resetting it.
    monkeypatch.setattr(squid.stage.prior.PriorStage, "STATUS_REPLY_TIMEOUT_S", 0.05)
    stage, ser = make_stage(fake)
    ser.busy_polls = 30  # 30 polls x >= 10 ms, well past the budget
    stage.wait_for_stop()
    assert not stage.get_state().busy


def test_stray_status_line_is_skipped(fake):
    stage, ser = make_stage(fake)
    _status_replies(ser, [b"R\r", None])  # a stray line, then one unanswered poll
    stage.wait_for_stop()
    assert not stage.get_state().busy


def test_missing_stage_model_fails_startup_clearly(fake, monkeypatch):
    normal = FakeProScan._reply
    monkeypatch.setattr(FakeProScan, "_reply", lambda self, cmd: b"" if cmd == "STAGE" else normal(self, cmd))
    with pytest.raises(squid.exceptions.SquidError, match="STAGE"):
        make_stage(fake)


def test_set_speed_and_acceleration_are_remembered(fake):
    stage, ser = make_stage(fake)
    stage.set_max_speed(400)
    stage.set_acceleration(300)
    assert (ser.sms, ser.sas) == (400, 300)
    assert (stage.speed, stage.acceleration) == (400, 300)


@pytest.mark.parametrize("value", [0, 1001])
def test_speed_and_acceleration_outside_1_to_1000_are_refused(fake, value):
    stage, ser = make_stage(fake)
    with pytest.raises(ValueError):
        stage.set_max_speed(value)
    with pytest.raises(ValueError):
        stage.set_acceleration(value)
    assert (ser.sms, ser.sas) == (100, 100)


def test_speed_and_acceleration_in_physical_units(fake):
    stage, ser = make_stage(fake)
    assert stage.get_max_speed_mm_s() == pytest.approx(6.0)
    assert stage.get_acceleration_mm_s2() == pytest.approx(28.55)
    stage.set_max_speed(1000)
    assert stage.get_max_speed_mm_s() == pytest.approx(60.0)


def test_physical_units_are_none_when_the_controller_cannot_say(fake, monkeypatch):
    normal = FakeProScan._reply
    monkeypatch.setattr(FakeProScan, "_reply", lambda self, cmd: b"E,1\r" if cmd.endswith(",u") else normal(self, cmd))
    stage, ser = make_stage(fake)
    assert stage.get_max_speed_mm_s() is None
    assert stage.get_acceleration_mm_s2() is None


def test_ceiling_lowers_a_larger_startup_value(fake):
    stage, ser = make_stage(fake, max_speed=300, acceleration=750, max_speed_limit=200, acceleration_limit=500)
    assert (ser.sms, ser.sas) == (200, 500)
    assert (stage.speed, stage.acceleration) == (200, 500)


def test_setters_refuse_values_above_the_ceiling(fake):
    stage, ser = make_stage(fake, max_speed_limit=200, acceleration_limit=500)
    with pytest.raises(ValueError, match="200"):
        stage.set_max_speed(201)
    with pytest.raises(ValueError, match="500"):
        stage.set_acceleration(501)
    stage.set_max_speed(200)
    stage.set_acceleration(500)
    assert (ser.sms, ser.sas) == (200, 500)


def test_ceilings_are_kept_within_what_the_controller_takes(fake):
    stage, ser = make_stage(fake, max_speed_limit=5000, acceleration_limit=0)
    assert (stage.max_speed_limit, stage.acceleration_limit) == (1000, 1)


def test_microscope_build_passes_the_ini_ceilings_to_prior_stage(fake, monkeypatch):
    import control._def
    import control.microscope

    monkeypatch.setattr(control._def, "USE_PRIOR_STAGE", True)
    monkeypatch.setattr(control._def, "PRIOR_STAGE_SN", "FAKE_SN")
    monkeypatch.setattr(control._def, "PRIOR_STAGE_MAX_SPEED", 300)
    monkeypatch.setattr(control._def, "PRIOR_STAGE_ACCELERATION", 250)
    monkeypatch.setattr(control._def, "PRIOR_STAGE_MAX_SPEED_LIMIT", 200)
    monkeypatch.setattr(control._def, "PRIOR_STAGE_ACCELERATION_LIMIT", 500)
    scope = control.microscope.Microscope.build_from_global_config(simulated=True)
    try:
        assert (scope.stage.max_speed_limit, scope.stage.acceleration_limit) == (200, 500)
        assert (fake["fake"].sms, fake["fake"].sas) == (200, 250)
    finally:
        scope.close()


def test_apply_motion_settings_sends_only_what_changed(fake):
    stage, ser = make_stage(fake, max_speed_limit=200)
    ser.commands.clear()
    stage.apply_motion_settings(100, 300)
    assert [c for c in ser.commands if c.startswith(("SMS", "SAS"))] == ["SAS 300"]
    stage.apply_motion_settings(5000, 300)  # above the ceiling: lowered to it
    assert (ser.sms, stage.speed) == (200, 200)


def test_immersion_objectives_get_the_immersion_limits(fake, monkeypatch):
    import control._def
    import control.microscope
    from control.core.objective_store import ObjectiveStore

    for name, value in {
        "USE_PRIOR_STAGE": True,
        "PRIOR_STAGE_SN": "FAKE_SN",
        "PRIOR_STAGE_MAX_SPEED": 200,
        "PRIOR_STAGE_ACCELERATION": 250,
        "PRIOR_STAGE_MAX_SPEED_LIMIT": 1000,
        "PRIOR_STAGE_ACCELERATION_LIMIT": 1000,
        "PRIOR_STAGE_IMMERSION_MAX_SPEED": 80,
        "PRIOR_STAGE_IMMERSION_ACCELERATION": 150,
    }.items():
        monkeypatch.setattr(control._def, name, value)
    scope = control.microscope.Microscope.build_from_global_config(simulated=True)
    try:
        ser = fake["fake"]
        objectives = scope.objective_store.objectives_dict
        dry = next(name for name, o in objectives.items() if not ObjectiveStore.is_immersion(o))
        immersion = next(name for name, o in objectives.items() if ObjectiveStore.is_immersion(o))

        scope.set_objective(immersion)
        assert (ser.sms, ser.sas) == (80, 150)
        scope.set_objective(dry)
        assert (ser.sms, ser.sas) == (200, 250)
        # The immersion values only ever lower the general ones.
        monkeypatch.setattr(control._def, "PRIOR_STAGE_IMMERSION_MAX_SPEED", 900)
        scope.set_objective(immersion)
        assert (ser.sms, ser.sas) == (200, 150)
    finally:
        scope.close()
