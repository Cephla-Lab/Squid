# Filter wheel controller widget
from qtpy.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QComboBox,
    QPushButton,
    QCheckBox,
)

from squid.abc import AbstractFilterWheelController
from control.core.live_controller import LiveController


class FilterControllerWidget(QFrame):
    def __init__(
        self,
        filterController: AbstractFilterWheelController,
        liveController: LiveController,
        main=None,
        *args,
        **kwargs
    ):
        super().__init__(*args, **kwargs)
        self.filterController: AbstractFilterWheelController = filterController
        self.liveController = liveController
        self.wheel_index = 1  # Control the first filter wheel
        self.add_components()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

    def add_components(self):
        # Get filter wheel info to populate combo box
        try:
            wheel_info = self.filterController.get_filter_wheel_info(self.wheel_index)
            num_positions = wheel_info.number_of_slots
        except:
            # Fallback to 7 positions if we can't get info
            num_positions = 7

        self.comboBox = QComboBox()
        for i in range(1, num_positions + 1):
            self.comboBox.addItem(f"Position {i}")

        self.checkBox = QCheckBox("Disable filter wheel movement on changing Microscope Configuration", self)

        # Create buttons
        self.get_position_btn = QPushButton("Get Position")
        self.home_btn = QPushButton("Home")
        self.next_btn = QPushButton("Next")
        self.previous_btn = QPushButton("Previous")

        layout = QGridLayout()
        layout.addWidget(QLabel("Filter wheel position:"), 0, 0)
        layout.addWidget(self.comboBox, 0, 1)
        layout.addWidget(self.get_position_btn, 0, 2)
        layout.addWidget(self.checkBox, 2, 0, 1, 3)  # Span across 3 columns
        layout.addWidget(self.home_btn, 3, 0)
        layout.addWidget(self.next_btn, 3, 1)
        layout.addWidget(self.previous_btn, 3, 2)
        layout.addWidget(
            QLabel("For acquisition, filter wheel positions need to be set in channel configurations."), 4, 0, 1, 3
        )

        self.setLayout(layout)

        # Connect signals
        self.comboBox.currentIndexChanged.connect(self.on_selection_change)
        self.checkBox.stateChanged.connect(self.disable_movement_by_switching_channels)
        self.get_position_btn.clicked.connect(self.update_position_from_controller)
        self.home_btn.clicked.connect(self.home)
        self.next_btn.clicked.connect(self.go_to_next_position)
        self.previous_btn.clicked.connect(self.go_to_previous_position)

    def home(self):
        """Home the filter wheel."""
        self.filterController.home(self.wheel_index)

    def update_position_from_controller(self):
        """Poll the current position from the controller and update the dropdown."""
        try:
            current_pos = self.filterController.get_filter_wheel_position().get(self.wheel_index, 1)
            # Block signals temporarily to avoid triggering position change
            self.comboBox.blockSignals(True)
            self.comboBox.setCurrentIndex(current_pos - 1)  # Convert 1-indexed to 0-indexed
            self.comboBox.blockSignals(False)
            print(f"Filter wheel position updated: {current_pos}")
        except Exception as e:
            print(f"Error getting filter wheel position: {e}")

    def on_selection_change(self, index):
        """Handle position selection from combo box."""
        if index >= 0:
            position = index + 1  # Combo box is 0-indexed, positions are 1-indexed
            self.filterController.set_filter_wheel_position({self.wheel_index: position})

    def go_to_next_position(self):
        """Move to the next position."""
        try:
            current_pos = self.filterController.get_filter_wheel_position().get(self.wheel_index, 1)
            wheel_info = self.filterController.get_filter_wheel_info(self.wheel_index)
            max_pos = wheel_info.number_of_slots

            if current_pos < max_pos:
                new_pos = current_pos + 1
                self.filterController.set_filter_wheel_position({self.wheel_index: new_pos})
                self.comboBox.setCurrentIndex(new_pos - 1)  # Update combo box
        except Exception as e:
            print(f"Error moving to next position: {e}")

    def go_to_previous_position(self):
        """Move to the previous position."""
        try:
            current_pos = self.filterController.get_filter_wheel_position().get(self.wheel_index, 1)

            if current_pos > 1:
                new_pos = current_pos - 1
                self.filterController.set_filter_wheel_position({self.wheel_index: new_pos})
                self.comboBox.setCurrentIndex(new_pos - 1)  # Update combo box
        except Exception as e:
            print(f"Error moving to previous position: {e}")

    def disable_movement_by_switching_channels(self, state):
        """Enable/disable automatic filter wheel movement when changing channels."""
        if state:
            self.liveController.enable_channel_auto_filter_switching = False
        else:
            self.liveController.enable_channel_auto_filter_switching = True
