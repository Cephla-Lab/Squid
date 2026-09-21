"""The wide Fluidics display tab: instrument on the left (Initialize, manual control, device status,
Log | Temperature | Flow | Reagents), the Protocol editor on the right."""

from typing import Callable, Optional, Tuple

from qtpy.QtCore import QEvent, Qt, QTimer, Signal
from qtpy.QtWidgets import (
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

import squid.logging
from control.widgets_fluidics.log_view import FluidicsLogView, ReagentsTable
from control.widgets_fluidics.protocol_tab import ProtocolTab
from control.widgets_fluidics.system_panel import DeviceStatusGroup, SystemPanel


class InstrumentColumn(QSplitter):
    """The display tab's left column: the instrument block over the Log / sensor tabs.

    The block asks for more height than a short window has, and a matplotlib canvas accepts any
    height down to nothing, so in a plain column the plots were squeezed to a sliver. Here the block
    scrolls instead: it gets the height it asks for unless that leaves the tabs less than TABS_ROOM
    times their minimum height. Once the operator drags the divider the split is theirs (a window
    resize then scales both panes, as any splitter does)."""

    # Stands in for the library's plot canvas declaring no minimum height of its own (were it to,
    # the splitter would hold that floor natively and this goes). The tabs' minimum height is where
    # their tallest page's plot is flat (all controls, no canvas); half as much again is a plot one
    # can read. Dimensionless, so it follows the font and the style.
    TABS_ROOM = 1.5

    def __init__(self, instrument: QWidget, tabs: QWidget, parent=None):
        super().__init__(Qt.Vertical, parent)
        self._instrument = instrument
        self._tabs = tabs
        self.instrument_scroll = QScrollArea()
        self.instrument_scroll.setWidgetResizable(True)
        self.instrument_scroll.setFrameShape(QFrame.NoFrame)
        self.instrument_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.instrument_scroll.setWidget(instrument)
        self.addWidget(self.instrument_scroll)
        self.addWidget(tabs)
        self.setChildrenCollapsible(False)
        self._dragged = False
        self.splitterMoved.connect(self._on_dragged)
        for pane in (instrument, tabs):
            pane.installEventFilter(self)
        self._content_changed()

    def tabs_floor(self) -> int:
        return round(self.TABS_ROOM * self._tabs.minimumSizeHint().height())

    def eventFilter(self, watched, event) -> bool:
        # Installed on the two panes only. A pane's size hints are fresh once its own LayoutRequest
        # arrives (Qt propagates them up one posted event at a time), so that is when to look, not
        # when a widget is added.
        if event.type() == QEvent.LayoutRequest:
            self._content_changed()
        return super().eventFilter(watched, event)

    def _content_changed(self) -> None:
        """A pane gained or lost widgets (Initialize mounts manual control and the sensor tabs):
        never clip the block sideways, scroll bar included, and share the height again."""
        scroll_bar_width = self.instrument_scroll.verticalScrollBar().sizeHint().width()
        self.instrument_scroll.setMinimumWidth(self._instrument.minimumSizeHint().width() + scroll_bar_width)
        self._balance()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._balance()

    def _on_dragged(self, _pos: int, _index: int) -> None:
        self._dragged = True

    def _balance(self) -> None:
        height = sum(self.sizes())
        if self._dragged or height == 0:  # 0: not laid out yet
            return
        wanted = self._instrument.sizeHint().height()
        block = max(0, min(wanted, height - self.tabs_floor()))
        self.setSizes([block, height - block])


class FluidicsDisplayTab(QWidget):
    system_ready = Signal()

    def __init__(
        self,
        service,
        current_source: Optional[Callable[[], Tuple[Optional[str], dict, dict]]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self._log = squid.logging.get_logger(__name__)
        self.service = service
        self.fluidics_port = None
        self.temperature_tab = None
        self.flow_tab = None
        self.run_line_provider: Callable[[], str] = lambda: "—"

        self.system_panel = SystemPanel(service)
        self.system_panel.initialized.connect(self._on_initialized)

        self.manual_group = QGroupBox("Manual control")
        manual_layout = QVBoxLayout()
        self._manual_placeholder = QLabel("Initialize the fluidics system to enable manual control.")
        self._manual_placeholder.setStyleSheet("color: gray;")
        manual_layout.addWidget(self._manual_placeholder)

        # One-off priming/cleaning as an inline row (no pop-ups), the old widget's fields:
        # which ports to prime (their tubing filled with the config's per-port amounts), the
        # wash port the final volume is drawn from, that volume, the flow rate, and a repeat.
        self.ports_edit = QLineEdit()
        self.ports_edit.setPlaceholderText("ports to prime, e.g. 1-4, 25")
        self.wash_port_spin = QSpinBox()
        self.wash_port_spin.setRange(1, 100000)
        self.wash_port_spin.setValue(1)
        self.wash_port_spin.setPrefix("wash ")
        self.volume_spin = QSpinBox()
        self.volume_spin.setRange(1, 100000)
        self.volume_spin.setValue(200)
        self.volume_spin.setSuffix(" µL")
        self.flow_spin = QSpinBox()
        self.flow_spin.setRange(1, 100000)
        self.flow_spin.setValue(2000)
        self.flow_spin.setSuffix(" µL/min")
        self.repeat_spin = QSpinBox()
        self.repeat_spin.setRange(1, 99)
        self.repeat_spin.setValue(1)
        self.repeat_spin.setPrefix("×")
        self.prime_button = QPushButton("Prime")
        self.prime_button.clicked.connect(lambda: self._quick_op("priming"))
        self.clean_button = QPushButton("Clean")
        self.clean_button.clicked.connect(lambda: self._quick_op("clean_up"))
        self.stop_quick_button = QPushButton("Stop")
        self.stop_quick_button.clicked.connect(self._stop_quick_op)
        self.stop_quick_button.hide()
        self._quick_widgets = [
            self.ports_edit,
            self.wash_port_spin,
            self.volume_spin,
            self.flow_spin,
            self.repeat_spin,
            self.prime_button,
            self.clean_button,
        ]
        for widget in self._quick_widgets:
            widget.setEnabled(False)  # need the initialized system

        quick_row = QHBoxLayout()
        quick_row.addWidget(QLabel("Prime / Clean:"))
        quick_row.addWidget(self.ports_edit, 1)
        quick_row.addWidget(self.wash_port_spin)
        quick_row.addWidget(self.volume_spin)
        quick_row.addWidget(self.flow_spin)
        quick_row.addWidget(self.repeat_spin)
        quick_row.addWidget(self.prime_button)
        quick_row.addWidget(self.clean_button)
        quick_row.addWidget(self.stop_quick_button)
        manual_layout.addLayout(quick_row)
        self.manual_group.setLayout(manual_layout)
        self._quick_running = False

        self.device_status = DeviceStatusGroup()

        self.log_view = FluidicsLogView()
        self.log_view.connect_logging()
        self.reagents_table = ReagentsTable()
        self.tabs = QTabWidget()
        self.tabs.addTab(self.log_view, "Log")
        self.tabs.addTab(self.reagents_table, "Reagents")

        instrument = QWidget()
        instrument_layout = QVBoxLayout()
        instrument_layout.addWidget(self.system_panel)
        instrument_layout.addWidget(self.manual_group)
        instrument_layout.addWidget(self.device_status)
        instrument.setLayout(instrument_layout)
        self.instrument_column = InstrumentColumn(instrument, self.tabs)

        self.protocol_tab = ProtocolTab(service, current_source=current_source)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.instrument_column)
        splitter.addWidget(self.protocol_tab)
        # Content-driven so the instrument column (the temperature plots need real width)
        # is never clipped; the divider is draggable and both panes share resize space.
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        layout = QVBoxLayout()
        layout.addWidget(splitter)
        self.setLayout(layout)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_status)
        self._timer.start(1000)

    def _on_initialized(self) -> None:
        try:
            from control.core.fluidics_protocol.library_port import LibraryFluidicsPort

            self.fluidics_port = LibraryFluidicsPort(self.service.system)
        except Exception:
            self._log.exception("Could not build the fluidics port")
        self.device_status.attach(self.service)

        try:
            from fluidics.qt.manual_control import ManualControlWidget

            manual = ManualControlWidget(self.service.config, self.service.system)
            self._manual_placeholder.hide()
            self.manual_group.layout().addWidget(manual)
            self.manual_widget = manual
        except ImportError:
            self._manual_placeholder.setText("Manual control needs the updated fluidics library (fluidics.qt).")
        except Exception:
            self._log.exception("Could not build the manual-control widget")

        tc = self.service.system.devices.temperature_controller
        if tc is not None:
            try:
                from fluidics.qt.sensor_plots import TemperatureControlWidget

                self.temperature_tab = TemperatureControlWidget(tc)
                self.tabs.insertTab(self.tabs.indexOf(self.reagents_table), self.temperature_tab, "Temperature")
            except Exception:
                self._log.exception("Could not build the Temperature tab")

        sensors = self.service.system.devices.flow_sensors
        if sensors:
            try:
                from fluidics.qt.sensor_plots import FlowSensorControlWidget

                # Only the Flow Cell operations arm the sensors (the library's own GUI draws the same line).
                draw_protection = self.service.config.application == "Flow Cell"
                if not draw_protection:
                    self._switch_off_inert_draw_protection(sensors)
                self.flow_tab = FlowSensorControlWidget(sensors, draw_protection=draw_protection)
                self.tabs.insertTab(self.tabs.indexOf(self.reagents_table), self.flow_tab, "Flow")
            except Exception:
                self._log.exception("Could not build the Flow tab")
        for widget in self._quick_widgets:
            widget.setEnabled(True)
        self.system_ready.emit()

    def _switch_off_inert_draw_protection(self, sensors) -> None:
        """A warn/stop mode configured on an application that never arms the sensors would
        leave the operator believing a draw is protected: switch it off and say so."""
        configured = [sensor.name for sensor in sensors if sensor.monitor != "off"]
        if not configured:
            return
        for sensor in sensors:
            sensor.monitor = "off"
        self._log.warning(
            f"Draw protection is configured for {', '.join(configured)} but is only available for the "
            "Flow Cell application. The sensors will read and plot; they will not stop a draw."
        )

    def shutdown(self) -> None:
        """Exit/restart path: detach logging and close the plot widgets' open CSV
        recordings (an embedded tab gets no closeEvent, so the host must ask; see
        SensorTabWidget.close_recordings)."""
        self.log_view.disconnect_logging()
        for sensor_tab in (self.temperature_tab, self.flow_tab):
            if sensor_tab is not None:
                sensor_tab.close_recordings()

    def set_run_active(self, active: bool) -> None:
        """A running protocol owns the instrument: manual control and TEC setpoints go
        dead (the plots and their recording stay live)."""
        self.manual_group.setEnabled(not active)
        if self.temperature_tab is not None:
            self.temperature_tab.setControlsEnabled(not active)

    def quick_op_active(self) -> bool:
        return self._quick_running

    def _quick_op(self, op: str) -> None:
        # The old widget's behavior: prime the tubing for use_ports (config per-port amounts),
        # then draw `volume` from the wash port. Driven as a manual verb so use_ports survives
        # (the sequence model dropped it); Clean just repeats.
        if self.fluidics_port is None or self._quick_running:
            return
        from control.models.fluidics_protocol import parse_port_list

        use_ports = parse_port_list(self.ports_edit.text())
        if not use_ports:
            self._log.error("Prime/Clean: name at least one port to prime (e.g. 1-4, 25)")
            return
        verb_name = "prime" if op == "priming" else "clean"
        wash_port = int(self.wash_port_spin.value())
        flow_rate = int(self.flow_spin.value())
        volume = int(self.volume_spin.value())
        repeat = int(self.repeat_spin.value()) if op == "clean_up" else 1
        operations = self.service.system.operations

        def verb():
            for _ in range(repeat):
                operations.priming_or_clean_up(wash_port, flow_rate, volume, use_ports=use_ports)

        try:
            self.service.system.run_manual(
                verb,
                callbacks={
                    "on_error": lambda message: self._log.error(f"Manual {verb_name} failed: {message}"),
                    "on_stopped": lambda: self._log.info(f"Manual {verb_name} stopped"),
                    "on_finished": lambda: self._log.info(f"Manual {verb_name} finished"),
                },
            )
        except Exception as e:  # RuntimeError while another job holds the session
            self._log.error(f"Prime/Clean could not start: {e}")
            return
        self._set_quick_running(True)
        self._log.info(f"Manual {verb_name} started on {len(use_ports)} port(s)")

    def _stop_quick_op(self) -> None:
        if self._quick_running:
            self.service.system.abort()

    def _set_quick_running(self, running: bool) -> None:
        """Flip the inline Prime/Clean row between running (fields locked, Stop shown) and idle."""
        self._quick_running = running
        for widget in self._quick_widgets:
            widget.setEnabled(not running)
        self.stop_quick_button.setVisible(running)

    def _poll_quick_op(self) -> None:
        # The manual verb runs on the library's job thread; when the session reads free the
        # inline row comes back (the callbacks already logged the outcome).
        if not self._quick_running or self.service.system.busy:
            return
        self._set_quick_running(False)

    def _refresh_status(self) -> None:
        try:
            self._poll_quick_op()
            self.device_status.refresh(self.run_line_provider())
        except Exception as e:  # Qt swallows timer-slot exceptions: log explicitly
            self._log.error(f"Device status refresh failed: {e}")
