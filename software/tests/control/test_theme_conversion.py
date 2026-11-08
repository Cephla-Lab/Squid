# tests/control/test_theme_conversion.py
import re
from pathlib import Path

LITERALS = ["#C2C2FF", "#C2FFC2", "color: #666", "color: gray", "#CC0000", "#e74c3c", "#fff3cd", "#FF6B6B", "#4a90d9"]


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


# Blocks still owned by later tasks. Task 9 deletes the first entry, Task 10 the second.
DEFERRED = {"widgets.py": [r"    def update_tab_styles\(", r"class WarningErrorWidget\("]}


def test_main_window_files_carry_no_role_literals():
    root = Path(__file__).resolve().parents[2] / "control"
    offenders = []
    for name in ("widgets.py", "gui_hcs.py"):
        lines = (root / name).read_text(encoding="utf-8").splitlines()
        exempt = set()
        for pattern in DEFERRED.get(name, []):
            exempt.update(_method_range(lines, pattern))
        for number, line in enumerate(lines, 1):
            if number in exempt or "setStyleSheet(f" in line:  # swatch buttons set colours from data
                continue
            for lit in LITERALS:
                if lit in line:
                    offenders.append(f"{name}:{number}:{lit}")
    assert offenders == [], offenders
