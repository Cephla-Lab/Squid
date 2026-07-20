"""Tests for ObjectiveTurret4PosControllerSimulation (no hardware required)."""

from __future__ import annotations

import pytest

import control._def
import control.objective_turret_controller as otc
from control._def import OBJECTIVE_TURRET_POSITIONS, OBJECTIVE_RETRACTED_POS_MM
from control.objective_turret_controller import (
    ObjectiveTurret4PosController,
    ObjectiveTurret4PosControllerSimulation,
    CW_DISABLE,
    CW_ENABLE,
    REG_CONTROL_WORD,
    REG_CURRENT_POSITION,
    REG_MICROSTEP,
    REG_STATUS_WORD,
    REG_TARGET_POSITION,
)


class FakeStage:
    """Records move_z_to calls and reports a preset Z position."""

    def __init__(self, z_mm: float = 3.5):
        self._z_mm = z_mm
        self.z_moves: list[float] = []

    def move_z_to(self, abs_mm: float, blocking: bool = True):
        self.z_moves.append(abs_mm)
        self._z_mm = abs_mm

    def get_pos(self):
        class _Pos:
            pass

        p = _Pos()
        p.z_mm = self._z_mm
        return p


def _make_sim(stage=None):
    return ObjectiveTurret4PosControllerSimulation(
        serial_number="SIM-001",
        positions=OBJECTIVE_TURRET_POSITIONS,
        stage=stage,
    )


def test_init_opens_controller():
    sim = _make_sim()
    assert sim.is_open
    assert sim.current_objective is None
    sim.close()


def test_home_clears_current_objective():
    sim = _make_sim()
    sim.move_to_objective("10x")
    sim.home()
    assert sim.current_objective is None
    sim.close()


@pytest.mark.parametrize("name", list(OBJECTIVE_TURRET_POSITIONS))
def test_move_to_each_known_objective(name):
    sim = _make_sim()
    sim.move_to_objective(name)
    assert sim.current_objective == name
    sim.close()


def test_move_unknown_objective_raises_key_error():
    sim = _make_sim()
    with pytest.raises(KeyError):
        sim.move_to_objective("1000x")
    sim.close()


def test_clear_alarm_is_callable():
    sim = _make_sim()
    sim.clear_alarm()
    assert sim.is_open
    sim.close()


def test_enable_is_callable():
    sim = _make_sim()
    sim.enable()
    assert sim.is_open
    sim.close()


def test_operations_after_close_raise():
    sim = _make_sim()
    sim.close()
    with pytest.raises(RuntimeError):
        sim.home()
    with pytest.raises(RuntimeError):
        sim.move_to_objective("10x")
    with pytest.raises(RuntimeError):
        sim.clear_alarm()
    with pytest.raises(RuntimeError):
        sim.enable()


def test_close_is_idempotent():
    sim = _make_sim()
    sim.close()
    sim.close()
    assert not sim.is_open


def test_context_manager_closes_on_exit():
    with _make_sim() as sim:
        sim.move_to_objective("20x")
        assert sim.is_open
    assert not sim.is_open


def test_move_to_objective_retracts_and_restores_z(monkeypatch):
    monkeypatch.setattr(control._def, "HOMING_ENABLED_Z", True)
    stage = FakeStage(z_mm=3.5)
    sim = _make_sim(stage=stage)

    sim.move_to_objective("40x")

    # First switch: retract to OBJECTIVE_RETRACTED_POS_MM, then restore captured z.
    assert stage.z_moves == [OBJECTIVE_RETRACTED_POS_MM, 3.5]
    assert sim.current_objective == "40x"

    # Second call with same objective: no-op (early exit), no new z motion.
    stage.z_moves.clear()
    sim.move_to_objective("40x")
    assert stage.z_moves == []

    sim.close()


def test_move_to_objective_skips_z_retract_when_no_stage(monkeypatch):
    monkeypatch.setattr(control._def, "HOMING_ENABLED_Z", True)
    sim = _make_sim(stage=None)
    sim.move_to_objective("10x")  # must not raise even without a stage
    assert sim.current_objective == "10x"
    sim.close()


def test_move_to_objective_skips_z_retract_when_homing_z_disabled(monkeypatch):
    monkeypatch.setattr(control._def, "HOMING_ENABLED_Z", False)
    stage = FakeStage(z_mm=3.5)
    sim = _make_sim(stage=stage)
    sim.move_to_objective("10x")
    assert stage.z_moves == []  # retract is gated on HOMING_ENABLED_Z
    assert sim.current_objective == "10x"
    sim.close()


