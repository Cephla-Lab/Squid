"""Tests for the VersaLase status tab."""

from control.illumination_versalase import HeadStatus
from control.versalase_widget import COLUMNS, RED, VersaLaseWidget


def _status(wavelength, **overrides):
    values = dict(
        wavelength=wavelength,
        serial_number=f"VL{wavelength}",
        max_power_mw=100.8,
        emission=True,
        modulation=True,
        set_power_mw=25.0,
        measured_power_mw=0.1,
        base_plate_temp_c=27.0,
        optical_block_temp_c=25.1,
        interlock_closed=True,
        fault_code=0,
        fault_description="",
        hours=1604.7,
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


def _cell(widget, row, column_name):
    return widget._table.item(row, COLUMNS.index(column_name))


def test_table_shows_one_row_per_head(qtbot):
    laser = FakeVersaLase([_status(405), _status(639, emission=False, modulation=False)])
    widget = VersaLaseWidget(laser)
    qtbot.addWidget(widget)

    widget.poll()
    qtbot.waitUntil(lambda: widget._table.rowCount() == 2)

    assert _cell(widget, 0, "Laser").text() == "405 nm"
    assert _cell(widget, 0, "Mode").text() == "TTL"
    assert _cell(widget, 0, "Set / max (mW)").text() == "25.0 / 100.8"
    assert _cell(widget, 0, "Status").text() == "OK"
    assert _cell(widget, 1, "Emission").text() == "Off"
    assert _cell(widget, 1, "Mode").text() == "CW"
    assert widget._summary_label.text() == "VersaLase: Emission off"


def test_fault_is_shown_in_red(qtbot):
    laser = FakeVersaLase([_status(488, fault_code=16, fault_description="INTERLOCK OPEN", interlock_closed=False)])
    widget = VersaLaseWidget(laser)
    qtbot.addWidget(widget)

    widget.poll()
    qtbot.waitUntil(lambda: widget._table.rowCount() == 1)

    assert _cell(widget, 0, "Status").text() == "INTERLOCK OPEN"
    assert _cell(widget, 0, "Status").foreground().color().name() == RED
    assert _cell(widget, 0, "Interlock").text() == "OPEN"
    assert widget._summary_label.text() == "VersaLase: Fault"


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
