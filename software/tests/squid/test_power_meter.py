"""squid/power_meter.py: finding a Thorlabs meter, talking SCPI to it (with its sensor's limits), and the simulated
meter."""

import sys
from pathlib import Path

import pytest

from squid.power_meter import (
    METER_TIMEOUT_MS,
    SETTLE_S_BY_MODEL,
    SETTLE_S_UNKNOWN_MODEL,
    KNOWN_SENSOR_LIMITS,
    PowerMeterError,
    PowerMeterOverrange,
    SETUP_DOC,
    SimulatedPowerMeter,
    ThorlabsPowerMeter,
    UDEV_RULE,
    find_thorlabs_resource,
    simulated_laser_mw,
    simulated_led_mw,
)

SOFTWARE_DIR = Path(__file__).resolve().parents[2]

ANSWERS = {
    "*IDN?": "Thorlabs,PM100USB,P2001234,1.7.0\n",
    "SYST:SENS:IDN?": "S170C,220304512,24-Mar-2025,1,18,289\n",
    "SENS:POW:RANG:UPP? MAX": "5.000000E-01\n",
    "SENS:CORR:WAV? MIN": "4.000000E+02\n",
    "SENS:CORR:WAV? MAX": "1.100000E+03\n",
}


class FakeInstrument:
    """Like the PM16-121 on the bench (2026-10-09): a command it does not know puts -113 in its error queue (a query
    also times out), and SYST:ERR? answers the oldest error, or 0 when the queue is empty."""

    def __init__(
        self, power_w="1.5E-03", answers=None, fail=(), fail_writes=(), fail_close=False, reject_writes=(), errors=()
    ):
        self.power_w = power_w
        self.answers = dict(ANSWERS if answers is None else answers)
        self.fail = set(fail)
        self.fail_writes = tuple(fail_writes)
        self.reject_writes = tuple(reject_writes)
        self.errors = list(errors)
        self.fail_close = fail_close
        self.writes = []
        self.closed = False
        self.timeout = None

    def query(self, command):
        if command == "SYST:ERR?":
            return (self.errors.pop(0) if self.errors else '+0,"No error"') + "\n"
        if command in self.fail or (command not in self.answers and command != "MEAS:POW?"):
            self.errors.append('-113,"Undefined header"')
            raise OSError(f"VI_ERROR_TMO ({command})")  # what pyvisa raises looks like this
        return self.answers.get(command, f"{self.power_w}\n")

    def write(self, command):
        if command.startswith(self.fail_writes) if self.fail_writes else False:
            raise OSError(f"VI_ERROR_CONN_LOST ({command})")
        self.writes.append(command)
        if self.reject_writes and command.startswith(self.reject_writes):
            self.errors.append('-113,"Undefined header"')

    def close(self):
        if self.fail_close:
            raise OSError("VI_ERROR_CONN_LOST (close)")
        self.closed = True


class FakeResourceManager:
    def __init__(self, resources, instrument):
        self.resources = resources
        self.instrument = instrument
        self.opened = None

    def list_resources(self):
        return tuple(self.resources)

    def open_resource(self, name):
        self.opened = name
        return self.instrument


def _meter(instrument):
    return ThorlabsPowerMeter(
        resource="USB0::0x1313::0x8072::X::INSTR", resource_manager=FakeResourceManager([], instrument)
    )


def test_find_thorlabs_resource_picks_vendor_0x1313():
    resources = ["ASRL1::INSTR", "USB0::0x0699::0x0368::C012345::INSTR", "USB0::0x1313::0x8072::P2001234::INSTR"]
    assert find_thorlabs_resource(resources) == "USB0::0x1313::0x8072::P2001234::INSTR"


def test_find_thorlabs_resource_says_what_visa_sees_when_absent():
    with pytest.raises(PowerMeterError, match="0x1313.*ASRL1::INSTR"):
        find_thorlabs_resource(["ASRL1::INSTR"])


def test_thorlabs_meter_configures_identifies_and_reads_milliwatts():
    instrument = FakeInstrument(power_w="1.5E-03")
    rm = FakeResourceManager(["USB0::0x1313::0x8072::P2001234::INSTR"], instrument)
    meter = ThorlabsPowerMeter(resource_manager=rm, averaging=7)

    assert rm.opened == "USB0::0x1313::0x8072::P2001234::INSTR"
    assert instrument.timeout == METER_TIMEOUT_MS
    assert meter.info.meter == "Thorlabs PM100USB P2001234"
    assert meter.info.sensor == "S170C 220304512"
    assert meter.info.max_power_mw == pytest.approx(500.0)
    assert meter.info.wavelength_range_nm == (400.0, 1100.0)
    assert meter.info.settle_s == SETTLE_S_BY_MODEL["PM100"]
    assert instrument.writes == ["SENS:POW:UNIT W", "SENS:AVER:COUN 7", "SENS:POW:RANG:AUTO ON"]
    meter.set_wavelength(405)
    assert instrument.writes[-1] == "SENS:CORR:WAV 405"
    assert meter.read_mw() == pytest.approx(1.5)
    meter.close()
    assert instrument.closed


