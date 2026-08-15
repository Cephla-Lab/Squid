"""WellplateFormatWidget: cancel-revert and single-emission format selection.

Two long-standing dialog bugs are pinned here:

* Cancelling the calibration dialog used to strand the dropdown on
  "calibrate format...": wellplateChanged overwrote self.wellplate_format with
  "custom" BEFORE the Rejected branch read it back, so findData("custom")
  found the calibrate item itself. (A second, competing revert inside
  WellplateCalibration.reject() parsed the sample name to an int that
  findData() could never match - it is deleted outright.)

* Re-selecting a format after calibration used populate_combo_box() with
  signals live. populate starts with comboBox.clear(), so currentIndexChanged
  fired three times (-1, 0, target) and the index-0 emission momentarily
  reconfigured the app for whatever format is first in the dict.
"""

from unittest.mock import MagicMock, patch

from qtpy.QtWidgets import QDialog

import control._def as _def
import control.widgets


def _make_widget(qtbot):
    widget = control.widgets.WellplateFormatWidget(
        stage=MagicMock(), navigationViewer=MagicMock(), streamHandler=MagicMock(), liveController=MagicMock()
    )
    qtbot.addWidget(widget)
    return widget


def _select_data(widget, data):
    index = widget.comboBox.findData(data)
    assert index >= 0, f"combo has no item with data {data!r}"
    widget.comboBox.setCurrentIndex(index)


def test_cancelling_calibration_restores_previous_format(qtbot):
    widget = _make_widget(qtbot)
    _select_data(widget, "96 well plate")
    assert widget.wellplate_format == "96 well plate"

    with patch.object(control.widgets, "WellplateCalibration") as dialog_cls:
        dialog_cls.return_value.exec_.return_value = QDialog.Rejected
        _select_data(widget, "custom")

    assert widget.wellplate_format == "96 well plate"
    assert widget.comboBox.currentData() == "96 well plate"


def test_cancelling_twice_still_restores(qtbot):
    """The restore must be repeatable - the old bug only bit on the first cancel."""
    widget = _make_widget(qtbot)
    _select_data(widget, "384 well plate")

    with patch.object(control.widgets, "WellplateCalibration") as dialog_cls:
        dialog_cls.return_value.exec_.return_value = QDialog.Rejected
        _select_data(widget, "custom")
        _select_data(widget, "custom")

    assert widget.comboBox.currentData() == "384 well plate"


def test_select_format_silently_emits_exactly_once(qtbot):
    widget = _make_widget(qtbot)
    _select_data(widget, "96 well plate")

    emissions = []
    widget.signalWellplateSettings.connect(lambda *args: emissions.append(args))

    index = widget.select_format_silently("384 well plate")

    assert index >= 0
    assert len(emissions) == 1, f"expected one emission, saw {[e[0] for e in emissions]}"
    assert emissions[0][0] == "384 well plate"
    assert widget.wellplate_format == "384 well plate"
    assert widget.comboBox.currentData() == "384 well plate"


def test_select_format_silently_never_emits_the_first_dict_entry(qtbot):
    """The spurious index-0 emission is the bug this helper exists to prevent."""
    widget = _make_widget(qtbot)
    first_format = next(iter(_def.WELLPLATE_FORMAT_SETTINGS))
    target = "1536 well plate"
    assert first_format != target

    _select_data(widget, "96 well plate")
    emissions = []
    widget.signalWellplateSettings.connect(lambda *args: emissions.append(args[0]))

    widget.select_format_silently(target)

    assert first_format not in emissions
    assert emissions == [target]
