# set QT_API environment variable
import argparse
import glob
import logging
import os
os.environ["QT_API"] = "pyqt5"
import signal
import sys

# qt libraries
from qtpy.QtWidgets import *
from qtpy.QtGui import *

import squid.logging
squid.logging.setup_uncaught_exception_logging()

# app specific libraries
import control.gui_hcs as gui
from configparser import ConfigParser
from control.widgets import ConfigEditorBackwardsCompatible, ConfigEditorForAcquisitions
from control._def import CACHED_CONFIG_FILE_PATH
from control.console import ConsoleThread


def show_config(cfp, configpath, main_gui):
    config_widget = ConfigEditorBackwardsCompatible(cfp, configpath, main_gui)
    config_widget.exec_()


def show_acq_config(cfm):
    acq_config_widget = ConfigEditorForAcquisitions(cfm)
    acq_config_widget.exec_()

class GabeenSquid:
    def __init__(self, simulation=False, live_only=False, verbose=False):
        self.simulation = simulation
        self.live_only = live_only
        self.verbose = verbose
        self.run()
        
    def run(self):
        log = squid.logging.get_logger("main_hcs")

        if self.verbose:
            log.info("Turning on debug logging.")
            squid.logging.set_stdout_log_level(logging.DEBUG)

        if not squid.logging.add_file_logging(f"{squid.logging.get_default_log_directory()}/main_hcs.log"):
            log.error("Couldn't setup logging to file!")
            sys.exit(1)

        legacy_config = False
        cf_editor_parser = ConfigParser()
        config_files = glob.glob('.' + '/' + 'configuration*.ini')
        if config_files:
            cf_editor_parser.read(CACHED_CONFIG_FILE_PATH)
        else:
            log.error('configuration*.ini file not found, defaulting to legacy configuration')
            legacy_config = True
        app = QApplication([])
        app.setStyle('Fusion')
        # This allows shutdown via ctrl+C even after the gui has popped up.
        signal.signal(signal.SIGINT, signal.SIG_DFL)

        self.win = gui.HighContentScreeningGui(is_simulation=self.simulation, live_only_mode=self.live_only)

        acq_config_action = QAction("Acquisition Settings", self.win)
        acq_config_action.triggered.connect(lambda : show_acq_config(self.win.configurationManager))

        file_menu = QMenu("File", self.win)
        file_menu.addAction(acq_config_action)

        if not legacy_config:
            config_action = QAction("Microscope Settings", self.win)
            config_action.triggered.connect(lambda : show_config(cf_editor_parser, config_files[0], self.win))
            file_menu.addAction(config_action)

        try:
            csw = self.win.cswWindow
            if csw is not None:
                csw_action = QAction("Camera Settings",self.win)
                csw_action.triggered.connect(csw.show)
                file_menu.addAction(csw_action)
        except AttributeError:
            pass

        try:
            csw_fc = self.win.cswfcWindow
            if csw_fc is not None:
                csw_fc_action = QAction("Camera Settings (Focus Camera)", self.win)
                csw_fc_action.triggered.connect(csw_fc.show)
                file_menu.addAction(csw_fc_action)
        except AttributeError:
            pass

        menu_bar = self.win.menuBar()
        menu_bar.addMenu(file_menu)
        self.win.show()

        console_locals = {
            'microscope': self.win.microscope
        }

        console_thread = ConsoleThread(console_locals)
        console_thread.start()

        sys.exit(app.exec_())
