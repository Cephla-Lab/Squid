"""The Time at XY parameter model and its pure helpers (no Qt, no hardware).

One channel table (ChannelPlan rows with Zi / R / Zf flags), one z plan shared by every phase
(ZStackPlan: From Bottom / Set Range / Relative), and a choice of which of its planes the recordings
visit (RecordSliceSelection).  The widget's validate(), the confirm dialog and the worker all go
through these helpers, so what is confirmed is what is acquired.
"""

import pytest

from control.core.camera_geometry import CameraGeometry
from control.core.record_zstack_controller import (
    ChannelPlan,
    FrameRateTestResult,
    RecordSliceSelection,
    RecordZStackAcquisitionParameters,
    ZStackPlan,
    contrast_af_applies,
    describe_per_fov_plan,
    frame_count,
    frame_rate_stats,
    recording_dirnames,
    recording_offsets_for_plan,
    resolve_effective_fps,
    set_range_plane_count,
    slice_label,
    slice_role_indices,
    validate_record_zstack_params,
    zstack_offsets_for_mode,
    zstack_plane_count_for_plan,
    zstack_subdir,
)
from control.core.zstack_modes import Z_MODE_FROM_BOTTOM, Z_MODE_RELATIVE, Z_MODE_SET_RANGE, Z_MODES, relative_stack_Nz
from control.models.acquisition_config import AcquisitionChannel, CameraSettings, IlluminationSettings


def _channel(name="BF", exposure=50.0):
    return AcquisitionChannel(
        name=name,
        camera_settings=CameraSettings(exposure_time_ms=exposure, gain_mode=0.0),
        illumination_settings=IlluminationSettings(intensity=50.0),
    )


def _row(name="BF", **fields):
    exposure = fields.pop("exposure", 50.0)
    channel = _channel(name, max(exposure, 0.1))
    # The table's editors write the exposure into a copy of the channel, as this does: assignment
    # is not validated, so a non-positive value has to be caught by validate_record_zstack_params.
    channel.exposure_time = exposure
    return ChannelPlan(channel=channel, **fields)


# ---------------------------------------------------------------------------- rows and parameters


def test_frame_count():
    assert frame_count(10.0, 30.0) == 300
    assert frame_count(7.5, 2.0) == 15


def test_channel_plan_defaults_and_activity():
    row = _row()
    assert (row.fps, row.duration_s, row.zi, row.r, row.zf) == (10.0, 1.0, False, False, False)
    assert row.name == "BF" and not row.active and row.geometry.is_inherit and row.effective_fps is None
    assert _row(zi=True).active and _row(r=True).active and _row(zf=True).active


def test_parameters_split_the_table_into_phases_in_row_order():
    params = RecordZStackAcquisitionParameters(
        base_path="/tmp",
        experiment_id="x",
        channels=[_row("c", r=True), _row("a", zi=True, zf=True), _row("idle"), _row("b", zi=True, r=True)],
    )
    assert [c.name for c in params.zi_channels] == ["a", "b"]
    assert [p.name for p in params.r_plans] == ["c", "b"]
    assert [c.name for c in params.zf_channels] == ["a"]
    assert [c.name for c in params.active_channels] == ["c", "a", "b"]


def test_channel_plan_yaml_round_trip():
    row = _row(
        "GFP", fps=40.0, duration_s=6.0, zi=True, r=True, geometry=CameraGeometry(2, 1024, 512, "MONO8"), exposure=7.5
    )
    d = row.to_yaml_dict()
    assert list(d) == ["channel", "fps", "duration_s", "zi", "r", "zf", "camera"]
    assert d["camera"] == {"binning": 2, "crop_width": 1024, "crop_height": 512, "pixel_format": "MONO8"}
    assert ChannelPlan.from_yaml_dict(d) == row
    # Nothing pinned: no camera key at all.
    assert "camera" not in _row().to_yaml_dict()
    # A row's effective fps is a run's record of what happened, not a setting: written for an R row
    # once it is resolved, never read back.
    row.effective_fps = 28.0
    d = row.to_yaml_dict()
    assert d["effective_fps"] == 28.0
    assert ChannelPlan.from_yaml_dict(d).effective_fps is None
    stack_row = _row(zf=True)
    stack_row.effective_fps = 28.0
    assert "effective_fps" not in stack_row.to_yaml_dict()


def test_zstack_subdir_names():
    assert (zstack_subdir("initial"), zstack_subdir("final")) == ("zstack_initial", "zstack_final")


# ---------------------------------------------------------------------------- z modes


