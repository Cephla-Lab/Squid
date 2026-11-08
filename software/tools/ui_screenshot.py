"""Screenshots of the Squid main window from `main_hcs.py --simulation`, on the real display.

Usage (from software/):
    py -3.12 tools/ui_screenshot.py --out DIR [--theme light|dark|classic] [--font "Family:pt"]
    py -3.12 tools/ui_screenshot.py --out DIR --compare A.png B.png

The first form starts the simulated GUI, grabs a fixed set of views to PNG files in DIR, prints one line
`FONT <family> <pt>pt; controls column width <N>px`, and exits. The second form prints
`max_abs_diff=<n> differing_pixels=<m>` for two PNGs and writes DIR/diff.png.

Use the real display: the offscreen platform renders no text on this machine.
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
_aside_state = None  # (original path, set-aside path) of persisted GUI state, or None


def _default_machine_ini():
    """The single *.ini in cache/ui_screenshot/, or None."""
    inis = glob.glob(os.path.join(SOFTWARE_DIR, MACHINE_INI_DIR, "*.ini"))
    return inis[0] if len(inis) == 1 else None


def _install_machine_ini(ini_path):
    """Make control._def select ini_path for this run. Must run before any `control` import."""
    global _config_state
    name = os.path.basename(ini_path)
    target = os.path.join(SOFTWARE_DIR, name)
    copied = not os.path.exists(target)
    if copied:
        shutil.copyfile(ini_path, target)
    cache_path = os.path.join(SOFTWARE_DIR, CONFIG_CACHE)
    previous = None
    if os.path.exists(cache_path):
        with open(cache_path, "rb") as f:
            previous = f.read()
    _config_state = {"copied": target if copied else None, "cache_path": cache_path, "previous": previous}
    # _def compares the cached value against the strings of this exact glob expression.
    entries = [p for p in glob.glob("." + "/" + "configuration*.ini") if os.path.basename(p) == name]
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w") as f:
        f.write(entries[0])


def _set_aside_persisted_gui_state():
    """Hide the GUI state Squid persists between runs (the acquisition-widget checkboxes), so every capture
    starts from the widget defaults rather than whatever the last session, test or run left behind."""
    global _aside_state
    path = os.path.join(SOFTWARE_DIR, "cache", "multipoint_widget_config.yaml")
    if os.path.exists(path):
        _aside_state = (path, path + ".ui_screenshot_aside")
        os.replace(*_aside_state)


def _restore_config():
    """Undo _install_machine_ini and _set_aside_persisted_gui_state. Safe to call more than once."""
    global _config_state, _aside_state
    aside, _aside_state = _aside_state, None
    if aside is not None:
        os.replace(aside[1], aside[0])  # also replaces any file the run wrote
    state, _config_state = _config_state, None
    if state is None:
        return
    if state["copied"] and os.path.exists(state["copied"]):
        os.remove(state["copied"])
    if state["previous"] is None:
        if os.path.exists(state["cache_path"]):
            os.remove(state["cache_path"])
    else:
        with open(state["cache_path"], "wb") as f:
            f.write(state["previous"])


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
        for p in multiprocessing.active_children():
            p.terminate()
            p.join(2)
        _restore_config()
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
    _set_aside_persisted_gui_state()
    if machine_ini:
        _install_machine_ini(machine_ini)
    os.environ.setdefault("QT_API", "pyqt5")
    sys.argv = ["main_hcs.py", "--simulation"]

    from qtpy import QtCore
    from qtpy.QtWidgets import QApplication

    _install_process_hygiene()
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
    try:
        return run_gui(os.path.abspath(args.out), args.theme, args.font, machine_ini)
    finally:
        _restore_config()


if __name__ == "__main__":
    sys.exit(main())
