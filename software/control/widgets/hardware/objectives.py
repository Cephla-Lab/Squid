# Objective lens selector widget
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from qtpy.QtCore import Signal
from qtpy.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QSizePolicy,
)

from control._def import (
    USE_XERYON,
    XERYON_OBJECTIVE_SWITCHER_POS_1,
    XERYON_OBJECTIVE_SWITCHER_POS_2,
)

if TYPE_CHECKING:
    from control.core.navigation import ObjectiveStore
    from control.peripherals.objective_changer import ObjectiveChanger2PosController


class ObjectivesWidget(QWidget):
    signal_objective_changed: Signal = Signal()

    def __init__(
        self,
        objective_store: ObjectiveStore,
        objective_changer: Optional[ObjectiveChanger2PosController] = None,
    ) -> None:
        super(ObjectivesWidget, self).__init__()
        self.objectiveStore: ObjectiveStore = objective_store
        self.objective_changer: Optional[ObjectiveChanger2PosController] = (
            objective_changer
        )
        self.dropdown: QComboBox
        self.init_ui()
        self.dropdown.setCurrentText(self.objectiveStore.current_objective)

    def init_ui(self) -> None:
        self.dropdown = QComboBox(self)
        self.dropdown.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.dropdown.addItems(self.objectiveStore.objectives_dict.keys())
        self.dropdown.currentTextChanged.connect(self.on_objective_changed)

        layout = QHBoxLayout()
        layout.addWidget(QLabel("Objective Lens"))
        layout.addWidget(self.dropdown)
        self.setLayout(layout)

    def on_objective_changed(self, objective_name: str) -> None:
        self.objectiveStore.set_current_objective(objective_name)
        if USE_XERYON and self.objective_changer is not None:
            if (
                objective_name in XERYON_OBJECTIVE_SWITCHER_POS_1
                and self.objective_changer.currentPosition() != 1
            ):
                self.objective_changer.moveToPosition1()
            elif (
                objective_name in XERYON_OBJECTIVE_SWITCHER_POS_2
                and self.objective_changer.currentPosition() != 2
            ):
                self.objective_changer.moveToPosition2()
        self.signal_objective_changed.emit()