def test_z_mode_labels():
    assert Z_MODES == ["From Bottom", "Set Range", "Relative"]
    assert (Z_MODE_FROM_BOTTOM, Z_MODE_SET_RANGE, Z_MODE_RELATIVE) == ("From Bottom", "Set Range", "Relative")


def test_from_bottom_starts_at_the_reference():
    plan = ZStackPlan(mode=Z_MODE_FROM_BOTTOM, dz_um=1.5, nz=4)
    assert zstack_plane_count_for_plan(plan) == 4
    assert zstack_offsets_for_mode(plan, z_ref_mm=2.0) == [0.0, 1.5, 3.0, 4.5]
    assert zstack_offsets_for_mode(ZStackPlan(nz=1), 0.0) == [0.0]
    with pytest.raises(ValueError):
        zstack_plane_count_for_plan(ZStackPlan(nz=0))
    with pytest.raises(ValueError):
        zstack_offsets_for_mode(ZStackPlan(nz=3, dz_um=0.0), 0.0)


@pytest.mark.parametrize(
    "z_range, dz, nz",
    [
        (6.0, 1.0, 7),
        (5.0, 1.0, 7),  # rounded up to cover the range, and odd
        (4.0, 2.0, 3),
        (0.5, 1.0, 3),  # never fewer than three: one plane each side of the reference
        (20.0, 5.0, 5),
        (20.0, 4.969, 5),  # dz snapped to a whole Z microstep must not tip 5 planes into 7
        (20.0, 0.0, 1),
    ],
)
def test_relative_stack_nz_is_odd_and_covers_the_range(z_range, dz, nz):
    assert relative_stack_Nz(z_range, dz) == nz


