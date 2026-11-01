"""PriorStage against a fake ProScan that, like the real controller, ends every reply with a bare CR."""

import logging
import threading
import time
from types import SimpleNamespace

import pytest
import serial.tools.list_ports  # prior.py relies on the app having imported it already

import squid.exceptions
import squid.logging
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
        # X and Y encoders, as a ProScan has them after power-up: detected and switched on
        self.encoders_fitted = 3
        self.encoder_axes = 3
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
        if cmd == "ENCODER":
            return f"{self.encoder_axes}\r".encode()
        if cmd.startswith("SENCODER,"):  # SENCODER,b switches every fitted encoder, SENCODER,<axis>,b just one
            *axis, b = cmd.split(",")[1:]
            axes = ({"X": 1, "Y": 2, "Z": 4}[axis[0]] if axis else 0b111) & self.encoders_fitted
            self.encoder_axes = self.encoder_axes | axes if b == "1" else self.encoder_axes & ~axes
            return b"0\r"
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


@pytest.fixture
def prior_log():
    """Warnings and info lines PriorStage logs (the squid loggers don't propagate to pytest's caplog)."""
    records = []
    handler = logging.Handler()
    handler.emit = records.append
    logger = squid.logging.get_logger("PriorStage")
    logger.addHandler(handler)
    yield records
    logger.removeHandler(handler)


def _messages(records, level):
    return [r.getMessage() for r in records if r.levelno == level]


def test_by_default_nothing_about_the_encoders_is_sent(fake):
    stage, ser = make_stage(fake)
    assert not [c for c in ser.commands if "ENCODER" in c]  # neither SENCODER nor even an ENCODER query
    assert stage.encoder_axes is None


@pytest.mark.parametrize("use_encoders", [False, True])
def test_x_and_y_are_switched_as_configured_after_the_stage_check(fake, prior_log, use_encoders):
    stage, ser = make_stage(fake, use_encoders=use_encoders)
    sent = [c for c in ser.commands if c.startswith("SENCODER")]
    assert sent == [f"SENCODER,X,{int(use_encoders)}", f"SENCODER,Y,{int(use_encoders)}"]  # never all axes at once
    assert ser.commands.index(sent[0]) > ser.commands.index("STAGE")
    assert stage.encoder_axes == (3 if use_encoders else 0)
    assert _messages(prior_log, logging.WARNING) == []
    expected = "X+Y (closed loop)" if use_encoders else "off (open loop)"
    assert any(expected in m for m in _messages(prior_log, logging.INFO))


def test_switching_on_without_fitted_encoders_is_reported(fake, prior_log, monkeypatch):
    normal = FakeProScan._reply
    monkeypatch.setattr(FakeProScan, "_reply", lambda self, cmd: b"0\r" if cmd == "ENCODER" else normal(self, cmd))
    make_stage(fake, use_encoders=True)  # acknowledged, but no axis comes under encoder control
    assert any("asked for X and Y on" in m for m in _messages(prior_log, logging.WARNING))


def test_encoders_left_on_after_switching_off_are_reported(fake, prior_log, monkeypatch):
    normal = FakeProScan._reply
    monkeypatch.setattr(
        FakeProScan, "_reply", lambda self, cmd: b"0\r" if cmd.startswith("SENCODER") else normal(self, cmd)
    )
    stage, ser = make_stage(fake, use_encoders=False)  # the controller acknowledges but keeps them on
    assert stage.encoder_axes == 3
    assert any("asked for X and Y off" in m for m in _messages(prior_log, logging.WARNING))


@pytest.mark.parametrize("use_encoders", [False, True])
@pytest.mark.parametrize("read_back, state", [(1, "X on, Y off"), (2, "X off, Y on")])
def test_one_axis_on_and_one_off_is_reported(fake, prior_log, monkeypatch, use_encoders, read_back, state):
    normal = FakeProScan._reply
    reply = f"{read_back}\r".encode()
    monkeypatch.setattr(FakeProScan, "_reply", lambda self, cmd: reply if cmd == "ENCODER" else normal(self, cmd))
    stage, _ = make_stage(fake, use_encoders=use_encoders)
    assert stage.encoder_axes == read_back
    wanted = "on" if use_encoders else "off"
    assert _messages(prior_log, logging.WARNING) == [
        f"Prior stage encoders: asked for X and Y {wanted}, but they read back {state}"
    ]
    assert not any("loop)" in m for m in _messages(prior_log, logging.INFO))  # no closed / open loop success line


