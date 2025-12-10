from control.widgets.stage._common import *
from squid.events import (
    StartAutofocusCommand,
    StopAutofocusCommand,
    SetAutofocusParamsCommand,
    AutofocusProgress,
    AutofocusCompleted,
)


class AutoFocusWidget(EventBusFrame):
    signal_autoLevelSetting: Signal = Signal(bool)

    def __init__(self, event_bus: "EventBus", *args: Any, **kwargs: Any) -> None:
        super().__init__(event_bus, *args, **kwargs)
        self.log = squid.logging.get_logger(self.__class__.__name__)

        # UI components
        self.entry_delta: QDoubleSpinBox
        self.entry_N: QSpinBox
        self.btn_autofocus: QPushButton
        self.btn_autolevel: QPushButton
        self.grid: QVBoxLayout

        # Subscribe to autofocus state events
        self._subscribe(AutofocusProgress, self._on_autofocus_progress)
        self._subscribe(AutofocusCompleted, self._on_autofocus_completed)

        self.add_components()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

    def add_components(self) -> None:
        self.entry_delta = QDoubleSpinBox()
        self.entry_delta.setMinimum(0)
        self.entry_delta.setMaximum(20)
        self.entry_delta.setSingleStep(0.2)
        self.entry_delta.setDecimals(3)
        self.entry_delta.setSuffix(" μm")
        self.entry_delta.setValue(1.524)
        self.entry_delta.setKeyboardTracking(False)
        self.entry_delta.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.entry_N = QSpinBox()
        self.entry_N.setMinimum(3)
        self.entry_N.setMaximum(10000)
        self.entry_N.setFixedWidth(self.entry_N.sizeHint().width())
        self.entry_N.setMaximum(20)
        self.entry_N.setSingleStep(1)
        self.entry_N.setValue(10)
        self.entry_N.setKeyboardTracking(False)
        self.entry_N.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.btn_autofocus = QPushButton("Autofocus")
        self.btn_autofocus.setDefault(False)
        self.btn_autofocus.setCheckable(True)
        self.btn_autofocus.setChecked(False)

        self.btn_autolevel = QPushButton("Autolevel")
        self.btn_autolevel.setCheckable(True)
        self.btn_autolevel.setChecked(False)
        self.btn_autolevel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        # layout
        self.grid = QVBoxLayout()
        grid_line0 = QHBoxLayout()
        grid_line0.addWidget(QLabel("\u0394 Z"))
        grid_line0.addWidget(self.entry_delta)
        grid_line0.addSpacing(20)
        grid_line0.addWidget(QLabel("# of Z-Planes"))
        grid_line0.addWidget(self.entry_N)
        grid_line0.addSpacing(20)
        grid_line0.addWidget(self.btn_autolevel)

        self.grid.addLayout(grid_line0)
        self.grid.addWidget(self.btn_autofocus)
        self.setLayout(self.grid)

        # connections
        self.btn_autofocus.toggled.connect(self._on_autofocus_toggled)
        self.btn_autolevel.toggled.connect(self.signal_autoLevelSetting.emit)
        self.entry_delta.valueChanged.connect(self._publish_params)
        self.entry_N.valueChanged.connect(self._publish_params)

    def _publish_params(self) -> None:
        """Publish autofocus parameters."""
        self._publish(
            SetAutofocusParamsCommand(
                n_planes=int(self.entry_N.value()),
                delta_z_um=float(self.entry_delta.value()),
            )
        )

    def _on_autofocus_toggled(self, enabled: bool) -> None:
        """Start or stop autofocus."""
        if enabled:
            self._publish_params()
            self._publish(StartAutofocusCommand())
        else:
            self._publish(StopAutofocusCommand())

    def _on_autofocus_progress(self, event: AutofocusProgress) -> None:
        """Handle progress updates (placeholder for future UI)."""
        # No progress bar present; keep hook for future use.
        self.log.debug(
            f"Autofocus progress: step {event.current_step}/{event.total_steps}"
        )

    def _on_autofocus_completed(self, event: AutofocusCompleted) -> None:
        """Re-enable controls after autofocus finishes."""
        self.btn_autofocus.setChecked(False)