def test_a_meter_that_does_not_report_its_sensor_limits_gets_none_and_a_slow_settle():
    answers = {"*IDN?": "Thorlabs,PM999,X1,1.0\n", "SYST:SENS:IDN?": "S999,1\n"}
    meter = _meter(FakeInstrument(answers=answers))
    assert meter.info.max_power_mw is None and meter.info.wavelength_range_nm is None
    assert meter.info.settle_s == SETTLE_S_UNKNOWN_MODEL and not meter.info.validated


def test_a_pm16_121_that_answers_only_the_basic_commands_connects_with_its_known_limits():
    # the commands the pre-2026-10 tools/PM16.py used with a PM16; nothing about the built-in sensor
    meter = _meter(FakeInstrument(answers={"*IDN?": "Thorlabs,PM16-121,M00412345,1.2.0\n"}))
    assert meter.info.meter == "Thorlabs PM16-121 M00412345" and meter.info.sensor == "PM16-121 built-in"
    assert (meter.info.max_power_mw, meter.info.wavelength_range_nm) == KNOWN_SENSOR_LIMITS["PM16-121"]
    assert meter.info.settle_s == SETTLE_S_BY_MODEL["PM16"]


def test_the_lower_of_the_reported_and_the_known_maximum_applies():
    answers = {"*IDN?": "Thorlabs,PM16-121,M1,1.2.0\n", "SENS:POW:RANG:UPP? MAX": "1.0E+00\n"}
    assert _meter(FakeInstrument(answers=answers)).info.max_power_mw == 500.0


@pytest.mark.parametrize("answer", ["9.9E37", "NAN"])
def test_thorlabs_meter_overrange_raises(answer):
    with pytest.raises(PowerMeterOverrange):
        _meter(FakeInstrument(power_w=answer)).read_mw()


def test_a_read_that_times_out_or_is_garbled_is_a_power_meter_error():
    with pytest.raises(PowerMeterError, match="did not answer 'MEAS:POW\\?'"):
        _meter(FakeInstrument(fail={"MEAS:POW?"})).read_mw()
    with pytest.raises(PowerMeterError, match="answered 'garbage'"):
        _meter(FakeInstrument(power_w="garbage")).read_mw()


def test_simulated_meter_reads_the_commanded_light():
    state = {"on": False, "x": 0.6}
    meter = SimulatedPowerMeter(lambda: (state["on"], state["x"]), noise_fraction=0.0, ambient_mw=0.0)
    meter.set_wavelength(488)
    assert meter.read_mw() == 0.0
    state["on"] = True
    assert meter.read_mw() == pytest.approx(300.0)
    assert meter.info.max_power_mw == 500.0 and meter.info.settle_s == 0.0


def test_simulated_source_models():
    assert float(simulated_laser_mw(0.18)) == 0.0
    assert float(simulated_laser_mw(0.39)) == pytest.approx(150.0)
    assert float(simulated_laser_mw(1.0)) == pytest.approx(450.0)  # driver limit
    assert float(simulated_led_mw(0.0)) == 0.0
    assert float(simulated_led_mw(0.492)) == pytest.approx(312.0)
    assert float(simulated_led_mw(0.6)) == pytest.approx(290.0)
    assert float(simulated_led_mw(1.0)) == pytest.approx(290.0)


class _UnopenableResourceManager(FakeResourceManager):
    def open_resource(self, name):
        raise OSError("VI_ERROR_RSRC_BUSY")


def test_every_visa_failure_is_a_power_meter_error():
    with pytest.raises(PowerMeterError, match="could not open"):
        ThorlabsPowerMeter(
            resource="USB0::0x1313::0x8072::X::INSTR", resource_manager=_UnopenableResourceManager([], None)
        )
    with pytest.raises(PowerMeterError, match="did not take 'SENS:POW:UNIT W'"):
        _meter(FakeInstrument(fail_writes=("SENS:POW:UNIT",)))
    with pytest.raises(PowerMeterError, match="did not take 'SENS:CORR:WAV 405'"):
        _meter(FakeInstrument(fail_writes=("SENS:CORR:WAV",))).set_wavelength(405)
    with pytest.raises(PowerMeterError, match="closing"):
        _meter(FakeInstrument(fail_close=True)).close()


@pytest.mark.parametrize(
    "platform, steps",
    [
        ("linux", ["pip install pyvisa pyvisa-py pyusb", "99-thorlabs-pm16.rules"]),
        ("darwin", ["brew install libusb", "pip install pyvisa pyvisa-py pyusb"]),
        ("win32", ["pip install pyvisa pyvisa-py pyusb", "libusb-1.0.dll", "Zadig", "WinUSB"]),
    ],
)
def test_a_missing_pyvisa_names_the_setup_steps_for_this_os(monkeypatch, platform, steps):
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setitem(sys.modules, "pyvisa", None)  # `import pyvisa` raises ImportError
    with pytest.raises(PowerMeterError) as raised:
        ThorlabsPowerMeter()
    message = str(raised.value)
    assert message.startswith("pyvisa is not installed") and SETUP_DOC in message
    for step in steps:
        assert step in message


