import builtins
import logging
import pytest
import tempfile

import squid.stage.cephla
import squid.stage.prior
import squid.stage.utils
import squid.stage.pi
import squid.config
import squid.abc
from tests.control.test_microcontroller import get_test_micro


def test_create_simulated_stages():
    microcontroller = get_test_micro()
    cephla_stage = squid.stage.cephla.CephlaStage(microcontroller, squid.config.get_stage_config())


def test_simulated_cephla_stage_ops():
    microcontroller = get_test_micro()
    stage: squid.stage.cephla.CephlaStage = squid.stage.cephla.CephlaStage(
        microcontroller, squid.config.get_stage_config()
    )

    assert stage.get_pos() == squid.abc.Pos(x_mm=0.0, y_mm=0.0, z_mm=0.0, theta_rad=0.0)


def test_position_caching():
    (unused_temp_fd, temp_cache_path) = tempfile.mkstemp(".cache", "squid_testing_")

    # Use 6 figures after the decimal so we test that we can capture nanometers
    p = squid.abc.Pos(x_mm=11.111111, y_mm=22.222222, z_mm=1.333333, theta_rad=None)
    squid.stage.utils.cache_position(pos=p, stage_config=squid.config.get_stage_config(), cache_path=temp_cache_path)

    p_read = squid.stage.utils.get_cached_position(cache_path=temp_cache_path)

    assert p_read == p


def test_get_cached_position_returns_none_when_cache_file_is_missing(tmp_path):
    assert squid.stage.utils.get_cached_position(cache_path=str(tmp_path / "missing.txt")) is None


@pytest.mark.parametrize(
    "contents",
    [
        pytest.param(b"garbage", id="garbage"),
        pytest.param(b"1.0,2.0", id="too-few-fields"),
        pytest.param(b"1.0,2.0,3.0,4.0", id="too-many-fields"),
        pytest.param(b"1.0,abc,3.0", id="bad-field"),
        pytest.param(b"", id="empty"),
        pytest.param(b"nan,2.0,3.0", id="nan"),
        pytest.param(b"1.0,inf,3.0", id="inf"),
        pytest.param(b"\xff\xfe\x00\x80 not text", id="undecodable"),
    ],
)
def test_get_cached_position_treats_corrupted_file_as_no_cache(tmp_path, caplog, contents):
    """A corrupted cache must not raise (it would abort startup); it warns and behaves like a missing cache."""
    cache_path = tmp_path / "last_coords.txt"
    cache_path.write_bytes(contents)

    with caplog.at_level(logging.WARNING, logger="squid"):
        assert squid.stage.utils.get_cached_position(cache_path=str(cache_path)) is None

    assert any(
        r.levelno == logging.WARNING and str(cache_path) in r.getMessage() for r in caplog.records
    ), "expected a warning naming the corrupted cache file"


def test_get_cached_position_rejects_a_file_larger_than_the_read_cap(tmp_path, caplog):
    """A valid prefix padded past the read cap must be rejected, not parsed from the truncated prefix."""
    cache_path = tmp_path / "last_coords.txt"
    valid_prefix = "11.0,22.0,1.0"
    padding = " " * (squid.stage.utils._MAX_CACHE_BYTES - len(valid_prefix))
    cache_path.write_text(valid_prefix + padding + "garbage past the cap")

    with caplog.at_level(logging.WARNING, logger="squid"):
        assert squid.stage.utils.get_cached_position(cache_path=str(cache_path)) is None

    assert any(
        r.levelno == logging.WARNING and str(cache_path) in r.getMessage() for r in caplog.records
    ), "expected a warning naming the oversized cache file"


def test_get_cached_position_rejects_a_second_line(tmp_path):
    """Only a single coordinate triple is a valid cache; extra lines mean the file is not ours."""
    cache_path = tmp_path / "last_coords.txt"
    cache_path.write_text("11.0,22.0,1.0\n44.0,55.0,2.0\n")

    assert squid.stage.utils.get_cached_position(cache_path=str(cache_path)) is None


