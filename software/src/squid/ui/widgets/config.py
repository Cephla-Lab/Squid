# Configuration editor widgets
import squid.core.logging
from typing import Any, Callable, Dict, List, Optional, Tuple, Type, TYPE_CHECKING
from configparser import ConfigParser
from qtpy.QtCore import Signal
from qtpy.QtWidgets import (
    QDialog,
    QScrollArea,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QLineEdit,
    QComboBox,
    QFileDialog,
    QMessageBox,
    QFrame,
    QSizePolicy,
    QInputDialog,
)

from squid.ui.widgets.base import CollapsibleGroupBox
from squid.core.events import ProfileChanged

if TYPE_CHECKING:
    from squid.ops.configuration import ConfigurationManager
    from squid.ui.ui_event_bus import UIEventBus


class ConfigEditor(QDialog):
    config: ConfigParser
    scroll_area: QScrollArea
    scroll_area_widget: QWidget
    scroll_area_layout: QVBoxLayout
    save_config_button: QPushButton
    save_to_file_button: QPushButton
    load_config_button: QPushButton
    config_value_widgets: Dict[str, Dict[str, Any]]
    groups: Dict[str, CollapsibleGroupBox]

    def __init__(self, config: ConfigParser) -> None:
        super().__init__()
        self._log = squid.core.logging.get_logger(self.__class__.__name__)
        self.config = config

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area_widget = QWidget()
        self.scroll_area_layout = QVBoxLayout()
        self.scroll_area_widget.setLayout(self.scroll_area_layout)
        self.scroll_area.setWidget(self.scroll_area_widget)

        self.save_config_button = QPushButton("Save Config")
        self.save_config_button.clicked.connect(self.save_config)
        self.save_to_file_button = QPushButton("Save to File")
        self.save_to_file_button.clicked.connect(self.save_to_file)
        self.load_config_button = QPushButton("Load Config from File")
        self.load_config_button.clicked.connect(self.load_config_from_file)

        layout = QVBoxLayout()
        layout.addWidget(self.scroll_area)
        layout.addWidget(self.save_config_button)
        layout.addWidget(self.save_to_file_button)
        layout.addWidget(self.load_config_button)

        self.config_value_widgets = {}

        self.setLayout(layout)
        self.setWindowTitle("Configuration Editor")
        self.init_ui()

    def init_ui(self) -> None:
        self.groups = {}
        for section in self.config.sections():
            group_box = CollapsibleGroupBox(section)
            group_layout = QVBoxLayout()

            section_value_widgets = {}

            self.groups[section] = group_box

            for option in self.config.options(section):
                if option.startswith("_") and option.endswith("_options"):
                    continue
                option_value = self.config.get(section, option)
                option_name = QLabel(option)
                option_layout = QHBoxLayout()
                option_layout.addWidget(option_name)
                if f"_{option}_options" in self.config.options(section):
                    option_value_list = self.config.get(section, f"_{option}_options")
                    values = option_value_list.strip("[]").split(",")
                    for i in range(len(values)):
                        values[i] = values[i].strip()
                    if option_value not in values:
                        values.append(option_value)
                    combo_box = QComboBox()
                    combo_box.addItems(values)
                    combo_box.setCurrentText(option_value)
                    option_layout.addWidget(combo_box)
                    section_value_widgets[option] = combo_box
                else:
                    option_input = QLineEdit(option_value)
                    option_layout.addWidget(option_input)
                    section_value_widgets[option] = option_input
                group_layout.addLayout(option_layout)

            self.config_value_widgets[section] = section_value_widgets
            group_box.content.addLayout(group_layout)
            self.scroll_area_layout.addWidget(group_box)

    def save_config(self) -> None:
        for section in self.config.sections():
            for option in self.config.options(section):
                if option.startswith("_") and option.endswith("_options"):
                    continue
                old_val = self.config.get(section, option)
                widget = self.config_value_widgets[section][option]
                if type(widget) is QLineEdit:
                    self.config.set(section, option, widget.text())
                else:
                    self.config.set(section, option, widget.currentText())
                if old_val != self.config.get(section, option):
                    print(self.config.get(section, option))

    def save_to_filename(self, filename: str) -> bool:
        try:
            with open(filename, "w") as configfile:
                self.config.write(configfile)
                return True
        except IOError:
            self._log.exception(f"Failed to write config file to '{filename}'")
            return False

    def save_to_file(self) -> None:
        self.save_config()
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save Config File", "", "INI Files (*.ini);;All Files (*)"
        )
        if file_path:
            if not self.save_to_filename(file_path):
                QMessageBox.warning(
                    self,
                    "Warning",
                    f"Failed to write config file to '{file_path}'.  Check permissions!",
                )

    def load_config_from_file(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Load Config File", "", "INI Files (*.ini);;All Files (*)"
        )
        if file_path:
            self.config.read(file_path)
            # Clear and re-initialize the UI
            self.scroll_area_widget.deleteLater()
            self.scroll_area_widget = QWidget()
            self.scroll_area_layout = QVBoxLayout()
            self.scroll_area_widget.setLayout(self.scroll_area_layout)
            self.scroll_area.setWidget(self.scroll_area_widget)
            self.init_ui()


