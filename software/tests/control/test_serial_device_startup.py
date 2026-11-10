"""SerialDevice behaviour the startup path relies on: a missing port fails at construction with the device's
name, write_and_check can wait out a transient reply against a deadline, and the LDI waits out its warm-up."""

from types import SimpleNamespace

import pytest

import control._def
import control.serial_peripherals as serial_peripherals
from control.serial_peripherals import (
    LDI,
    SerialDevice,
    SerialDeviceAborted,
    SerialDeviceError,
    SerialDeviceTimeout,
    SerialPortNotFoundError,
)

WARMUP = "ERR=System in Warmup State"


class FakePort:
    """A serial port that answers each write with the next scripted reply (the last one repeats)."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.writes = []
        self.is_open = True
        self.closed = False
        self.in_waiting = 0

    def write(self, data):
        self.writes.append(data.decode())

    def readline(self):
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return (reply + "\n").encode()

    def open(self):
        self.is_open = True

    def close(self):
        self.closed = True


class Clock:
    """time.monotonic that only advances when the code under test sleeps."""

    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(serial_peripherals.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(serial_peripherals.time, "sleep", clock.sleep)
    return clock


def make_device(monkeypatch, replies, label="Test device"):
    port = FakePort(replies)
    monkeypatch.setattr(serial_peripherals.serial, "Serial", lambda *args, **kwargs: port)
    return SerialDevice(port="/dev/fake", device_label=label), port


class TestMissingPort:
    @pytest.fixture(autouse=True)
    def no_ports(self, monkeypatch):
        monkeypatch.setattr(serial_peripherals.list_ports, "comports", lambda: [])

    def test_construction_fails_naming_the_device(self):
        with pytest.raises(SerialPortNotFoundError) as excinfo:
            SerialDevice(SN="A1234", device_label="LDI laser engine")
        assert "LDI laser engine" in str(excinfo.value)
        assert "SN='A1234'" in str(excinfo.value)
        assert isinstance(excinfo.value, SerialDeviceError)  # existing handlers still catch it

    def test_require_port_false_defers_the_failure_to_first_use(self):
        device = SerialDevice(SN="A1234", device_label="CellX (simulated)", require_port=False)
        assert device.serial is None
        device.open_ser()  # still nothing there, still harmless
        with pytest.raises(SerialPortNotFoundError, match="CellX"):
            device.write("x")
        with pytest.raises(SerialPortNotFoundError):
            device.write_and_check("x", "ok")
        device.close()  # teardown after a device that was never found must not raise


class TestWriteAndCheck:
    def test_without_the_new_arguments_it_gives_up_after_max_attempts(self, monkeypatch, clock):
        device, port = make_device(monkeypatch, [WARMUP])
        with pytest.raises(SerialDeviceError, match="Max attempts") as excinfo:
            device.write_and_check("run!\r", "ok", max_attempts=5, attempt_delay=1)
        assert not isinstance(excinfo.value, SerialDeviceTimeout)
        assert len(port.writes) == 5

    def test_a_transient_reply_is_retried_past_max_attempts_until_it_clears(self, monkeypatch, clock):
        device, port = make_device(monkeypatch, [WARMUP] * 20 + ["ok"])
        retries = []
        response = device.write_and_check(
            "run!\r",
            "ok",
            max_attempts=5,
            timeout_s=90,
            retry_if=serial_peripherals._is_warmup_response,
            on_retry=lambda reply, elapsed: retries.append((reply, elapsed)),
        )
        assert response == "ok"
        assert len(port.writes) == 21
        assert len(retries) == 20
        assert retries[0][0] == WARMUP
        assert retries[-1][1] > retries[0][1]  # elapsed time is reported and grows

    def test_a_transient_reply_that_never_clears_times_out(self, monkeypatch, clock):
        device, port = make_device(monkeypatch, [WARMUP], label="LDI laser engine")
        with pytest.raises(SerialDeviceTimeout) as excinfo:
            device.write_and_check(
                "run!\r", "ok", timeout_s=10, retry_if=serial_peripherals._is_warmup_response, attempt_delay=1
            )
        assert excinfo.value.last_response == WARMUP
        assert "LDI laser engine" in str(excinfo.value) and "10.0s" in str(excinfo.value)
        assert 10 <= clock.now < 13  # stopped at the deadline, not after max_attempts and not much later

    def test_a_wrong_reply_that_is_not_transient_still_fails_fast(self, monkeypatch, clock):
        device, port = make_device(monkeypatch, ["ERR=Unknown command"])
        with pytest.raises(SerialDeviceError, match="Max attempts") as excinfo:
            device.write_and_check(
                "run!\r", "ok", max_attempts=5, timeout_s=90, retry_if=serial_peripherals._is_warmup_response
            )
        assert not isinstance(excinfo.value, SerialDeviceTimeout)
        assert len(port.writes) == 5
        assert clock.now < 10  # nowhere near the 90 s deadline

    def test_cancel_stops_the_wait_before_the_next_write(self, monkeypatch, clock):
        device, port = make_device(monkeypatch, [WARMUP])
        with pytest.raises(SerialDeviceAborted, match="Test device"):
            device.write_and_check(
                "run!\r",
                "ok",
                timeout_s=90,
                retry_if=serial_peripherals._is_warmup_response,
                cancel_fn=lambda: len(port.writes) >= 3,
            )
        assert len(port.writes) == 3

    def test_sleep_fn_replaces_time_sleep(self, monkeypatch, clock):
        device, port = make_device(monkeypatch, [WARMUP, "ok"])
        pumped = []

        def pumping_sleep(seconds):
            pumped.append(seconds)
            clock.now += seconds

        device.write_and_check(
            "run!\r", "ok", timeout_s=90, retry_if=serial_peripherals._is_warmup_response, sleep_fn=pumping_sleep
        )
        assert pumped and clock.sleeps == []

    def test_match_anywhere_still_works(self, monkeypatch, clock):
        device, port = make_device(monkeypatch, ["B1 J800"])
        assert device.write_and_check("J800\r", "J800", check_prefix=False, match_anywhere=True) == "B1 J800"


@pytest.mark.parametrize(
    "reply, expected",
    [
        (WARMUP, True),
        ("ERR=System warming up", True),
        ("err=system in warm up state", True),
        ("ERR=Interlock open", False),
        ("ok", False),
    ],
)
def test_is_warmup_response(reply, expected):
    assert serial_peripherals._is_warmup_response(reply) is expected


class TestLdiWarmup:
    @pytest.fixture
    def ldi_port(self, monkeypatch, clock):
        port = FakePort([WARMUP] * 30 + ["ok"])
        monkeypatch.setattr(
            serial_peripherals.list_ports,
            "comports",
            lambda: [SimpleNamespace(serial_number="LDI123", device="/dev/fake-ldi", vid=None, pid=None)],
        )
        monkeypatch.setattr(serial_peripherals.serial, "Serial", lambda *args, **kwargs: port)
        return port

    def test_initialize_waits_out_the_warmup_and_reports_it(self, ldi_port, clock):
        ldi = LDI(SN="LDI123")
        seen = []
        ldi.initialize(timeout_s=90, on_retry=lambda reply, elapsed: seen.append(reply))
        assert ldi_port.writes[-1] == "run!\r"
        assert len(ldi_port.writes) == 31  # far more than the five attempts that used to end startup
        assert set(seen) == {WARMUP}

    def test_initialize_defaults_to_the_configured_startup_timeout(self, ldi_port, clock, monkeypatch):
        ldi_port.replies = [WARMUP]
        monkeypatch.setattr(control._def, "STARTUP_DEVICE_TIMEOUT_S", 20.0)
        ldi = LDI(SN="LDI123")
        with pytest.raises(SerialDeviceTimeout, match="20.0s"):
            ldi.initialize()

    def test_initialize_honours_abort(self, ldi_port, clock):
        ldi = LDI(SN="LDI123")
        with pytest.raises(SerialDeviceAborted):
            ldi.initialize(timeout_s=90, cancel_fn=lambda: len(ldi_port.writes) >= 2)

    def test_the_simulated_ldi_accepts_the_same_arguments(self):
        serial_peripherals.LDI_Simulation().initialize(timeout_s=1, cancel_fn=lambda: False, on_retry=None)
