"""A change of the effective XY offsets clears the mosaic and the live-drawn regions (spec C §7.3, the
"Calibration change (C2)" tests of §9): by provenance, with no active-tab guard, and imported plans survive."""

import types

import numpy as np
import pandas as pd
import pytest

import control._def
import control.widgets
from control.core.scan_coordinates import ScanCoordinates
from control.widgets_mosaic import MANUAL_ROI_LAYER, DisplayMode, UnifiedMosaicWidget
from tests.control.test_mosaic_xy_offsets import _Camera, _Contrast, _Stage, _store, _tile
from tests.control.test_objective_store_offsets import OBJECTIVES, ObjectiveStore

SQUARE = np.array([[25.0, 30.0], [25.3, 30.0], [25.3, 30.3], [25.0, 30.3]])  # mm, well inside the XY limits


def _scan(store=None):
    store = store or ObjectiveStore(objectives_dict=OBJECTIVES, default_objective="20x")
    return ScanCoordinates(store, _Stage(), _Camera())


class TestProvenance:
    def test_only_live_drawn_regions_are_removed(self):
        scan = _scan()
        scan.set_manual_coordinates([SQUARE, SQUARE + 1.0], overlap_percent=10)
        assert scan.live_drawn_region_ids == {"manual0", "manual1"}
        # A well region and an imported "Manual" region added next to them (the acquisition YAML path)
        scan.add_region("A1", 10.0, 10.0, 1.0, 10, "Square")
        scan.add_region_from_fovs("imported", [(12.0, 12.0), (12.2, 12.0)], shape="Manual")
        scan.remove_live_drawn_regions()
        assert set(scan.region_centers) == {"A1", "imported"}
        assert scan.region_shapes["imported"] == "Manual"  # the label did not decide
        assert scan.live_drawn_region_ids == set()

    def test_a_reapplied_yaml_that_reuses_manual0_survives(self):
        scan = _scan()
        scan.set_manual_coordinates([SQUARE, SQUARE + 1.0], overlap_percent=10)
        # Re-applying an acquisition YAML clears the plan and registers its regions, "manual0" among them
        scan.clear_regions()
        scan.add_region_from_fovs("manual0", [(12.0, 12.0), (12.2, 12.0)], shape="Manual")
        scan.remove_live_drawn_regions()
        assert set(scan.region_centers) == {"manual0"}

    def test_a_loaded_coordinate_csv_survives(self):
        scan = _scan()
        scan.set_manual_coordinates([SQUARE], overlap_percent=10)
        df = pd.DataFrame({"region": ["manual", "manual"], "x (mm)": [12.0, 12.2], "y (mm)": [12.0, 12.0]})
        control.widgets.load_coordinate_regions_from_dataframe(scan, df)  # registers "Manual" shapes too
        scan.remove_live_drawn_regions()
        assert set(scan.region_centers) == {"manual"}
        assert scan.region_shapes["manual"] == "Manual"

    def test_removing_a_live_region_by_hand_forgets_it(self):
        scan = _scan()
        scan.set_manual_coordinates([SQUARE], overlap_percent=10)
        scan.remove_region("manual")
        assert scan.live_drawn_region_ids == set()


class TestWellplateWidget:
    def _widget(self, scan, current_tab_is_self):
        """The real method on a stand-in, as test_live_scan_grid does: the tab guard is the point."""
        widget = types.SimpleNamespace(
            shapes_mm=[SQUARE],
            scanCoordinates=scan,
            performance_mode=True,
            update_coordinates=lambda: None,  # what update_manual_shape calls past its guard; not under test here
            _log=types.SimpleNamespace(info=lambda *a, **k: None, debug=lambda *a, **k: None),
        )
        widget.tab_widget = types.SimpleNamespace(currentWidget=lambda: widget if current_tab_is_self else object())
        return widget

    @pytest.mark.parametrize("current_tab_is_self", [True, False], ids=["tab-active", "tab-inactive"])
    def test_clear_manual_regions_ignores_the_tab_guard_and_performance_mode(self, current_tab_is_self):
        scan = _scan()
        scan.set_manual_coordinates([SQUARE], overlap_percent=10)
        scan.add_region("A1", 10.0, 10.0, 1.0, 10, "Square")
        widget = self._widget(scan, current_tab_is_self)
        # The existing shape path stops at the guard when the tab is inactive...
        control.widgets.WellplateMultiPointWidget.update_manual_shape(widget, [])
        assert (widget.shapes_mm is None) == current_tab_is_self
        widget.shapes_mm = [SQUARE]
        # ...the dedicated path does not.
        control.widgets.WellplateMultiPointWidget.clear_manual_regions(widget)
        assert widget.shapes_mm is None
        assert set(scan.region_centers) == {"A1"}


class TestMosaicWidget:
    def test_clear_for_calibration_change_removes_tiles_and_the_manual_roi_layer(self, qtbot, monkeypatch):
        monkeypatch.setattr(control._def, "MOSAIC_VIEW_TARGET_PIXEL_SIZE_UM", 2.0)
        store = _store("20x")
        widget = UnifiedMosaicWidget(store, _Camera(), _Contrast())
        qtbot.addWidget(widget)
        widget.mode = DisplayMode.MOSAIC
        widget.updateTile(_tile(np.full((100, 100), 200, dtype=np.uint16), (25000.0, 30000.0), "20x"))
        widget.enable_shape_drawing(True)
        assert "BF" in widget.viewer.layers and MANUAL_ROI_LAYER in widget.viewer.layers
        widget.clearAllLayers()
        assert MANUAL_ROI_LAYER in widget.viewer.layers  # today's clear keeps the ROI layer on purpose
        widget.updateTile(_tile(np.full((100, 100), 200, dtype=np.uint16), (25000.0, 30000.0), "20x"))
        widget.clear_for_calibration_change()
        assert "BF" not in widget.viewer.layers
        assert MANUAL_ROI_LAYER not in widget.viewer.layers
        assert widget.shapes_mm == [] and widget.shape_layer is None and not widget.layers_initialized
