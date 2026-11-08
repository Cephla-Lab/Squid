"""The stop sign before XY homing on a piezo-only system (Z_MOTOR_CONFIG = PIEZO).

Such a system has no Z motor: the objective sits on a piezo, so the software cannot move it clear of the sample holder
before the stage drives to its limit switches. A person lowers the nosepiece with the focus knob and confirms at the stop
sign first. main_hcs.py asks before the startup home, where declining quits Squid before anything moves; Stage Utils
asks before Home X / Home Y, where declining cancels that home.
"""

from qtpy.QtCore import QPointF, QRectF, Qt
from qtpy.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from qtpy.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

import control._def

STOP_RED = QColor(200, 16, 46)

# The sign on a 64-unit square: the octagon, and the white rim just inside it.
_OCTAGON = [(19, 1), (45, 1), (63, 19), (63, 45), (45, 63), (19, 63), (1, 45), (1, 19)]
_RIM = [(20.5, 4.5), (43.5, 4.5), (59.5, 20.5), (59.5, 43.5), (43.5, 59.5), (20.5, 59.5), (4.5, 43.5), (4.5, 20.5)]


def startup_needs_nosepiece_check(skip_init: bool) -> bool:
    """True when startup will send the stage to its X and Y limit switches (Microscope.home_xyz) on a piezo-only
    system. A restart (skip_init) does not home."""
    return (
        not skip_init and control._def.IS_PIEZO_ONLY and control._def.HOMING_ENABLED_X and control._def.HOMING_ENABLED_Y
    )


class StopSign(QWidget):
    """A red octagonal stop sign. Drawn rather than loaded, so it is sharp at any display scaling and the same on every
    platform (QMessageBox's critical icon is not)."""

    def __init__(self, size: int, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.scale(self.width() / 64, self.height() / 64)
        painter.setPen(Qt.NoPen)
        painter.setBrush(STOP_RED)
        painter.drawPolygon(QPolygonF([QPointF(x, y) for x, y in _OCTAGON]))
        painter.setPen(QPen(Qt.white, 2.5))
        painter.setBrush(Qt.NoBrush)
        painter.drawPolygon(QPolygonF([QPointF(x, y) for x, y in _RIM]))
        font = QFont("Arial")
        font.setBold(True)
        font.setPixelSize(16)
        painter.setFont(font)
        painter.drawText(QRectF(0, 0, 64, 64), Qt.AlignCenter, "STOP")
        painter.end()


class NosepieceLoweredDialog(QDialog):
    """The stop sign, what to do, and two buttons: decline_text (reject) and Home (accept). Enter and Esc decline
    wherever the focus is, so a stray key never starts a home."""

    def __init__(self, parent=None, decline_text: str = "Cancel"):
        super().__init__(parent)
        self.setWindowTitle("Lower the nosepiece")
        self.setMinimumWidth(460)

        heading = QLabel("Lower the nosepiece before the stage homes")
        font = heading.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 2)
        heading.setFont(font)
        heading.setAlignment(Qt.AlignCenter)

        text = QLabel(
            "This system has no Z motor, so Squid cannot move the objective out of the way. Turn the focus knob to "
            "lower the nosepiece clear of the sample holder. The stage will then move to its limit switches."
        )
        text.setWordWrap(True)
        text.setAlignment(Qt.AlignCenter)

        self.decline_button = QPushButton(decline_text)
        self.decline_button.setDefault(True)
        self.decline_button.clicked.connect(self.reject)
        self.home_button = QPushButton("Nosepiece lowered – Home")
        self.home_button.setAutoDefault(False)  # focused, it would otherwise take Enter from the default button
        self.home_button.clicked.connect(self.accept)

        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(self.decline_button)
        buttons.addWidget(self.home_button)
        buttons.addStretch()

        layout = QVBoxLayout(self)
        layout.addWidget(StopSign(112), alignment=Qt.AlignHCenter)
        layout.addWidget(heading)
        layout.addWidget(text)
        layout.addLayout(buttons)
        self.decline_button.setFocus()


def confirm_nosepiece_lowered(parent, decline_text: str) -> bool:
    """Ask at the stop sign. True only if the person clicked Home."""
    dialog = NosepieceLoweredDialog(parent, decline_text=decline_text)
    accepted = dialog.exec_() == QDialog.Accepted
    dialog.deleteLater()
    return accepted
