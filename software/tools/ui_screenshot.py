"""Screenshots of the Squid main window from `main_hcs.py --simulation`, on the real display.

Usage (from software/):
    py -3.12 tools/ui_screenshot.py --out DIR [--theme light|dark|classic] [--font "Family:pt"]
    py -3.12 tools/ui_screenshot.py --out DIR --compare A.png B.png

The first form starts the simulated GUI, grabs a fixed set of views to PNG files in DIR, prints one line
`FONT <family> <pt>pt; controls column width <N>px`, and exits. The second form prints
`max_abs_diff=<n> differing_pixels=<m>` for two PNGs and writes DIR/diff.png.

Use the real display: the offscreen platform renders no text on this machine.

The GUI run installs the machine ini for the run only, refuses configurations that would drive real hardware
under --simulation, and never reads or writes the operator's persisted GUI state (acquisition-widget settings,
camera settings cache, cached stage position, saved objective and plate format). It can run beside a running
Squid instance (private lock and log
directory). One shared file is touched: cache/config_file_path.txt points at the capture's ini while
control._def imports (about a second) and is put back before the GUI starts; do not start or restart Squid in
that moment, or it would load the capture's configuration.
"""

import argparse
import glob
import shutil
import multiprocessing
import os
import runpy
import sys
import traceback

SOFTWARE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOT_DELAY_MS = 4000
EXPECTED_SHOTS = 8
_run = {"saved": 0, "failed": False}  # outcome of the GUI run; decides the process exit code


CONFIG_CACHE = os.path.join("cache", "config_file_path.txt")
MACHINE_INI_DIR = os.path.join("cache", "ui_screenshot")
# What _install_machine_ini changed, so _restore_config can undo it. None while nothing is installed.
_config_state = None
# control._def flags that make the Microscope factories construct real hardware even under --simulation
# (control/microscope.py: the Prior stage and the SCI Microscopy LED array have no simulated counterpart; every
# other device branch there consults the flag). This tool answers the homing prompt and bypasses the
# single-instance lock, so such a configuration must never reach the GUI.
HARDWARE_WITHOUT_SIMULATION = ("USE_PRIOR_STAGE", "SUPPORT_SCIMICROSCOPY_LED_ARRAY")


def _default_machine_ini():
    """The single *.ini in cache/ui_screenshot/, or None."""
    inis = glob.glob(os.path.join(SOFTWARE_DIR, MACHINE_INI_DIR, "*.ini"))
    return inis[0] if len(inis) == 1 else None


def _same_contents(path_a, path_b):
    with open(path_a, "rb") as a, open(path_b, "rb") as b:
        return a.read() == b.read()


def _install_machine_ini(ini_path):
    """Make control._def select ini_path for this run. Must run before any `control` import."""
    global _config_state
    name = os.path.basename(ini_path)
    if not (name.startswith("configuration") and name.endswith(".ini")):
        # control._def only considers software/configuration*.ini; anything else would never be selected.
        raise SystemExit(f"{ini_path}: the machine ini must be named configuration*.ini for control._def to find it")
    target = os.path.join(SOFTWARE_DIR, name)
    copied = not os.path.exists(target)
    if copied:
        shutil.copyfile(ini_path, target)
    elif not _same_contents(target, ini_path):
        # Loading the file already there would capture a different configuration than the one asked for.
        raise SystemExit(
            f"{target} already exists and differs from {ini_path}: move it away or pass it with --machine-ini, then rerun"
        )
    cache_path = os.path.join(SOFTWARE_DIR, CONFIG_CACHE)
    previous = None
    if os.path.exists(cache_path):
        with open(cache_path, "rb") as f:
            previous = f.read()
    _config_state = {
        "copied": target if copied else None,
        "cache_path": cache_path,
        "previous": previous,
        "selector_restored": False,
    }
    # _def compares the cached value against the strings of this exact glob expression.
    entries = [p for p in glob.glob("." + "/" + "configuration*.ini") if os.path.basename(p) == name]
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w") as f:
        f.write(entries[0])


def _release_selector():
    """Put cache/config_file_path.txt back as it was. Idempotent.

    The selector is shared with every Squid process on this machine and control._def reads it only at import, so
    it is released right after that import: a Squid started during the rest of the capture loads the operator's
    configuration, not the capture's. (The ini copy stays until exit: control._def keeps its path for later reads.)
    """
    state = _config_state
    if state is None or state["selector_restored"]:
        return
    if state["previous"] is None:
        if os.path.exists(state["cache_path"]):
            os.remove(state["cache_path"])
    else:
        with open(state["cache_path"], "wb") as f:
            f.write(state["previous"])
    state["selector_restored"] = True


