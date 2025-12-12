from squid.ui.widgets.wellplate._common import *


class SampleSettingsWidget(QFrame):
    def __init__(self, ObjectivesWidget, WellplateFormatWidget, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.objectivesWidget = ObjectivesWidget
        self.wellplateFormatWidget = WellplateFormatWidget

        # Set up the layout
        top_row_layout = QGridLayout()
        top_row_layout.setSpacing(2)
        top_row_layout.setContentsMargins(0, 2, 0, 2)
        top_row_layout.addWidget(self.objectivesWidget, 0, 0)
        top_row_layout.addWidget(self.wellplateFormatWidget, 0, 1)
        self.setLayout(top_row_layout)
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

        # Connect signals for saving settings
        self.objectivesWidget.signal_objective_changed.connect(self.save_settings)
        self.wellplateFormatWidget.signalWellplateSettings.connect(
            lambda *args: self.save_settings()
        )

    def save_settings(self):
        """Save current objective and wellplate format to cache"""
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "objective": self.objectivesWidget.dropdown.currentText(),
            "wellplate_format": self.wellplateFormatWidget.wellplate_format,
        }

        with open(CACHE_DIR / "objective_and_sample_format.txt", "w") as f:
            json.dump(data, f)
