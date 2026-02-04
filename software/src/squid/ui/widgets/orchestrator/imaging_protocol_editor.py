"""
Imaging Protocol Editor Dialog.

Dialog for creating, editing, and saving imaging protocols.
Protocols define per-tile acquisition procedures: channel selection and
order, z-stack parameters, channel/z interleaving, and autofocus strategy.
"""

from typing import List, Optional, TYPE_CHECKING

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QCheckBox,
)

from squid.core.protocol.imaging_protocol import (
    ImagingProtocol,
    ZStackConfig,
    FocusConfig,
)

import squid.core.logging

if TYPE_CHECKING:
    from squid.core.config.repository import ConfigRepository

_log = squid.core.logging.get_logger(__name__)


class ImagingProtocolEditor(QDialog):
    """Dialog to create/edit/save imaging protocols.

    Features:
    - Channel selection from available channels (checkboxes)
    - Drag-to-reorder for acquisition order
    - Z-stack settings (planes, step, direction)
    - Channel/z interleaving mode (channel_first / z_first)
    - Focus settings (method, interval)
    - Save to profile with a name

    Signals:
        protocol_saved: Emitted when a protocol is saved (name, protocol)
    """

    protocol_saved = pyqtSignal(str, object)  # name, ImagingProtocol

    def __init__(
        self,
        available_channels: Optional[List[str]] = None,
        config_repo: Optional["ConfigRepository"] = None,
        profile: Optional[str] = None,
        parent: Optional[QDialog] = None,
    ):
        super().__init__(parent)
        self._available_channels = available_channels or []
        self._config_repo = config_repo
        self._profile = profile

        self.setWindowTitle("Imaging Protocol Editor")
        self.setMinimumSize(500, 650)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Protocol name + description
        name_group = QGroupBox("Protocol")
        name_layout = QFormLayout(name_group)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("e.g. fish_standard")
        name_layout.addRow("Name:", self._name_edit)

        self._desc_edit = QLineEdit()
        self._desc_edit.setPlaceholderText("Optional description")
        name_layout.addRow("Description:", self._desc_edit)

        layout.addWidget(name_group)

        # Channel selection and ordering
        ch_group = QGroupBox("Channels (drag to reorder)")
        ch_layout = QVBoxLayout(ch_group)

        self._channel_list = QListWidget()
        self._channel_list.setDragDropMode(QAbstractItemView.InternalMove)
        self._channel_list.setDefaultDropAction(Qt.MoveAction)
        for ch_name in self._available_channels:
            item = QListWidgetItem(ch_name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled)
            item.setCheckState(Qt.Unchecked)
            self._channel_list.addItem(item)
        ch_layout.addWidget(self._channel_list)

        layout.addWidget(ch_group)

        # Z-stack settings
        z_group = QGroupBox("Z-Stack")
        z_layout = QFormLayout(z_group)

        self._z_planes_spin = QSpinBox()
        self._z_planes_spin.setRange(1, 500)
        self._z_planes_spin.setValue(1)
        z_layout.addRow("Planes:", self._z_planes_spin)

        self._z_step_spin = QDoubleSpinBox()
        self._z_step_spin.setRange(0.01, 100.0)
        self._z_step_spin.setValue(0.5)
        self._z_step_spin.setDecimals(2)
        self._z_step_spin.setSuffix(" \u00b5m")
        z_layout.addRow("Step size:", self._z_step_spin)

        self._z_dir_combo = QComboBox()
        self._z_dir_combo.addItems(["from_center", "from_bottom", "from_top"])
        z_layout.addRow("Direction:", self._z_dir_combo)

        layout.addWidget(z_group)

        # Acquisition order
        order_group = QGroupBox("Acquisition Order")
        order_layout = QVBoxLayout(order_group)

        self._order_combo = QComboBox()
        self._order_combo.addItem("channel_first \u2014 all channels per z-plane", "channel_first")
        self._order_combo.addItem("z_first \u2014 all z-planes per channel", "z_first")
        order_layout.addWidget(self._order_combo)

        layout.addWidget(order_group)

        # Focus settings
        focus_group = QGroupBox("Autofocus")
        focus_layout = QFormLayout(focus_group)

        self._focus_enabled = QCheckBox("Enabled")
        focus_layout.addRow(self._focus_enabled)

        self._focus_method = QComboBox()
        self._focus_method.addItems(["laser", "contrast", "none"])
        focus_layout.addRow("Method:", self._focus_method)

        self._focus_interval = QSpinBox()
        self._focus_interval.setRange(1, 1000)
        self._focus_interval.setValue(1)
        self._focus_interval.setSuffix(" FOVs")
        focus_layout.addRow("Interval:", self._focus_interval)

        layout.addWidget(focus_group)

        # Skip saving checkbox
        self._skip_saving = QCheckBox("Skip saving images (preview only)")
        layout.addWidget(self._skip_saving)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        load_btn = QPushButton("Load...")
        load_btn.clicked.connect(self._on_load_clicked)
        btn_layout.addWidget(load_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self._on_save_clicked)
        btn_layout.addWidget(save_btn)

        layout.addLayout(btn_layout)

    def _get_selected_channels(self) -> List[str]:
        """Get checked channel names in current list order."""
        channels = []
        for i in range(self._channel_list.count()):
            item = self._channel_list.item(i)
            if item.checkState() == Qt.Checked:
                channels.append(item.text())
        return channels

    def build_protocol(self) -> Optional[ImagingProtocol]:
        """Build an ImagingProtocol from the current form values.

        Returns None if validation fails.
        """
        channels = self._get_selected_channels()
        if not channels:
            QMessageBox.warning(self, "Validation", "Select at least one channel.")
            return None

        name = self._name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Validation", "Enter a protocol name.")
            return None

        return ImagingProtocol(
            description=self._desc_edit.text().strip(),
            channels=channels,
            z_stack=ZStackConfig(
                planes=self._z_planes_spin.value(),
                step_um=self._z_step_spin.value(),
                direction=self._z_dir_combo.currentText(),
            ),
            acquisition_order=self._order_combo.currentData(),
            focus=FocusConfig(
                enabled=self._focus_enabled.isChecked(),
                method=self._focus_method.currentText(),
                interval_fovs=self._focus_interval.value(),
            ),
            skip_saving=self._skip_saving.isChecked(),
        )

    def load_protocol(self, name: str, protocol: ImagingProtocol) -> None:
        """Populate the form from an existing ImagingProtocol."""
        self._name_edit.setText(name)
        self._desc_edit.setText(protocol.description)

        # Set channel checkboxes and order
        channel_names = protocol.get_channel_names()
        checked_set = set(channel_names)

        # Uncheck all first
        for i in range(self._channel_list.count()):
            self._channel_list.item(i).setCheckState(Qt.Unchecked)

        # Check matching channels
        for i in range(self._channel_list.count()):
            item = self._channel_list.item(i)
            if item.text() in checked_set:
                item.setCheckState(Qt.Checked)

        # Z-stack
        self._z_planes_spin.setValue(protocol.z_stack.planes)
        self._z_step_spin.setValue(protocol.z_stack.step_um)
        idx = self._z_dir_combo.findText(protocol.z_stack.direction)
        if idx >= 0:
            self._z_dir_combo.setCurrentIndex(idx)

        # Acquisition order
        order_idx = self._order_combo.findData(protocol.acquisition_order)
        if order_idx >= 0:
            self._order_combo.setCurrentIndex(order_idx)

        # Focus
        self._focus_enabled.setChecked(protocol.focus.enabled)
        method_idx = self._focus_method.findText(protocol.focus.method)
        if method_idx >= 0:
            self._focus_method.setCurrentIndex(method_idx)
        self._focus_interval.setValue(protocol.focus.interval_fovs)

        # Skip saving
        self._skip_saving.setChecked(protocol.skip_saving)

    def _on_save_clicked(self) -> None:
        """Validate and save the protocol."""
        protocol = self.build_protocol()
        if protocol is None:
            return

        name = self._name_edit.text().strip()

        # Save to profile via ConfigRepository if available
        if self._config_repo is not None and self._profile:
            try:
                self._config_repo.save_imaging_protocol(self._profile, name, protocol)
                _log.info(f"Saved imaging protocol '{name}' to profile '{self._profile}'")
            except Exception as e:
                QMessageBox.critical(self, "Save Error", f"Failed to save protocol: {e}")
                return

        self.protocol_saved.emit(name, protocol)
        self.accept()

    def _on_load_clicked(self) -> None:
        """Load a stored protocol from the profile."""
        if self._config_repo is None or self._profile is None:
            QMessageBox.information(self, "Load", "No profile configured for loading protocols.")
            return

        available = self._config_repo.get_available_imaging_protocols(self._profile)
        if not available:
            QMessageBox.information(self, "Load", "No stored protocols found in profile.")
            return

        from PyQt5.QtWidgets import QInputDialog

        name, ok = QInputDialog.getItem(
            self, "Load Protocol", "Select protocol:", available, 0, False
        )
        if ok and name:
            protocol = self._config_repo.get_imaging_protocol(name, self._profile)
            if protocol is not None:
                self.load_protocol(name, protocol)
            else:
                QMessageBox.warning(self, "Load", f"Failed to load protocol '{name}'.")
