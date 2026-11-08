from unittest.mock import MagicMock

from control.piezo import PiezoStage
from control.widgets import PiezoWidget


def test_the_piezo_widget_opens_at_the_piezo_position_without_moving_it(qtbot):
    """A warm start leaves the piezo where the last session left it, not at OBJECTIVE_PIEZO_HOME_UM."""
    piezo = MagicMock(spec=PiezoStage)
    piezo.position = 150.0
    piezo.range_um = 300
    piezo._home_position_um = 20
    widget = PiezoWidget(piezo)
    qtbot.addWidget(widget)
    assert widget.spinBox.value() == 150.0 and widget.slider.value() == 15000
    piezo.move_to.assert_not_called()
    widget.move_up_btn.click()  # one 1 um step from where the piezo is
    piezo.move_to.assert_called_once_with(151.0)
