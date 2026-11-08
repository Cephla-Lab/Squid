"""The stop sign before XY homing on a piezo-only system (Z_MOTOR_CONFIG = PIEZO).

Such a system has no Z motor: the objective sits on a piezo, so the software cannot move it clear of the sample holder
before the stage drives to its limit switches. A person lowers the nosepiece with the focus knob and confirms at the stop
sign first. At startup the Microscope asks, through the prompt main_hcs.py gives it, before anything moves (only when
startup will home X and Y); declining quits Squid. Home stage and Stage Utils' Home X / Home Y ask too, where declining
cancels.
"""

from qtpy.QtCore import QPointF, QRectF, Qt
from qtpy.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from qtpy.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

STOP_RED = QColor(200, 16, 46)

# The sign on a 64-unit square: the octagon, and the white rim just inside it.
_OCTAGON = [(19, 1), (45, 1), (63, 19), (63, 45), (45, 63), (19, 63), (1, 45), (1, 19)]
_RIM = [(20.5, 4.5), (43.5, 4.5), (59.5, 20.5), (59.5, 43.5), (43.5, 59.5), (20.5, 59.5), (4.5, 43.5), (4.5, 20.5)]


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
    wherever the focus is, so a stray key never starts a home. stage_homes False: only an objective changer moves (a
    startup that does not home the stage), and the sign says so."""

    def __init__(self, parent=None, decline_text: str = "Cancel", stage_homes: bool = True):
        super().__init__(parent)
        self.setWindowTitle("Lower the nosepiece")
        self.setMinimumWidth(460)

        moving = "the stage homes" if stage_homes else "the objective changer moves"
        self.heading = QLabel(f"Lower the nosepiece before {moving}")
        font = self.heading.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 2)
        self.heading.setFont(font)
        self.heading.setAlignment(Qt.AlignCenter)

        then = (
            "The stage will then move to its limit switches."
            if stage_homes
            else "The objective changer will then home and turn to the default objective."
        )
        self.text = QLabel(
            "This system has no Z motor, so Squid cannot move the objective out of the way. Turn the focus knob to "
            f"lower the nosepiece clear of the sample holder. {then}"
        )
        self.text.setWordWrap(True)
        self.text.setAlignment(Qt.AlignCenter)

        self.decline_button = QPushButton(decline_text)
        self.decline_button.setDefault(True)
        self.decline_button.clicked.connect(self.reject)
        self.home_button = QPushButton(f"Nosepiece lowered – {'Home' if stage_homes else 'Continue'}")
        self.home_button.setAutoDefault(False)  # focused, it would otherwise take Enter from the default button
        self.home_button.clicked.connect(self.accept)

        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(self.decline_button)
        buttons.addWidget(self.home_button)
        buttons.addStretch()

        layout = QVBoxLayout(self)
        layout.addWidget(StopSign(112), alignment=Qt.AlignHCenter)
        layout.addWidget(self.heading)
        layout.addWidget(self.text)
        layout.addLayout(buttons)
        self.decline_button.setFocus()


def confirm_nosepiece_lowered(parent, decline_text: str, stage_homes: bool = True) -> bool:
    """Ask at the stop sign. True only if the person clicked Home (Continue)."""
    dialog = NosepieceLoweredDialog(parent, decline_text=decline_text, stage_homes=stage_homes)
    accepted = dialog.exec_() == QDialog.Accepted
    dialog.deleteLater()
    return accepted
