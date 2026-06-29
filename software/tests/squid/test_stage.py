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


def test_pi_focus_home_references():
    stage = _sim_pi_stage()
    stage.move_z_to(2.0)
    stage.home(False, False, True, False, blocking=True)
    assert abs(stage.get_pos().z_mm) < 1e-9


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
    micro = get_test_micro()
    xy = squid.stage.cephla.CephlaStage(micro, squid.config.get_stage_config())
    z = _sim_pi_stage()
    combined = squid.stage.pi.CombinedStage(xy_stage=xy, z_stage=z, stage_config=squid.config.get_stage_config())
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
