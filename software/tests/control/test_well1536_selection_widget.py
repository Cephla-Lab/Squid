"""Well1536SelectionWidget must take its geometry from the format settings.

It used to carry nine hardcoded fallback constants that had drifted from
sample_formats.csv (well_size 1.5 vs 1.53, a1 (11.0, 7.86) vs (11.01, 7.87)) —
dead on every reachable path, but one refactor away from navigating the 1536
selector to different positions than the acquisition planner.
"""

from types import SimpleNamespace

import control._def as _def
import control.widgets


def test_geometry_comes_from_format_settings(qtbot):
    expected = _def.WELLPLATE_FORMAT_SETTINGS["1536 well plate"]
    stub = SimpleNamespace(getWellplateSettings=lambda fmt: _def.WELLPLATE_FORMAT_SETTINGS[fmt])

    widget = control.widgets.Well1536SelectionWidget(stub)
    qtbot.addWidget(widget)

    assert widget.rows == expected["rows"]
    assert widget.columns == expected["cols"]
    assert widget.spacing_mm == expected["well_spacing_mm"]
    assert widget.number_of_skip == expected["number_of_skip"]
    assert widget.a1_x_mm == expected["a1_x_mm"]
    assert widget.a1_y_mm == expected["a1_y_mm"]
    assert widget.a1_x_pixel == expected["a1_x_pixel"]
    assert widget.a1_y_pixel == expected["a1_y_pixel"]
    assert widget.well_size_mm == expected["well_size_mm"]


def test_navigation_position_matches_planner_formula(qtbot):
    """The selector's emitted position must be the planner's arithmetic exactly."""
    s = _def.WELLPLATE_FORMAT_SETTINGS["1536 well plate"]
    stub = SimpleNamespace(getWellplateSettings=lambda fmt: _def.WELLPLATE_FORMAT_SETTINGS[fmt])

    widget = control.widgets.Well1536SelectionWidget(stub)
    qtbot.addWidget(widget)

    captured = []
    widget.signal_wellSelectedPos.connect(lambda x, y: captured.append((x, y)))

    widget.current_cell = (30, 46)  # AE47, the reachable far corner
    widget.update_current_cell()

    assert len(captured) == 1
    x, y = captured[0]
    assert x == 46 * s["well_spacing_mm"] + s["a1_x_mm"] + _def.WELLPLATE_OFFSET_X_mm
    assert y == 30 * s["well_spacing_mm"] + s["a1_y_mm"] + _def.WELLPLATE_OFFSET_Y_mm
