# tests/control/test_theme_conversion.py
import re
from pathlib import Path

LITERALS = ["#C2C2FF", "#C2FFC2", "color: #666", "color: gray", "#CC0000", "#e74c3c", "#fff3cd", "#FF6B6B", "#4a90d9"]
LITERALS += ["#b00020", "#2e7d32", "color: green", "color: red", "#c0392b"]

SCANNED = (
    "widgets.py",
    "gui_hcs.py",
    "laser_engine_widget.py",
    "laser_engine_v2_widget.py",
    "laser_engine_v2_bench.py",
    "versalase_widget.py",
    "widgets_claude.py",
    "widgets_slack.py",
    "widgets_workflow.py",
    "widgets_z_lift.py",
    "widgets_fluidics/dialogs.py",
    "widgets_fluidics/protocol_tab.py",
    "widgets_fluidics/protocol_widget.py",
    "widgets_fluidics/display_tab.py",
    "widgets_fluidics/system_panel.py",
)
# Status dots and head colours are content colours and stay literal.
DOT_MARKERS = ("GREY", "RED", "AMBER", "GREEN", "_head_color", "color.name()", "QColor(")


def _method_range(lines, start_pattern):
    """Line numbers (1-based, inclusive) of the method or class whose header matches, up to the next header."""
    start = next(i for i, l in enumerate(lines) if re.match(start_pattern, l))
    indent = len(lines[start]) - len(lines[start].lstrip())
    end = next(
        (
            i
            for i in range(start + 1, len(lines))
            if lines[i].strip()
            and (len(lines[i]) - len(lines[i].lstrip())) <= indent
            and not lines[i].lstrip().startswith(("#", ")", "]", "}"))
        ),
        len(lines),
    )
    return range(start + 1, end + 1)


# Blocks still owned by later tasks. Task 10 deletes it.
DEFERRED = {}


def test_main_window_files_carry_no_role_literals():
    root = Path(__file__).resolve().parents[2] / "control"
    offenders = []
    for name in SCANNED:
        lines = (root / name).read_text(encoding="utf-8").splitlines()
        exempt = set()
        for pattern in DEFERRED.get(name, []):
            exempt.update(_method_range(lines, pattern))
        for number, line in enumerate(lines, 1):
            if number in exempt or "setStyleSheet(f" in line:  # swatch buttons set colours from data
                continue
            if any(m in line for m in DOT_MARKERS):
                continue
            for lit in LITERALS:
                if lit in line:
                    offenders.append(f"{name}:{number}:{lit}")
    assert offenders == [], offenders
