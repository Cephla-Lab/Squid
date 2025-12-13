from squid.ui.widgets.wellplate._common import *
from squid.ui.widgets.base import EventBusWidget
from squid.core.events import ObjectiveChanged, WellplateFormatChanged


class SampleSettingsWidget(EventBusWidget):
    def __init__(
        self,
        ObjectivesWidget,
        WellplateFormatWidget,
        event_bus: "UIEventBus",
        *args,
        **kwargs,
    ):
        super().__init__(event_bus, *args, **kwargs)
        self.objectivesWidget = ObjectivesWidget
        self.wellplateFormatWidget = WellplateFormatWidget
        self._current_objective = getattr(self.objectivesWidget.dropdown, "currentText", lambda: "")()
        self._current_format = getattr(self.wellplateFormatWidget, "wellplate_format", None) or WELLPLATE_FORMAT

        # Set up the layout
        top_row_layout = QGridLayout()
        top_row_layout.setSpacing(2)
        top_row_layout.setContentsMargins(0, 2, 0, 2)
        top_row_layout.addWidget(self.objectivesWidget, 0, 0)
        top_row_layout.addWidget(self.wellplateFormatWidget, 0, 1)
        self.setLayout(top_row_layout)

        # Subscribe to event-driven updates.
        self._subscribe(ObjectiveChanged, self._on_objective_changed)
        self._subscribe(WellplateFormatChanged, self._on_wellplate_format_changed)

        self.save_settings()

    def _on_objective_changed(self, event: ObjectiveChanged) -> None:
        self._current_objective = event.objective_name
        self.save_settings()

    def _on_wellplate_format_changed(self, event: WellplateFormatChanged) -> None:
        self._current_format = event.format_name
        self.save_settings()

    def save_settings(self):
        """Save current objective and wellplate format to cache"""
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "objective": self._current_objective,
            "wellplate_format": self._current_format,
        }

        with open(CACHE_DIR / "objective_and_sample_format.txt", "w") as f:
            json.dump(data, f)