# Operator state control._def reads at import with a literal path. The capture must start from the defaults, so
# these opens answer "no such file" during that import; the files themselves are never moved or written.
_OPERATOR_STATE_READ_AT_IMPORT = ("cache/objective_and_sample_format.txt",)


def _open_without_operator_state(real_open):
    """An ``open`` that reports the files in _OPERATOR_STATE_READ_AT_IMPORT as missing when opened for reading."""

    def open_(file, mode="r", *args, **kwargs):
        if isinstance(file, str) and "r" in mode and "+" not in mode:
            if file.replace("\\", "/") in _OPERATOR_STATE_READ_AT_IMPORT:
                raise FileNotFoundError(file)
        return real_open(file, mode, *args, **kwargs)

    return open_


def _load_config():
    """Import control._def (which selects the ini through the selector and reads the operator's saved objective and
    plate format), release the selector at once, then refuse configurations that would drive real hardware."""
    import builtins

    real_open = builtins.open
    builtins.open = _open_without_operator_state(real_open)
    try:
        import control._def  # noqa: F401
    finally:
        builtins.open = real_open
        _release_selector()
    _refuse_real_hardware()


def _refuse_real_hardware():
    """Stop before any Qt or device code runs if the selected configuration would drive real hardware.

    Runs from _load_config, after control._def has read the ini and before the dialog stubs and the private
    instance lock are installed.
    """
    import control._def

    live = [name for name in HARDWARE_WITHOUT_SIMULATION if getattr(control._def, name, False)]
    if live:
        raise SystemExit(
            f"the configuration enables {', '.join(live)}, which --simulation does not simulate: this capture would "
            "open and configure real hardware (and home it, for a stage). Pass --machine-ini with a configuration "
            "that does not enable it."
        )


def _install_private_gui_state():
    """The capture starts from the widgets' defaults and never writes the operator's persisted GUI state.

    The acquisition-widget settings (cache/multipoint_widget_config.yaml), the camera settings cache, the cached
    stage position and the saved objective / plate format are shared with a real Squid instance that may be running
    alongside this capture; moving them aside and back would discard whatever that instance saved in the meantime,
    and reading them would make the captures depend on the operator's last session. Reads answer "nothing cached",
    writes do nothing.
    """
    import squid.camera.settings_cache
    import squid.stage.utils

    squid.camera.settings_cache.load_camera_settings = lambda *args, **kwargs: None
    squid.camera.settings_cache.save_camera_settings = lambda *args, **kwargs: None
    squid.stage.utils.get_cached_position = lambda *args, **kwargs: None
    squid.stage.utils.cache_position = lambda *args, **kwargs: None

    import control.widgets

    control.widgets.WellplateMultiPointWidget.load_multipoint_widget_config_from_cache = lambda self: None
    control.widgets.WellplateMultiPointWidget.save_multipoint_widget_config_to_cache = lambda self: None
    # The saved objective / plate format is hidden from control._def at import (_load_config); never write it back.
    control.widgets.SampleSettingsWidget.save_settings = lambda self: None


def _leftovers_of_an_interrupted_run(machine_ini):
    """Print a recovery hint for a file an interrupted run left behind; True if the run must not start."""
    if machine_ini:
        copy = os.path.join(SOFTWARE_DIR, os.path.basename(machine_ini))
        if os.path.exists(copy) and os.path.abspath(copy) != os.path.abspath(machine_ini):
            if _same_contents(copy, machine_ini):
                print(f"{copy} matches {machine_ini}: if you did not put it there, an interrupted run left it")
    return False


def _restore_config():
    """Undo _install_machine_ini. Safe to call more than once.

    The selector goes back first (normally already done by _load_config), so the machine is never left pointed at
    the run's ini; the ini copy is removed second. The recovery state is kept until both steps have succeeded, so
    a step that raises (a PermissionError while another process holds the file) can be retried by a later call.
    """
    global _config_state
    state = _config_state
    if state is None:
        return
    _release_selector()
    if state["copied"] and os.path.exists(state["copied"]):
        os.remove(state["copied"])
    _config_state = None