def test_a_meter_linux_cannot_see_or_open_points_at_the_udev_rule(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(PowerMeterError, match="0x1313.*ASRL1::INSTR.*99-thorlabs-pm16.rules"):
        find_thorlabs_resource(["ASRL1::INSTR"])
    with pytest.raises(PowerMeterError, match="could not open.*99-thorlabs-pm16.rules"):
        ThorlabsPowerMeter(
            resource="USB0::0x1313::0x807B::X::INSTR", resource_manager=_UnopenableResourceManager([], None)
        )


def test_the_udev_rule_and_the_setup_doc_the_messages_name_ship_with_squid():
    rule = (SOFTWARE_DIR / UDEV_RULE).read_text()
    assert 'ATTRS{idVendor}=="1313"' in rule and 'ATTRS{idProduct}=="807b"' in rule
    assert (SOFTWARE_DIR / SETUP_DOC).is_file()


PM16_ANSWERS = {"*IDN?": "Thorlabs,PM16-121,250328410,1.6.0\n"}


def test_a_pm16_is_sent_no_averaging_command():
    # the PM16-121 on the bench (fw 1.6.0) answers -113 to SENS:AVER n, SENS:AVER:COUN n and SENS:AVER:COUNT n
    instrument = FakeInstrument(answers=PM16_ANSWERS, reject_writes=("SENS:AVER",))
    _meter(instrument)
    assert not [w for w in instrument.writes if w.startswith("SENS:AVER")]
    assert instrument.writes == ["SENS:POW:UNIT W", "SENS:POW:RANG:AUTO ON"]


@pytest.mark.parametrize("rejected", ["SENS:POW:UNIT W", "SENS:POW:RANG:AUTO ON"])
def test_a_setting_the_meter_rejects_fails_the_connect(rejected):
    # readings in the wrong unit, or on a fixed range, would make a wrong calibration without any error
    with pytest.raises(PowerMeterError, match=f"rejected '{rejected}'.*-113"):
        _meter(FakeInstrument(reject_writes=(rejected,)))


def test_a_wavelength_the_meter_rejects_is_an_error():
    meter = _meter(FakeInstrument(answers=PM16_ANSWERS))
    meter._inst.reject_writes = ("SENS:CORR:WAV",)
    with pytest.raises(PowerMeterError, match="rejected 'SENS:CORR:WAV 405'"):
        meter.set_wavelength(405)


def test_errors_already_queued_are_not_blamed_on_a_setting():
    # an earlier session's errors, and the -113 a PM16 queues for each sensor query it does not answer
    instrument = FakeInstrument(answers=PM16_ANSWERS, errors=['-113,"Undefined header"'] * 3)
    meter = _meter(instrument)
    meter.set_wavelength(405)
    assert instrument.writes[-1] == "SENS:CORR:WAV 405"


def test_the_resource_name_pyvisa_py_gives_the_bench_pm16_is_found():
    # pyvisa-py names USB resources with decimal IDs and the interface number: 4883 = 0x1313, 32891 = 0x807B
    bench = "USB0::4883::32891::250328410::0::INSTR"
    assert find_thorlabs_resource(["ASRL3::INSTR", bench]) == bench


def test_holding_a_range_stops_auto_ranging_on_the_range_that_holds_the_limit():
    answers = dict(PM16_ANSWERS, **{"SENS:POW:RANG:UPP? MAX": "1.197352E+00\n", "SENS:POW:RANG:UPP?": "1.197352E+00\n"})
    instrument = FakeInstrument(answers=answers)
    meter = _meter(instrument)
    assert meter.hold_range(500.0) == pytest.approx(1197.352)  # the meter rounds up to its next range
    assert instrument.writes[-2:] == ["SENS:POW:RANG:AUTO OFF", "SENS:POW:RANG:UPP 0.5"]
    meter.release_range()
    assert instrument.writes[-1] == "SENS:POW:RANG:AUTO ON"


def test_a_limit_above_the_top_range_holds_the_top_range():
    # the top range depends on the wavelength: 0.703 W at 730 nm on the bench PM16-121
    answers = dict(PM16_ANSWERS, **{"SENS:POW:RANG:UPP? MAX": "7.030021E-01\n"})
    instrument = FakeInstrument(answers=answers)
    assert _meter(instrument).hold_range(900.0) == pytest.approx(703.0021)
    assert instrument.writes[-1] == "SENS:POW:RANG:UPP 0.703002"


def test_the_simulated_meter_overranges_above_the_range_it_holds():
    state = {"on": True, "x": 0.6}
    meter = SimulatedPowerMeter(lambda: (state["on"], state["x"]), noise_fraction=0.0, ambient_mw=0.0)
    assert meter.hold_range(10.0) == 11.93
    with pytest.raises(PowerMeterOverrange):
        meter.read_mw()  # 300 mW
    meter.release_range()
    assert meter.read_mw() == pytest.approx(300.0)