def test_move_between_aliased_objectives_skips_z_retract(monkeypatch):
    monkeypatch.setattr(control._def, "HOMING_ENABLED_Z", True)
    stage = FakeStage(z_mm=3.5)
    sim = ObjectiveTurret4PosControllerSimulation(
        serial_number="SIM-001",
        positions={"4x_A": 1, "4x_B": 1, "10x": 2},
        stage=stage,
    )

    sim.move_to_objective("4x_A")
    assert stage.z_moves == [OBJECTIVE_RETRACTED_POS_MM, 3.5]
    stage.z_moves.clear()

    # Switching to a different name that maps to the same physical
    # position updates the tracked objective but skips the Z dance.
    sim.move_to_objective("4x_B")
    assert stage.z_moves == []
    assert sim.current_objective == "4x_B"

    sim.close()


def test_move_to_objective_skips_restore_when_restore_z_false(monkeypatch):
    # At startup Z was just homed to 0 (below the working floor), so the turret
    # retracts and rotates but must NOT restore Z; the cached-Z restore handles it.
    monkeypatch.setattr(control._def, "HOMING_ENABLED_Z", True)
    stage = FakeStage(z_mm=3.5)
    sim = _make_sim(stage=stage)

    sim.move_to_objective("40x", restore_z=False)

    # Retract happened; the restore back to the captured Z did not.
    assert stage.z_moves == [OBJECTIVE_RETRACTED_POS_MM]
    assert sim.current_objective == "40x"
    sim.close()


class _FakeModbus:
    """Minimal ModbusRTUClient stand-in that records register writes.

    Reads return values that drive the controller's wait loops straight to a
    completed/idle state; writes are recorded so tests can assert the control-word
    sequence (in particular, that the motor ends de-energized).
    """

    def __init__(self):
        self.connected = False
        self.writes = []  # (address, value) in order
        self._position = 0

    def connect(self, port=None, baudrate=None):
        self.connected = True

    def disconnect(self):
        self.connected = False

    @property
    def is_connected(self):
        return self.connected

    def read_register(self, slave_id, address):
        # Microstep register must be 0..7; 4 -> 16 microsteps.
        return 4 if address == REG_MICROSTEP else 0

    def read_register_32bit(self, slave_id, address, signed=False):
        return 0

    def read_input_register(self, slave_id, address):
        # Status word: neither RUNNING nor FAULT -> wait loops see "idle".
        return 0

    def read_input_register_32bit(self, slave_id, address, signed=False):
        # Report the commanded target as the live position so the move-complete
        # tolerance check passes immediately.
        return self._position if address == REG_CURRENT_POSITION else 0

    def write_register(self, slave_id, address, value):
        self.writes.append((address, value))

    def write_register_32bit(self, slave_id, address, value, signed=False):
        self.writes.append((address, value))
        if address == REG_TARGET_POSITION:
            self._position = value

    def control_word_writes(self):
        return [value for (address, value) in self.writes if address == REG_CONTROL_WORD]

    def target_position_writes(self):
        return [value for (address, value) in self.writes if address == REG_TARGET_POSITION]


def _make_real_controller(monkeypatch, **controller_kwargs):
    fake = _FakeModbus()
    monkeypatch.setattr(otc, "_find_port", lambda serial_number: "FAKE_PORT")
    monkeypatch.setattr(otc, "ModbusRTUClient", lambda **kwargs: fake)
    controller = ObjectiveTurret4PosController(serial_number="SIM", stage=None, **controller_kwargs)
    return controller, fake


def test_init_leaves_motor_deenergized(monkeypatch):
    controller, fake = _make_real_controller(monkeypatch)
    assert fake.control_word_writes()[-1] == CW_DISABLE
    controller.close()


def test_move_to_objective_deenergizes_when_idle(monkeypatch):
    controller, fake = _make_real_controller(monkeypatch)
    fake.writes.clear()
    controller.move_to_objective("40x")
    # The motor energizes to rotate but is de-energized once the move completes.
    assert fake.control_word_writes()[-1] == CW_DISABLE
    assert CW_ENABLE in fake.control_word_writes()  # it did energize to move
    controller.close()


def test_home_deenergizes_when_idle(monkeypatch):
    controller, fake = _make_real_controller(monkeypatch)
    fake.writes.clear()
    controller.home()
    assert fake.control_word_writes()[-1] == CW_DISABLE
    controller.close()