def test_get_cached_position_warning_shows_the_corrupt_contents(tmp_path, caplog):
    """The warning must quote what was in the file, so a bad cache can be diagnosed from a log alone."""
    cache_path = tmp_path / "last_coords.txt"
    cache_path.write_text("1.0,not-a-number,3.0")

    with caplog.at_level(logging.WARNING, logger="squid"):
        squid.stage.utils.get_cached_position(cache_path=str(cache_path))

    assert any("contents='1.0,not-a-number,3.0'" in r.getMessage() for r in caplog.records)


class _WriteFailsFile:
    """Wraps a real file object so opening (and truncating) succeeds but writing fails."""

    def __init__(self, wrapped):
        self._wrapped = wrapped

    def __enter__(self):
        self._wrapped.__enter__()
        return self

    def __exit__(self, *exc_info):
        return self._wrapped.__exit__(*exc_info)

    def write(self, _data):
        raise OSError("simulated disk full")


def test_cache_position_keeps_the_previous_position_when_the_write_fails(tmp_path, monkeypatch):
    """An interrupted write must not leave a truncated cache behind - that is what corrupts the file."""
    cache_path = str(tmp_path / "last_coords.txt")
    stage_config = squid.config.get_stage_config()
    already_cached = squid.abc.Pos(x_mm=11.0, y_mm=22.0, z_mm=1.0, theta_rad=None)
    squid.stage.utils.cache_position(pos=already_cached, stage_config=stage_config, cache_path=cache_path)

    real_open = builtins.open
    monkeypatch.setattr(builtins, "open", lambda *args, **kwargs: _WriteFailsFile(real_open(*args, **kwargs)))
    with pytest.raises(OSError):
        squid.stage.utils.cache_position(
            pos=squid.abc.Pos(x_mm=33.0, y_mm=44.0, z_mm=2.0, theta_rad=None),
            stage_config=stage_config,
            cache_path=cache_path,
        )
    monkeypatch.undo()

    assert squid.stage.utils.get_cached_position(cache_path=cache_path) == already_cached


# --- PI V-308 / C-414 focus stage --------------------------------------------


def test_simulated_c414_move_and_clamp():
    sim = squid.stage.pi._SimulatedC414(axis="1")
    sim.initialize(reference=True)
    assert sim.is_referenced() is True
    assert sim.move_to(1.0) == 1.0
    assert sim.get_position_mm() == 1.0
    assert sim.move_relative(-0.25) == 0.75
    assert sim.is_moving() is False
    sim.set_travel_limits(-1.0, 1.0)
    assert sim.move_to(5.0) == 1.0  # clamped to travel limit, like the controller


def _make_referenced_sim():
    sim = squid.stage.pi._SimulatedC414()
    sim.initialize(reference=True)
    return sim


def _sim_pi_stage():
    return squid.stage.pi.PIFocusStage(_make_referenced_sim(), stage_config=squid.config.get_stage_config())


def _sim_combined_stage():
    """Simulated Cephla XY + PI Z composite; returns (combined, xy, z)."""
    xy = squid.stage.cephla.CephlaStage(get_test_micro(), squid.config.get_stage_config())
    z = _sim_pi_stage()
    combined = squid.stage.pi.CombinedStage(xy_stage=xy, z_stage=z, stage_config=squid.config.get_stage_config())
    return combined, xy, z


def test_pi_focus_z_passthrough_no_sign():
    stage = _sim_pi_stage()
    stage.move_z_to(1.0)
    assert abs(stage.get_pos().z_mm - 1.0) < 1e-9  # native mm, NOT negated
    stage.move_z(-0.5)
    assert abs(stage.get_pos().z_mm - 0.5) < 1e-9


def test_pi_focus_zero_is_inert():
    stage = _sim_pi_stage()
    stage.move_z_to(1.0)
    stage.zero(False, False, True, False)
    assert abs(stage.get_pos().z_mm - 1.0) < 1e-9  # unchanged


