"""HolderAlignmentSession: the wizard's logic, tested without Qt."""

import math

import numpy as np
import pytest

import control._def as _def
from control.core.holder_alignment import (
    circumcenter,
    HolderAlignmentSession,
    SessionError,
)
from control.models.plate_holder import load_plate_holder
from control.models.plate_placement import (
    load_plate_placements,
    PlatePlacement,
    PlatePlacements,
    save_plate_placements,
)

RNG = np.random.default_rng(20260815)


@pytest.fixture
def tree(catalog_tree, design_travel_limits):
    return catalog_tree


def rot(theta_deg, x, y):
    t = math.radians(theta_deg)
    return (math.cos(t) * x - math.sin(t) * y, math.sin(t) * x + math.cos(t) * y)


def well_center(session, row, col, theta_deg, a1):
    dx, dy = rot(theta_deg, col * session.pitch_x_mm, row * session.pitch_y_mm)
    return (a1[0] + dx, a1[1] + dy)


def touch_all_square(session, theta_deg=0.21, a1=(11.01, 7.87), corner=(-0.5, -0.5)):
    """One same-corner touch per well: the corner sits at a constant
    plate-frame offset, so it rides the rotation like everything else."""
    for i, w in enumerate(session.reference_wells):
        cx, cy = well_center(session, w.row, w.col, theta_deg, a1)
        ox, oy = rot(theta_deg, corner[0] * session.well_size_mm, corner[1] * session.well_size_mm)
        session.record_touch(i, cx + ox, cy + oy)


def touch_all_round(session, theta_deg=0.21, a1=(11.31, 10.75)):
    """Three rim touches per well at arbitrary angles."""
    radius = session.well_size_mm / 2
    for i, w in enumerate(session.reference_wells):
        cx, cy = well_center(session, w.row, w.col, theta_deg, a1)
        for phi in (0.3 + i, 2.4 + i, 4.4 + i):
            session.record_touch(i, cx + radius * math.cos(phi), cy + radius * math.sin(phi))


# ------------------------------------------------------------- reference wells


def test_reference_wells_match_design_table(tree):
    assert [w.well_id for w in HolderAlignmentSession("96 well plate").reference_wells] == ["A1", "A12", "H1", "H12"]
    # skip=1 window on 384:
    assert [w.well_id for w in HolderAlignmentSession("384 well plate").reference_wells] == ["B2", "B23", "O2", "O23"]
    # 1536: nominal far corner AF48 is OUTSIDE stage travel; computed ring
    # backs off to the extreme REACHABLE corners:
    assert [w.well_id for w in HolderAlignmentSession("1536 well plate").reference_wells] == [
        "A1",
        "A47",
        "AE1",
        "AE47",
    ]


def test_pitch_zero_format_refused(tree):
    with pytest.raises(SessionError, match="no grid"):
        HolderAlignmentSession("glass slide")


def test_nominate(tree):
    session = HolderAlignmentSession("96 well plate")
    session.nominate(0, "B2")
    assert session.reference_wells[0].well_id == "B2"
    with pytest.raises(SessionError, match="not a well name"):
        session.nominate(0, "12A")
    with pytest.raises(SessionError, match="outside the .* grid"):
        session.nominate(0, "Z99")
    with pytest.raises(SessionError, match="already one of"):
        session.nominate(0, "H12")


def test_nominate_unreachable_refused(tree, monkeypatch):
    session = HolderAlignmentSession("96 well plate")
    monkeypatch.setattr(_def.SOFTWARE_POS_LIMIT, "X_POSITIVE", 100.0)  # A12 at x=110.31
    with pytest.raises(SessionError, match="travel"):
        session.nominate(0, "A12")


# ------------------------------------------------------------------- methods


def test_square_wells_use_one_corner_touch(tree):
    session = HolderAlignmentSession("1536 well plate")
    assert session.touches_per_well == 1
    assert session.feature == "corner_top_left"


def test_round_wells_use_three_rim_touches(tree):
    session = HolderAlignmentSession("96 well plate")
    assert session.touches_per_well == 3
    assert session.feature == "center"
    with pytest.raises(SessionError, match="square-well"):
        session.set_corner_feature("corner_top_right")


def test_circumcenter_recovers_center_and_radius(tree):
    center, radius = (40.0, 30.0), 3.105
    points = [(center[0] + radius * math.cos(a), center[1] + radius * math.sin(a)) for a in (0.5, 2.0, 4.5)]
    cx, cy, r = circumcenter(*points)
    assert (cx, cy) == pytest.approx(center, abs=1e-9)
    assert r == pytest.approx(radius, abs=1e-9)


def test_collinear_rim_touches_rejected_and_undone(tree):
    session = HolderAlignmentSession("96 well plate")
    session.record_touch(0, 10.0, 10.0)
    session.record_touch(0, 11.0, 10.0)
    with pytest.raises(SessionError, match="in a line"):
        session.record_touch(0, 12.0, 10.0)
    # the bad third touch was dropped: the well can be re-touched
    assert len(session.reference_wells[0].touches) == 2


def test_undo_touch(tree):
    session = HolderAlignmentSession("1536 well plate")
    session.record_touch(0, 10.0, 7.0)
    assert session.reference_wells[0].point_mm is not None
    session.undo_touch(0)
    assert session.reference_wells[0].point_mm is None
    with pytest.raises(SessionError, match="no touches"):
        session.undo_touch(0)


# ----------------------------------------------------------------------- fit