def test_deenergize_is_best_effort(monkeypatch):
    # _deenergize() runs from finally blocks after a move/home, so a failed disable
    # write must not raise and mask the real timeout/fault that triggered the cleanup.
    controller, fake = _make_real_controller(monkeypatch)

    def failing_write(slave_id, address, value):
        if address == REG_CONTROL_WORD:
            raise IOError("modbus link down")
        fake.writes.append((address, value))

    monkeypatch.setattr(fake, "write_register", failing_write)
    controller._deenergize()  # must not raise despite the failing control-word write
    controller.close()


@pytest.mark.parametrize("offset", [0, 37, -30], ids=["default", "positive", "negative"])
def test_move_targets_apply_offset(monkeypatch, offset):
    # Every slot N targets (N-1)*pulses_per_position + offset. offset=0 proves default
    # behavior is unchanged; the negative case drives slot 1 to a negative absolute
    # target, exercising the signed 32-bit write and the tolerance check.
    controller, fake = _make_real_controller(monkeypatch, offset_pulses=offset)
    pp = controller.pulses_per_position
    for name, index in OBJECTIVE_TURRET_POSITIONS.items():
        fake.writes.clear()
        controller.move_to_objective(name)
        assert fake.target_position_writes()[-1] == (index - 1) * pp + offset
    controller.close()


def test_offset_falls_back_to_def_when_not_passed(monkeypatch):
    # With no explicit kwarg, the controller picks up the per-machine _def value.
    monkeypatch.setattr(control._def, "OBJECTIVE_TURRET_OFFSET_PULSES", 25)
    controller, fake = _make_real_controller(monkeypatch)
    pp = controller.pulses_per_position
    fake.writes.clear()
    controller.move_to_objective("40x")  # slot index 4
    assert fake.target_position_writes()[-1] == 3 * pp + 25
    controller.close()


@pytest.mark.parametrize("bad_offset", [37.5, "30", True])
def test_non_int_offset_raises(monkeypatch, bad_offset):
    # .ini parsing can yield a float/str/bool; a non-int offset must fail fast at init
    # rather than deep in the signed Modbus write.
    monkeypatch.setattr(otc, "_find_port", lambda serial_number: "FAKE_PORT")
    monkeypatch.setattr(otc, "ModbusRTUClient", lambda **kwargs: _FakeModbus())
    with pytest.raises(ValueError):
        ObjectiveTurret4PosController(serial_number="SIM", stage=None, offset_pulses=bad_offset)


def test_out_of_range_offset_raises(monkeypatch):
    # An offset beyond one slot (the 90-degree spacing) is a misconfiguration and must be
    # rejected. With the fake's microstep 4 -> 16 microsteps, pulses/position = 2200, so
    # 5000 is over one slot (but under a full rev) — it must still be rejected.
    monkeypatch.setattr(otc, "_find_port", lambda serial_number: "FAKE_PORT")
    monkeypatch.setattr(otc, "ModbusRTUClient", lambda **kwargs: _FakeModbus())
    with pytest.raises(ValueError):
        ObjectiveTurret4PosController(serial_number="SIM", stage=None, offset_pulses=5_000)


def test_sim_accepts_offset_kwarg():
    # The simulation twin must accept the same kwarg (built from the same turret_kwargs).
    sim = ObjectiveTurret4PosControllerSimulation(
        serial_number="SIM-001",
        positions=OBJECTIVE_TURRET_POSITIONS,
        offset_pulses=42,
    )
    assert sim.is_open
    sim.move_to_objective("20x")
    assert sim.current_objective == "20x"
    sim.close()


# --- per-slot calibration ---


def test_calibrated_slots_used_verbatim_others_fall_back(monkeypatch):
    # A calibrated slot targets its calibrated value (global offset NOT added);
    # uncalibrated slots keep the theoretical (slot-1)*pp + offset behavior.
    controller, fake = _make_real_controller(monkeypatch, offset_pulses=25, calibrated_pulses={2: 2210, 4: 6585})
    pp = controller.pulses_per_position
    expected = {"4x": 0 * pp + 25, "10x": 2210, "20x": 2 * pp + 25, "40x": 6585}
    for name, target in expected.items():
        fake.writes.clear()
        controller.move_to_objective(name)
        assert fake.target_position_writes()[-1] == target
    controller.close()


def test_calibration_accepts_string_keys_from_ini(monkeypatch):
    # .ini JSON parsing yields string keys; they must be normalized to int slots.
    controller, fake = _make_real_controller(monkeypatch, calibrated_pulses={"3": 4415})
    fake.writes.clear()
    controller.move_to_objective("20x")  # slot 3
    assert fake.target_position_writes()[-1] == 4415
    controller.close()


