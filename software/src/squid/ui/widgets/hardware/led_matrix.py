# LED matrix settings dialog
from typing import Any

from qtpy.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QDoubleSpinBox,
    QDialogButtonBox,
)


class LedMatrixSettingsDialog(QDialog):
    def __init__(self, led_array: Any) -> None:
        self.led_array: Any = led_array
        super().__init__()
        self.setWindowTitle("LED Matrix Settings")

        main_layout = QVBoxLayout()

        # Add QDoubleSpinBox for LED intensity (0-1)
        self.NA_spinbox = QDoubleSpinBox()
        self.NA_spinbox.setKeyboardTracking(False)
        self.NA_spinbox.setRange(0, 1)
        self.NA_spinbox.setSingleStep(0.01)
        self.NA_spinbox.setValue(self.led_array.NA)

        NA_layout = QHBoxLayout()
        NA_layout.addWidget(QLabel("NA"))
        NA_layout.addWidget(self.NA_spinbox)

        main_layout.addLayout(NA_layout)
        self.setLayout(main_layout)

        # add ok/cancel buttons
        self.button_box = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        main_layout.addWidget(self.button_box)

        self.button_box.accepted.connect(self.update_NA)

    def update_NA(self) -> None:
        self.led_array.set_NA(self.NA_spinbox.value())
