"""Golden oracle: pins today's well -> stage arithmetic at every producer site.

The forward formula `a1 + index * spacing + offset` exists in several
independent copies. Before any of them is routed through a shared transform,
this file pins their CURRENT behaviour with exact equality (==, never
approx) so the refactor is provably a no-op where it claims to be, and the
intentional divergences are written down rather than discovered:

* The MCP server's ``_parse_wells`` used to omit WELLPLATE_OFFSET; the
  server-fix commit made it resolve through plate_transform_for and flipped
  the expectation here in the same diff (see
  test_server_parse_wells_applies_offset).
* The plate-PNG renderer (widgets.py create_wellplate_image) is a plate-frame
  display asset drawn offset-free; it is asserted at the pixel level when it
  is routed through the shared transform.

Operand order note: the sites disagree about operand order
(``a1 + x + off`` vs ``x + a1 + off``), but that is commutativity of a single
addition, which IEEE-754 guarantees exact - one oracle formula serves all
sites with bitwise ==.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import control._def as _def
import control.utils
from control.core.plate_transform import WellplateSettings
from control.core.scan_coordinates import ScanCoordinates, ScanCoordinatesSiLA2
from control.microscope_control_server import MicroscopeControlServer

OFFSETS = [(0.0, 0.0), (1.375, -0.625)]

PLATE_FORMATS = [f for f in _def.WELLPLATE_FORMAT_SETTINGS if f != "glass slide"]


def expected_xy(settings, row, col, off_x, off_y):
    x = settings["a1_x_mm"] + (col * settings["well_spacing_mm"]) + off_x
    y = settings["a1_y_mm"] + (row * settings["well_spacing_mm"]) + off_y
    return x, y


def all_wells(settings):
    for row in range(settings["rows"]):
        for col in range(settings["cols"]):
            yield row, col


def index_to_row_label(index):
    index += 1
    label = ""
    while index > 0:
        index -= 1
        label = chr(index % 26 + ord("A")) + label
        index //= 26
    return label


def range_string(settings):
    last = index_to_row_label(settings["rows"] - 1) + str(settings["cols"])
    return f"A1:{last}"


# ---- site 1: ScanCoordinates.get_selected_wells (GUI acquisition planning) ----


@pytest.mark.parametrize("off", OFFSETS, ids=["offset0", "offsetXY"])
@pytest.mark.parametrize("format_", PLATE_FORMATS)
def test_scan_coordinates_get_selected_wells(monkeypatch, format_, off):
    # Offsets are resolved at compute time, so patch order vs construction no
    # longer matters (see test_offset_read_time_agreement).
    monkeypatch.setattr(_def, "WELLPLATE_OFFSET_X_mm", off[0])
    monkeypatch.setattr(_def, "WELLPLATE_OFFSET_Y_mm", off[1])

    sc = ScanCoordinates(objectiveStore=MagicMock(), stage=MagicMock(), camera=MagicMock())
    s = _def.WELLPLATE_FORMAT_SETTINGS[format_]
    sc.update_wellplate_settings(WellplateSettings.from_format(format_))
    cells = [[r, c] for r, c in all_wells(s)]
    sc.well_selector = SimpleNamespace(get_selected_cells=lambda: cells)

    centers = sc.get_selected_wells()

    assert len(centers) == s["rows"] * s["cols"]
    for row, col in all_wells(s):
        well_id = index_to_row_label(row) + str(col + 1)
        assert centers[well_id] == expected_xy(s, row, col, off[0], off[1]), (format_, well_id)


# ---- site 2: ScanCoordinatesSiLA2.get_selected_well_coordinates (headless) ----


@pytest.mark.parametrize("off", OFFSETS, ids=["offset0", "offsetXY"])
@pytest.mark.parametrize("format_", PLATE_FORMATS)
def test_sila2_selected_well_coordinates(monkeypatch, format_, off):
    monkeypatch.setattr(_def, "WELLPLATE_OFFSET_X_mm", off[0])
    monkeypatch.setattr(_def, "WELLPLATE_OFFSET_Y_mm", off[1])

    sc = ScanCoordinatesSiLA2(
        objectiveStore=MagicMock(), stage=MagicMock(), camera=MagicMock(), update_callback=lambda update: None
    )
    s = _def.WELLPLATE_FORMAT_SETTINGS[format_]

    # Range branch (serpentine expansion) over the whole plate...
    sc.get_selected_well_coordinates(range_string(s), s)
    assert len(sc.region_centers) == s["rows"] * s["cols"]
    for row, col in all_wells(s):
        well_id = index_to_row_label(row) + str(col + 1)
        assert sc.region_centers[well_id] == expected_xy(s, row, col, off[0], off[1]), (format_, well_id)

    # ...and the single-well branch, which is separate arithmetic in the source.
    sc.region_centers.clear()
    last_row, last_col = s["rows"] - 1, s["cols"] - 1
    single = index_to_row_label(last_row) + str(last_col + 1)
    sc.get_selected_well_coordinates(single, s)
    assert sc.region_centers[single] == expected_xy(s, last_row, last_col, off[0], off[1])


# ---- site 3: MicroscopeControlServer._parse_wells (MCP/remote) ----


@pytest.mark.parametrize("off", OFFSETS, ids=["offset0", "offsetXY"])
@pytest.mark.parametrize("format_", PLATE_FORMATS)
def test_server_parse_wells_applies_offset(monkeypatch, format_, off):
    """EXPECTATION FLIPPED by the server-fix commit: _parse_wells now resolves
    through plate_transform_for, so it applies WELLPLATE_OFFSET like every
    other site instead of omitting it (and no longer invents a1=0/spacing=9
    fallbacks for missing keys - unknown formats raise instead)."""
    monkeypatch.setattr(_def, "WELLPLATE_OFFSET_X_mm", off[0])
    monkeypatch.setattr(_def, "WELLPLATE_OFFSET_Y_mm", off[1])

    s = _def.WELLPLATE_FORMAT_SETTINGS[format_]
    coords = MicroscopeControlServer._parse_wells(None, range_string(s), format_)

    assert len(coords) == s["rows"] * s["cols"]
    for row, col in all_wells(s):
        well_id = index_to_row_label(row) + str(col + 1)
        assert coords[well_id] == expected_xy(s, row, col, off[0], off[1]), (format_, well_id)


def test_server_parse_wells_rejects_unknown_format():
    with pytest.raises(ValueError):
        MicroscopeControlServer._parse_wells(None, "A1", "no such plate")


# ---- sites 4 & 5: the two well-selector widgets (Qt, emit straight to move) ----


@pytest.mark.parametrize("off", OFFSETS, ids=["offset0", "offsetXY"])
def test_well_selection_widget_double_click(qtbot, monkeypatch, off):
    import control.widgets

    # The selectors resolve through plate_transform_for at CLICK time, which
    # reads control._def - patch the resolver's source of truth. (They used to
    # read a star-imported copy in the widgets namespace; positions for
    # uncalibrated formats are unchanged, only the read moved.)
    monkeypatch.setattr(_def, "WELLPLATE_OFFSET_X_mm", off[0])
    monkeypatch.setattr(_def, "WELLPLATE_OFFSET_Y_mm", off[1])

    stub_fmt = SimpleNamespace(getWellplateSettings=lambda fmt: _def.WELLPLATE_FORMAT_SETTINGS[fmt])
    widget = control.widgets.WellSelectionWidget("96 well plate", stub_fmt)
    qtbot.addWidget(widget)

    s = _def.WELLPLATE_FORMAT_SETTINGS["96 well plate"]
    captured = []
    widget.signal_wellSelectedPos.connect(lambda x, y: captured.append((x, y)))

    probes = [(0, 0), (0, s["cols"] - 1), (s["rows"] - 1, 0), (s["rows"] - 1, s["cols"] - 1), (3, 7)]
    for row, col in probes:
        widget.onDoubleClick(row, col)

    assert captured == [expected_xy(s, r, c, off[0], off[1]) for r, c in probes]


@pytest.mark.parametrize("off", OFFSETS, ids=["offset0", "offsetXY"])
def test_well1536_selection_widget_navigation(qtbot, monkeypatch, off):
    import control.widgets

    # Click-time resolution reads control._def (see the 96-well test above).
    monkeypatch.setattr(_def, "WELLPLATE_OFFSET_X_mm", off[0])
    monkeypatch.setattr(_def, "WELLPLATE_OFFSET_Y_mm", off[1])

    stub_fmt = SimpleNamespace(getWellplateSettings=lambda fmt: _def.WELLPLATE_FORMAT_SETTINGS[fmt])
    widget = control.widgets.Well1536SelectionWidget(stub_fmt)
    qtbot.addWidget(widget)

    s = _def.WELLPLATE_FORMAT_SETTINGS["1536 well plate"]
    captured = []
    widget.signal_wellSelectedPos.connect(lambda x, y: captured.append((x, y)))

    probes = [(0, 0), (0, 46), (30, 0), (30, 46), (15, 23)]
    for row, col in probes:
        widget.current_cell = (row, col)
        widget.update_current_cell()

    assert captured == [expected_xy(s, r, c, off[0], off[1]) for r, c in probes]


# ---- snapshot-vs-live: FLIPPED to agreement by compute-time resolution ----


def test_offset_read_time_agreement(monkeypatch):
    """EXPECTATION FLIPPED by the compute-time-resolution commit:
    ScanCoordinates used to snapshot the offsets at __init__ (an offset or
    calibration change was silently ignored until a signal re-emit) while the
    SiLA2 path read them live. Both now resolve at call time and agree."""
    monkeypatch.setattr(_def, "WELLPLATE_OFFSET_X_mm", 0.0)
    monkeypatch.setattr(_def, "WELLPLATE_OFFSET_Y_mm", 0.0)

    sc = ScanCoordinates(objectiveStore=MagicMock(), stage=MagicMock(), camera=MagicMock())
    sila = ScanCoordinatesSiLA2(
        objectiveStore=MagicMock(), stage=MagicMock(), camera=MagicMock(), update_callback=lambda update: None
    )
    s = _def.WELLPLATE_FORMAT_SETTINGS["96 well plate"]
    sc.update_wellplate_settings(WellplateSettings.from_format("96 well plate"))
    sc.well_selector = SimpleNamespace(get_selected_cells=lambda: [[0, 0]])

    # Offset changes AFTER construction:
    monkeypatch.setattr(_def, "WELLPLATE_OFFSET_X_mm", 2.0)
    monkeypatch.setattr(_def, "WELLPLATE_OFFSET_Y_mm", 2.0)

    gui_center = sc.get_selected_wells()["A1"]
    sila.get_selected_well_coordinates("A1", s)
    sila_center = sila.region_centers["A1"]

    expected = (s["a1_x_mm"] + 2.0, s["a1_y_mm"] + 2.0)
    assert gui_center == expected  # live now
    assert sila_center == expected  # live as before


def test_calibration_edit_applies_without_reemit(monkeypatch):
    """The concrete payoff: the calibration dialog mutates
    WELLPLATE_FORMAT_SETTINGS in place; planning must see it without a signal."""
    sc = ScanCoordinates(objectiveStore=MagicMock(), stage=MagicMock(), camera=MagicMock())
    sc.update_wellplate_settings(WellplateSettings.from_format("96 well plate"))
    sc.well_selector = SimpleNamespace(get_selected_cells=lambda: [[0, 0]])

    original = _def.WELLPLATE_FORMAT_SETTINGS["96 well plate"]["a1_x_mm"]
    try:
        _def.WELLPLATE_FORMAT_SETTINGS["96 well plate"]["a1_x_mm"] = original + 0.5
        x, _ = sc.get_selected_wells()["A1"]
        assert x == original + 0.5 + _def.WELLPLATE_OFFSET_X_mm
    finally:
        _def.WELLPLATE_FORMAT_SETTINGS["96 well plate"]["a1_x_mm"] = original
