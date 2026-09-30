"""The laser-AF per-channel Z-offset checkbox must never become a window of its own.

_ApplyChannelOffsetMixin builds the checkbox unconditionally, but the host widgets
add it to a layout only when SUPPORT_LASER_AUTOFOCUS is true. On a machine without
laser AF it therefore stays parentless, and restoring a cached ``laser_af: true``
toggled it visible: Qt showed it as a stray top-level "Per-channel Z-offset" window
that outlived the main window and kept the application from quitting.
"""

from unittest.mock import MagicMock

from qtpy.QtWidgets import QApplication, QCheckBox, QVBoxLayout, QWidget

import control.widgets
from control.widgets import _ApplyChannelOffsetMixin


class Host(_ApplyChannelOffsetMixin, QWidget):
    """The multipoint widgets' wiring, reduced to what the mixin touches: the
    checkbox joins a layout only when laser AF is supported."""

    def __init__(self, laser_af_supported: bool):
        super().__init__()
        self.multipointController = MagicMock()
        self.checkbox_withReflectionAutofocus = QCheckBox("laser AF")
        self._create_apply_channel_offset_checkbox()
        if laser_af_supported:
            layout = QVBoxLayout(self)
            layout.addWidget(self.checkbox_withReflectionAutofocus)
            layout.addWidget(self.checkbox_applyChannelOffset)
        self.checkbox_withReflectionAutofocus.toggled.connect(self._update_apply_channel_offset_enable_state)
        self._update_apply_channel_offset_enable_state(self.checkbox_withReflectionAutofocus.isChecked())


def _stray_checkbox_windows():
    return [w for w in QApplication.topLevelWidgets() if isinstance(w, QCheckBox) and w.isVisible()]


def test_without_laser_af_the_checkbox_never_shows(qtbot, monkeypatch):
    monkeypatch.setattr(control.widgets, "SUPPORT_LASER_AUTOFOCUS", False)
    host = Host(laser_af_supported=False)
    qtbot.addWidget(host)
    qtbot.addWidget(host.checkbox_applyChannelOffset)  # parentless: clean it up too
    host.show()

    host.checkbox_withReflectionAutofocus.setChecked(True)  # what a cached laser_af: true does at startup

    assert not host.checkbox_applyChannelOffset.isVisible()
    assert _stray_checkbox_windows() == []
    host.close()
    assert _stray_checkbox_windows() == []  # nothing left to keep the app alive


def test_with_laser_af_the_checkbox_follows_the_laser_af_box(qtbot, monkeypatch):
    monkeypatch.setattr(control.widgets, "SUPPORT_LASER_AUTOFOCUS", True)
    host = Host(laser_af_supported=True)
    qtbot.addWidget(host)
    host.show()
    checkbox = host.checkbox_applyChannelOffset
    assert not checkbox.isVisible()

    host.checkbox_withReflectionAutofocus.setChecked(True)
    assert checkbox.isVisible()
    assert not checkbox.isWindow()  # inside the host, not a window of its own

    host.checkbox_withReflectionAutofocus.setChecked(False)
    assert not checkbox.isVisible()