def test_relative_is_centred_on_the_reference():
    plan = ZStackPlan(mode=Z_MODE_RELATIVE, dz_um=2.0, nz=99, z_range_um=8.0)  # nz is derived, not read
    assert zstack_plane_count_for_plan(plan) == 5
    offsets = zstack_offsets_for_mode(plan, z_ref_mm=1.234)
    assert offsets == [-4.0, -2.0, 0.0, 2.0, 4.0]
    assert offsets[len(offsets) // 2] == 0.0  # the middle plane is the reference


def test_set_range_plane_count_follows_the_multipoint_rule():
    assert set_range_plane_count(1000.0, 1006.0, 1.0) == 7  # an exact multiple does not gain a plane
    assert set_range_plane_count(1000.0, 1005.0, 2.0) == 4  # rounded up to reach z_max
    assert set_range_plane_count(1000.0, 1000.0, 1.0) == 1
    with pytest.raises(ValueError):
        set_range_plane_count(1000.0, 1006.0, 0.0)
    with pytest.raises(ValueError):
        set_range_plane_count(1006.0, 1000.0, 1.0)


def test_set_range_is_absolute_and_expressed_relative_to_the_reference():
    plan = ZStackPlan(mode=Z_MODE_SET_RANGE, dz_um=2.0, z_min_mm=1.000, z_max_mm=1.006)
    assert zstack_plane_count_for_plan(plan) == 4
    # Wherever the FOV's reference is, reference + offset lands on the same absolute planes.
    for z_ref_mm in (1.000, 1.003, 0.5):
        offsets = zstack_offsets_for_mode(plan, z_ref_mm)
        assert [z_ref_mm * 1000.0 + off for off in offsets] == pytest.approx([1000.0, 1002.0, 1004.0, 1006.0])


def test_every_mode_agrees_on_count_and_offsets():
    for plan in (
        ZStackPlan(mode=Z_MODE_FROM_BOTTOM, dz_um=0.7, nz=6),
        ZStackPlan(mode=Z_MODE_RELATIVE, dz_um=0.7, z_range_um=5.0),
        ZStackPlan(mode=Z_MODE_SET_RANGE, dz_um=0.7, z_min_mm=1.0, z_max_mm=1.0049),
    ):
        offsets = zstack_offsets_for_mode(plan, 1.002)
        assert len(offsets) == zstack_plane_count_for_plan(plan)
        assert offsets == sorted(offsets)
    with pytest.raises(ValueError, match="unknown z mode"):
        zstack_plane_count_for_plan(ZStackPlan(mode="Sideways"))


# ---------------------------------------------------------------------------- record planes


@pytest.mark.parametrize(
    "nz, roles", [(1, (0, 0, 0)), (2, (0, 0, 1)), (3, (0, 1, 2)), (4, (0, 1, 3)), (7, (0, 3, 6)), (8, (0, 3, 7))]
)
def test_slice_role_indices(nz, roles):
    assert slice_role_indices(nz) == dict(zip(("bottom", "mid", "top"), roles))


def test_record_slice_selection_resolves_roles_and_explicit_planes():
    assert RecordSliceSelection().resolve(7) == [0, 3, 6]  # Bottom / Mid / Top by default
    assert RecordSliceSelection().resolve(1) == [0]  # they coincide
    assert RecordSliceSelection(roles=["mid"]).resolve(5) == [2]
    assert RecordSliceSelection(roles=["top", "bottom"], indices=[4, 1, 4]).resolve(7) == [0, 1, 4, 6]
    # Explicit planes outside the current stack are dropped; the presets follow the plane count.
    assert RecordSliceSelection(roles=["top"], indices=[5, -1]).resolve(3) == [2]
    assert RecordSliceSelection(roles=[], indices=[]).resolve(5) == []
    assert RecordSliceSelection(roles=["nonsense"]).resolve(5) == []


def test_record_slice_selection_yaml_round_trip():
    selection = RecordSliceSelection(roles=["top", "bottom"], indices=[5, 2])
    d = selection.to_yaml_dict()
    assert d == {"roles": ["bottom", "top"], "indices": [2, 5]}
    assert RecordSliceSelection.from_yaml_dict(d).resolve(9) == selection.resolve(9)
    assert RecordSliceSelection.from_yaml_dict(None).resolve(3) == [0, 1, 2]
    assert RecordSliceSelection.from_yaml_dict({"roles": ["mid", "junk"], "indices": ["3"]}).resolve(5) == [2, 3]


def test_slice_labels():
    assert [slice_label(i, 7) for i in range(7)] == ["Bottom", "Mid−2·dz", "Mid−dz", "Mid", "Mid+dz", "Mid+2·dz", "Top"]
    assert slice_label(0, 1) == "Bottom"


def test_recordings_only_visit_planes_of_the_stack():
    plan = ZStackPlan(mode=Z_MODE_RELATIVE, dz_um=2.0, z_range_um=8.0)
    assert recording_offsets_for_plan(plan, RecordSliceSelection(), 0.0) == [(0, -4.0), (2, 0.0), (4, 4.0)]
    assert recording_offsets_for_plan(plan, RecordSliceSelection(roles=[], indices=[1, 3]), 0.0) == [
        (1, -2.0),
        (3, 2.0),
    ]
    set_range = ZStackPlan(mode=Z_MODE_SET_RANGE, dz_um=2.0, z_min_mm=1.000, z_max_mm=1.004)
    assert recording_offsets_for_plan(set_range, RecordSliceSelection(roles=["top"]), 1.001) == [
        (2, pytest.approx(3.0))
    ]


def test_recording_dirnames_are_safe_unique_and_in_order():
    assert recording_dirnames(["Fluorescence 488 nm Ex", "BF LED matrix full"]) == [
        "Fluorescence_488_nm_Ex",
        "BF_LED_matrix_full",
    ]
    # Names that only differ in punctuation never share a directory; nothing escapes its parent.
    assert recording_dirnames(["a/b", "a b", "a:b"]) == ["a_b", "a_b_1", "a_b_2"]
    assert recording_dirnames(["../..", "", "x"]) == ["channel", "channel_1", "x"]


# ---------------------------------------------------------------------------- validation


def _valid(**overrides):
    kwargs = dict(
        base_path="/tmp/test",
        selected_well_count=1,
        channels=[_row(zf=True)],
        zstack=ZStackPlan(),
        record_slices=RecordSliceSelection(),
        use_laser_af=False,
        use_contrast_af=False,
        laser_af_has_reference=False,
    )
    kwargs.update(overrides)
    return kwargs


def test_valid_combinations():
    assert validate_record_zstack_params(**_valid()) is None
    assert validate_record_zstack_params(**_valid(channels=[_row(r=True)])) is None
    assert validate_record_zstack_params(**_valid(channels=[_row(zi=True, r=True, zf=True), _row("idle")])) is None
    assert validate_record_zstack_params(**_valid(use_laser_af=True, laser_af_has_reference=True)) is None
    relative = ZStackPlan(mode=Z_MODE_RELATIVE, dz_um=1.0, z_range_um=4.0)
    assert validate_record_zstack_params(**_valid(zstack=relative, use_contrast_af=True)) is None
    set_range = ZStackPlan(mode=Z_MODE_SET_RANGE, dz_um=1.0, z_min_mm=1.0, z_max_mm=1.01)
    assert validate_record_zstack_params(**_valid(zstack=set_range)) is None
    assert validate_record_zstack_params(**_valid(use_focus_map=True, focus_map_ready=True)) is None


@pytest.mark.parametrize(
    "overrides, fragment",
    [
        (dict(base_path=""), "Base path"),
        (dict(selected_well_count=0), "At least one well"),
        (dict(channels=[]), "tick Zi, R, or Zf"),
        (dict(channels=[_row()]), "tick Zi, R, or Zf"),
        (dict(channels=[_row(r=True, fps=0.0)]), "FPS must be greater than 0"),
        (dict(channels=[_row(r=True, duration_s=0.0)]), "duration must be greater than 0"),
        (dict(channels=[_row(r=True, fps=1.0, duration_s=0.4)]), "yields 0 frames"),
        (dict(channels=[_row("GFP", zf=True, exposure=0.0)]), "GFP: exposure must be greater than 0"),
        (
            dict(channels=[_row(r=True, geometry=CameraGeometry(pixel_format="BAYER_RG8"))]),
            "recordings need a mono pixel format",
        ),
        (dict(zstack=ZStackPlan(mode="Sideways")), "unknown mode"),
        (dict(zstack=ZStackPlan(dz_um=0.0)), "dz must be greater than 0"),
        (dict(zstack=ZStackPlan(nz=0)), "Nz must be at least 1"),
        (dict(zstack=ZStackPlan(mode=Z_MODE_RELATIVE, z_range_um=0.0)), "range must be greater than 0"),
        (
            dict(zstack=ZStackPlan(mode=Z_MODE_SET_RANGE, z_min_mm=1.0, z_max_mm=1.0)),
            "Z-max must be greater than Z-min",
        ),
        (
            dict(zstack=ZStackPlan(mode=Z_MODE_SET_RANGE, z_min_mm=1.0, z_max_mm=1.1), use_laser_af=True),
            "Set Range z mode is incompatible with autofocus",
        ),
        (
            dict(zstack=ZStackPlan(mode=Z_MODE_SET_RANGE, z_min_mm=1.0, z_max_mm=1.1), use_contrast_af=True),
            "Set Range z mode is incompatible with autofocus",
        ),
        (
            dict(channels=[_row(r=True)], record_slices=RecordSliceSelection(roles=[], indices=[])),
            "Pick at least one plane to record",
        ),
        (dict(use_laser_af=True), "no reference position"),
        (dict(use_focus_map=True), "no focus surface is fitted"),
    ],
)
def test_validation_failures(overrides, fragment):
    error = validate_record_zstack_params(**_valid(**overrides))
    assert error is not None and fragment in error


def test_validation_ignores_rows_that_are_not_used():
    # An unticked row's exposure and a stack-only row's fps are never applied.
    rows = [_row(zf=True, fps=0.0, duration_s=0.0), _row("idle", exposure=0.0)]
    assert validate_record_zstack_params(**_valid(channels=rows)) is None
    # A colour stack row is fine; only recordings need mono.
    colour = [_row(zf=True, geometry=CameraGeometry(pixel_format="BAYER_RG8"))]
    assert validate_record_zstack_params(**_valid(channels=colour)) is None
    # No record plane picked matters only when something is recorded.
    assert validate_record_zstack_params(**_valid(record_slices=RecordSliceSelection(roles=[]))) is None


def test_one_frame_recording_is_valid():
    assert validate_record_zstack_params(**_valid(channels=[_row(r=True, fps=1.0, duration_s=1.0)])) is None


# ---------------------------------------------------------------------------- focus / summary


def _params(**fields):
    fields.setdefault("channels", [_row(zf=True)])
    return RecordZStackAcquisitionParameters(base_path="/tmp", experiment_id="x", **fields)


def test_contrast_af_applies_only_to_one_plane_or_a_centred_stack():
    assert contrast_af_applies(_params(use_contrast_af=True))  # nz = 1
    assert contrast_af_applies(_params(use_contrast_af=True, zstack=ZStackPlan(mode=Z_MODE_RELATIVE)))
    assert not contrast_af_applies(_params(use_contrast_af=True, zstack=ZStackPlan(nz=3)))
    assert not contrast_af_applies(_params(use_contrast_af=True, use_laser_af=True))  # laser AF wins
    assert not contrast_af_applies(_params())


def test_per_fov_plan_names_the_focus_source_and_every_phase():
    params = _params(
        use_focus_map=True,
        use_laser_af=True,
        channels=[
            _row("488", fps=50.0, duration_s=10.0, zi=True, r=True, zf=True),
            _row("561", fps=20.0, duration_s=5.0, r=True, geometry=CameraGeometry(binning=2)),
            _row("BF", zi=True),
        ],
        zstack=ZStackPlan(mode=Z_MODE_RELATIVE, dz_um=1.5, z_range_um=12.0),
        record_slices=RecordSliceSelection(roles=["bottom", "top"]),
    )
    lines = describe_per_fov_plan(params).split("\n")
    assert lines[0] == "Focus: focus map z → Laser AF"
    assert lines[1] == "Zi: 2 ch × 9 planes (-6.0 … +6.0 µm rel. reference)"
    assert lines[2] == (
        "R: 2 recordings × 2 of 9 planes (Bottom -6.0 µm, Top +6.0 µm): 488 50 fps × 10 s, "
        "561 20 fps × 5 s [binning 2×2, crop current, format current] — 30.0 s/FOV"
    )
    assert lines[3] == "Zf: 1 ch × 9 planes (-6.0 … +6.0 µm rel. reference)"
    assert len(lines) == 4


def test_per_fov_plan_says_when_stack_channels_are_saved_separately():
    params = _params(
        channels=[_row("488", zi=True), _row("BF", zi=True, geometry=CameraGeometry(binning=2)), _row("561", zi=True)],
        zstack=ZStackPlan(mode=Z_MODE_SET_RANGE, dz_um=2.0, z_min_mm=1.0, z_max_mm=1.004),
    )
    text = describe_per_fov_plan(params)
    assert "Focus: stage z at each position" in text
    assert "Zi: 3 ch × 3 planes (1.0000 … 1.0040 mm abs)" in text
    assert "2 camera settings: acquired one after another into separate stores (not overlaid)" in text
    assert "488, 561: binning current, crop current, format current" in text
    assert "BF: binning 2×2, crop current, format current" in text


def test_per_fov_plan_flags_contrast_af_that_will_not_run():
    text = describe_per_fov_plan(_params(use_contrast_af=True, zstack=ZStackPlan(nz=3)))
    assert "Contrast AF (skipped: only runs with Nz = 1 or Relative)" in text
    assert describe_per_fov_plan(_params(use_contrast_af=True)).startswith("Focus: Contrast AF\n")


# ---------------------------------------------------------------------------- frame rate


def test_resolve_effective_fps_bounds_the_request_by_the_camera_maximum():
    from unittest.mock import MagicMock

    cam = MagicMock()
    cam.get_max_frame_rate.return_value = 28.0
    assert resolve_effective_fps(cam, 30.0, 2.0) == 28.0
    assert resolve_effective_fps(cam, 10.0, 2.0) == 10.0
    cam.get_max_frame_rate.assert_called_with(2.0)
    # No query, or a failing one: the request stands.
    assert resolve_effective_fps(object(), 30.0, 2.0) == 30.0
    cam.get_max_frame_rate.side_effect = RuntimeError("no sdk")
    assert resolve_effective_fps(cam, 30.0, 2.0) == 30.0


def test_frame_rate_stats():
    stamps = [0.0, 0.1, 0.2, 0.5, 0.6]
    mean, min_fps, max_interval, gaps = frame_rate_stats(stamps, [10, 11, 12, 15, 16], duration_s=1.0)
    assert mean == pytest.approx(4 / 0.6)
    assert max_interval == pytest.approx(0.3) and min_fps == pytest.approx(1 / 0.3)
    assert gaps == 2  # ids 13 and 14 never arrived
    assert frame_rate_stats([], [], 5.0) == (0.0, 0.0, 0.0, 0)
    assert frame_rate_stats([1.0], [3], 5.0) == (0.2, 0.0, 0.0, 0)


def test_frame_rate_test_result_texts():
    ok = FrameRateTestResult("GFP", 4.0, 2.0, 120.0, 5.0, 598, 119.6, 80.0, 0.0125, 0)
    assert ok.summary() == "119.6 fps (min 80.0 over 5.0 s)"
    assert "camera offered 120.00 fps; 598 frames in 5.0 s" in ok.detail()
    gappy = FrameRateTestResult("GFP", duration_s=5.0, frame_count=10, mean_fps=2.0, min_fps=1.0, id_gaps=1)
    assert gappy.summary() == "2.0 fps (min 1.0, 1 gap over 5.0 s)"
    assert FrameRateTestResult("GFP", duration_s=5.0).summary() == "no frames in 5.0 s"
    assert FrameRateTestResult("GFP", error="camera unplugged").summary() == "failed: camera unplugged"
