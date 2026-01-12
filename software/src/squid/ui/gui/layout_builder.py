"""Layout configuration helpers for the main GUI.

These helper functions extract layout setup logic from HighContentScreeningGui.setup_layout()
to reduce the size of the main gui_hcs.py file.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from squid.ui.main_window import HighContentScreeningGui

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QPushButton,
    QApplication,
    QMainWindow,
    QScrollArea,
    QFrame,
)
import pyqtgraph.dockarea as dock

from squid.core.config.feature_flags import get_feature_flags
from squid.ui.widgets import CollapsibleGroupBox

_FEATURE_FLAGS = get_feature_flags()

# Fixed width for the right control panel
CONTROL_PANEL_WIDTH = 500
CONTROL_PANEL_MIN_WIDTH = 480  # Strict minimum to show all controls


def setup_control_panel_layout(gui: "HighContentScreeningGui") -> None:
    """Configure the main control panel layout with collapsible sections."""
    layout = QVBoxLayout()
    layout.setContentsMargins(4, 4, 4, 4)
    layout.setSpacing(4)

    # Top section (Profile/Live Control) - always visible, not collapsible
    if _FEATURE_FLAGS.is_enabled("USE_NAPARI_FOR_LIVE_CONTROL") and not gui.live_only_mode:
        layout.addWidget(gui.navigationWidget)
    else:
        layout.addWidget(gui.profileWidget)
        layout.addWidget(gui.liveControlWidget)

    # Camera section - collapsible
    camera_group = CollapsibleGroupBox("Camera")
    camera_group.content.addWidget(gui.cameraTabWidget)
    layout.addWidget(camera_group)

    # DAC Control section - collapsible (if enabled)
    if _FEATURE_FLAGS.is_enabled("SHOW_DAC_CONTROL"):
        dac_group = CollapsibleGroupBox("DAC Control")
        dac_group.content.addWidget(gui.dacControlWidget)
        layout.addWidget(dac_group)

    # Acquisition section - collapsible
    acquisition_group = CollapsibleGroupBox("Acquisition")
    acquisition_group.content.addWidget(gui.recordTabWidget)
    layout.addWidget(acquisition_group)

    # Navigation section - collapsible
    navigation_group = CollapsibleGroupBox("Navigation")
    navigation_group.content.addWidget(gui.sampleSettingsWidget)
    navigation_group.content.addWidget(gui.navigationViewer)
    layout.addWidget(navigation_group)

    # Add performance mode toggle button at the bottom with natural height
    if not gui.live_only_mode:
        gui.performanceModeToggle = QPushButton("Enable Performance Mode")
        gui.performanceModeToggle.setCheckable(True)
        gui.performanceModeToggle.setChecked(gui.performance_mode)
        gui.performanceModeToggle.clicked.connect(gui.togglePerformanceMode)
        layout.addWidget(gui.performanceModeToggle)

    # Push everything up when sections are collapsed
    layout.addStretch()

    # Create content widget for controls
    controls_content = QWidget()
    controls_content.setLayout(layout)

    # Wrap in scroll area for vertical scrolling when content overflows
    gui.controlsScrollArea = QScrollArea()
    gui.controlsScrollArea.setWidget(controls_content)
    gui.controlsScrollArea.setWidgetResizable(True)
    gui.controlsScrollArea.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    gui.controlsScrollArea.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    gui.controlsScrollArea.setFrameShape(QFrame.NoFrame)


def get_main_window_minimum_size() -> tuple[int, int]:
    """Get minimum window size based on primary screen."""
    screen = QApplication.primaryScreen()
    screen_geometry = screen.availableGeometry()

    height_min = int(0.9 * screen_geometry.height())
    width_min = int(0.96 * screen_geometry.width())

    return (width_min, height_min)


def _build_image_display_container(gui: "HighContentScreeningGui") -> QWidget:
    return gui.imageDisplayTabs


def setup_single_window_layout(gui: "HighContentScreeningGui") -> None:
    """Configure single window layout with dock areas."""
    # Main display area using pyqtgraph DockArea
    main_dockArea = dock.DockArea()

    # Image Display dock
    dock_display = dock.Dock("Image Display", autoOrientation=False)
    dock_display.showTitleBar()
    dock_display.addWidget(_build_image_display_container(gui))
    dock_display.setStretch(x=100, y=100)
    main_dockArea.addDock(dock_display)

    # Well Selector dock (bottom)
    gui.dock_wellSelection = dock.Dock("Well Selector", autoOrientation=False)
    gui.dock_wellSelection.showTitleBar()
    if not _FEATURE_FLAGS.is_enabled("USE_NAPARI_WELL_SELECTION") or gui.live_only_mode:
        gui.dock_wellSelection.addWidget(gui.wellSelectionWidget)
        gui.dock_wellSelection.setFixedHeight(
            gui.dock_wellSelection.minimumSizeHint().height()
        )
        main_dockArea.addDock(gui.dock_wellSelection, "bottom")

    # Controls dock (right side)
    gui.dock_controls = dock.Dock("Controls", autoOrientation=False)
    gui.dock_controls.showTitleBar()
    gui.dock_controls.addWidget(gui.controlsScrollArea)
    gui.dock_controls.setStretch(x=1, y=100)
    # Set minimum width on the scroll area content
    gui.controlsScrollArea.setMinimumWidth(CONTROL_PANEL_MIN_WIDTH)
    main_dockArea.addDock(gui.dock_controls, "right")

    # Focus Lock dock (below Controls, if available)
    if _FEATURE_FLAGS.is_enabled("SUPPORT_LASER_AUTOFOCUS"):
        focus_lock_widget = getattr(gui, "focusLockStatusWidget", None)
        if focus_lock_widget is not None:
            gui.dock_focusLock = dock.Dock("Focus Lock", autoOrientation=False)
            gui.dock_focusLock.showTitleBar()
            gui.dock_focusLock.addWidget(focus_lock_widget)
            gui.dock_focusLock.setStretch(x=1, y=1)
            # Set minimum width on Focus Lock widget
            focus_lock_widget.setMinimumWidth(CONTROL_PANEL_MIN_WIDTH)
            main_dockArea.addDock(gui.dock_focusLock, "bottom", gui.dock_controls)

            # Connect collapse to change height
            _setup_focus_lock_collapse(gui, focus_lock_widget)

    # Wrap with warning banner if simulated disk I/O is enabled
    central_widget = _wrap_with_warning_banner(gui, main_dockArea)
    gui.setCentralWidget(central_widget)

    gui.setMinimumSize(*get_main_window_minimum_size())
    gui.onTabChanged(gui.recordTabWidget.currentIndex())


def _setup_focus_lock_collapse(gui: "HighContentScreeningGui", focus_lock_widget) -> None:
    """Connect Focus Lock collapse to adjust dock height."""
    EXPANDED_HEIGHT = 380  # Extra height for bar spacing
    COLLAPSED_HEIGHT = 70  # Enough to show summary row

    # Set initial height
    gui.dock_focusLock.setFixedHeight(EXPANDED_HEIGHT)

    # Override the collapse toggle to also resize the dock
    if hasattr(focus_lock_widget, "_collapse_btn"):
        original_toggle = focus_lock_widget._toggle_collapsed

        def on_collapse_toggle() -> None:
            original_toggle()
            if focus_lock_widget._collapsed:
                gui.dock_focusLock.setFixedHeight(COLLAPSED_HEIGHT)
            else:
                gui.dock_focusLock.setFixedHeight(EXPANDED_HEIGHT)

        focus_lock_widget._collapse_btn.clicked.disconnect()
        focus_lock_widget._collapse_btn.clicked.connect(on_collapse_toggle)


def _wrap_with_warning_banner(gui: "HighContentScreeningGui", main_widget: QWidget) -> QWidget:
    """Wrap main widget with a warning banner if simulated disk I/O is enabled."""
    if getattr(gui, "simulated_io_warning_banner", None) is None:
        return main_widget

    # Create container with banner on top
    container = QWidget()
    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    layout.addWidget(gui.simulated_io_warning_banner)
    layout.addWidget(main_widget)
    return container


def setup_multi_window_layout(gui: "HighContentScreeningGui") -> None:
    """Configure multi-window layout with separate display window."""
    gui.setCentralWidget(gui.controlsScrollArea)
    gui.tabbedImageDisplayWindow = QMainWindow()
    gui.tabbedImageDisplayWindow.setCentralWidget(_build_image_display_container(gui))
    gui.tabbedImageDisplayWindow.setWindowFlags(
        gui.windowFlags() | Qt.CustomizeWindowHint
    )
    gui.tabbedImageDisplayWindow.setWindowFlags(
        gui.windowFlags() & ~Qt.WindowCloseButtonHint
    )
    (width_min, height_min) = get_main_window_minimum_size()
    gui.tabbedImageDisplayWindow.setFixedSize(width_min, height_min)
    gui.tabbedImageDisplayWindow.show()
