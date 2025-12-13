# Objective lens selector widget
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from qtpy.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QSizePolicy,
)

from _def import (
    USE_XERYON,
    XERYON_OBJECTIVE_SWITCHER_POS_1,
    XERYON_OBJECTIVE_SWITCHER_POS_2,
)
from squid.core.events import ObjectiveChanged

if TYPE_CHECKING:
    from squid.ops.navigation import ObjectiveStore
    from squid.mcs.drivers.peripherals.objective_changer import ObjectiveChanger2PosController
    from squid.ui.ui_event_bus import UIEventBus


class ObjectivesWidget(QWidget):
    def __init__(
        self,
        objective_store: ObjectiveStore,
        objective_changer: Optional[ObjectiveChanger2PosController] = None,
        event_bus: "UIEventBus" = None,
    ) -> None:
        super(ObjectivesWidget, self).__init__()
        if event_bus is None:
            raise ValueError("ObjectivesWidget requires a UIEventBus instance")
        self._event_bus = event_bus
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

        # Publish ObjectiveChanged event for simulated camera FOV adjustment
        objective_info = self.objectiveStore.get_current_objective_info()
        magnification = objective_info.get("magnification", 1.0)
        pixel_size_factor = self.objectiveStore.get_pixel_size_factor()
        self._event_bus.publish(ObjectiveChanged(
            position=0,
            objective_name=objective_name,
            magnification=magnification,
            pixel_size_um=pixel_size_factor,  # This is the lens factor, camera multiplies by sensor size
        ))