def test_a_controller_refusing_sencoder_does_not_stop_start_up(fake, prior_log, monkeypatch):
    normal = FakeProScan._reply
    monkeypatch.setattr(
        FakeProScan, "_reply", lambda self, cmd: b"E,1\r" if cmd.startswith("SENCODER") else normal(self, cmd)
    )
    stage, ser = make_stage(fake, use_encoders=False)
    assert stage.encoder_axes == 3
    warnings = _messages(prior_log, logging.WARNING)
    assert any("switching the X encoder off was answered" in m for m in warnings)
    assert any("switching the Y encoder off was answered" in m for m in warnings)
    assert any("asked for X and Y off" in m for m in warnings)


def test_an_unreadable_encoder_state_is_reported(fake, prior_log, monkeypatch):
    normal = FakeProScan._reply
    monkeypatch.setattr(FakeProScan, "_reply", lambda self, cmd: b"?\r" if cmd == "ENCODER" else normal(self, cmd))
    stage, ser = make_stage(fake, use_encoders=False)
    assert stage.encoder_axes is None
    assert any("could not read back" in m for m in _messages(prior_log, logging.WARNING))


@pytest.mark.parametrize("use_encoders", [False, True])
def test_a_focus_drive_encoder_is_left_alone(fake, prior_log, monkeypatch, use_encoders):
    init = FakeProScan.__init__

    def with_a_focus_encoder(self, *args, **kwargs):
        init(self, *args, **kwargs)
        self.encoders_fitted = self.encoder_axes = 0b111  # X, Y and an encoded Z focus drive, all on after power-up

    monkeypatch.setattr(FakeProScan, "__init__", with_a_focus_encoder)
    stage, ser = make_stage(fake, use_encoders=use_encoders)
    b = int(use_encoders)
    assert [c for c in ser.commands if c.startswith("SENCODER")] == [f"SENCODER,X,{b}", f"SENCODER,Y,{b}"]
    assert stage.encoder_axes == (0b111 if use_encoders else 0b100)  # Z stays on either way
    assert _messages(prior_log, logging.WARNING) == []  # and doesn't make the stage count as closed loop


@pytest.mark.parametrize(
    "value, parsed",
    [
        (None, None),
        ("", None),
        ("none", None),
        ("None", None),
        (False, False),
        ("off", False),
        ("no", False),
        ("FALSE", False),
        (0, False),
        (True, True),
        ("on", True),
        ("yes", True),
        (1, True),
    ],
)
def test_the_ini_setting_is_read_as_true_false_or_none(value, parsed):
    assert squid.stage.prior.PriorStage.parse_use_encoders(value) is parsed


def test_an_unclear_ini_setting_stops_start_up(fake):
    with pytest.raises(ValueError, match="prior_stage_use_encoders"):
        make_stage(fake, use_encoders="maybe")


def test_positional_arguments_keep_their_places(fake):
    squid.stage.prior.PriorStage("FAKE_SN", 115200, None, 150, 500)  # sn, baudrate, stage_config, speed, acceleration
    ser = fake["fake"]
    assert (ser.sms, ser.sas) == (150, 500)
    assert not [c for c in ser.commands if "ENCODER" in c]
    with pytest.raises(TypeError):  # the encoder setting only by name
        squid.stage.prior.PriorStage("FAKE_SN", 115200, None, 150, 500, False)


def test_microscope_build_passes_the_ini_encoder_setting(fake, monkeypatch):
    import control._def
    import control.microscope

    monkeypatch.setattr(control._def, "USE_PRIOR_STAGE", True)
    monkeypatch.setattr(control._def, "PRIOR_STAGE_SN", "FAKE_SN")
    monkeypatch.setattr(control._def, "PRIOR_STAGE_USE_ENCODERS", False)
    scope = control.microscope.Microscope.build_from_global_config(simulated=True)
    try:
        assert {"SENCODER,X,0", "SENCODER,Y,0"} <= set(fake["fake"].commands)
        assert scope.stage.encoder_axes == 0
    finally:
        scope.close()


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
