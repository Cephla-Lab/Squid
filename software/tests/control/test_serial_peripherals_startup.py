"""Tests for the startup-hardening changes to SerialDevice and LDI.

Covers the two failure modes that used to kill startup silently:
a device that is powered off (no COM port at all), and a device that answers
"still warming up" for longer than the old five-attempt retry allowed.
"""

import time

import pytest

import control._def
from control.serial_peripherals import (
    LDI,
    SerialDevice,
    SerialDeviceAborted,
    SerialDeviceError,
    SerialDeviceTimeout,
    SerialPortNotFoundError,
    _is_warmup_response,
)


class FakePort:
    """Minimal stand-in for serial.Serial driven by a scripted reply list."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.written = []
        self.closed = False
        self.is_open = True
        self.in_waiting = 0

    def write(self, data):
        self.written.append(data)

    def readline(self):
        if self._responses:
            reply = self._responses.pop(0)
        else:
            reply = ""
        return (reply + "\n").encode()

    def close(self):
        self.closed = True


@pytest.fixture
def no_comports(monkeypatch):
    monkeypatch.setattr("control.serial_peripherals.list_ports.comports", lambda: [])


def make_device(responses, **kwargs):
    """A SerialDevice with a fake port bolted on.

    Constructed with no port and require_port=False so __init__ never touches
    real hardware, then handed the fake.
    """
    device = SerialDevice(require_port=False, device_label="Test device", **kwargs)
    device.serial = FakePort(responses)
    return device


# ── an absent device is named, not an AttributeError later ──────────────────


def test_missing_port_raises_named_error_at_construction(no_comports):
    with pytest.raises(SerialPortNotFoundError) as excinfo:
        SerialDevice(SN="NOPE", device_label="Spinning disk (X-Light/Cicero)")
    message = str(excinfo.value)
    assert "Spinning disk (X-Light/Cicero)" in message
    assert "NOPE" in message
    assert "powered off" in message


def test_missing_port_can_be_tolerated_for_simulation(no_comports):
    device = SerialDevice(SN="NOPE", device_label="sim", require_port=False)
    assert device.serial is None


def test_open_ser_raises_when_port_still_absent(no_comports):
    device = SerialDevice(SN="NOPE", device_label="sim", require_port=False)
    with pytest.raises(SerialPortNotFoundError):
        device.open_ser(require_port=True)


def test_write_paths_raise_named_error_not_attribute_error(no_comports):
    device = SerialDevice(SN="NOPE", device_label="LDI laser engine", require_port=False)
    for call in (
        lambda: device.write("x\r"),
        lambda: device.write_and_read("x\r"),
        lambda: device.write_and_check("x\r", "ok"),
    ):
        with pytest.raises(SerialPortNotFoundError):
            call()


def test_close_is_safe_when_port_was_never_found(no_comports):
    device = SerialDevice(SN="NOPE", device_label="sim", require_port=False)
    device.close()  # must not raise - this is the teardown path for this failure


# ── the legacy retry contract is unchanged ─────────────────────────────────


def test_legacy_behaviour_is_unchanged(monkeypatch):
    """With no new kwargs: exactly max_attempts tries, then SerialDeviceError."""
    slept = []
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))
    device = make_device(["nope"] * 20)

    with pytest.raises(SerialDeviceError) as excinfo:
        device.write_and_check("go\r", "ok", max_attempts=5, attempt_delay=1)

    assert "Max attempts reached" in str(excinfo.value)
    assert len(device.serial.written) == 5
    assert slept.count(1) == 5  # one attempt_delay per attempt, as before


def test_matching_response_returns_immediately(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    device = make_device(["ok"])
    assert device.write_and_check("go\r", "ok") == "ok"
    assert len(device.serial.written) == 1


def test_prefix_match_still_accepted(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    device = make_device(["ok-and-then-some"])
    assert device.write_and_check("go\r", "ok") == "ok-and-then-some"


# ── warm-up replies are waited out ─────────────────────────────────────────


def test_warmup_is_retried_past_max_attempts_then_succeeds(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    warmups = ["ERR=System in Warmup State"] * 12
    device = make_device(warmups + ["ok"])
    seen = []

    result = device.write_and_check(
        "run!\r",
        "ok",
        max_attempts=5,
        timeout_s=90.0,
        retry_if=_is_warmup_response,
        on_retry=lambda response, elapsed: seen.append(response),
    )

    assert result == "ok"
    # 12 warm-up replies is well past the old 5-attempt budget that killed startup.
    assert len(seen) == 12
    assert seen[0] == "ERR=System in Warmup State"


def test_warmup_that_never_clears_times_out_with_the_last_reply(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    clock = {"t": 0.0}

    def fake_monotonic():
        clock["t"] += 1.0
        return clock["t"]

    monkeypatch.setattr(time, "monotonic", fake_monotonic)
    device = make_device(["ERR=System in Warmup State"] * 500)

    with pytest.raises(SerialDeviceTimeout) as excinfo:
        device.write_and_check("run!\r", "ok", timeout_s=10.0, retry_if=_is_warmup_response)

    message = str(excinfo.value)
    assert "ERR=System in Warmup State" in message
    assert "Test device" in message


def test_a_wrong_reply_still_fails_fast_even_with_a_long_timeout(monkeypatch):
    """A genuinely wrong answer must not burn the whole 90s budget."""
    monkeypatch.setattr(time, "sleep", lambda s: None)
    device = make_device(["ERR=Bad Command"] * 20)

    with pytest.raises(SerialDeviceError) as excinfo:
        device.write_and_check("run!\r", "ok", max_attempts=5, timeout_s=90.0, retry_if=_is_warmup_response)

    assert not isinstance(excinfo.value, SerialDeviceTimeout)
    assert len(device.serial.written) == 5


def test_silence_fails_fast_rather_than_waiting_out_the_timeout(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    device = make_device([])  # device says nothing at all

    with pytest.raises(SerialDeviceError):
        device.write_and_check("run!\r", "ok", max_attempts=3, timeout_s=90.0, retry_if=_is_warmup_response)

    assert len(device.serial.written) == 3


def test_cancel_fn_aborts_promptly(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    device = make_device(["ERR=System in Warmup State"] * 100)
    calls = {"n": 0}

    def cancel():
        calls["n"] += 1
        return calls["n"] > 3

    with pytest.raises(SerialDeviceAborted):
        device.write_and_check("run!\r", "ok", timeout_s=90.0, retry_if=_is_warmup_response, cancel_fn=cancel)

    assert len(device.serial.written) == 3


def test_sleep_fn_is_used_instead_of_time_sleep():
    """The reporter injects a pumping sleep so the window stays alive."""
    device = make_device(["ok"])
    pumped = []
    device.write_and_check("go\r", "ok", sleep_fn=lambda s: pumped.append(s))
    assert pumped, "sleep_fn was never called"


# ── the warm-up matcher ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "response",
    ["ERR=System in Warmup State", "err=system in warm up state", "Warming up, please wait"],
)
def test_warmup_matcher_accepts_expected_wordings(response):
    assert _is_warmup_response(response)


@pytest.mark.parametrize("response", ["ok", "ERR=Bad Command", "", "ERR=Interlock Open"])
def test_warmup_matcher_rejects_everything_else(response):
    assert not _is_warmup_response(response)


# ── LDI.initialize wiring ──────────────────────────────────────────────────


def test_ldi_initialize_waits_out_warmup(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    ldi = LDI.__new__(LDI)  # bypass __init__, which would go looking for hardware
    ldi.serial_connection = make_device(["ERR=System in Warmup State"] * 8 + ["ok"])
    seen = []

    ldi.initialize(timeout_s=90.0, on_retry=lambda response, elapsed: seen.append(response))

    assert len(seen) == 8


def test_ldi_initialize_defaults_to_the_configured_timeout(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr(control._def, "STARTUP_DEVICE_TIMEOUT_S", 123.0)
    captured = {}

    class RecordingDevice:
        def write_and_check(self, command, expected, **kwargs):
            captured.update(kwargs)
            return "ok"

    ldi = LDI.__new__(LDI)
    ldi.serial_connection = RecordingDevice()
    ldi.initialize()

    # Read live from control._def, not the star-imported snapshot, so the
    # Preferences dialog can change it without a restart.
    assert captured["timeout_s"] == 123.0
