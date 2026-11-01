"""find_controller_ports(): which serial port is the Cephla controller.

The bench on 2026-10-28 opened a Xeryon stage controller's COM port as the microcontroller: on
Windows the old rule took any port whose driver is Microsoft's usbser, and the stage was listed
first. The Teensy's USB vendor id is what identifies the controller.
"""

import logging
import types

from control.microcontroller import TEENSY_USB_VID, find_controller_ports


def port(device, vid=None, manufacturer=None, serial_number=None, description=""):
    return types.SimpleNamespace(
        device=device, vid=vid, manufacturer=manufacturer, serial_number=serial_number, description=description
    )


TEENSY_WIN = port("COM4", vid=TEENSY_USB_VID, manufacturer="Microsoft", serial_number="16818920")
XERYON_WIN = port("COM5", vid=0x0403, manufacturer="Microsoft", serial_number="FT9ABC")  # FTDI, usbser driver
TEENSY_LINUX = port("/dev/ttyACM1", vid=TEENSY_USB_VID, manufacturer="Teensyduino", serial_number="16818920")
OTHER_LINUX = port("/dev/ttyACM0", vid=0x0403, manufacturer="FTDI", serial_number="FT9ABC")


def test_the_teensy_vendor_id_wins_over_another_usbser_instrument_on_windows():
    assert find_controller_ports([XERYON_WIN, TEENSY_WIN], platform="win32") == ["COM4"]


def test_the_teensyduino_manufacturer_string_identifies_it_on_linux_and_macos():
    assert find_controller_ports([OTHER_LINUX, TEENSY_LINUX], platform="linux") == ["/dev/ttyACM1"]
    assert find_controller_ports([OTHER_LINUX, TEENSY_LINUX], platform="darwin") == ["/dev/ttyACM1"]


def test_a_serial_number_pins_the_port_regardless_of_vendor():
    assert find_controller_ports([TEENSY_WIN, XERYON_WIN], sn="FT9ABC", platform="win32") == ["COM5"]
    assert find_controller_ports([TEENSY_WIN, XERYON_WIN], sn="nope", platform="win32") == []


def test_an_arduino_due_is_found_by_its_description():
    due = port("/dev/ttyACM2", vid=0x2341, description="Arduino Due")
    assert find_controller_ports([TEENSY_LINUX, due], version="Arduino Due", platform="linux") == ["/dev/ttyACM2"]


def test_windows_falls_back_to_usbser_ports_only_without_a_teensy_and_says_so(caplog):
    with caplog.at_level(logging.WARNING):
        assert find_controller_ports([XERYON_WIN], platform="win32") == ["COM5"]
    assert "COM5" in caplog.text and "serial number" in caplog.text


def test_no_fallback_outside_windows():
    assert find_controller_ports([OTHER_LINUX], platform="linux") == []


def test_nothing_plugged_in_finds_nothing():
    assert find_controller_ports([], platform="win32") == []
