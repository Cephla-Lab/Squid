# set QT_API environment variable
import argparse
import faulthandler
import logging
import os

os.environ["QT_API"] = "pyqt5"
import signal
import sys

# Enable faulthandler to print traceback on segfault or deadlock
# This helps debug hung threads and crashes
faulthandler.enable()

# qt libraries
from qtpy.QtWidgets import *
from qtpy.QtGui import *

import squid.core.logging

squid.core.logging.setup_uncaught_exception_logging()

# app specific libraries
import squid.ui.main_window as gui
from _def import USE_TERMINAL_CONSOLE
from _def import SQUID_ICON_PATH
import squid.core.utils.hardware_utils
from squid.application import ApplicationContext


if USE_TERMINAL_CONSOLE:
    from squid.ui.console import ConsoleThread


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--simulation", help="Run the GUI with simulated hardware.", action="store_true"
    )
    parser.add_argument(
        "--live-only", help="Run the GUI only the live viewer.", action="store_true"
    )
    parser.add_argument(
        "--verbose", help="Turn on verbose logging (DEBUG level)", action="store_true"
    )
    parser.add_argument(
        "--debug-bus",
        help="Print all messages going through the event bus",
        action="store_true",
    )
    args = parser.parse_args()

    log = squid.core.logging.get_logger("main_hcs")

    if args.verbose:
        log.info("Turning on debug logging.")
        squid.core.logging.set_stdout_log_level(logging.DEBUG)

    if not squid.core.logging.add_file_logging(
        f"{squid.core.logging.get_default_log_directory()}/main_hcs.log"
    ):
        log.error("Couldn't setup logging to file!")
        sys.exit(1)

    log.info(
        f"Squid Repository State: {squid.core.utils.hardware_utils.get_squid_repo_state_description()}"
    )

    app = QApplication([])
    app.setStyle("Fusion")
    app.setWindowIcon(QIcon(str(SQUID_ICON_PATH)))
    # This allows shutdown via ctrl+C even after the gui has popped up.
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    # Create application context (centralizes microscope and controller creation)
    context = ApplicationContext(simulation=args.simulation)

    # Enable event bus debug mode if requested
    if args.debug_bus:
        from squid.core.events import event_bus

        # Ensure debug logging is enabled so the messages are visible
        if not args.verbose:
            squid.core.logging.set_stdout_log_level(logging.DEBUG)
        event_bus.set_debug(True)
        log.info("Event bus debug mode enabled")

    win = gui.HighContentScreeningGui(
        microscope=context.microscope,
        controllers=context.controllers,
        services=context.services,
        is_simulation=args.simulation,
        live_only_mode=args.live_only,
    )

    file_menu = QMenu("File", win)

    microscope_utils_menu = QMenu("Utils", win)

    stage_utils_action = QAction("Stage Utils", win)
    stage_utils_action.triggered.connect(win.stageUtils.show)
    microscope_utils_menu.addAction(stage_utils_action)

    try:
        csw = win.cswWindow
        if csw is not None:
            csw_action = QAction("Camera Settings", win)
            csw_action.triggered.connect(csw.show)
            file_menu.addAction(csw_action)
    except AttributeError:
        pass

    try:
        csw_fc = win.cswfcWindow
        if csw_fc is not None:
            csw_fc_action = QAction("Camera Settings (Focus Camera)", win)
            csw_fc_action.triggered.connect(csw_fc.show)
            file_menu.addAction(csw_fc_action)
    except AttributeError:
        pass

    menu_bar = win.menuBar()
    menu_bar.addMenu(file_menu)
    menu_bar.addMenu(microscope_utils_menu)
    win.show()

    if USE_TERMINAL_CONSOLE:
        console_locals = {"microscope": context.microscope, "context": context}
        console_thread = ConsoleThread(console_locals)
        console_thread.start()

    try:
        sys.exit(app.exec_())
    finally:
        # Clean shutdown of application context
        context.shutdown()
