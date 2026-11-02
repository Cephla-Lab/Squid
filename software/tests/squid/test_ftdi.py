import sys
import types

import pytest

import squid.ftdi
import squid.filter_wheel_controller.optospin as optospin
import squid.stage.pi


class FakeKey:
    def __init__(self, children=None, values=None):
        self.children = children or {}
        self.values = values or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_winreg(devices):
    """A winreg stand-in whose FTDIBUS key holds `devices`: {device: {instance: {value name: value}}}."""
    ftdibus = FakeKey(
        {
            device: FakeKey(
                {instance: FakeKey({"Device Parameters": FakeKey(values=v)}) for instance, v in inst.items()}
            )
            for device, inst in devices.items()
        }
    )
    hklm = FakeKey({"SYSTEM": FakeKey({"CurrentControlSet": FakeKey({"Enum": FakeKey({"FTDIBUS": ftdibus})})})})

    def open_key(key, path):
        for part in path.split("\\"):
            if part not in key.children:
                raise OSError(f"no key {part}")
            key = key.children[part]
        return key

    def enum_key(key, i):
        names = list(key.children)
        if i >= len(names):
            raise OSError("no more keys")
        return names[i]

    def query_value(key, name):
        if name not in key.values:
            raise OSError(f"no value {name}")
        return key.values[name], 4

    return types.SimpleNamespace(HKEY_LOCAL_MACHINE=hklm, OpenKey=open_key, EnumKey=enum_key, QueryValueEx=query_value)


AION_PC = {
    "VID_0403+PID_6001+SCU-00012986A": {"0000": {"PortName": "COM7", "LatencyTimer": 16}},
    "VID_156B+PID_0003+12464A": {"0000": {"PortName": "COM6", "LatencyTimer": 1}},
    "VID_1A72+PID_102B+1DLCI3O!A": {"0000": {"PortName": "COM4", "LatencyTimer": 1}},
    "VID_0403+PID_6001+NOPORT": {"0000": {"LatencyTimer": 16}},  # an FTDI device without a COM port
}


@pytest.fixture
def windows(monkeypatch):
    def install(devices):
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setitem(sys.modules, "winreg", fake_winreg(devices))

    return install


class RecordingLog:
    def __init__(self):
        self.warnings = []
        self.debugs = []

    def warning(self, msg):
        self.warnings.append(msg)

    def debug(self, msg):
        self.debugs.append(msg)


def test_reads_the_latency_timer_of_the_port(windows):
    windows(AION_PC)
    assert squid.ftdi.windows_latency_timer_ms("COM7") == 16
    assert squid.ftdi.windows_latency_timer_ms("COM4") == 1
    assert squid.ftdi.windows_latency_timer_ms("com6") == 1  # port names compare case-insensitively


def test_no_latency_timer_for_a_port_that_is_not_ftdi(windows):
    windows(AION_PC)
    assert squid.ftdi.windows_latency_timer_ms("COM3") is None  # e.g. the Teensy (usbser)
    assert squid.ftdi.windows_latency_timer_ms("") is None


def test_no_latency_timer_without_an_ftdibus_key(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    winreg = fake_winreg({})
    winreg.HKEY_LOCAL_MACHINE.children.clear()
    monkeypatch.setitem(sys.modules, "winreg", winreg)
    assert squid.ftdi.windows_latency_timer_ms("COM4") is None


def test_no_latency_timer_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")  # Linux sets it with the udev rule
    assert squid.ftdi.windows_latency_timer_ms("COM4") is None


def test_warns_with_the_fix_when_the_timer_is_high(windows):
    windows(AION_PC)
    log = RecordingLog()
    assert squid.ftdi.warn_if_latency_timer_high("COM7", "SmarAct SCU", log) == 16
    assert len(log.warnings) == 1
    assert "COM7" in log.warnings[0] and "16 ms" in log.warnings[0] and "Latency Timer to 1" in log.warnings[0]


@pytest.mark.parametrize("port", ["COM4", "COM3"])
def test_no_warning_at_1_ms_or_when_unknown(windows, port):
    windows(AION_PC)
    log = RecordingLog()
    squid.ftdi.warn_if_latency_timer_high(port, "device", log)
    assert log.warnings == []


def test_a_failed_read_never_raises(monkeypatch):
    def broken(port):
        raise RuntimeError("registry exploded")

    monkeypatch.setattr(squid.ftdi, "windows_latency_timer_ms", broken)
    log = RecordingLog()
    assert squid.ftdi.warn_if_latency_timer_high("COM4", "device", log) is None
    assert log.warnings == [] and len(log.debugs) == 1


@pytest.mark.skipif(sys.platform != "win32", reason="reads the real Windows registry")
def test_reads_the_real_registry_without_error():
    assert squid.ftdi.windows_latency_timer_ms("COM999") is None


def _record_checks(monkeypatch):
    checks = []
    monkeypatch.setattr(squid.ftdi, "warn_if_latency_timer_high", lambda port, name, log: checks.append((port, name)))
    return checks


def test_the_pi_stage_checks_its_port_on_connect(monkeypatch):
    checks = _record_checks(monkeypatch)
    monkeypatch.setattr(squid.stage.pi, "C414FocusStage", squid.stage.pi._SimulatedC414)  # no hardware
    squid.stage.pi.connect_pi_focus_stage(serial_port="COM4")
    assert checks == [("COM4", "PI C-414 (V-308 focus)")]


def test_the_optospin_checks_its_port_on_connect(monkeypatch):
    checks = _record_checks(monkeypatch)
    port = types.SimpleNamespace(device="COM6", serial_number="12464A")
    monkeypatch.setattr(optospin.serial.tools.list_ports, "comports", lambda: [port])
    monkeypatch.setattr(optospin.serial, "Serial", lambda *args, **kwargs: types.SimpleNamespace())
    optospin.Optospin(types.SimpleNamespace(serial_number="12464A"))
    assert checks == [("COM6", "Optospin filter wheel")]
