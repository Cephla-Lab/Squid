"""The LDI line that each channel wavelength uses comes from LDI_CHANNEL_MAPPINGS, which an .ini can set."""

import control._def
from control._def import conf_attribute_reader
from control.serial_peripherals import LDI_Simulation, ldi_channel_mappings


def test_the_default_sends_488_to_the_470_line():
    mappings = LDI_Simulation().channel_mappings
    assert mappings[470] == 470 and mappings[488] == 470 and mappings[561] == 555


def test_an_ini_value_for_an_ldi_with_a_488_line(monkeypatch):
    value = conf_attribute_reader('{"405": 405, "470": 488, "488": 488, "561": 555, "638": 640}  # 488 nm LDI')
    monkeypatch.setattr(control._def, "LDI_CHANNEL_MAPPINGS", value)
    assert ldi_channel_mappings() == {405: 405, 470: 488, 488: 488, 561: 555, 638: 640}
    assert LDI_Simulation().channel_mappings[488] == 488


def test_shut_down_turns_off_each_line_once(monkeypatch):
    monkeypatch.setattr(control._def, "LDI_CHANNEL_MAPPINGS", {"470": 488, "488": 488, "561": 555})
    ldi = LDI_Simulation()
    calls = []
    monkeypatch.setattr(ldi, "set_intensity", lambda channel, intensity: calls.append(("intensity", channel)))
    monkeypatch.setattr(ldi, "set_shutter_state", lambda channel, on: calls.append(("shutter", channel)))
    ldi.shut_down()
    assert sorted(calls) == [("intensity", 488), ("intensity", 555), ("shutter", 488), ("shutter", 555)]