def test_pi_focus_home_moves_home_without_resweep_when_referenced():
    sim = _make_referenced_sim()  # already referenced (ref_count == 1)
    stage = squid.stage.pi.PIFocusStage(sim, stage_config=squid.config.get_stage_config(), home_mm=0.0)
    stage.move_z_to(2.0)
    before = sim._ref_count
    stage.home(False, False, True, False, blocking=True)
    assert sim._ref_count == before  # no re-reference (no FRF re-sweep)
    assert abs(stage.get_pos().z_mm - 0.0) < 1e-9  # but DID move to the home position


def test_pi_focus_home_references_then_moves_when_unreferenced():
    sim = squid.stage.pi._SimulatedC414()  # not referenced
    stage = squid.stage.pi.PIFocusStage(sim, stage_config=squid.config.get_stage_config(), home_mm=0.0)
    assert sim.is_referenced() is False
    stage.home(False, False, True, False, blocking=True)
    assert sim.is_referenced() is True
    assert sim._ref_count == 1  # referenced exactly once
    assert abs(stage.get_pos().z_mm - 0.0) < 1e-9  # and at the home position


def test_pi_focus_set_limits_reaches_backend():
    stage = _sim_pi_stage()
    stage.set_limits(z_pos_mm=1.0, z_neg_mm=-1.0)
    stage.move_z_to(5.0)
    assert abs(stage.get_pos().z_mm - 1.0) < 1e-9


def test_pi_focus_xy_noop():
    stage = _sim_pi_stage()
    stage.move_x(1.0)
    stage.move_y(1.0)
    assert stage.get_pos().x_mm == 0.0 and stage.get_pos().y_mm == 0.0


def test_combined_stage_routes_axes():
    combined, _, _ = _sim_combined_stage()
    combined.move_z_to(1.0)
    assert abs(combined.get_pos().z_mm - 1.0) < 1e-9  # Z from V-308
    assert combined.get_pos().x_mm == 0.0  # X from cephla
    combined.zero(False, False, True, False)  # z-zero routes to PIFocusStage (inert)
    assert abs(combined.get_pos().z_mm - 1.0) < 1e-9


def test_pi_builder_simulated_returns_working_stage():
    stage = squid.stage.pi.connect_pi_focus_stage(
        simulated=True, reference=True, stage_config=squid.config.get_stage_config()
    )
    assert isinstance(stage, squid.stage.pi.PIFocusStage)
    stage.move_z_to(0.5)
    assert abs(stage.get_pos().z_mm - 0.5) < 1e-9


def test_resolve_port_by_sn(monkeypatch):
    import serial.tools.list_ports

    class _P:
        def __init__(self, dev, sn):
            self.device, self.serial_number = dev, sn

    monkeypatch.setattr(
        serial.tools.list_ports,
        "comports",
        lambda: [_P("/dev/ttyUSB0", "1UETR6I!"), _P("/dev/ttyUSB1", "other")],
    )
    assert squid.stage.pi._resolve_port_by_sn("1UETR6I!") == "/dev/ttyUSB0"


def test_resolve_port_by_sn_missing_mentions_bind_rule(monkeypatch):
    import serial.tools.list_ports

    monkeypatch.setattr(serial.tools.list_ports, "comports", lambda: [])
    with pytest.raises(RuntimeError, match="98-pi-c414-bind"):
        squid.stage.pi._resolve_port_by_sn("1UETR6I!")


def test_microscope_wraps_pi_focus_when_enabled(monkeypatch):
    import control._def
    import control.microscope

    monkeypatch.setattr(control._def, "USE_PI_FOCUS_STAGE", True, raising=False)
    monkeypatch.setattr(control._def, "SIMULATE_PI_FOCUS_STAGE", True, raising=False)
    scope = control.microscope.Microscope.build_from_global_config(simulated=True, skip_init=True)
    assert isinstance(scope.stage, squid.stage.pi.CombinedStage)
    # skip_init leaves the V-308 unreferenced (reference=...and not skip_init); reference before moving.
    scope.stage.home(x=False, y=False, z=True, theta=False)
    scope.stage.move_z_to(0.3)
    assert abs(scope.stage.get_pos().z_mm - 0.3) < 1e-9
    scope.close()  # exercises Microscope.close() -> CombinedStage.close() (V-308 handle)