def test_calibration_falls_back_to_def_when_not_passed(monkeypatch):
    monkeypatch.setattr(control._def, "OBJECTIVE_TURRET_CALIBRATED_PULSES", {1: -12})
    controller, fake = _make_real_controller(monkeypatch)
    fake.writes.clear()
    controller.move_to_objective("4x")  # slot 1
    assert fake.target_position_writes()[-1] == -12
    controller.close()


@pytest.mark.parametrize(
    "bad_calibration",
    [{"x": 100}, {0: 100}, {5: 100}, {2: 100.5}, {2: True}, {2: "100"}],
    ids=["non-int-key", "slot-0", "slot-5", "float-value", "bool-value", "str-value"],
)
def test_invalid_calibration_raises(monkeypatch, bad_calibration):
    monkeypatch.setattr(otc, "_find_port", lambda serial_number: "FAKE_PORT")
    monkeypatch.setattr(otc, "ModbusRTUClient", lambda **kwargs: _FakeModbus())
    with pytest.raises(ValueError):
        ObjectiveTurret4PosController(serial_number="SIM", stage=None, calibrated_pulses=bad_calibration)


def test_calibration_deviating_more_than_one_slot_raises(monkeypatch):
    # Slot 2's theoretical target is 2200 (microstep 16); a calibrated value a full
    # slot away means it was measured at another microstep or against the wrong slot.
    monkeypatch.setattr(otc, "_find_port", lambda serial_number: "FAKE_PORT")
    monkeypatch.setattr(otc, "ModbusRTUClient", lambda **kwargs: _FakeModbus())
    with pytest.raises(ValueError):
        ObjectiveTurret4PosController(serial_number="SIM", stage=None, calibrated_pulses={2: 4500})


# --- backlash compensation ---


def test_backlash_moves_via_undershoot_then_target(monkeypatch):
    # comp > 0: pre-move to target-comp, then final approach from below.
    controller, fake = _make_real_controller(monkeypatch, backlash_deg=0.5)
    pp = controller.pulses_per_position
    comp = round(0.5 / 360 * 4 * pp)
    assert comp > 0
    fake.writes.clear()
    controller.move_to_objective("40x")  # slot 4 -> target 3*pp
    assert fake.target_position_writes() == [3 * pp - comp, 3 * pp]
    controller.close()


def test_zero_backlash_moves_directly(monkeypatch):
    controller, fake = _make_real_controller(monkeypatch, backlash_deg=0.0)
    pp = controller.pulses_per_position
    fake.writes.clear()
    controller.move_to_objective("10x")  # slot 2
    assert fake.target_position_writes() == [1 * pp]
    controller.close()


def test_backlash_applies_to_calibrated_slots_too(monkeypatch):
    controller, fake = _make_real_controller(monkeypatch, calibrated_pulses={2: 2210}, backlash_deg=1.0)
    pp = controller.pulses_per_position
    comp = round(1.0 / 360 * 4 * pp)
    fake.writes.clear()
    controller.move_to_objective("10x")  # slot 2, calibrated
    assert fake.target_position_writes() == [2210 - comp, 2210]
    controller.close()


def test_backlash_falls_back_to_def_when_not_passed(monkeypatch):
    monkeypatch.setattr(control._def, "OBJECTIVE_TURRET_BACKLASH_DEG", 0.5)
    controller, fake = _make_real_controller(monkeypatch)
    pp = controller.pulses_per_position
    comp = round(0.5 / 360 * 4 * pp)
    fake.writes.clear()
    controller.move_to_objective("4x")  # slot 1 -> target 0
    assert fake.target_position_writes() == [-comp, 0]
    controller.close()


@pytest.mark.parametrize("bad_deg", [-0.1, 1.5, True, "0.5"], ids=["negative", "too-large", "bool", "str"])
def test_invalid_backlash_raises(monkeypatch, bad_deg):
    monkeypatch.setattr(otc, "_find_port", lambda serial_number: "FAKE_PORT")
    monkeypatch.setattr(otc, "ModbusRTUClient", lambda **kwargs: _FakeModbus())
    with pytest.raises(ValueError):
        ObjectiveTurret4PosController(serial_number="SIM", stage=None, backlash_deg=bad_deg)


def test_sim_accepts_calibration_and_backlash_kwargs():
    # Constructor parity: microscope.py builds one turret_kwargs dict for both twins.
    sim = ObjectiveTurret4PosControllerSimulation(
        serial_number="SIM-001",
        positions=OBJECTIVE_TURRET_POSITIONS,
        offset_pulses=42,
        calibrated_pulses={2: 2210},
        backlash_deg=0.5,
    )
    assert sim.is_open
    sim.move_to_objective("20x")
    assert sim.current_objective == "20x"
    sim.close()
