import subprocess
import sys
from pathlib import Path

SOFTWARE_DIR = Path(__file__).parents[3]


def test_the_engine_imports_no_app_or_qt_module():
    """The engine is Qt-free and independent of control.*: it runs in the GUI worker, under --simulation, and later headless."""
    code = (
        "import sys\n"
        "import squid.objective_calibration.engine, squid.objective_calibration.synthetic\n"
        "bad = sorted(m for m in sys.modules if m.split('.')[0] in ('control', 'qtpy', 'PyQt5', 'PySide6', 'napari'))\n"
        "print(bad)\n"
        "sys.exit(1 if bad else 0)\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=SOFTWARE_DIR)
    assert result.returncode == 0, result.stdout + result.stderr
