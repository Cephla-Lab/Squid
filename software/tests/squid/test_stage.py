import pytest
import tempfile

import squid.stage.cephla
import squid.stage.prior
import squid.stage.utils
import squid.stage.pi
import squid.stage.asi
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


def _sim_combined_stage(z_stage=None):
    """Simulated Cephla XY + Z-only composite (PI Z by default); returns (combined, xy, z)."""
    xy = squid.stage.cephla.CephlaStage(get_test_micro(), squid.config.get_stage_config())
    z = z_stage if z_stage is not None else _sim_pi_stage()
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


# --- ASI LS50 Z stage ---------------------------------------------------------


def _sim_ls50():
    return squid.stage.asi._SimulatedLS50()


def test_simulated_ls50_move_and_clamp():
    sim = _sim_ls50()
    assert sim.get_position_mm() == 0.0  # power-on zero
    assert sim.move_to(1.0) == 1.0
    assert sim.move_relative(-0.25) == 0.75
    assert sim.is_moving() is False
    sim.set_travel_limits(-1.0, 1.0)
    assert sim.move_to(5.0) == 1.0  # clamped to the fence
    assert sim.move_to(-5.0) == -1.0


def test_simulated_ls50_unfenced_passthrough():
    # Native 0 is just the power-on position; until a fence is set the limits are unknown,
    # so targets pass through unclamped (mirrors the real backend's clamp cache-miss).
    sim = _sim_ls50()
    assert sim.hardware_limits_mm() == (None, None)
    assert sim.move_to(123.0) == 123.0


def test_ls50_zero_here_redefines_frame():
    # H Z=0 capability exists on the backend but is deliberately NOT wired to zero().
    sim = _sim_ls50()
    sim.move_to(1.0)
    sim.zero_here()
    assert sim.get_position_mm() == 0.0
    assert sim._zero_count == 1


class _FakeSerialConn:
    """Scripted pyserial-like object: records writes, pops queued replies."""

    def __init__(self, replies=(), default=b""):
        self.written = []
        self.replies = list(replies)
        self.default = default

    def write(self, data):
        self.written.append(data)

    def read_until(self, expected=b"\n"):
        return self.replies.pop(0) if self.replies else self.default

    def close(self):
        pass


def test_ms2000_command_framing_and_error_ack():
    conn = _FakeSerialConn(replies=[b":A 0\r\n", b":N-4\r\n"])
    ser = squid.stage.asi.MS2000Serial(conn)
    assert ser.command("W Z") == ":A 0"
    assert conn.written == [b"W Z\r"]
    with pytest.raises(RuntimeError, match="-4"):
        ser.command("M Z=99999999")


def _ls50_ctrl(conn):
    """Real LS50Controller + MS2000Serial over a scripted connection."""
    ctrl = squid.stage.asi.LS50Controller()
    ctrl._serial = squid.stage.asi.MS2000Serial(conn)
    return ctrl


def _sim_asi_stage(**kwargs):
    return squid.stage.asi.ASIZStage(_sim_ls50(), stage_config=squid.config.get_stage_config(), **kwargs)


def _sim_asi_combined():
    """Simulated Cephla XY + ASI LS50 Z via the reused pi.CombinedStage; returns (combined, xy, z)."""
    return _sim_combined_stage(z_stage=_sim_asi_stage(invert_z=True, home_mm=0.0))


def test_asi_z_passthrough_no_sign():
    stage = _sim_asi_stage(invert_z=False)
    stage.move_z_to(1.0)
    assert abs(stage.get_pos().z_mm - 1.0) < 1e-9
    stage.move_z(-0.5)
    assert abs(stage.get_pos().z_mm - 0.5) < 1e-9


def test_asi_z_invert_is_sign_flip():
    # Native + is away from the sample; inverted squid Z shows the negation, so squid + is
    # toward the sample and squid 0 == native 0 == the retracted end.
    sim = _sim_ls50()
    stage = squid.stage.asi.ASIZStage(sim, stage_config=squid.config.get_stage_config(), invert_z=True)
    stage.move_z_to(1.0)
    assert abs(sim.get_position_mm() - (-1.0)) < 1e-9  # native went negative (toward sample)
    assert abs(stage.get_pos().z_mm - 1.0) < 1e-9  # squid reports the positive value
    stage.move_z(0.5)  # squid + relative move -> native negative
    assert abs(sim.get_position_mm() - (-1.5)) < 1e-9
    stage.move_z_to(0.0)
    assert abs(sim.get_position_mm() - 0.0) < 1e-9  # squid 0 == native 0 (retract)