class ConfigEditorBackwardsCompatible(ConfigEditor):
    original_filepath: str
    main_window: QWidget
    apply_exit_button: QPushButton

    def __init__(
        self, config: ConfigParser, original_filepath: str, main_window: QWidget
    ) -> None:
        super().__init__(config)
        self.original_filepath = original_filepath
        self.main_window = main_window

        self.apply_exit_button = QPushButton("Apply and Exit")
        self.apply_exit_button.clicked.connect(self.apply_and_exit)

        layout = self.layout()
        if layout is not None:
            layout.addWidget(self.apply_exit_button)

    def apply_and_exit(self) -> None:
        self.save_config()
        with open(self.original_filepath, "w") as configfile:
            self.config.write(configfile)
        try:
            self.main_window.close()
        except Exception:
            pass
        self.close()


class ProfileWidget(QFrame):
    signal_profile_changed: Signal = Signal()

    configurationManager: "ConfigurationManager"
    dropdown_profiles: QComboBox
    btn_newProfile: QPushButton

    def __init__(
        self,
        configurationManager: "ConfigurationManager",
        event_bus: Optional["UIEventBus"] = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.configurationManager = configurationManager
        self._event_bus = event_bus
        self._subscriptions: List[Tuple[Type, Callable]] = []

        self.setFrameStyle(QFrame.Panel | QFrame.Raised)
        self.setup_ui()

    def setup_ui(self) -> None:
        # Create widgets
        self.dropdown_profiles = QComboBox()
        self.dropdown_profiles.addItems(self.configurationManager.available_profiles)
        self.dropdown_profiles.setCurrentText(self.configurationManager.current_profile)
        sizePolicy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.dropdown_profiles.setSizePolicy(sizePolicy)

        self.btn_newProfile = QPushButton("Save As")

        # Connect signals
        self.dropdown_profiles.currentTextChanged.connect(self.load_profile)
        self.btn_newProfile.clicked.connect(self.create_new_profile)

        # Layout
        layout = QHBoxLayout()
        layout.addWidget(QLabel("Configuration Profile"))
        layout.addWidget(self.dropdown_profiles, 2)
        layout.addWidget(self.btn_newProfile)

        self.setLayout(layout)

    def load_profile(self) -> None:
        """Load the selected profile."""
        profile_name = self.dropdown_profiles.currentText()
        # Load the profile
        self.configurationManager.load_profile(profile_name)
        self.signal_profile_changed.emit()
        if self._event_bus is not None:
            self._event_bus.publish(ProfileChanged(profile_name=profile_name))

    def create_new_profile(self) -> None:
        """Create a new profile with current configurations."""
        dialog = QInputDialog()
        profile_name, ok = dialog.getText(
            self, "New Profile", "Enter new profile name:", QLineEdit.Normal, ""
        )

        if ok and profile_name:
            try:
                self.configurationManager.create_new_profile(profile_name)
                # Update profile dropdown
                self.dropdown_profiles.addItem(profile_name)
                self.dropdown_profiles.setCurrentText(profile_name)
            except ValueError as e:
                QMessageBox.warning(self, "Error", str(e))

    def get_current_profile(self) -> str:
        """Return the currently selected profile name."""
        return self.dropdown_profiles.currentText()
