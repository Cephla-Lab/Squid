# Filter wheel controller widget
from qtpy.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QComboBox,
    QPushButton,
    QCheckBox,
)

from squid.core.events import (
    EventBus,
    SetFilterPositionCommand,
    HomeFilterWheelCommand,
    SetFilterAutoSwitchCommand,
    FilterPositionChanged,
    FilterAutoSwitchChanged,
)


class FilterControllerWidget(QFrame):
    def __init__(
        self,
        event_bus: EventBus,
        wheel_index: int = 1,
        num_positions: int = 7,
        initial_position: int = 1,
        initial_auto_switch: bool = True,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._event_bus = event_bus
        self.wheel_index = wheel_index
        self._current_position = initial_position  # Track position via events
        self._num_positions = num_positions
        self._auto_switch_enabled = initial_auto_switch
        self.add_components()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

        # Subscribe to state events
        self._event_bus.subscribe(FilterPositionChanged, self._on_filter_position_changed)
        self._event_bus.subscribe(FilterAutoSwitchChanged, self._on_auto_switch_changed)

    def add_components(self):
        self.comboBox = QComboBox()
        for i in range(1, self._num_positions + 1):
            self.comboBox.addItem(f"Position {i}")
        # Set initial position
        self.comboBox.setCurrentIndex(self._current_position - 1)

        self.checkBox = QCheckBox(
            "Disable filter wheel movement on changing Microscope Configuration", self
        )

        # Create buttons
        self.home_btn = QPushButton("Home")
        self.next_btn = QPushButton("Next")
        self.previous_btn = QPushButton("Previous")

        layout = QGridLayout()
        layout.addWidget(QLabel("Filter wheel position:"), 0, 0)
        layout.addWidget(self.comboBox, 0, 1, 1, 2)  # Span 2 columns since Get Position removed
        layout.addWidget(self.checkBox, 2, 0, 1, 3)  # Span across 3 columns
        layout.addWidget(self.home_btn, 3, 0)
        layout.addWidget(self.next_btn, 3, 1)
        layout.addWidget(self.previous_btn, 3, 2)
        layout.addWidget(
            QLabel(
                "For acquisition, filter wheel positions need to be set in channel configurations."
            ),
            4,
            0,
            1,
            3,
        )

        self.setLayout(layout)

        # Connect signals
        self.comboBox.currentIndexChanged.connect(self.on_selection_change)
        self.checkBox.stateChanged.connect(self.disable_movement_by_switching_channels)
        self.home_btn.clicked.connect(self.home)
        self.next_btn.clicked.connect(self.go_to_next_position)
        self.previous_btn.clicked.connect(self.go_to_previous_position)

        # Apply initial states without emitting commands
        self.comboBox.blockSignals(True)
        self.comboBox.setCurrentIndex(self._current_position - 1)
        self.comboBox.blockSignals(False)
        self.checkBox.blockSignals(True)
        self.checkBox.setChecked(not self._auto_switch_enabled)
        self.checkBox.blockSignals(False)

    def home(self):
        """Home the filter wheel via EventBus command."""
        self._event_bus.publish(HomeFilterWheelCommand(wheel_index=self.wheel_index))

    def on_selection_change(self, index):
        """Handle position selection from combo box."""
        if index >= 0:
            position = index + 1  # Combo box is 0-indexed, positions are 1-indexed
            self._event_bus.publish(
                SetFilterPositionCommand(position=position, wheel_index=self.wheel_index)
            )

    def go_to_next_position(self):
        """Move to the next position via EventBus command."""
        if self._current_position < self._num_positions:
            new_pos = self._current_position + 1
            self._event_bus.publish(
                SetFilterPositionCommand(position=new_pos, wheel_index=self.wheel_index)
            )

    def go_to_previous_position(self):
        """Move to the previous position via EventBus command."""
        if self._current_position > 1:
            new_pos = self._current_position - 1
            self._event_bus.publish(
                SetFilterPositionCommand(position=new_pos, wheel_index=self.wheel_index)
            )

    def disable_movement_by_switching_channels(self, state):
        """Enable/disable automatic filter wheel movement when changing channels."""
        # Publish command - LiveController subscribes to this
        self._event_bus.publish(SetFilterAutoSwitchCommand(enabled=not state))

    def _on_filter_position_changed(self, event: FilterPositionChanged):
        """Handle filter position changes from EventBus."""
        if event.wheel_index != self.wheel_index:
            return
        self._current_position = event.position
        # Update combo box
        self.comboBox.blockSignals(True)
        self.comboBox.setCurrentIndex(event.position - 1)
        self.comboBox.blockSignals(False)

    def _on_auto_switch_changed(self, event: FilterAutoSwitchChanged):
        """Handle auto-switch state changes from EventBus."""
        # Update checkbox (checked = disabled = not enabled)
        self.checkBox.blockSignals(True)
        self.checkBox.setChecked(not event.enabled)
        self.checkBox.blockSignals(False)
