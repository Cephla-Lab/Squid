from control.widgets.tracking._common import *
from qtpy.QtCore import QPointF, QRectF
from qtpy.QtGui import QPaintEvent, QMouseEvent


class Joystick(QWidget):
    joystickMoved = Signal(float, float)  # Emits x and y values between -1 and 1

    inner_radius: int
    max_distance: int
    outer_radius: int
    current_x: float
    current_y: float
    is_pressed: bool
    timer: QTimer

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(200, 200)
        self.inner_radius = 40
        self.max_distance = self.width() // 2 - self.inner_radius
        self.outer_radius = int(self.width() * 3 / 8)
        self.current_x = 0.0
        self.current_y = 0.0
        self.is_pressed = False
        self.timer = QTimer(self)

    def paintEvent(self, event: Optional[QPaintEvent]) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Calculate the painting area
        paint_rect = QRectF(0, 0, 200, 200)

        # Draw outer circle
        painter.setBrush(QColor(230, 230, 230))  # Light grey fill
        painter.setPen(QPen(QColor(100, 100, 100), 2))  # Dark grey outline
        painter.drawEllipse(paint_rect.center(), self.outer_radius, self.outer_radius)

        # Draw inner circle (joystick position)
        painter.setBrush(QColor(100, 100, 100))
        painter.setPen(Qt.PenStyle.NoPen)
        joystick_x = paint_rect.center().x() + self.current_x * self.max_distance
        joystick_y = paint_rect.center().y() + self.current_y * self.max_distance
        painter.drawEllipse(
            QPointF(joystick_x, joystick_y), self.inner_radius, self.inner_radius
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if QRectF(0, 0, 200, 200).contains(event.pos()):
            self.is_pressed = True
            self.updateJoystickPosition(event.pos())
            self.timer.timeout.connect(self.update_position)
            self.timer.start(10)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.is_pressed and QRectF(0, 0, 200, 200).contains(event.pos()):
            self.updateJoystickPosition(event.pos())

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self.is_pressed = False
        self.updateJoystickPosition(QPointF(100, 100))  # Center position
        self.timer.timeout.disconnect(self.update_position)
        self.joystickMoved.emit(0, 0)

    def update_position(self) -> None:
        if self.is_pressed:
            self.joystickMoved.emit(self.current_x, -self.current_y)

    def updateJoystickPosition(self, pos: QPointF) -> None:
        center = QPointF(100, 100)
        dx = pos.x() - center.x()
        dy = pos.y() - center.y()
        distance = math.sqrt(dx**2 + dy**2)

        if distance > self.max_distance:
            dx = dx * self.max_distance / distance
            dy = dy * self.max_distance / distance

        self.current_x = dx / self.max_distance
        self.current_y = dy / self.max_distance
        self.update()
