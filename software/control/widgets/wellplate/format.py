from control.widgets.wellplate._common import *

class WellplateFormatWidget(QWidget):

    signalWellplateSettings = Signal(QVariant, float, float, int, int, float, float, int, int, int)

    def __init__(self, stage: AbstractStage, navigationViewer, streamHandler, liveController):
        super().__init__()
        self.stage = stage
        self.navigationViewer = navigationViewer
        self.streamHandler = streamHandler
        self.liveController = liveController
        self.wellplate_format = WELLPLATE_FORMAT
        self.csv_path = SAMPLE_FORMATS_CSV_PATH  # 'sample_formats.csv'
        self.initUI()

    def initUI(self):
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

    def populate_combo_box(self):
        self.comboBox.clear()
        for format_, settings in WELLPLATE_FORMAT_SETTINGS.items():
            self.comboBox.addItem(format_, format_)

        # Add custom item and set its font to italic
        self.comboBox.addItem("calibrate format...", "custom")
        index = self.comboBox.count() - 1  # Get the index of the last item
        font = QFont()
        font.setItalic(True)
        self.comboBox.setItemData(index, font, Qt.FontRole)

    def wellplateChanged(self, index):
        self.wellplate_format = self.comboBox.itemData(index)
        if self.wellplate_format == "custom":
            calibration_dialog = WellplateCalibration(
                self, self.stage, self.navigationViewer, self.streamHandler, self.liveController
            )
            result = calibration_dialog.exec_()
            if result == QDialog.Rejected:
                # If the dialog was closed without adding a new format, revert to the previous selection
                prev_index = self.comboBox.findData(self.wellplate_format)
                self.comboBox.setCurrentIndex(prev_index)
        else:
            self.setWellplateSettings(self.wellplate_format)

    def setWellplateSettings(self, wellplate_format):
        if wellplate_format in WELLPLATE_FORMAT_SETTINGS:
            settings = WELLPLATE_FORMAT_SETTINGS[wellplate_format]
        elif wellplate_format == "glass slide":
            self.signalWellplateSettings.emit(QVariant("glass slide"), 0, 0, 0, 0, 0, 0, 0, 1, 1)
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

    def getWellplateSettings(self, wellplate_format):
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

    def add_custom_format(self, name, settings):
        WELLPLATE_FORMAT_SETTINGS[name] = settings
        self.populate_combo_box()
        index = self.comboBox.findData(name)
        if index >= 0:
            self.comboBox.setCurrentIndex(index)
        self.wellplateChanged(index)

    def save_formats_to_csv(self):
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
    def parse_csv_row(row):
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