def test_asi_z_zero_is_inert():
    sim = _sim_ls50()
    stage = squid.stage.asi.ASIZStage(sim, stage_config=squid.config.get_stage_config())
    stage.move_z_to(1.0)
    stage.zero(False, False, True, False)
    assert abs(stage.get_pos().z_mm - 1.0) < 1e-9  # unchanged; zero_here() stays unwired
    assert sim._zero_count == 0


def test_asi_z_home_noop_without_target():
    # Defensive: with no home target configured, home(z) must not move.
    stage = _sim_asi_stage(home_mm=None)
    stage.move_z_to(1.0)
    stage.home(False, False, True, False, blocking=True)
    assert abs(stage.get_pos().z_mm - 1.0) < 1e-9


def test_asi_z_home_moves_to_target():
    # Default wiring: home = retract to squid 0 (native 0, the power-on/retracted end).
    sim = _sim_ls50()
    stage = squid.stage.asi.ASIZStage(sim, stage_config=squid.config.get_stage_config(), home_mm=0.0, invert_z=True)
    stage.move_z_to(2.0)
    stage.home(False, False, True, False, blocking=True)
    assert abs(stage.get_pos().z_mm - 0.0) < 1e-9
    assert abs(sim.get_position_mm() - 0.0) < 1e-9
    # A custom squid-frame target maps through the inversion.
    stage2 = _sim_asi_stage(home_mm=0.2, invert_z=True)
    stage2.home(False, False, True, False, blocking=True)
    assert abs(stage2.get_pos().z_mm - 0.2) < 1e-9


def test_asi_z_set_limits_reaches_backend():
    stage = _sim_asi_stage(invert_z=False)
    stage.set_limits(z_pos_mm=1.0, z_neg_mm=-1.0)
    stage.move_z_to(5.0)
    assert abs(stage.get_pos().z_mm - 1.0) < 1e-9


def test_asi_z_set_limits_inverted_orders_fence():
    # Software [0.05, 6.0] (squid frame) -> native fence [-6.0, -0.05] (min/max after flip).
    sim = _sim_ls50()
    stage = squid.stage.asi.ASIZStage(sim, stage_config=squid.config.get_stage_config(), invert_z=True)
    stage.set_limits(z_pos_mm=6.0, z_neg_mm=0.05)
    assert sim.hardware_limits_mm() == (-6.0, -0.05)
    stage.move_z_to(10.0)  # over-range squid target clamps at the fence
    assert abs(stage.get_pos().z_mm - 6.0) < 1e-9


def test_asi_z_xy_noop():
    stage = _sim_asi_stage()
    stage.move_x(1.0)
    stage.move_y(1.0)
    assert stage.get_pos().x_mm == 0.0 and stage.get_pos().y_mm == 0.0


def test_asi_z_grid_is_tenth_micron():
    stage = _sim_asi_stage()
    assert stage.z_mm_to_usteps(1.0) == 10000


def test_asi_combined_stage_routes_axes():
    combined, _, z = _sim_asi_combined()
    combined.move_z_to(1.0)
    assert abs(combined.get_pos().z_mm - 1.0) < 1e-9  # Z from the LS50
    assert combined.get_pos().x_mm == 0.0  # X from cephla
    combined.zero(False, False, True, False)  # z-zero routes to ASIZStage (inert)
    assert abs(combined.get_pos().z_mm - 1.0) < 1e-9


def test_asi_combined_zaxis_reports_ls50_grid():
    combined, xy, z = _sim_asi_combined()
    grid = combined.get_config().Z_AXIS.convert_real_units_to_ustep(1.0)
    assert abs(grid) == abs(z.z_mm_to_usteps(1.0))  # 0.1 um grid, not the Cephla stepper grid
    assert abs(grid) != abs(xy.get_config().Z_AXIS.convert_real_units_to_ustep(1.0))


def test_asi_z_close_closes_backend():
    sim = _sim_ls50()
    stage = squid.stage.asi.ASIZStage(sim, stage_config=squid.config.get_stage_config())
    stage.close()
    assert sim._closed is True


def test_asi_z_home_after_close_is_noop():
    # Once closed, home must not touch the torn-down backend (non-blocking-home race guard).
    sim = _sim_ls50()
    stage = squid.stage.asi.ASIZStage(sim, stage_config=squid.config.get_stage_config(), home_mm=0.0)
    stage.move_z_to(1.0)
    stage.close()
    stage.home(False, False, True, False, blocking=True)
    assert abs(sim.get_position_mm() - 1.0) < 1e-9  # unmoved after close


