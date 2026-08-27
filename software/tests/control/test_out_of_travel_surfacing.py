"""FOVs outside stage travel are dropped LOUDLY, not silently.

Dropping them is correct - the stage cannot go there - but before this,
a plate seated near the travel edge (or a measured rotation pushing an edge
well over the limit) just quietly imaged fewer FOVs than the user selected.
"""

import logging
from unittest.mock import MagicMock

import pytest

import control._def as _def
from control.core.scan_coordinates import ScanCoordinates


@pytest.fixture
def scan(monkeypatch):
    objective_store = MagicMock()
    objective_store.get_pixel_size_factor.return_value = 1.0
    camera = MagicMock()
    camera.get_fov_size_mm.return_value = 1.0
    stage = MagicMock()
    stage.get_pos.return_value.z_mm = 0.0
    # A narrow travel window so drops are deterministic: x,y in [0, 10] mm.
    monkeypatch.setattr(_def.SOFTWARE_POS_LIMIT, "X_NEGATIVE", 0.0)
    monkeypatch.setattr(_def.SOFTWARE_POS_LIMIT, "X_POSITIVE", 10.0)
    monkeypatch.setattr(_def.SOFTWARE_POS_LIMIT, "Y_NEGATIVE", 0.0)
    monkeypatch.setattr(_def.SOFTWARE_POS_LIMIT, "Y_POSITIVE", 10.0)
    return ScanCoordinates(objectiveStore=objective_store, stage=stage, camera=camera)


def test_add_region_records_and_warns(scan, caplog):
    """A 3x3 grid centered at the travel corner: only the in-travel quadrant
    survives, and the drop is counted and warned about."""
    with caplog.at_level(logging.WARNING):
        scan.add_region("A1", center_x=0.0, center_y=0.0, scan_size_mm=2.8, overlap_percent=10, shape="Square")

    kept = len(scan.region_fov_coordinates["A1"])
    dropped = scan.out_of_travel["A1"]
    assert kept == 4  # the (0..1)^2 quadrant of the 3x3 grid
    assert dropped == 5
    assert any("outside the stage travel" in r.getMessage() and "5 of 9" in r.getMessage() for r in caplog.records)


def test_fully_in_travel_region_has_no_entry(scan, caplog):
    with caplog.at_level(logging.WARNING):
        scan.add_region("B2", center_x=5.0, center_y=5.0, scan_size_mm=2.8, overlap_percent=10, shape="Square")
    assert len(scan.region_fov_coordinates["B2"]) == 9
    assert "B2" not in scan.out_of_travel
    assert not any("outside the stage travel" in r.getMessage() for r in caplog.records)


def test_re_adding_in_travel_clears_stale_entry(scan):
    scan.add_region("A1", center_x=0.0, center_y=0.0, scan_size_mm=2.8, overlap_percent=10, shape="Square")
    assert "A1" in scan.out_of_travel
    scan.add_region("A1", center_x=5.0, center_y=5.0, scan_size_mm=2.8, overlap_percent=10, shape="Square")
    assert "A1" not in scan.out_of_travel


def test_remove_and_clear_purge_entries(scan):
    scan.add_region("A1", center_x=0.0, center_y=0.0, scan_size_mm=2.8, overlap_percent=10, shape="Square")
    scan.remove_region("A1")
    assert scan.out_of_travel == {}

    scan.add_region("A1", center_x=0.0, center_y=0.0, scan_size_mm=2.8, overlap_percent=10, shape="Square")
    scan.clear_regions()
    assert scan.out_of_travel == {}


def test_flexible_region_counts_drops(scan, caplog):
    with caplog.at_level(logging.WARNING):
        scan.add_flexible_region("roi", center_x=0.0, center_y=5.0, center_z=0.0, Nx=3, Ny=3, overlap_percent=10)
    # left column of the 3x3 grid is at x = -0.9 -> dropped
    assert scan.out_of_travel["roi"] == 3
    assert len(scan.region_fov_coordinates["roi"]) == 6


def test_flexible_region_with_step_size_counts_drops(scan):
    scan.add_flexible_region_with_step_size(
        "roi2", center_x=0.0, center_y=5.0, center_z=0.0, Nx=3, Ny=3, dx=1.0, dy=1.0
    )
    assert scan.out_of_travel["roi2"] == 3
    assert len(scan.region_fov_coordinates["roi2"]) == 6


def test_template_region_counts_drops(scan):
    import numpy as np

    scan.add_template_region(
        x_mm=0.0,
        y_mm=5.0,
        z_mm=0.0,
        template_x_mm=np.array([-1.0, 0.0, 1.0]),
        template_y_mm=np.array([0.0, 0.0, 0.0]),
        region_id="tmpl",
    )
    assert scan.out_of_travel["tmpl"] == 1
    assert len(scan.region_fov_coordinates["tmpl"]) == 2


def test_manual_region_warns_on_drops(scan, caplog):
    # a polygon straddling the x=0 travel edge
    polygon = [(-2.0, 4.0), (2.0, 4.0), (2.0, 6.0), (-2.0, 6.0)]
    with caplog.at_level(logging.WARNING):
        points = scan.get_points_for_manual_region(polygon, overlap_percent=10)
    assert points  # the in-travel part survives
    assert any(
        "Manual region" in r.getMessage() and "outside the stage travel" in r.getMessage() for r in caplog.records
    )