def test_corner_method_constant_offset_cancels_exactly(tree):
    """The theorem the 4-touch method rests on: a constant same-corner offset
    shifts every nominal identically, so the CENTERED rotation estimate is
    exactly as if the centers had been touched."""
    session = HolderAlignmentSession("1536 well plate")
    touch_all_square(session, theta_deg=0.37)
    result = session.fit()
    assert result.rotation_deg == pytest.approx(0.37, abs=1e-9)
    assert not result.rejected


def test_round_method_recovers_rotation(tree):
    session = HolderAlignmentSession("96 well plate")
    touch_all_round(session, theta_deg=0.21)
    result = session.fit()
    assert result.rotation_deg == pytest.approx(0.21, abs=1e-9)
    assert not result.rejected
    # rim QC: every fitted radius equals the actual well radius
    for w in session.reference_wells:
        assert w.fitted_radius_mm == pytest.approx(session.well_size_mm / 2, abs=1e-9)


def test_three_wells_is_the_accepted_fallback(tree):
    session = HolderAlignmentSession("1536 well plate")
    touch_all_square(session, theta_deg=0.2)
    session.undo_touch(3)
    assert session.wells_measured == 3 and session.can_fit
    assert session.fit().rotation_deg == pytest.approx(0.2, abs=1e-9)
    session.undo_touch(2)
    assert not session.can_fit
    with pytest.raises(SessionError, match="at least 3"):
        session.fit()


# ---------------------------------------------------------------------- save


def test_save_writes_minimal_holder_record_and_nothing_else(tree):
    session = HolderAlignmentSession("1536 well plate")
    touch_all_square(session, theta_deg=0.37)
    session.save()

    holder = load_plate_holder()
    assert holder.rotation_deg == 0.37
    assert holder.measured.on == "1536 well plate"
    assert holder.measured.feature == "corner_top_left"
    assert [p.well for p in holder.measured.points] == ["A1", "A47", "AE1", "AE47"]
    assert holder.measured.timestamp  # provenance present
    # the fitted translation died here: no placement entry was written
    assert load_plate_placements() is None or "1536 well plate" not in load_plate_placements().placements


def test_save_refuses_rejected_fit(tree):
    session = HolderAlignmentSession("1536 well plate")
    touch_all_square(session, theta_deg=0.1)
    # re-record the last well one pitch off (the classic mis-click)
    session.undo_touch(3)
    w = session.reference_wells[3]
    cx, cy = well_center(session, w.row, w.col, 0.1, (11.01, 7.87))
    session.record_touch(3, cx + session.pitch_x_mm - 0.5 * session.well_size_mm, cy - 0.5 * session.well_size_mm)
    with pytest.raises(SessionError, match="well you named"):
        session.save()
    assert load_plate_holder() is None


def test_save_warn_gate_requires_confirmation(tree):
    session = HolderAlignmentSession("1536 well plate")
    touch_all_square(session, theta_deg=1.8)  # seating cannot legitimately do this
    with pytest.raises(SessionError, match="normal seating"):
        session.save()
    session.save(confirm_warnings=True)
    assert load_plate_holder().rotation_deg == pytest.approx(1.8, abs=0.011)


def test_save_offers_and_clears_stale_overrides(tree):
    save_plate_placements(
        PlatePlacements(
            placements={
                "96 well plate": PlatePlacement(a1_dx_mm=0.1, rotation_deg=0.5),
                "384 well plate": PlatePlacement(a1_dx_mm=0.2),
            }
        )
    )
    session = HolderAlignmentSession("1536 well plate")
    assert session.formats_with_measured_overrides() == ["96 well plate"]

    touch_all_square(session, theta_deg=0.37)
    session.save(clear_overrides=("96 well plate",))

    placements = load_plate_placements().placements
    assert placements["96 well plate"].rotation_deg is None  # now inherits the holder
    assert placements["96 well plate"].a1_dx_mm == 0.1  # the a1 delta survives
    assert placements["384 well plate"].a1_dx_mm == 0.2


# -------------------------------------------------------------------- verify


def test_holdout_residual_is_zero_for_consistent_touch(tree):
    session = HolderAlignmentSession("1536 well plate")
    theta, a1, corner = 0.37, (11.01, 7.87), (-0.5, -0.5)
    touch_all_square(session, theta_deg=theta, a1=a1, corner=corner)
    # a 5th well NOT in the fit, touched at the same corner:
    cx, cy = well_center(session, 15, 23, theta, a1)
    ox, oy = rot(theta, corner[0] * session.well_size_mm, corner[1] * session.well_size_mm)
    assert session.holdout_residual_um("P24", (cx + ox, cy + oy)) == pytest.approx(0.0, abs=1e-6)


def test_holdout_rejects_fit_wells(tree):
    session = HolderAlignmentSession("1536 well plate")
    touch_all_square(session)
    with pytest.raises(SessionError, match="hold-out"):
        session.holdout_residual_um("A1", (11.0, 7.0))


# -------------------------------------------------------------------- status


def test_status_line_tracks_provenance(tree):
    session = HolderAlignmentSession("96 well plate")
    assert "0.00 deg assumed" in session.status_line()

    other = HolderAlignmentSession("1536 well plate")
    touch_all_square(other, theta_deg=0.37)
    other.save()
    assert "0.37 deg (holder record)" in session.status_line()

    save_plate_placements(PlatePlacements(placements={"96 well plate": PlatePlacement(rotation_deg=0.5)}))
    assert "0.50 deg (measured for this format)" in session.status_line()
