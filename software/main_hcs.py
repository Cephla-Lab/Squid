# set QT_API environment variable
import argparse
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
from control._def import USE_TERMINAL_CONSOLE, ENABLE_MCP_SERVER_SUPPORT, CONTROL_SERVER_HOST, CONTROL_SERVER_PORT
import control._def
import control.utils
import control.microscope


if USE_TERMINAL_CONSOLE:
    from control.console import ConsoleThread

if ENABLE_MCP_SERVER_SUPPORT:
    from control.microscope_control_server import MicroscopeControlServer
    import subprocess
    import shutil


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--simulation", help="Run the GUI with simulated hardware.", action="store_true")
    parser.add_argument("--live-only", help="Run the GUI only the live viewer.", action="store_true")
    parser.add_argument("--verbose", help="Turn on verbose logging (DEBUG level)", action="store_true")
    parser.add_argument(
        "--start-server", help="Auto-start the MCP control server for programmatic control", action="store_true"
    )
    args = parser.parse_args()

    log = squid.logging.get_logger("main_hcs")

    if args.verbose:
        log.info("Turning on debug logging.")
        squid.logging.set_stdout_log_level(logging.DEBUG)

    if not squid.logging.add_file_logging(f"{squid.logging.get_default_log_directory()}/main_hcs.log"):
        log.error("Couldn't setup logging to file!")
        sys.exit(1)

    log.info(f"Squid Repository State: {control.utils.get_squid_repo_state_description()}")

    app = QApplication(["Squid"])
    app.setStyle("Fusion")
    app.setWindowIcon(QIcon("icon/cephla_logo.ico"))
    # This allows shutdown via ctrl+C even after the gui has popped up.
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    microscope = control.microscope.Microscope.build_from_global_config(args.simulation)
    win = gui.HighContentScreeningGui(
        microscope=microscope, is_simulation=args.simulation, live_only_mode=args.live_only
    )

    microscope_utils_menu = QMenu("Utils", win)

    stage_utils_action = QAction("Stage Utils", win)
    stage_utils_action.triggered.connect(win.stageUtils.show)
    microscope_utils_menu.addAction(stage_utils_action)

    menu_bar = win.menuBar()
    menu_bar.addMenu(microscope_utils_menu)

    # Show startup warning if simulated disk I/O mode is enabled
    if control._def.SIMULATED_DISK_IO_ENABLED:
        QMessageBox.warning(
            None,
            "Development Mode Active",
            "SIMULATED DISK I/O IS ENABLED\n\n"
            "Images are encoded to memory (exercises RAM/CPU) but NOT saved to disk.\n"
            f"Simulated write speed: {control._def.SIMULATED_DISK_IO_SPEED_MB_S} MB/s\n\n"
            "This mode is for development/testing only.\n\n"
            "To disable: Settings > Preferences > Advanced",
            QMessageBox.Ok,
        )

    win.show()

    if USE_TERMINAL_CONSOLE:
        console_locals = {"microscope": win.microscope}
        console_thread = ConsoleThread(console_locals)
        console_thread.start()

    if ENABLE_MCP_SERVER_SUPPORT:
        # Create control server but don't start it yet (on-demand)
        control_server = MicroscopeControlServer(
            microscope=microscope,
            host=CONTROL_SERVER_HOST,
            port=CONTROL_SERVER_PORT,
            multipoint_controller=win.multipointController,
            scan_coordinates=win.scanCoordinates,
            gui=win,
        )
        # Ensure clean shutdown of control server socket on app exit
        app.aboutToQuit.connect(control_server.stop)

        def start_control_server_if_needed():
            """Start the control server if not already running."""
            if not control_server.is_running():
                control_server.start()
                log.info(f"MCP control server started on {CONTROL_SERVER_HOST}:{CONTROL_SERVER_PORT}")
                return True
            return False

        # Auto-start server if --start-server flag is provided
        if args.start_server:
            start_control_server_if_needed()

        # Add MCP menu items to Settings menu
        settings_menu = None
        for action in menu_bar.actions():
            if action.text() == "Settings":
                settings_menu = action.menu()
                break

        if settings_menu:
            settings_menu.addSeparator()

            # Control server toggle
            control_server_action = QAction("Enable MCP Control Server", win)
            control_server_action.setCheckable(True)
            control_server_action.setChecked(False)
            control_server_action.setToolTip("Start/stop the MCP control server for Claude Code integration")

            def on_control_server_toggled(checked):
                if checked:
                    start_control_server_if_needed()
                else:
                    control_server.stop()
                    log.info("MCP control server stopped")

            control_server_action.toggled.connect(on_control_server_toggled)
            settings_menu.addAction(control_server_action)

            # Python exec toggle
            python_exec_action = QAction("Enable MCP Python Exec", win)
            python_exec_action.setCheckable(True)
            python_exec_action.setChecked(False)
            python_exec_action.setToolTip("Allow MCP clients (e.g., Claude Code) to execute arbitrary Python code")

            def on_python_exec_toggled(checked):
                if checked:
                    reply = QMessageBox.warning(
                        win,
                        "Security Warning",
                        "Enabling MCP Python Exec allows AI agents to execute arbitrary Python code "
                        "with full access to microscope hardware and system resources.\n\n"
                        "Only enable this if you trust the connected MCP client.\n\n"
                        "Warning: Even trusted clients can execute code that may cause unintended "
                        "damage to hardware or samples due to bugs or mistakes.\n\n"
                        "Do you want to continue?",
                        QMessageBox.Yes | QMessageBox.No,
                        QMessageBox.No,
                    )
                    if reply == QMessageBox.Yes:
                        control_server.set_python_exec_enabled(True)
                    else:
                        python_exec_action.setChecked(False)
                else:
                    control_server.set_python_exec_enabled(False)

            python_exec_action.toggled.connect(on_python_exec_toggled)
            settings_menu.addAction(python_exec_action)

            # Add Launch Claude Code action
            def launch_claude_code():
                # Start control server if not running
                if start_control_server_if_needed():
                    control_server_action.setChecked(True)

                # Get the directory containing .mcp.json
                working_dir = os.path.dirname(os.path.abspath(__file__))

                # Check if claude is installed
                if not shutil.which("claude"):
                    reply = QMessageBox.question(
                        win,
                        "Claude Code Not Found",
                        "Claude Code CLI is not installed.\n\n" "Would you like to install it now?",
                        QMessageBox.Yes | QMessageBox.No,
                        QMessageBox.Yes,
                    )
                    if reply == QMessageBox.Yes:
                        try:
                            if sys.platform in ("linux", "darwin"):
                                # Use official install script for Linux/macOS
                                install_cmd = (
                                    "curl -fsSL https://claude.ai/install.sh | bash && "
                                    "echo 'Installation complete! Press Enter to continue...' && read"
                                )
                                if sys.platform == "linux":
                                    subprocess.Popen(["gnome-terminal", "--", "bash", "-c", install_cmd])
                                else:
                                    script = f'tell application "Terminal" to do script "{install_cmd}"'
                                    subprocess.Popen(["osascript", "-e", script])
                            elif sys.platform == "win32":
                                # Use official install script for Windows (CMD version)
                                install_cmd = "curl -fsSL https://claude.ai/install.cmd -o install.cmd && install.cmd && del install.cmd"
                                subprocess.Popen(f'start cmd /k "{install_cmd}"', shell=True)
                            log.info("Started Claude Code installation")
                            QMessageBox.information(
                                win,
                                "Installation Started",
                                "Claude Code installation has started in a terminal window.\n\n"
                                "After installation completes, restart Squid to use Claude Code.",
                            )
                        except Exception as e:
                            log.error(f"Failed to start installation: {e}")
                            QMessageBox.warning(
                                win, "Installation Failed", f"Failed to start installation:\n\n{str(e)}"
                            )
                    return

                try:
                    if sys.platform == "darwin":  # macOS
                        script = f"""
                        tell application "Terminal"
                            activate
                            do script "cd '{working_dir}' && claude"
                        end tell
                        """
                        subprocess.Popen(["osascript", "-e", script])

                    elif sys.platform == "win32":  # Windows
                        # Quote path to handle spaces
                        subprocess.Popen(
                            f'start cmd /k "cd /d \\"{working_dir}\\" && claude"',
                            shell=True,
                        )

                    else:  # Linux
                        terminals = [
                            ["gnome-terminal", "--", "bash", "-c", f'cd "{working_dir}" && claude; exec bash'],
                            ["konsole", "-e", "bash", "-c", f'cd "{working_dir}" && claude; exec bash'],
                            ["xfce4-terminal", "-e", f'bash -c "cd \\"{working_dir}\\" && claude; exec bash"'],
                            ["xterm", "-e", f'bash -c "cd \\"{working_dir}\\" && claude; exec bash"'],
                        ]
                        launched = False
                        for cmd in terminals:
                            try:
                                subprocess.Popen(cmd)
                                launched = True
                                break
                            except FileNotFoundError:
                                continue

                        if not launched:
                            QMessageBox.warning(
                                win,
                                "Terminal Not Found",
                                "Could not find a supported terminal emulator.\n\n"
                                "Supported: gnome-terminal, konsole, xfce4-terminal, xterm",
                            )

                    log.info("Launched Claude Code")

                except Exception as e:
                    log.error(f"Failed to launch Claude Code: {e}")
                    QMessageBox.warning(
                        win,
                        "Launch Failed",
                        f"Failed to launch Claude Code:\n\n{str(e)}",
                    )

            launch_claude_action = QAction("Launch Claude Code", win)
            launch_claude_action.setToolTip("Open Claude Code CLI in a terminal with MCP connection")
            launch_claude_action.triggered.connect(launch_claude_code)
            settings_menu.addAction(launch_claude_action)

    # Use os._exit() to prevent segfault during Python's shutdown sequence.
    # PyQt5's C++ destructor order conflicts with Python's garbage collector.
    #
    # Note: This does NOT skip critical cleanup because:
    # - closeEvent() runs when the window closes (before app.exec_() returns)
    # - aboutToQuit signal fires before app.exec_() returns
    # All hardware cleanup (camera, stage, microcontroller) happens in closeEvent,
    # which completes before os._exit() is called.
    exit_code = app.exec_()
    logging.shutdown()  # Flush log handlers before os._exit() bypasses Python cleanup
    os._exit(exit_code)
