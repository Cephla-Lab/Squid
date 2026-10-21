"""Tests for the VersaLase status tab."""

from control.illumination_versalase import HeadStatus
from control.versalase_widget import AMBER, GREEN, GREY, RED, VersaLaseWidget


def _status(wavelength, **overrides):
    values = dict(
        wavelength=wavelength,
        serial_number=f"VL{wavelength}",
        max_power_mw=100.8,
        emission=True,
        modulation=False,
        set_power_mw=20.2,
        measured_power_mw=0.1,
        base_plate_temp_c=27.0,
        optical_block_temp_c=25.1,
        interlock_closed=True,
        fault_code=0,
        fault_description="",
    )
    values.update(overrides)
    return HeadStatus(**values)


class FakeVersaLase:
    def __init__(self, statuses=None, error=None):
        self.statuses = statuses or []
        self.error = error
        self.calls = 0

    def get_status(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.statuses


def _show_status(qtbot, statuses):
    widget = VersaLaseWidget(FakeVersaLase(statuses))
    qtbot.addWidget(widget)
    widget.poll()
    qtbot.waitUntil(lambda: len(widget._head_lines) == len(statuses))
    return widget


def _dot_color(widget, row):
    return widget._head_dots[row].styleSheet().split("color:")[1].strip(" ;")


def test_one_line_per_head(qtbot):
    widget = _show_status(qtbot, [_status(405), _status(639, emission=False, max_power_mw=160.0)])

    line_405 = widget._head_lines[0].text()
    assert line_405.startswith("405 nm ON  CW ")
    assert "20.2/100.8 mW" in line_405 and "out  0.1 " in line_405
    assert "27.0/25.1 °C" in line_405 and line_405.endswith("OK")
    assert _dot_color(widget, 0) == GREEN

    assert widget._head_lines[1].text().startswith("639 nm OFF")
    assert _dot_color(widget, 1) == GREY
    assert widget._summary_label.text() == "VersaLase: Emission off"


def test_hours_are_not_shown(qtbot):
    widget = _show_status(qtbot, [_status(405)])
    assert "h" not in widget._head_lines[0].text().replace("nm", "")
    assert "hour" not in widget._head_lines[0].toolTip().lower()


def test_fault_and_open_interlock_are_red(qtbot):
    widget = _show_status(
        qtbot,
        [
            _status(488, fault_code=16, fault_description="INTERLOCK OPEN", interlock_closed=False),
            _status(561, fault_code=128, fault_description="DIODE TEMPERATURE FAULT"),
        ],
    )

    assert widget._head_lines[0].text().endswith("INTERLOCK OPEN")
    assert widget._head_lines[1].text().endswith("DIODE TEMPERATURE FAULT")
    assert _dot_color(widget, 0) == RED and _dot_color(widget, 1) == RED
    assert widget._summary_label.text() == "VersaLase: Fault"


def test_warmup_is_amber(qtbot):
    widget = _show_status(qtbot, [_status(405, fault_code=2, fault_description="WARMUP")])
    assert _dot_color(widget, 0) == AMBER
    assert widget._head_lines[0].text().endswith("WARMUP")


def test_tooltip_has_the_details(qtbot):
    widget = _show_status(qtbot, [_status(405)])
    tooltip = widget._head_lines[0].toolTip()
    assert "S/N VL405" in tooltip and "Base plate 27.0 °C, optical block 25.1 °C" in tooltip


def test_no_polling_during_acquisition(qtbot):
    laser = FakeVersaLase([_status(405)])
    widget = VersaLaseWidget(laser, is_busy=lambda: True)
    qtbot.addWidget(widget)

    widget.poll()

    assert laser.calls == 0
    assert widget._paused_label.text() == "Paused during acquisition"


def test_lost_contact_shows_banner(qtbot):
    widget = VersaLaseWidget(FakeVersaLase(error=RuntimeError("No reply to '?PUL'")))
    qtbot.addWidget(widget)

    widget.poll()
    qtbot.waitUntil(lambda: not widget._banner.isHidden())

    assert "No reply to '?PUL'" in widget._banner.text()
    assert widget._summary_label.text() == "VersaLase: Disconnected"


def test_polls_only_while_visible(qtbot):
    laser = FakeVersaLase([_status(405)])
    widget = VersaLaseWidget(laser)
    qtbot.addWidget(widget)
    assert laser.calls == 0 and not widget._timer.isActive()

    widget.show()
    qtbot.waitUntil(lambda: laser.calls == 1)
    assert widget._timer.isActive()

    widget.hide()
    assert not widget._timer.isActive()
