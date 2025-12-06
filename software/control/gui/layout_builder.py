"""Layout configuration helpers for the main GUI.

These helper functions extract layout setup logic from HighContentScreeningGui.setup_layout()
to reduce the size of the main gui_hcs.py file.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from control.gui_hcs import HighContentScreeningGui

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QSplitter,
    QPushButton,
    QDesktopWidget,
    QMainWindow,
)
import pyqtgraph.dockarea as dock

from control._def import (
    USE_NAPARI_FOR_LIVE_CONTROL,
    USE_NAPARI_WELL_SELECTION,
    SHOW_DAC_CONTROL,
)


def setup_control_panel_layout(gui: "HighContentScreeningGui") -> None:
    """Configure the main control panel layout."""
    layout = QVBoxLayout()

    if USE_NAPARI_FOR_LIVE_CONTROL and not gui.live_only_mode:
        layout.addWidget(gui.navigationWidget)
    else:
        layout.addWidget(gui.profileWidget)
        layout.addWidget(gui.liveControlWidget)

    layout.addWidget(gui.cameraTabWidget)

    if SHOW_DAC_CONTROL:
        layout.addWidget(gui.dacControlWidget)

    # Create a widget to hold sample settings and navigation viewer
    navigation_section_widget = QWidget()
    navigation_section_layout = QVBoxLayout()
    navigation_section_layout.setContentsMargins(0, 0, 0, 0)
    navigation_section_layout.setSpacing(0)
    navigation_section_layout.addWidget(gui.sampleSettingsWidget)
    navigation_section_layout.addWidget(gui.navigationViewer)
    navigation_section_widget.setLayout(navigation_section_layout)

    # Create a splitter between recordTabWidget and navigation section (50/50)
    splitter = QSplitter(Qt.Vertical)
    splitter.addWidget(gui.recordTabWidget)
    splitter.addWidget(navigation_section_widget)
    splitter.setStretchFactor(0, 1)  # recordTabWidget 50%
    splitter.setStretchFactor(1, 1)  # navigation section 50%

    layout.addWidget(splitter)

    # Add performance mode toggle button at the bottom with natural height
    if not gui.live_only_mode:
        gui.performanceModeToggle = QPushButton("Enable Performance Mode")
        gui.performanceModeToggle.setCheckable(True)
        gui.performanceModeToggle.setChecked(gui.performance_mode)
        gui.performanceModeToggle.clicked.connect(gui.togglePerformanceMode)
        layout.addWidget(gui.performanceModeToggle)

    gui.centralWidget = QWidget()
    gui.centralWidget.setLayout(layout)
    gui.centralWidget.setFixedWidth(gui.centralWidget.minimumSizeHint().width())


def get_main_window_minimum_size() -> tuple[int, int]:
    """Get minimum window size based on primary screen."""
    desktop_info = QDesktopWidget()
    primary_screen_size = desktop_info.screen(desktop_info.primaryScreen()).size()

    height_min = int(0.9 * primary_screen_size.height())
    width_min = int(0.96 * primary_screen_size.width())

    return (width_min, height_min)


def setup_single_window_layout(gui: "HighContentScreeningGui") -> None:
    """Configure single window layout with dock areas."""
    main_dockArea = dock.DockArea()

    dock_display = dock.Dock("Image Display", autoOrientation=False)
    dock_display.showTitleBar()
    dock_display.addWidget(gui.imageDisplayTabs)
    dock_display.setStretch(x=100, y=100)
    main_dockArea.addDock(dock_display)

    gui.dock_wellSelection = dock.Dock("Well Selector", autoOrientation=False)
    gui.dock_wellSelection.showTitleBar()
    if not USE_NAPARI_WELL_SELECTION or gui.live_only_mode:
        gui.dock_wellSelection.addWidget(gui.wellSelectionWidget)
        gui.dock_wellSelection.setFixedHeight(gui.dock_wellSelection.minimumSizeHint().height())
        main_dockArea.addDock(gui.dock_wellSelection, "bottom")

    dock_controlPanel = dock.Dock("Controls", autoOrientation=False)
    dock_controlPanel.addWidget(gui.centralWidget)
    dock_controlPanel.setStretch(x=1, y=None)
    dock_controlPanel.setFixedWidth(dock_controlPanel.minimumSizeHint().width())
    main_dockArea.addDock(dock_controlPanel, "right")
    gui.setCentralWidget(main_dockArea)

    gui.setMinimumSize(*get_main_window_minimum_size())
    gui.onTabChanged(gui.recordTabWidget.currentIndex())


def setup_multi_window_layout(gui: "HighContentScreeningGui") -> None:
    """Configure multi-window layout with separate display window."""
    gui.setCentralWidget(gui.centralWidget)
    gui.tabbedImageDisplayWindow = QMainWindow()
    gui.tabbedImageDisplayWindow.setCentralWidget(gui.imageDisplayTabs)
    gui.tabbedImageDisplayWindow.setWindowFlags(gui.windowFlags() | Qt.CustomizeWindowHint)
    gui.tabbedImageDisplayWindow.setWindowFlags(gui.windowFlags() & ~Qt.WindowCloseButtonHint)
    (width_min, height_min) = get_main_window_minimum_size()
    gui.tabbedImageDisplayWindow.setFixedSize(width_min, height_min)
    gui.tabbedImageDisplayWindow.show()
