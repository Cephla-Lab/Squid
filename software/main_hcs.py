# set QT_API environment variable
import argparse
import faulthandler
import glob
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

import squid.logging

squid.logging.setup_uncaught_exception_logging()

# app specific libraries
import control.gui_hcs as gui
from configparser import ConfigParser
from control.widgets import ConfigEditorBackwardsCompatible
from control._def import CACHED_CONFIG_FILE_PATH
from control._def import USE_TERMINAL_CONSOLE
import control.utils
from squid.application import ApplicationContext


if USE_TERMINAL_CONSOLE:
    from control.console import ConsoleThread


def show_config(cfp, configpath, main_gui):
    config_widget = ConfigEditorBackwardsCompatible(cfp, configpath, main_gui)
    config_widget.exec_()


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

    log = squid.logging.get_logger("main_hcs")

    if args.verbose:
        log.info("Turning on debug logging.")
        squid.logging.set_stdout_log_level(logging.DEBUG)

    if not squid.logging.add_file_logging(
        f"{squid.logging.get_default_log_directory()}/main_hcs.log"
    ):
        log.error("Couldn't setup logging to file!")
        sys.exit(1)

    log.info(
        f"Squid Repository State: {control.utils.get_squid_repo_state_description()}"
    )

    legacy_config = False
    cf_editor_parser = ConfigParser()
    config_files = glob.glob("." + "/" + "configuration*.ini")
    if config_files:
        cf_editor_parser.read(CACHED_CONFIG_FILE_PATH)
    else:
        log.error(
            "configuration*.ini file not found, defaulting to legacy configuration"
        )
        legacy_config = True
    app = QApplication([])
    app.setStyle("Fusion")
    # This allows shutdown via ctrl+C even after the gui has popped up.
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    # Create application context (centralizes microscope and controller creation)
    context = ApplicationContext(simulation=args.simulation)

    # Enable event bus debug mode if requested
    if args.debug_bus:
        from squid.events import event_bus

        # Ensure debug logging is enabled so the messages are visible
        if not args.verbose:
            squid.logging.set_stdout_log_level(logging.DEBUG)
        event_bus.set_debug(True)
        log.info("Event bus debug mode enabled")

    win = gui.HighContentScreeningGui(
        microscope=context.microscope,
        services=context.services,
        is_simulation=args.simulation,
        live_only_mode=args.live_only,
    )

    file_menu = QMenu("File", win)

    if not legacy_config:
        config_action = QAction("Microscope Settings", win)
        config_action.triggered.connect(
            lambda: show_config(cf_editor_parser, config_files[0], win)
        )
        file_menu.addAction(config_action)

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
