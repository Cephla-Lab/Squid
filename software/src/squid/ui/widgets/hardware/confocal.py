# Confocal microscope control widgets (XLight, Dragonfly)
from typing import Any

from qtpy.QtCore import Signal, Qt
from qtpy.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QSpinBox,
    QComboBox,
    QPushButton,
    QSlider,
    QSizePolicy,
)


class SpinningDiskConfocalWidget(QWidget):
    signal_toggle_confocal_widefield: Signal = Signal(bool)

    def __init__(self, xlight: Any) -> None:
        super(SpinningDiskConfocalWidget, self).__init__()

        self.xlight: Any = xlight

        self.init_ui()

        self.dropdown_emission_filter.setCurrentText(
            str(self.xlight.get_emission_filter())
        )
        self.dropdown_dichroic.setCurrentText(str(self.xlight.get_dichroic()))

        self.dropdown_emission_filter.currentIndexChanged.connect(
            self.set_emission_filter
        )
        self.dropdown_dichroic.currentIndexChanged.connect(self.set_dichroic)

        self.disk_position_state = self.xlight.get_disk_position()

        self.signal_toggle_confocal_widefield.emit(
            self.disk_position_state
        )  # signal initial state

        if self.disk_position_state == 1:
            self.btn_toggle_widefield.setText("Switch to Widefield")

        self.btn_toggle_widefield.clicked.connect(self.toggle_disk_position)
        self.btn_toggle_motor.clicked.connect(self.toggle_motor)

        self.dropdown_filter_slider.valueChanged.connect(self.set_filter_slider)

        if self.xlight.has_illumination_iris_diaphragm:
            illumination_iris = self.xlight.illumination_iris
            self.slider_illumination_iris.setValue(illumination_iris)
            self.spinbox_illumination_iris.setValue(illumination_iris)

            self.slider_illumination_iris.sliderReleased.connect(
                lambda: self.update_illumination_iris(True)
            )
            # Update spinbox values during sliding without sending to hardware
            self.slider_illumination_iris.valueChanged.connect(
                self.spinbox_illumination_iris.setValue
            )
            self.spinbox_illumination_iris.editingFinished.connect(
                lambda: self.update_illumination_iris(False)
            )
        if self.xlight.has_emission_iris_diaphragm:
            emission_iris = self.xlight.emission_iris
            self.slider_emission_iris.setValue(emission_iris)
            self.spinbox_emission_iris.setValue(emission_iris)

            self.slider_emission_iris.sliderReleased.connect(
                lambda: self.update_emission_iris(True)
            )
            # Update spinbox values during sliding without sending to hardware
            self.slider_emission_iris.valueChanged.connect(
                self.spinbox_emission_iris.setValue
            )
            self.spinbox_emission_iris.editingFinished.connect(
                lambda: self.update_emission_iris(False)
            )

    def init_ui(self) -> None:
        emissionFilterLayout = QHBoxLayout()
        emissionFilterLayout.addWidget(QLabel("Emission Position"))
        self.dropdown_emission_filter = QComboBox(self)
        self.dropdown_emission_filter.addItems([str(i + 1) for i in range(8)])
        emissionFilterLayout.addWidget(self.dropdown_emission_filter)

        dichroicLayout = QHBoxLayout()
        dichroicLayout.addWidget(QLabel("Dichroic Position"))
        self.dropdown_dichroic = QComboBox(self)
        self.dropdown_dichroic.addItems([str(i + 1) for i in range(5)])
        dichroicLayout.addWidget(self.dropdown_dichroic)

        illuminationIrisLayout = QHBoxLayout()
        illuminationIrisLayout.addWidget(QLabel("Illumination Iris"))
        self.slider_illumination_iris = QSlider(Qt.Orientation.Horizontal)
        self.slider_illumination_iris.setRange(0, 100)
        self.spinbox_illumination_iris = QSpinBox()
        self.spinbox_illumination_iris.setRange(0, 100)
        self.spinbox_illumination_iris.setKeyboardTracking(False)
        illuminationIrisLayout.addWidget(self.slider_illumination_iris)
        illuminationIrisLayout.addWidget(self.spinbox_illumination_iris)

        emissionIrisLayout = QHBoxLayout()
        emissionIrisLayout.addWidget(QLabel("Emission Iris"))
        self.slider_emission_iris = QSlider(Qt.Orientation.Horizontal)
        self.slider_emission_iris.setRange(0, 100)
        self.spinbox_emission_iris = QSpinBox()
        self.spinbox_emission_iris.setRange(0, 100)
        self.spinbox_emission_iris.setKeyboardTracking(False)
        emissionIrisLayout.addWidget(self.slider_emission_iris)
        emissionIrisLayout.addWidget(self.spinbox_emission_iris)

        filterSliderLayout = QHBoxLayout()
        filterSliderLayout.addWidget(QLabel("Filter Slider"))
        # self.dropdown_filter_slider = QComboBox(self)
        # self.dropdown_filter_slider.addItems(["0", "1", "2", "3"])
        self.dropdown_filter_slider = QSlider(Qt.Orientation.Horizontal)
        self.dropdown_filter_slider.setRange(0, 3)
        self.dropdown_filter_slider.setTickPosition(QSlider.TicksBelow)
        self.dropdown_filter_slider.setTickInterval(1)
        filterSliderLayout.addWidget(self.dropdown_filter_slider)

        self.btn_toggle_widefield = QPushButton("Switch to Confocal")

        self.btn_toggle_motor = QPushButton("Disk Motor On")
        self.btn_toggle_motor.setCheckable(True)

        layout = QGridLayout(self)

        # row 1
        if self.xlight.has_dichroic_filter_slider:
            layout.addLayout(filterSliderLayout, 0, 0, 1, 2)
        layout.addWidget(self.btn_toggle_motor, 0, 2)
        layout.addWidget(self.btn_toggle_widefield, 0, 3)

        # row 2
        if self.xlight.has_dichroic_filters_wheel:
            layout.addWidget(QLabel("Dichroic Filter Wheel"), 1, 0)
            layout.addWidget(self.dropdown_dichroic, 1, 1)
        if self.xlight.has_illumination_iris_diaphragm:
            layout.addLayout(illuminationIrisLayout, 1, 2, 1, 2)

        # row 3
        if self.xlight.has_emission_filters_wheel:
            layout.addWidget(QLabel("Emission Filter Wheel"), 2, 0)
            layout.addWidget(self.dropdown_emission_filter, 2, 1)
        if self.xlight.has_emission_iris_diaphragm:
            layout.addLayout(emissionIrisLayout, 2, 2, 1, 2)

        layout.setColumnStretch(2, 1)
        layout.setColumnStretch(3, 1)
        self.setLayout(layout)

    def enable_all_buttons(self, enable: bool) -> None:
        self.dropdown_emission_filter.setEnabled(enable)
        self.dropdown_dichroic.setEnabled(enable)
        self.btn_toggle_widefield.setEnabled(enable)
        self.btn_toggle_motor.setEnabled(enable)
        self.slider_illumination_iris.setEnabled(enable)
        self.spinbox_illumination_iris.setEnabled(enable)
        self.slider_emission_iris.setEnabled(enable)
        self.spinbox_emission_iris.setEnabled(enable)
        self.dropdown_filter_slider.setEnabled(enable)

    def block_iris_control_signals(self, block: bool) -> None:
        self.slider_illumination_iris.blockSignals(block)
        self.spinbox_illumination_iris.blockSignals(block)
        self.slider_emission_iris.blockSignals(block)
        self.spinbox_emission_iris.blockSignals(block)

    def toggle_disk_position(self) -> None:
        self.enable_all_buttons(False)
        if self.disk_position_state == 1:
            self.disk_position_state = self.xlight.set_disk_position(0)
            self.btn_toggle_widefield.setText("Switch to Confocal")
        else:
            self.disk_position_state = self.xlight.set_disk_position(1)
            self.btn_toggle_widefield.setText("Switch to Widefield")
        self.enable_all_buttons(True)
        self.signal_toggle_confocal_widefield.emit(self.disk_position_state)

    def toggle_motor(self) -> None:
        self.enable_all_buttons(False)
        if self.btn_toggle_motor.isChecked():
            self.xlight.set_disk_motor_state(True)
        else:
            self.xlight.set_disk_motor_state(False)
        self.enable_all_buttons(True)

    def set_emission_filter(self, index: int) -> None:
        self.enable_all_buttons(False)
        selected_pos = self.dropdown_emission_filter.currentText()
        self.xlight.set_emission_filter(selected_pos)
        self.enable_all_buttons(True)

    def set_dichroic(self, index: int) -> None:
        self.enable_all_buttons(False)
        selected_pos = self.dropdown_dichroic.currentText()
        self.xlight.set_dichroic(selected_pos)
        self.enable_all_buttons(True)

    def update_illumination_iris(self, from_slider: bool) -> None:
        self.block_iris_control_signals(
            True
        )  # avoid signals triggered by enable/disable buttons
        self.enable_all_buttons(False)
        if from_slider:
            value = self.slider_illumination_iris.value()
        else:
            value = self.spinbox_illumination_iris.value()
            self.slider_illumination_iris.setValue(value)
        self.xlight.set_illumination_iris(value)
        self.enable_all_buttons(True)
        self.block_iris_control_signals(False)

    def update_emission_iris(self, from_slider: bool) -> None:
        self.block_iris_control_signals(
            True
        )  # avoid signals triggered by enable/disable buttons
        self.enable_all_buttons(False)
        if from_slider:
            value = self.slider_emission_iris.value()
        else:
            value = self.spinbox_emission_iris.value()
            self.slider_emission_iris.setValue(value)
        self.xlight.set_emission_iris(value)
        self.enable_all_buttons(True)
        self.block_iris_control_signals(False)

    def set_filter_slider(self, index: int) -> None:
        self.enable_all_buttons(False)
        position = str(self.dropdown_filter_slider.value())
        self.xlight.set_filter_slider(position)
        self.enable_all_buttons(True)