def test_ls50_controller_framing_and_units():
    # Real LS50Controller + MS2000Serial over a scripted connection: 0.1 um units on M/W.
    conn = _FakeSerialConn(replies=[b":A\r\n", b":A -12345\r\n"])
    ctrl = _ls50_ctrl(conn)
    assert ctrl.move_to(0.1234, wait=False) == 0.1234
    assert conn.written == [b"M Z=1234\r"]
    assert abs(ctrl.get_position_mm() - (-1.2345)) < 1e-9  # ':A -12345' -> -1.2345 mm


def test_ls50_wait_idle_polls_status():
    # wait=True polls '/' until N; a stage that never idles raises RuntimeError on timeout.
    conn = _FakeSerialConn(replies=[b":A\r\n", b"B\r\n", b"B\r\n", b"N\r\n", b":A 5000\r\n"])
    ctrl = _ls50_ctrl(conn)
    assert abs(ctrl.move_to(0.5, wait=True) - 0.5) < 1e-9
    assert conn.written[0] == b"M Z=5000\r"
    assert conn.written.count(b"/\r") == 3

    stuck = _FakeSerialConn(replies=[b":A\r\n"], default=b"B\r\n")
    ctrl2 = _ls50_ctrl(stuck)
    with pytest.raises(RuntimeError, match="idle"):
        ctrl2.move_to(0.5, wait=True, timeout=0.12)


def test_ls50_stop_tolerates_halt_ack():
    # HALT ('\\') acks with ':N-21' on the MS-2000; stop() must not raise on it.
    conn = _FakeSerialConn(replies=[b":N-21\r\n"])
    ctrl = _ls50_ctrl(conn)
    ctrl.stop()
    assert conn.written == [b"\\\r"]


def test_asi_builder_simulated_returns_working_stage():
    stage = squid.stage.asi.connect_asi_z_stage(
        simulated=True, invert_z=True, stage_config=squid.config.get_stage_config()
    )
    assert isinstance(stage, squid.stage.asi.ASIZStage)
    stage.move_z_to(0.5)
    assert abs(stage.get_pos().z_mm - 0.5) < 1e-9


def test_asi_builder_default_causes_no_motion():
    # Bring-up must not move: no homing/reference/zero happens in the factory by default.
    stage = squid.stage.asi.connect_asi_z_stage(simulated=True, stage_config=squid.config.get_stage_config())
    assert abs(stage.get_pos().z_mm - 0.0) < 1e-9  # still at power-on zero


def test_asi_builder_home_on_startup_opt_in():
    stage = squid.stage.asi.connect_asi_z_stage(
        simulated=True, home_mm=0.5, home_on_startup=True, stage_config=squid.config.get_stage_config()
    )
    assert abs(stage.get_pos().z_mm - 0.5) < 1e-9  # retracted to the home target at bring-up
    # home_on_startup without a target: warn + no motion, no exception.
    stage2 = squid.stage.asi.connect_asi_z_stage(
        simulated=True, home_mm=None, home_on_startup=True, stage_config=squid.config.get_stage_config()
    )
    assert abs(stage2.get_pos().z_mm - 0.0) < 1e-9


def test_asi_builder_travel_fence():
    stage = squid.stage.asi.connect_asi_z_stage(
        simulated=True, z_travel_mm=50.0, stage_config=squid.config.get_stage_config()
    )
    assert stage._backend.hardware_limits_mm() == (-50.0, 50.0)
    stage.move_z_to(200.0)
    assert abs(stage.get_pos().z_mm - 50.0) < 1e-9


def test_connect_asi_requires_port():
    with pytest.raises(RuntimeError, match="ASI_Z_STAGE_SN or ASI_Z_SERIAL_PORT"):
        squid.stage.asi.connect_asi_z_stage(simulated=False)


def test_resolve_serial_port_by_sn_shared(monkeypatch):
    import serial.tools.list_ports

    class _P:
        def __init__(self, dev, sn):
            self.device, self.serial_number = dev, sn

    monkeypatch.setattr(
        serial.tools.list_ports, "comports", lambda: [_P("/dev/ttyUSB2", 12345), _P("/dev/ttyUSB3", "abc")]
    )
    # String-normalized compare: an int-coerced config serial still matches.
    assert squid.stage.utils.resolve_serial_port_by_sn("12345") == "/dev/ttyUSB2"
    assert squid.stage.utils.resolve_serial_port_by_sn(12345) == "/dev/ttyUSB2"
    monkeypatch.setattr(serial.tools.list_ports, "comports", lambda: [])
    with pytest.raises(RuntimeError, match="check the LS50 controller"):
        squid.stage.utils.resolve_serial_port_by_sn("12345", missing_hint="check the LS50 controller")


