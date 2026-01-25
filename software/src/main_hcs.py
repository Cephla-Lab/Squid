# set QT_API environment variable
import argparse
import faulthandler
import logging
import os
import shutil
import subprocess

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
from _def import SIMULATED_DISK_IO_ENABLED
from _def import SIMULATION_FORCE_SAVE_IMAGES
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
    parser.add_argument(
        "--start-server",
        help="Start TCP control server for headless automation",
        action="store_true",
    )
    parser.add_argument(
        "--server-port",
        type=int,
        default=5050,
        help="TCP server port (default: 5050)",
    )
    parser.add_argument(
        "--skip-init",
        action="store_true",
        help="Skip hardware initialization (MCU reset, homing, limits). "
        "Used when restarting after settings change.",
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
    context = ApplicationContext(simulation=args.simulation, skip_init=args.skip_init)

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

    # Settings menu with MCP integration
    settings_menu = QMenu("Settings", win)
    menu_bar.addMenu(settings_menu)

    # MCP Control Server state (using dict for mutable state in closures)
    mcp_state = {"process": None, "python_exec_enabled": False}

    def on_toggle_mcp_server(checked):
        if checked:
            # Start MCP server
            try:
                server_args = ["python", "-m", "squid.mcp.server"]
                server_args.append("--simulation" if args.simulation else "--real")
                mcp_state["process"] = subprocess.Popen(
                    server_args,
                    cwd=os.path.dirname(__file__),
                )
                log.info(f"MCP server started (PID: {mcp_state['process'].pid})")
            except Exception as e:
                log.error(f"Failed to start MCP server: {e}")
                action_enable_mcp.setChecked(False)
        else:
            # Stop MCP server
            if mcp_state["process"] is not None:
                mcp_state["process"].terminate()
                try:
                    mcp_state["process"].wait(timeout=5)
                except subprocess.TimeoutExpired:
                    mcp_state["process"].kill()
                log.info("MCP server stopped")
                mcp_state["process"] = None

    def on_toggle_python_exec(checked):
        if checked:
            reply = QMessageBox.warning(
                win,
                "Security Warning",
                "Enabling Python exec allows Claude to run arbitrary code.\n\n"
                "Only enable this if you trust the AI agent completely.\n\n"
                "Continue?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                action_enable_python_exec.setChecked(False)
                return
        mcp_state["python_exec_enabled"] = checked
        # Note: This sets a local flag. The MCP server would need to be restarted
        # or communicate via IPC to actually toggle this. For now, this is a placeholder.
        log.info(f"MCP Python exec {'enabled' if checked else 'disabled'}")

    def on_launch_claude_code():
        # Ensure MCP server is running
        if not action_enable_mcp.isChecked():
            action_enable_mcp.setChecked(True)
            on_toggle_mcp_server(True)

        # Check if claude is installed
        if shutil.which("claude") is None:
            reply = QMessageBox.question(
                win,
                "Claude Code Not Found",
                "Claude Code CLI not found. Would you like to install it?\n\n"
                "This requires npm to be installed.",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                try:
                    subprocess.run(
                        ["npm", "install", "-g", "@anthropic-ai/claude-code"],
                        check=True,
                    )
                    QMessageBox.information(
                        win, "Installation Complete", "Claude Code installed successfully."
                    )
                except Exception as e:
                    QMessageBox.critical(
                        win, "Installation Failed", f"Failed to install Claude Code: {e}"
                    )
                    return
            else:
                return

        # Open terminal with Claude Code in the software directory
        software_dir = os.path.dirname(__file__)
        try:
            if sys.platform == "darwin":
                # macOS: open Terminal.app in the directory
                subprocess.Popen(
                    ["open", "-a", "Terminal", software_dir],
                )
            elif sys.platform == "linux":
                # Linux: try common terminals
                for terminal in ["gnome-terminal", "konsole", "xterm"]:
                    if shutil.which(terminal):
                        subprocess.Popen([terminal, "--working-directory", software_dir])
                        break
            elif sys.platform == "win32":
                # Windows: open cmd in directory
                subprocess.Popen(["cmd", "/c", "start", "cmd", "/k", f"cd /d {software_dir}"])
            log.info("Launched terminal for Claude Code")
        except Exception as e:
            QMessageBox.warning(
                win, "Launch Failed", f"Could not launch terminal: {e}\n\nRun 'claude' manually."
            )

    action_enable_mcp = QAction("Enable MCP Control Server", win, checkable=True)
    action_enable_mcp.triggered.connect(on_toggle_mcp_server)
    settings_menu.addAction(action_enable_mcp)

    action_enable_python_exec = QAction("Enable MCP Python Exec", win, checkable=True)
    action_enable_python_exec.triggered.connect(on_toggle_python_exec)
    settings_menu.addAction(action_enable_python_exec)

    settings_menu.addSeparator()

    action_launch_claude = QAction("Launch Claude Code", win)
    action_launch_claude.triggered.connect(on_launch_claude_code)
    settings_menu.addAction(action_launch_claude)

    # TCP Control Server state
    tcp_server = None

    if args.start_server:
        try:
            from squid.backend.services.tcp_control_server import TCPControlServer
            from squid.core.events import event_bus

            tcp_server = TCPControlServer(
                event_bus=event_bus,
                objective_store=context.microscope.objective_store,
                camera=context.microscope.camera,
                stage=context.microscope.stage,
                channel_config_manager=context.microscope.channel_configuration_manager,
                port=args.server_port,
            )
            tcp_server.start()
            log.info(f"TCP control server started on port {args.server_port}")
        except Exception as e:
            log.error(f"Failed to start TCP control server: {e}")

    # Show startup warning if simulated disk I/O is enabled (but not if force save is on)
    if SIMULATED_DISK_IO_ENABLED and not SIMULATION_FORCE_SAVE_IMAGES:
        QMessageBox.warning(
            win,
            "Simulated Disk I/O Mode",
            "SIMULATED DISK I/O IS ENABLED\n\n"
            "Images are encoded but NOT saved to disk!\n\n"
            "This mode is for testing acquisition speed only.\n"
            "NO DATA WILL BE SAVED during acquisitions.\n\n"
            "To disable: Configuration > Advanced > Development Settings",
        )

    win.showMaximized()

    if USE_TERMINAL_CONSOLE:
        console_locals = {"microscope": context.microscope, "context": context}
        console_thread = ConsoleThread(console_locals)
        console_thread.start()

    # Use os._exit() to prevent segfault during Python's shutdown sequence.
    # PyQt5's C++ destructor order conflicts with Python's garbage collector.
    #
    # Note: This does NOT skip critical cleanup because:
    # - closeEvent() runs when the window closes (before app.exec_() returns)
    # - aboutToQuit signal fires before app.exec_() returns
    # All hardware cleanup (camera, stage, microcontroller) happens in closeEvent,
    # which completes before os._exit() is called.
    try:
        exit_code = app.exec_()
    finally:
        # Stop TCP server if running
        if tcp_server is not None:
            tcp_server.stop()
            log.info("TCP control server stopped")

        # Clean shutdown of application context
        context.shutdown()
    logging.shutdown()  # Flush log handlers before os._exit() bypasses Python cleanup
    os._exit(exit_code)