def _run_exit_code(code):
    """Exit code for the GUI run: non-zero if a shot is missing or anything failed, even if main_hcs asked for 0."""
    if code in (0, None) and (_run["failed"] or _run["saved"] != EXPECTED_SHOTS):
        return 1
    return code


def _log(msg):
    print(f"[shot] {msg}", flush=True)


def compare_images(path_a, path_b, out_dir):
    from PIL import Image, ImageChops

    try:
        a = Image.open(path_a).convert("RGB")
        b = Image.open(path_b).convert("RGB")
    except OSError as e:
        print(f"cannot read image: {e}")
        return 1
    if a.size != b.size:
        print(f"size mismatch: {a.size} vs {b.size}")
        return 1
    diff = ImageChops.difference(a, b)
    os.makedirs(out_dir, exist_ok=True)
    diff.save(os.path.join(out_dir, "diff.png"))
    r, g, b = diff.split()
    any_channel = ImageChops.lighter(ImageChops.lighter(r, g), b)
    differing = diff.width * diff.height - any_channel.histogram()[0]
    max_diff = max(hi for _, hi in diff.getextrema())
    print(f"max_abs_diff={max_diff} differing_pixels={differing}")
    return 0


def _install_process_hygiene():
    """main_hcs.py exits through os._exit, which would orphan multiprocessing workers it spawned."""
    real_exit = os._exit

    def exit_after_reaping_children(code):
        try:
            for p in multiprocessing.active_children():
                p.terminate()
                p.join(2)
            _restore_config()
        except BaseException:
            _log("cleanup at exit failed:\n" + traceback.format_exc())
            _run["failed"] = True
        finally:
            real_exit(_run_exit_code(code))

    os._exit = exit_after_reaping_children


def _install_dialog_stubs():
    from qtpy.QtWidgets import QDialog, QMessageBox

    def make_stub(result, name):
        def stub(*args, **kwargs):
            title = args[1] if len(args) > 1 else "?"
            _log(f"QMessageBox.{name} suppressed: {title!r}")
            return result

        return staticmethod(stub)

    for name, result in (
        ("warning", QMessageBox.Ok),
        ("critical", QMessageBox.Ok),
        ("information", QMessageBox.Ok),
        ("question", QMessageBox.Yes),
    ):
        setattr(QMessageBox, name, make_stub(result, name))

    def dialog_exec(self, *args, **kwargs):
        _log(f"QDialog.exec suppressed: {type(self).__name__} {self.windowTitle()!r}")
        return QDialog.Rejected

    QDialog.exec_ = dialog_exec
    QDialog.exec = dialog_exec

    # The nosepiece prompt must be replaced before main_hcs imports it.
    import control.widgets_homing as homing

    homing.confirm_nosepiece_lowered = lambda *args, **kwargs: True


def _install_private_instance_lock():
    """Simulation touches no hardware, so do not compete for Squid's single-instance lock or log files."""
    import control.single_instance as single_instance

    private_path = os.path.join(SOFTWARE_DIR, MACHINE_INI_DIR, "squid-ui-screenshot.lock")
    real_acquire = single_instance.acquire_single_instance_lock

    def acquire(lock_path=None):
        os.makedirs(os.path.dirname(private_path), exist_ok=True)
        return real_acquire(private_path if lock_path is None else lock_path)

    single_instance.acquire_single_instance_lock = acquire

    # Another instance holds the default log file open, and rotating it fails on Windows: log privately too.
    import squid.logging

    private_logs = os.path.join(SOFTWARE_DIR, MACHINE_INI_DIR, "logs")
    squid.logging.get_default_log_directory = lambda: private_logs


def _install_font_override(font_spec):
    from qtpy.QtGui import QFont
    from qtpy.QtWidgets import QApplication

    family, pt = font_spec.rsplit(":", 1)
    real_set_style = QApplication.setStyle

    def set_style_then_font(*args, **kwargs):
        result = real_set_style(*args, **kwargs)
        QApplication.instance().setFont(QFont(family, int(pt)))
        return result

    QApplication.setStyle = staticmethod(set_style_then_font)


def _settle(app, ms):
    from qtpy import QtCore

    deadline = QtCore.QDeadlineTimer(ms)
    while not deadline.hasExpired():
        app.processEvents(QtCore.QEventLoop.AllEvents, 50)