def test_sim_move_requires_reference():
    sim = squid.stage.pi._SimulatedC414()  # not referenced
    with pytest.raises(RuntimeError, match="not referenced"):
        sim.move_to(1.0)


def test_pi_focus_close_closes_backend():
    sim = _make_referenced_sim()
    stage = squid.stage.pi.PIFocusStage(sim, stage_config=squid.config.get_stage_config())
    stage.close()
    assert sim._closed is True


def test_pi_focus_home_after_close_is_noop():
    # Guards the non-blocking-home use-after-close race: once closed, home must not touch the backend.
    sim = _make_referenced_sim()
    stage = squid.stage.pi.PIFocusStage(sim, stage_config=squid.config.get_stage_config(), home_mm=0.0)
    stage.move_z_to(1.0)
    stage.close()
    stage.home(False, False, True, False, blocking=True)  # must return cleanly, not drive the closed handle
    assert sim._closed is True


def test_combined_stage_inits_scanning_position_attr():
    combined, _, _ = _sim_combined_stage()
    # squid.stage.utils loading/scanning flow reads this; CephlaStage sets it, so CombinedStage must too.
    assert combined._scanning_position_z_mm is None


def test_combined_stage_delegates_usteps_and_close():
    combined, xy, z = _sim_combined_stage()
    # NavigationWidget.set_deltaX/Y/Z call these; must not AttributeError.
    assert combined.x_mm_to_usteps(1.0) == xy.x_mm_to_usteps(1.0)  # X/Y from the XY stage
    assert combined.y_mm_to_usteps(1.0) == xy.y_mm_to_usteps(1.0)
    # Z grid comes from the V-308 (continuous), not the coarse Cephla stepper grid.
    assert combined.z_mm_to_usteps(1.0) == z.z_mm_to_usteps(1.0)
    assert abs(combined.z_mm_to_usteps(1.0)) > abs(xy.z_mm_to_usteps(1.0))
    combined.close()  # closes the V-308 backend; Cephla XY close() is the AbstractStage no-op
    assert z._c414._closed is True


def test_pi_focus_retracts_z_before_xy_homing(monkeypatch):
    import control._def
    import control.microscope

    monkeypatch.setattr(control._def, "USE_PI_FOCUS_STAGE", True, raising=False)
    monkeypatch.setattr(control._def, "SIMULATE_PI_FOCUS_STAGE", True, raising=False)
    monkeypatch.setattr(control._def, "HOMING_ENABLED_Z", False, raising=False)
    monkeypatch.setattr(control._def, "HOMING_ENABLED_X", False, raising=False)  # isolate the Z retract
    monkeypatch.setattr(control._def, "HOMING_ENABLED_Y", False, raising=False)
    monkeypatch.setattr(control._def, "OBJECTIVE_RETRACTED_POS_MM", 0.0, raising=False)

    scope = control.microscope.Microscope.build_from_global_config(simulated=True, skip_init=True)
    scope.stage.home(x=False, y=False, z=True, theta=False)  # skip_init left it unreferenced; reference it
    scope.stage.move_z_to(2.0)
    scope.home_xyz()
    assert abs(scope.stage.get_pos().z_mm - 0.0) < 1e-6  # retracted to the objective-clear end
    scope.close()


def test_pi_focus_homing_references_and_retracts_z(monkeypatch):
    import control._def
    import control.microscope

    monkeypatch.setattr(control._def, "USE_PI_FOCUS_STAGE", True, raising=False)
    monkeypatch.setattr(control._def, "SIMULATE_PI_FOCUS_STAGE", True, raising=False)
    monkeypatch.setattr(control._def, "HOMING_ENABLED_Z", False, raising=False)
    monkeypatch.setattr(control._def, "HOMING_ENABLED_X", False, raising=False)
    monkeypatch.setattr(control._def, "HOMING_ENABLED_Y", False, raising=False)
    monkeypatch.setattr(control._def, "OBJECTIVE_RETRACTED_POS_MM", 0.0, raising=False)

    scope = control.microscope.Microscope.build_from_global_config(simulated=True, skip_init=True)
    assert scope.stage.is_referenced() is False  # skip_init -> not referenced
    scope.home_xyz()  # must reference Z and retract it before XY, even starting unreferenced
    assert scope.stage.is_referenced() is True
    assert abs(scope.stage.get_pos().z_mm - 0.0) < 1e-6
    scope.close()