class DragonflyConfocalWidget(QWidget):
    signal_toggle_confocal_widefield: Signal = Signal(bool)

    def __init__(self, dragonfly: Any) -> None:
        super(DragonflyConfocalWidget, self).__init__()

        self.dragonfly: Any = dragonfly
        self.confocal_mode: bool = False

        self.init_ui()

        # Initialize current states from hardware
        try:
            current_modality = self.dragonfly.get_modality()
            self.confocal_mode = (
                current_modality == "CONFOCAL" if current_modality else False
            )

            current_dichroic = self.dragonfly.get_port_selection_dichroic()
            if current_dichroic is not None:
                self.dropdown_dichroic.setCurrentText(str(current_dichroic))

            current_port1_filter = self.dragonfly.get_emission_filter(1)
            if current_port1_filter is not None:
                self.dropdown_port1_emission_filter.setCurrentText(
                    str(current_port1_filter)
                )

            current_port2_filter = self.dragonfly.get_emission_filter(2)
            if current_port2_filter is not None:
                self.dropdown_port2_emission_filter.setCurrentText(
                    str(current_port2_filter)
                )

            current_field_aperture = self.dragonfly.get_field_aperture_wheel_position()
            if current_field_aperture is not None:
                self.dropdown_field_aperture.setCurrentText(str(current_field_aperture))

            motor_state = self.dragonfly.get_disk_motor_state()
            if motor_state is not None:
                self.btn_disk_motor.setChecked(motor_state)

        except Exception as e:
            print(f"Error initializing widget state: {e}")

        # Set initial button text
        if self.confocal_mode:
            self.btn_toggle_confocal.setText("Switch to Widefield")
        else:
            self.btn_toggle_confocal.setText("Switch to Confocal")

        # Connect signals
        self.btn_toggle_confocal.clicked.connect(self.toggle_confocal_mode)
        self.btn_disk_motor.clicked.connect(self.toggle_disk_motor)
        self.dropdown_dichroic.currentIndexChanged.connect(self.set_dichroic)
        self.dropdown_port1_emission_filter.currentIndexChanged.connect(
            self.set_port1_emission_filter
        )
        self.dropdown_port2_emission_filter.currentIndexChanged.connect(
            self.set_port2_emission_filter
        )
        self.dropdown_field_aperture.currentIndexChanged.connect(
            self.set_field_aperture
        )

        # Emit initial state
        self.signal_toggle_confocal_widefield.emit(self.confocal_mode)

    def init_ui(self) -> None:
        main_layout = QVBoxLayout()

        layout_confocal = QHBoxLayout()
        # Row 1: Switch to Confocal button, Disk Motor button, Dichroic dropdown
        self.btn_toggle_confocal = QPushButton("Switch to Confocal")
        self.btn_disk_motor = QPushButton("Disk Motor On")
        self.btn_disk_motor.setCheckable(True)

        dichroic_label = QLabel("Port Selection")
        dichroic_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        self.dropdown_dichroic = QComboBox(self)
        self.dropdown_dichroic.addItems(
            self.dragonfly.get_port_selection_dichroic_info()
        )

        layout_confocal.addWidget(self.btn_toggle_confocal)
        layout_confocal.addWidget(self.btn_disk_motor)
        layout_confocal.addWidget(dichroic_label)
        layout_confocal.addWidget(self.dropdown_dichroic)

        layout_wheels = QGridLayout()
        # Row 2: Camera Port 1 Emission Filter and Field Aperture
        port1_emission_label = QLabel("Port 1 Emission Filter")
        self.dropdown_port1_emission_filter = QComboBox(self)
        self.dropdown_port1_emission_filter.addItems(
            self.dragonfly.get_emission_filter_info(1)
        )

        port1_aperture_label = QLabel("Field Aperture")
        self.dropdown_field_aperture = QComboBox(self)
        self.dropdown_field_aperture.addItems(self.dragonfly.get_field_aperture_info())

        layout_wheels.addWidget(port1_emission_label, 0, 0)
        layout_wheels.addWidget(self.dropdown_port1_emission_filter, 0, 1)
        layout_wheels.addWidget(port1_aperture_label, 0, 2)
        layout_wheels.addWidget(self.dropdown_field_aperture, 0, 3)

        # Row 3: Camera Port 2 Emission Filter and Field Aperture
        port2_emission_label = QLabel("Port 2 Emission Filter")
        self.dropdown_port2_emission_filter = QComboBox(self)
        self.dropdown_port2_emission_filter.addItems(
            self.dragonfly.get_emission_filter_info(2)
        )

        layout_wheels.addWidget(port2_emission_label, 1, 0)
        layout_wheels.addWidget(self.dropdown_port2_emission_filter, 1, 1)

        main_layout.addLayout(layout_confocal)
        main_layout.addLayout(layout_wheels)

        self.setLayout(main_layout)

    def enable_all_buttons(self, enable: bool) -> None:
        """Enable or disable all controls"""
        self.btn_toggle_confocal.setEnabled(enable)
        self.btn_disk_motor.setEnabled(enable)
        self.dropdown_dichroic.setEnabled(enable)
        self.dropdown_port1_emission_filter.setEnabled(enable)
        self.dropdown_port2_emission_filter.setEnabled(enable)
        self.dropdown_field_aperture.setEnabled(enable)

    def toggle_confocal_mode(self) -> None:
        """Toggle between confocal and widefield modes"""
        self.enable_all_buttons(False)
        try:
            if self.confocal_mode:
                # Switch to widefield
                self.dragonfly.set_modality(
                    "BF"
                )  # or whatever widefield mode string is
                self.confocal_mode = False
                self.btn_toggle_confocal.setText("Switch to Confocal")
            else:
                # Switch to confocal
                self.dragonfly.set_modality("CONFOCAL")
                self.confocal_mode = True
                self.btn_toggle_confocal.setText("Switch to Widefield")

            self.signal_toggle_confocal_widefield.emit(self.confocal_mode)
        except Exception as e:
            print(f"Error toggling confocal mode: {e}")
        finally:
            self.enable_all_buttons(True)

    def toggle_disk_motor(self) -> None:
        """Toggle disk motor on/off"""
        self.enable_all_buttons(False)
        try:
            if self.btn_disk_motor.isChecked():
                self.dragonfly.set_disk_motor_state(True)
            else:
                self.dragonfly.set_disk_motor_state(False)
        except Exception as e:
            print(f"Error toggling disk motor: {e}")
        finally:
            self.enable_all_buttons(True)

    def set_dichroic(self, index: int) -> None:
        """Set dichroic position"""
        self.enable_all_buttons(False)
        try:
            selected_pos = self.dropdown_dichroic.currentIndex()
            self.dragonfly.set_port_selection_dichroic(selected_pos + 1)
        except Exception as e:
            print(f"Error setting dichroic: {e}")
        finally:
            self.enable_all_buttons(True)

    def set_port1_emission_filter(self, index: int) -> None:
        """Set port 1 emission filter position"""
        self.enable_all_buttons(False)
        try:
            selected_pos = self.dropdown_port1_emission_filter.currentIndex()
            self.dragonfly.set_emission_filter(1, selected_pos + 1)
        except Exception as e:
            print(f"Error setting port 1 emission filter: {e}")
        finally:
            self.enable_all_buttons(True)

    def set_port2_emission_filter(self, index: int) -> None:
        """Set port 2 emission filter position"""
        self.enable_all_buttons(False)
        try:
            selected_pos = self.dropdown_port2_emission_filter.currentIndex()
            self.dragonfly.set_emission_filter(2, selected_pos + 1)
        except Exception as e:
            print(f"Error setting port 2 emission filter: {e}")
        finally:
            self.enable_all_buttons(True)

    def set_field_aperture(self, index: int) -> None:
        """Set port 1 field aperture position"""
        self.enable_all_buttons(False)
        try:
            selected_pos = self.dropdown_field_aperture.currentIndex()
            self.dragonfly.set_field_aperture_wheel_position(selected_pos + 1)
        except Exception as e:
            print(f"Error setting port 1 field aperture: {e}")
        finally:
            self.enable_all_buttons(True)
