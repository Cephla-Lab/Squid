"""squid/power_meter.py: finding a Thorlabs meter, talking SCPI to it (with its sensor's limits), and the simulated
meter."""

import pytest

from squid.power_meter import (
    METER_TIMEOUT_MS,
    SETTLE_S_BY_MODEL,
    SETTLE_S_UNKNOWN_MODEL,
    KNOWN_SENSOR_LIMITS,
    PowerMeterError,
    PowerMeterOverrange,
    SimulatedPowerMeter,
    ThorlabsPowerMeter,
    find_thorlabs_resource,
    simulated_laser_mw,
    simulated_led_mw,
)

ANSWERS = {
    "*IDN?": "Thorlabs,PM100USB,P2001234,1.7.0\n",
    "SYST:SENS:IDN?": "S170C,220304512,24-Mar-2025,1,18,289\n",
    "SENS:POW:RANG:UPP? MAX": "5.000000E-01\n",
    "SENS:CORR:WAV? MIN": "4.000000E+02\n",
    "SENS:CORR:WAV? MAX": "1.100000E+03\n",
}


class FakeInstrument:
    def __init__(self, power_w="1.5E-03", answers=None, fail=(), fail_writes=(), fail_close=False):
        self.power_w = power_w
        self.answers = dict(ANSWERS if answers is None else answers)
        self.fail = set(fail)
        self.fail_writes = tuple(fail_writes)
        self.fail_close = fail_close
        self.writes = []
        self.closed = False
        self.timeout = None

    def query(self, command):
        if command in self.fail or (command not in self.answers and command != "MEAS:POW?"):
            raise OSError(f"VI_ERROR_TMO ({command})")  # what pyvisa raises looks like this
        return self.answers.get(command, f"{self.power_w}\n")

    def write(self, command):
        if command.startswith(self.fail_writes) if self.fail_writes else False:
            raise OSError(f"VI_ERROR_CONN_LOST ({command})")
        self.writes.append(command)

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
