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
import multiprocessing
import os
import runpy
import sys
import traceback

SOFTWARE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOT_DELAY_MS = 4000


def _log(msg):
    print(f"[shot] {msg}", flush=True)


def compare_images(path_a, path_b, out_dir):
    from PIL import Image, ImageChops

    a = Image.open(path_a).convert("RGBA")
    b = Image.open(path_b).convert("RGBA")
    if a.size != b.size:
        print(f"size mismatch: {a.size} vs {b.size}")
        return 1
    diff = ImageChops.difference(a, b)
    os.makedirs(out_dir, exist_ok=True)
    diff.save(os.path.join(out_dir, "diff.png"))
    r, g, b, a = diff.split()
    any_channel = ImageChops.lighter(ImageChops.lighter(r, g), ImageChops.lighter(b, a))
    differing = a.width * a.height - any_channel.histogram()[0]
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
        real_exit(code)

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

    try:
        import control.gui_hcs as gui

        wins = [w for w in QApplication.topLevelWidgets() if isinstance(w, gui.HighContentScreeningGui)]
        if not wins:
            _log("no main window found")
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
    finally:
        app.quit()


def run_gui(out_dir, theme, font_spec):
    print(f"PID {os.getpid()}", flush=True)
    os.makedirs(out_dir, exist_ok=True)
    os.chdir(SOFTWARE_DIR)
    sys.path.insert(0, SOFTWARE_DIR)
    os.environ.setdefault("QT_API", "pyqt5")
    sys.argv = ["main_hcs.py", "--simulation"]

    from qtpy import QtCore
    from qtpy.QtWidgets import QApplication

    _install_process_hygiene()
    _install_dialog_stubs()
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


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True, help="directory for PNGs (and diff.png with --compare)")
    parser.add_argument("--theme", choices=("light", "dark", "classic"), help="sets control._def.UI_THEME")
    parser.add_argument("--font", help='application font override, "Family:pt"')
    parser.add_argument("--compare", nargs=2, metavar=("A", "B"), help="compare two PNGs instead of running the GUI")
    args = parser.parse_args()
    if args.compare:
        return compare_images(args.compare[0], args.compare[1], args.out)
    run_gui(os.path.abspath(args.out), args.theme, args.font)
    return 0


if __name__ == "__main__":
    sys.exit(main())