def test_pi_focus_z_grid_is_10nm():
    stage = _sim_pi_stage()
    # The GUI Z step grid is 1 / z_mm_to_usteps(1.0); for the continuous V-308 it is the 10 nm
    # resolution, so um-scale Z-stack slices are effectively not snapped to a stepper grid.
    mm_per_ustep = 1.0 / stage.z_mm_to_usteps(1.0)
    assert abs(mm_per_ustep - 1e-5) < 1e-12


def test_combined_stage_zaxis_reports_v308_grid():
    combined, xy, z = _sim_combined_stage()
    # AutoFocus / multipoint snap Z steps via get_config().Z_AXIS; it must reflect the V-308's
    # 10 nm grid, not the coarse Cephla stepper grid (this is the path [5] that z_mm_to_usteps missed).
    grid = combined.get_config().Z_AXIS.convert_real_units_to_ustep(1.0)
    assert abs(grid) == abs(z.z_mm_to_usteps(1.0))
    assert abs(grid) != abs(xy.get_config().Z_AXIS.convert_real_units_to_ustep(1.0))


def test_resolve_port_by_sn_numeric(monkeypatch):
    # The config reader may coerce an all-digit serial to int; resolution must still match.
    import serial.tools.list_ports

    class _P:
        def __init__(self, dev, sn):
            self.device, self.serial_number = dev, sn

    monkeypatch.setattr(serial.tools.list_ports, "comports", lambda: [_P("/dev/ttyUSB0", "12345")])
    assert squid.stage.pi._resolve_port_by_sn(12345) == "/dev/ttyUSB0"


def test_connect_pi_focus_requires_port():
    # Hardware-free misconfiguration: raises before constructing C414FocusStage (no pipython needed).
    with pytest.raises(RuntimeError, match="PI_FOCUS_STAGE_SN or PI_FOCUS_SERIAL_PORT"):
        squid.stage.pi.connect_pi_focus_stage(simulated=False)


# --- PI V-308 upright / inverted-Z + range-limit reset ------------------------


def _referenced_sim_with_travel(lo=0.0, hi=7.0):
    sim = squid.stage.pi._SimulatedC414()
    sim.initialize(reference=True)
    sim.reset_range_limit(hi, lo)  # mirror the V-308's true travel
    return sim


def test_pi_focus_inverted_mapping():
    # Upright: squid_z = (native positive limit) - native. Z+ moves toward the sample.
    sim = _referenced_sim_with_travel(0.0, 7.0)
    stage = squid.stage.pi.PIFocusStage(sim, invert_z=True)
    assert stage._offset_mm == 7.0
    stage.move_z_to(1.0)  # squid 1.0 -> native 6.0
    assert abs(sim.get_position_mm() - 6.0) < 1e-9
    assert abs(stage.get_pos().z_mm - 1.0) < 1e-9
    before = sim.get_position_mm()
    stage.move_z(0.5)  # Z+ (toward sample) -> native decreases
    assert sim.get_position_mm() < before
    assert abs(stage.get_pos().z_mm - 1.5) < 1e-9


def test_pi_focus_inverted_home_retracts_to_positive_limit():
    sim = _referenced_sim_with_travel(0.0, 7.0)
    stage = squid.stage.pi.PIFocusStage(sim, invert_z=True, home_to_positive_limit=True)
    # software z [0.05, 6.0] -> native fence [1.0, 6.95]; home retracts to the fenced upper end.
    stage.set_limits(z_pos_mm=6.0, z_neg_mm=0.05)
    assert abs(sim._lo_mm - 1.0) < 1e-9 and abs(sim._hi_mm - 6.95) < 1e-9
    stage.move_z_to(3.0)  # somewhere toward the sample
    stage.home(False, False, True, False, blocking=True)
    assert abs(sim.get_position_mm() - 6.95) < 1e-9  # furthest from sample (native upper)
    assert abs(stage.get_pos().z_mm - 0.05) < 1e-9