def test_uses_external_z_stage_predicate(monkeypatch):
    import control._def

    monkeypatch.setattr(control._def, "USE_PI_FOCUS_STAGE", False, raising=False)
    monkeypatch.setattr(control._def, "USE_ASI_Z_STAGE", False, raising=False)
    assert control._def.uses_external_z_stage() is False
    # Must read the globals at CALL time (the machine-ini loader overrides them after
    # definition, and tests monkeypatch them), not capture them at definition time.
    monkeypatch.setattr(control._def, "USE_ASI_Z_STAGE", True, raising=False)
    assert control._def.uses_external_z_stage() is True
    monkeypatch.setattr(control._def, "USE_ASI_Z_STAGE", False, raising=False)
    monkeypatch.setattr(control._def, "USE_PI_FOCUS_STAGE", True, raising=False)
    assert control._def.uses_external_z_stage() is True


def test_microscope_wraps_asi_z_when_enabled(monkeypatch):
    import control._def
    import control.microscope

    monkeypatch.setattr(control._def, "USE_ASI_Z_STAGE", True, raising=False)
    monkeypatch.setattr(control._def, "SIMULATE_ASI_Z_STAGE", True, raising=False)
    scope = control.microscope.Microscope.build_from_global_config(simulated=True, skip_init=True)
    assert isinstance(scope.stage, squid.stage.pi.CombinedStage)
    scope.stage.move_z_to(0.3)
    assert abs(scope.stage.get_pos().z_mm - 0.3) < 1e-9
    scope.close()  # exercises Microscope.close() -> CombinedStage.close() -> ASIZStage.close()


def test_microscope_rejects_pi_and_asi_together(monkeypatch):
    import control._def
    import control.microscope

    monkeypatch.setattr(control._def, "USE_PI_FOCUS_STAGE", True, raising=False)
    monkeypatch.setattr(control._def, "SIMULATE_PI_FOCUS_STAGE", True, raising=False)
    monkeypatch.setattr(control._def, "USE_ASI_Z_STAGE", True, raising=False)
    monkeypatch.setattr(control._def, "SIMULATE_ASI_Z_STAGE", True, raising=False)
    with pytest.raises(ValueError, match="mutually exclusive"):
        control.microscope.Microscope.build_from_global_config(simulated=True, skip_init=True)


def test_asi_home_xyz_retracts_z_before_xy(monkeypatch):
    import control._def
    import control.microscope

    monkeypatch.setattr(control._def, "USE_ASI_Z_STAGE", True, raising=False)
    monkeypatch.setattr(control._def, "SIMULATE_ASI_Z_STAGE", True, raising=False)
    monkeypatch.setattr(control._def, "HOMING_ENABLED_Z", True, raising=False)
    monkeypatch.setattr(control._def, "HOMING_ENABLED_X", False, raising=False)
    monkeypatch.setattr(control._def, "HOMING_ENABLED_Y", False, raising=False)

    scope = control.microscope.Microscope.build_from_global_config(simulated=True, skip_init=True)
    assert isinstance(scope.stage, squid.stage.pi.CombinedStage)  # ASI Z wrapped, not stepper Z
    scope.stage.move_z_to(2.0)
    scope.home_xyz()
    # The external-Z branch retracts to the home target (squid 0 = native 0, the retracted
    # end) instead of running the stepper Z-homing path.
    assert abs(scope.stage.get_pos().z_mm - 0.0) < 1e-6
    scope.close()


def test_ls50_initialize_failure_mentions_baud():
    # A dead-air first query (wrong baud/port, unpowered controller) must fail with
    # actionable bring-up guidance, not a bare parse error.
    ctrl = _ls50_ctrl(_FakeSerialConn(replies=[b""]))
    with pytest.raises(RuntimeError, match="baud"):
        ctrl.initialize()


def test_ls50_axis_letter_configurable():
    # Single-axis MS-2000 builds may label their lone axis X (or other); every command
    # must use the configured letter.
    conn = _FakeSerialConn(replies=[b":A\r\n", b":A -100\r\n", b"N\r\n"])
    ctrl = squid.stage.asi.LS50Controller(axis="X")
    ctrl._serial = squid.stage.asi.MS2000Serial(conn)
    ctrl.move_to(0.5, wait=False)
    assert conn.written == [b"M X=5000\r"]
    assert abs(ctrl.get_position_mm() - (-0.01)) < 1e-9  # W X parsed
    assert ctrl.is_moving() is False
    assert conn.written[1:] == [b"W X\r", b"/\r"]
