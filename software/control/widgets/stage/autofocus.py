from control.widgets.stage._common import *

class AutoFocusWidget(QFrame):
    signal_autoLevelSetting = Signal(bool)

    def __init__(self, autofocusController, main=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.autofocusController = autofocusController
        self.log = squid.logging.get_logger(self.__class__.__name__)
        self.add_components()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)
        self.stage = self.autofocusController.stage

    def add_components(self):
        self.entry_delta = QDoubleSpinBox()
        self.entry_delta.setMinimum(0)
        self.entry_delta.setMaximum(20)
        self.entry_delta.setSingleStep(0.2)
        self.entry_delta.setDecimals(3)
        self.entry_delta.setSuffix(" μm")
        self.entry_delta.setValue(1.524)
        self.entry_delta.setKeyboardTracking(False)
        self.entry_delta.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.autofocusController.set_deltaZ(1.524)

        self.entry_N = QSpinBox()
        self.entry_N.setMinimum(3)
        self.entry_N.setMaximum(10000)
        self.entry_N.setFixedWidth(self.entry_N.sizeHint().width())
        self.entry_N.setMaximum(20)
        self.entry_N.setSingleStep(1)
        self.entry_N.setValue(10)
        self.entry_N.setKeyboardTracking(False)
        self.entry_N.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.autofocusController.set_N(10)

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
        self.btn_autofocus.toggled.connect(lambda: self.autofocusController.autofocus(False))
        self.btn_autolevel.toggled.connect(self.signal_autoLevelSetting.emit)
        self.entry_delta.valueChanged.connect(self.set_deltaZ)
        self.entry_N.valueChanged.connect(self.autofocusController.set_N)
        self.autofocusController.autofocusFinished.connect(self.autofocus_is_finished)

    def set_deltaZ(self, value):
        mm_per_ustep = 1.0 / self.stage.get_config().Z_AXIS.convert_real_units_to_ustep(1.0)
        deltaZ = round(value / 1000 / mm_per_ustep) * mm_per_ustep * 1000
        self.log.debug(f"{deltaZ=}")

        self.entry_delta.setValue(deltaZ)
        self.autofocusController.set_deltaZ(deltaZ)

    def autofocus_is_finished(self):
        self.btn_autofocus.setChecked(False)