def test_pi_focus_reset_range_limit_restores_travel():
    # On the C-414 qTMN/qTMX ARE the range limit; a prior fence shrinks them. reset_range_limit
    # widens them back (set_travel_limits could not, since it clamps to the shrunk range).
    sim = squid.stage.pi._SimulatedC414()
    sim.initialize(reference=True)
    sim.set_travel_limits(0.05, 5.95)
    assert sim.hardware_limits_mm() == (0.05, 5.95)
    sim.reset_range_limit(7.0, 0.0)
    assert sim.hardware_limits_mm() == (0.0, 7.0)


def test_connect_pi_focus_offset_stable_across_prior_fence():
    # Even if a prior session shrank the range, connect with z_travel_mm restores it so the
    # inversion offset is the true travel (not the drifted value).
    stage = squid.stage.pi.connect_pi_focus_stage(
        simulated=True, invert_z=True, home_to_positive_limit=True, z_travel_mm=7.0
    )
    assert stage._offset_mm == 7.0


def test_pi_focus_noninverted_unchanged():
    # Default (no invert / no positive-limit home) stays pure pass-through.
    sim = _referenced_sim_with_travel(0.0, 7.0)
    stage = squid.stage.pi.PIFocusStage(sim, home_mm=0.5)
    assert stage._offset_mm == 0.0
    stage.move_z_to(2.0)
    assert abs(stage.get_pos().z_mm - 2.0) < 1e-9
    stage.home(False, False, True, False, blocking=True)
    assert abs(stage.get_pos().z_mm - 0.5) < 1e-9  # home_mm pass-through


def test_c414_clamp_target_graceful_limit():
    # A jog past the range limit clamps (with a warning) instead of raising GCSError; needs pipython
    # only to construct the driver object (no hardware / no connection is used).
    pytest.importorskip("pipython")
    dev = squid.stage.pi.C414FocusStage(axis="1")
    dev._range_lo, dev._range_hi = 1.0, 6.95
    assert dev._clamp_target(3.0) == 3.0  # in range -> unchanged
    assert dev._clamp_target(10.0) == 6.95  # above hi -> clamped
    assert dev._clamp_target(-2.0) == 1.0  # below lo -> clamped
    dev._range_lo = dev._range_hi = None  # limits unknown -> pass through
    assert dev._clamp_target(999.0) == 999.0


def test_combined_stage_homes_z_before_xy(monkeypatch):
    # Z homes first and its leg blocks even for blocking=False (see CombinedStage.home).
    combined, xy, z = _sim_combined_stage()

    calls = []
    monkeypatch.setattr(xy, "home", lambda x, y, z, theta, blocking=True: calls.append(("xy", blocking)))
    monkeypatch.setattr(z, "home", lambda x, y, z, theta, blocking=True: calls.append(("z", blocking)))

    combined.home(x=True, y=True, z=True, theta=False, blocking=False)

    assert calls == [("z", True), ("xy", False)]


def test_calc_move_timeout_scales_with_axis_speed():
    """Regression: the divisor was min(0.1, speed) instead of max(0.1, speed), which pinned it at
    0.1 mm/s and made every timeout 38x (z) to 300x (x/y) longer than the intended 3x naive move time."""
    timeout = squid.stage.cephla.CephlaStage._calc_move_timeout

    assert timeout(60.0, 30.0) == pytest.approx(6.0)  # 3 * 60 mm / 30 mm/s
    assert timeout(-60.0, 30.0) == pytest.approx(6.0)  # direction does not matter
    assert timeout(0.5, 2.0) == 3.0  # short moves get the 3 s floor
    assert timeout(1.0, 0.0) == pytest.approx(30.0)  # zero speed falls back to 0.1 mm/s instead of dividing by zero
