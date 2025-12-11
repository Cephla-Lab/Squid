from control.widgets.wellplate._common import *
from control.widgets.base import EventBusWidget
from squid.events import LiveStateChanged, SaveWellplateCalibrationCommand, WellplateFormatChanged
import csv

if TYPE_CHECKING:
    from control.widgets.display.navigation import NavigationViewer
    from control.core.display import StreamHandler
    from squid.events import EventBus


class WellplateFormatWidget(EventBusWidget):
    """Wellplate format selector widget using EventBus.

    When "calibrate format..." is selected, opens WellplateCalibration dialog.
    Subscribes to LiveStateChanged to track live state for calibration dialog.
    """

    signalWellplateSettings: Signal = Signal(
        QVariant, float, float, int, int, float, float, int, int, int
    )

    def __init__(
        self,
        event_bus: "EventBus",
        navigationViewer: "NavigationViewer",
        streamHandler: "StreamHandler",
        # Read-only config for calibration (passed to dialog)
        pixel_size_factor: float = 1.0,
        pixel_size_binned_um: float = 0.084665,
    ) -> None:
        super().__init__(event_bus)
        self.navigationViewer: "NavigationViewer" = navigationViewer
        self.streamHandler: "StreamHandler" = streamHandler
        self._pixel_size_factor = pixel_size_factor
        self._pixel_size_binned_um = pixel_size_binned_um
        self._is_live: bool = False  # Track live state from events
        self.wellplate_format: str = WELLPLATE_FORMAT
        self.csv_path: str = SAMPLE_FORMATS_CSV_PATH  # 'sample_formats.csv'
        self.label: QLabel
        self.comboBox: QComboBox

        # Subscribe to live state events
        self._subscribe(LiveStateChanged, self._on_live_state_changed)
        self._subscribe(
            SaveWellplateCalibrationCommand, self._on_save_calibration  # type: ignore[arg-type]
        )

        self.initUI()

    def _on_live_state_changed(self, event: LiveStateChanged) -> None:
        """Track live state for passing to calibration dialog."""
        self._is_live = event.is_live

    def initUI(self) -> None:
        layout = QHBoxLayout(self)
        self.label = QLabel("Sample Format", self)
        self.comboBox = QComboBox(self)
        self.populate_combo_box()
        self.comboBox.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout.addWidget(self.label)
        layout.addWidget(self.comboBox)
        self.comboBox.currentIndexChanged.connect(self.wellplateChanged)
        index = self.comboBox.findData(self.wellplate_format)
        if index >= 0:
            self.comboBox.setCurrentIndex(index)

    def _on_save_calibration(self, event: SaveWellplateCalibrationCommand) -> None:
        """Handle calibration save event."""
        name = event.name
        calibration = event.calibration
        if not name or calibration is None:
            return
        try:
            # Expect calibration to be a dict of settings
            WELLPLATE_FORMAT_SETTINGS[name] = calibration  # type: ignore[index, assignment]
            self.save_formats_to_csv()
            self.populate_combo_box()
            idx = self.comboBox.findData(name)
            if idx >= 0:
                self.comboBox.setCurrentIndex(idx)
            self.setWellplateSettings(name)
        except Exception as exc:  # pragma: no cover - defensive
            print(f"Failed to save calibration for {name}: {exc}")

    def populate_combo_box(self) -> None:
        self.comboBox.clear()
        for format_, settings in WELLPLATE_FORMAT_SETTINGS.items():
            self.comboBox.addItem(format_, format_)

        # Add custom item and set its font to italic
        self.comboBox.addItem("calibrate format...", "custom")
        index = self.comboBox.count() - 1  # Get the index of the last item
        font = QFont()
        font.setItalic(True)
        self.comboBox.setItemData(index, font, Qt.ItemDataRole.FontRole)

    def wellplateChanged(self, index: int) -> None:
        self.wellplate_format = self.comboBox.itemData(index)
        if self.wellplate_format == "custom":
            calibration_dialog = WellplateCalibration(  # type: ignore[name-defined]
                event_bus=self._bus,
                wellplateFormatWidget=self,
                navigationViewer=self.navigationViewer,
                streamHandler=self.streamHandler,
                pixel_size_factor=self._pixel_size_factor,
                pixel_size_binned_um=self._pixel_size_binned_um,
                was_live=self._is_live,
            )
            result = calibration_dialog.exec_()
            if result == QDialog.Rejected:
                # If the dialog was closed without adding a new format, revert to the previous selection
                prev_index = self.comboBox.findData(self.wellplate_format)
                self.comboBox.setCurrentIndex(prev_index)
        else:
            self.setWellplateSettings(self.wellplate_format)

    def setWellplateSettings(self, wellplate_format: str) -> None:
        if wellplate_format in WELLPLATE_FORMAT_SETTINGS:
            settings = WELLPLATE_FORMAT_SETTINGS[wellplate_format]
        elif wellplate_format == "glass slide":
            self.signalWellplateSettings.emit(
                QVariant("glass slide"), 0, 0, 0, 0, 0, 0, 0, 1, 1
            )
            self._publish(WellplateFormatChanged(
                format_name="glass slide",
                rows=1,
                cols=1,
                well_spacing_mm=0.0,
                well_size_mm=0.0,
                a1_x_mm=0.0,
                a1_y_mm=0.0,
            ))
            return
        else:
            print(f"Wellplate format {wellplate_format} not recognized")
            return

        self.signalWellplateSettings.emit(
            QVariant(wellplate_format),
            settings["a1_x_mm"],
            settings["a1_y_mm"],
            settings["a1_x_pixel"],
            settings["a1_y_pixel"],
            settings["well_size_mm"],
            settings["well_spacing_mm"],
            settings["number_of_skip"],
            settings["rows"],
            settings["cols"],
        )
        self._publish(WellplateFormatChanged(
            format_name=wellplate_format,
            rows=settings["rows"],
            cols=settings["cols"],
            well_spacing_mm=settings["well_spacing_mm"],
            well_size_mm=settings["well_size_mm"],
            a1_x_mm=settings["a1_x_mm"],
            a1_y_mm=settings["a1_y_mm"],
        ))

    def getWellplateSettings(
        self, wellplate_format: str
    ) -> Optional[Dict[str, Union[str, int, float]]]:
        if wellplate_format in WELLPLATE_FORMAT_SETTINGS:
            settings = WELLPLATE_FORMAT_SETTINGS[wellplate_format]
        elif wellplate_format == "glass slide":
            settings = {
                "format": "glass slide",
                "a1_x_mm": 0,
                "a1_y_mm": 0,
                "a1_x_pixel": 0,
                "a1_y_pixel": 0,
                "well_size_mm": 0,
                "well_spacing_mm": 0,
                "number_of_skip": 0,
                "rows": 1,
                "cols": 1,
            }
        else:
            return None
        return settings

    def add_custom_format(
        self, name: str, settings: Dict[str, Union[int, float]]
    ) -> None:
        WELLPLATE_FORMAT_SETTINGS[name] = settings
        self.populate_combo_box()
        index = self.comboBox.findData(name)
        if index >= 0:
            self.comboBox.setCurrentIndex(index)
        self.wellplateChanged(index)

    def save_formats_to_csv(self) -> None:
        cache_path = os.path.join("cache", self.csv_path)
        os.makedirs("cache", exist_ok=True)

        fieldnames = [
            "format",
            "a1_x_mm",
            "a1_y_mm",
            "a1_x_pixel",
            "a1_y_pixel",
            "well_size_mm",
            "well_spacing_mm",
            "number_of_skip",
            "rows",
            "cols",
        ]
        with open(cache_path, "w", newline="") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            for format_, settings in WELLPLATE_FORMAT_SETTINGS.items():
                writer.writerow({**{"format": format_}, **settings})

    @staticmethod
    def parse_csv_row(row: Dict[str, str]) -> Dict[str, Union[int, float]]:
        return {
            "a1_x_mm": float(row["a1_x_mm"]),
            "a1_y_mm": float(row["a1_y_mm"]),
            "a1_x_pixel": int(row["a1_x_pixel"]),
            "a1_y_pixel": int(row["a1_y_pixel"]),
            "well_size_mm": float(row["well_size_mm"]),
            "well_spacing_mm": float(row["well_spacing_mm"]),
            "number_of_skip": int(row["number_of_skip"]),
            "rows": int(row["rows"]),
            "cols": int(row["cols"]),
        }