def _select_tab(tabs, title):
    for i in range(tabs.count()):
        if tabs.tabText(i) == title:
            tabs.setCurrentIndex(i)
            return


def _shoot(app, out_dir):
    from qtpy.QtWidgets import QApplication

    def save(widget, name):
        ok = widget.grab().save(os.path.join(out_dir, name), "PNG")
        _log(f"{'saved' if ok else 'FAILED'} {name}")
        if ok:
            _run["saved"] += 1
        else:
            _run["failed"] = True

    try:
        import control.gui_hcs as gui

        wins = [w for w in QApplication.topLevelWidgets() if isinstance(w, gui.HighContentScreeningGui)]
        if not wins:
            _log("no main window found")
            _run["failed"] = True
            return
        win = wins[0]
        _settle(app, 1500)
        font = app.font()
        print(
            f"FONT {font.family()} {font.pointSize()}pt; controls column width {win.centralWidget.width()}px",
            flush=True,
        )
        save(win, "01-main-default.png")
        save(win.profileWidget, "03-profile.png")
        save(win.liveControlWidget, "04-live-control.png")
        _select_tab(win.cameraTabWidget, "Stages")
        _settle(app, 400)
        save(win.cameraTabWidget, "05-camera-tabs-stages.png")
        save(win.recordTabWidget, "06-record-tabs-wellplate.png")
        save(win.sampleSettingsWidget, "07-sample-settings.png")
        save(win.wellSelectionWidget, "09-well-selector.png")
        _select_tab(win.cameraTabWidget, "Camera")
        _settle(app, 400)
        save(win.cameraTabWidget, "11-camera-tabs-camera.png")
    except Exception:
        _log("shoot failed:\n" + traceback.format_exc())
        _run["failed"] = True
    finally:
        app.quit()


def run_gui(out_dir, theme, font_spec, machine_ini):
    print(f"PID {os.getpid()}", flush=True)
    os.makedirs(out_dir, exist_ok=True)
    os.chdir(SOFTWARE_DIR)
    sys.path.insert(0, SOFTWARE_DIR)
    if machine_ini:
        _install_machine_ini(machine_ini)
    _load_config()
    os.environ.setdefault("QT_API", "pyqt5")
    sys.argv = ["main_hcs.py", "--simulation"]

    from qtpy import QtCore
    from qtpy.QtWidgets import QApplication

    _install_process_hygiene()
    _install_private_gui_state()
    _install_dialog_stubs()
    _install_private_instance_lock()
    if theme:
        import control._def

        setattr(control._def, "UI_THEME", theme)
    if font_spec:
        _install_font_override(font_spec)

    real_exec = QApplication.exec_

    def exec_with_screenshot(*args, **kwargs):
        app = QApplication.instance()
        QtCore.QTimer.singleShot(SHOT_DELAY_MS, lambda: _shoot(app, out_dir))
        return real_exec()

    QApplication.exec_ = exec_with_screenshot
    QApplication.exec = exec_with_screenshot

    try:
        runpy.run_path(os.path.join(SOFTWARE_DIR, "main_hcs.py"), run_name="__main__")
    except SystemExit as e:
        _log(f"SystemExit({e.code})")
    except Exception:
        _log("main_hcs failed:\n" + traceback.format_exc())
        _run["failed"] = True
    return _run_exit_code(0)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True, help="directory for PNGs (and diff.png with --compare)")
    parser.add_argument("--theme", choices=("light", "dark", "classic"), help="sets control._def.UI_THEME")
    parser.add_argument("--font", help='application font override, "Family:pt"')
    parser.add_argument(
        "--machine-ini",
        help="machine ini installed for the run only (default: the single *.ini in cache/ui_screenshot/, if any)",
    )
    parser.add_argument("--compare", nargs=2, metavar=("A", "B"), help="compare two PNGs instead of running the GUI")
    args = parser.parse_args()
    if args.compare:
        return compare_images(args.compare[0], args.compare[1], args.out)
    machine_ini = os.path.abspath(args.machine_ini) if args.machine_ini else _default_machine_ini()
    if _leftovers_of_an_interrupted_run(machine_ini):
        return 1
    try:
        return run_gui(os.path.abspath(args.out), args.theme, args.font, machine_ini)
    finally:
        _restore_config()


if __name__ == "__main__":
    sys.exit(main())
