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


def test_manual_region_records_and_warns_on_drops(scan, caplog):
    # a polygon straddling the x=0 travel edge
    polygon = [(-2.0, 4.0), (2.0, 4.0), (2.0, 6.0), (-2.0, 6.0)]
    with caplog.at_level(logging.WARNING):
        scan.set_manual_coordinates([polygon], overlap_percent=10)
    assert scan.region_fov_coordinates["manual"]  # the in-travel part survives
    assert scan.out_of_travel["manual"] > 0
    assert "Region 'manual'" in caplog.text and "outside the stage travel" in caplog.text


@pytest.mark.parametrize(
    "add",
    [
        lambda s, rid, x, y: s.add_flexible_region(rid, x, y, 0.0, Nx=2, Ny=2, overlap_percent=10),
        lambda s, rid, x, y: s.add_flexible_region_with_step_size(rid, x, y, 0.0, Nx=2, Ny=2, dx=1.0, dy=1.0),
    ],
    ids=["overlap", "step_size"],
)
def test_fully_dropped_flexible_region_is_stored_empty_with_its_drops(scan, caplog, add):
    """A region recomputed entirely out of range replaces its predecessor with
    an EMPTY region (a planned region always exists), and all Nx*Ny drops are
    recorded - the old coordinates must not stay active under the same ID."""
    add(scan, "roi", 5.0, 5.0)
    assert len(scan.region_fov_coordinates["roi"]) == 4

    with caplog.at_level(logging.WARNING):
        add(scan, "roi", 50.0, 50.0)

    assert scan.region_fov_coordinates["roi"] == []
    assert scan.region_centers["roi"][:2] == [50.0, 50.0]
    assert scan.out_of_travel["roi"] == 4
    assert "4 of 4 planned FOVs" in caplog.text


def test_drop_counts_follow_renames_and_replacements(scan):
    scan.add_flexible_region("roi", center_x=0.0, center_y=5.0, center_z=0.0, Nx=3, Ny=3, overlap_percent=10)
    assert scan.out_of_travel["roi"] == 3

    scan.rename_region("roi", "edge")
    assert "roi" not in scan.out_of_travel and scan.out_of_travel["edge"] == 3
    assert "roi" not in scan.region_centers and "edge" in scan.region_fov_coordinates

    scan.add_single_fov_region("edge", 5.0, 5.0, 0.0)  # replaced by a region with nothing dropped
    assert "edge" not in scan.out_of_travel

    scan.add_flexible_region("fovs", center_x=0.0, center_y=5.0, center_z=0.0, Nx=3, Ny=3, overlap_percent=10)
    scan.add_region_from_fovs("fovs", [(5.0, 5.0), (6.0, 5.0)])
    assert "fovs" not in scan.out_of_travel


def test_manual_region_counts_only_fovs_the_polygon_selects(scan):
    """A triangle crossing the x=0 edge: bounding-box points outside the
    polygon are not planned FOVs and must not count as dropped ones."""

    def dropped_for(polygon):
        scan.set_manual_coordinates([polygon], overlap_percent=0)
        return scan.out_of_travel.get("manual", 0)

    triangle = [(-3.0, 2.0), (3.0, 2.0), (3.0, 8.0)]  # out of travel: only its thin left tip
    bounding_box = [(-3.0, 2.0), (3.0, 2.0), (3.0, 8.0), (-3.0, 8.0)]  # out of travel: the whole x<0 strip
    assert 0 < dropped_for(triangle) < dropped_for(bounding_box)
