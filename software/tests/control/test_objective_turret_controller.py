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
    CW_STARTUP,
    CW_RUN_ABSOLUTE,
    DI1_FUNCTION_ORIGIN_SWITCH,
    EXPECTED_CURRENT_OVERLOAD,
    EXPECTED_CURRENT_RUN,
    EXPECTED_MAX_SPEED,
    EXPECTED_MIN_SPEED,
    HOMING_FINE_ACCEL,
    HOMING_SWEEP_SPEED,
    MICROSTEP_REG_VALUE,
    MODE_SPEED,
    REG_ACCEL,
    REG_CONTROL_WORD,
    REG_CURRENT_OVERLOAD,
    REG_CURRENT_POSITION,
    REG_CURRENT_RUN,
    REG_DIRECTION,
    REG_DI_FUNCTION,
    REG_MAX_SPEED,
    REG_MICROSTEP,
    REG_MIN_SPEED,
    REG_RUN_MODE,
    REG_SAVE_PARAMS,
    REG_SET_ZERO,
    REG_TARGET_POSITION,
    REG_TARGET_SPEED,
    SAVE_PARAMS_MAGIC,
    SET_ZERO_MAGIC,
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
        self.microstep_raw = MICROSTEP_REG_VALUE  # register value 4 -> 16 microsteps
        # DI1 levels consumed one per status-snapshot read; the last value repeats.
        self.di_script = []
        self._di = 0

    def connect(self, port=None, baudrate=None):
        self.connected = True

    def disconnect(self):
        self.connected = False

    @property
    def is_connected(self):
        return self.connected

    def read_register(self, slave_id, address):
        return self.microstep_raw if address == REG_MICROSTEP else 0

    def read_register_32bit(self, slave_id, address, signed=False):
        return 0

    def read_input_register(self, slave_id, address):
        # Status word: neither RUNNING nor FAULT -> wait loops see "idle".
        return 0

    def read_input_register_32bit(self, slave_id, address, signed=False):
        # Report the commanded target as the live position so the move-complete
        # tolerance check passes immediately.
        return self._position if address == REG_CURRENT_POSITION else 0

    def read_input_registers(self, slave_id, address, count):
        # Status snapshot for software homing: everything idle/zero except the DI
        # level (offset 1) driven by di_script, and the position (offsets 10..11).
        if self.di_script:
            self._di = self.di_script.pop(0)
        vals = [0] * count
        vals[1] = self._di
        pos = self._position & 0xFFFFFFFF
        vals[10] = (pos >> 16) & 0xFFFF
        vals[11] = pos & 0xFFFF
        return vals

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


def _fast_homing(monkeypatch):
    """Zero out the real-time settle/poll sleeps so homing tests run instantly."""
    monkeypatch.setattr(otc, "HOMING_SETTLE_MARGIN_S", 0.0)
    monkeypatch.setattr(otc, "HOMING_STOP_SETTLE_S", 0.0)
    monkeypatch.setattr(otc, "HOMING_POLL_S", 0.0)


def test_home_zeroes_at_edge_and_clamps(monkeypatch):
    controller, fake = _make_real_controller(monkeypatch)
    _fast_homing(monkeypatch)
    fake.writes.clear()
    # Snapshot sequence: already in the window (1) -> backoff sees released after one
    # jog (1, 0) -> first fine jog lands on the trigger edge (1).
    fake.di_script = [1, 1, 0, 1]
    controller.home()
    # SET_ZERO twice: once at start (travel bound) and once at the trigger edge.
    assert [v for (a, v) in fake.writes if a == REG_SET_ZERO] == [SET_ZERO_MAGIC, SET_ZERO_MAGIC]
    # Ends clamped at home (position-mode 0x06/0x07/0x0F, no trigger), not de-energized.
    assert fake.control_word_writes()[-3:] == [CW_STARTUP, CW_ENABLE, CW_RUN_ABSOLUTE]
    assert controller.current_objective is None
    controller.close()


def test_home_sweeps_in_velocity_mode_when_off_sensor(monkeypatch):
    controller, fake = _make_real_controller(monkeypatch)
    _fast_homing(monkeypatch)
    fake.writes.clear()
    # Off the window (0) -> sweep polls miss then hit (0, 1) -> backoff already
    # released (0) -> fine jog hits the edge (1).
    fake.di_script = [0, 0, 1, 0, 1]
    controller.home()
    assert (REG_RUN_MODE, MODE_SPEED) in fake.writes
    assert (REG_DIRECTION, 0) in fake.writes  # sweep runs negative, toward the sensor
    assert (REG_TARGET_SPEED, HOMING_SWEEP_SPEED) in fake.writes
    controller.close()


def test_home_lowers_accel_for_fine_search_and_restores(monkeypatch):
    # The fine search runs at HOMING_FINE_ACCEL to soften the microstep approach to
    # the trigger edge; the original acceleration (fake reads 0) is restored after.
    controller, fake = _make_real_controller(monkeypatch)
    _fast_homing(monkeypatch)
    fake.writes.clear()
    fake.di_script = [1, 1, 0, 1]
    controller.home()
    accel_writes = [v for (a, v) in fake.writes if a == REG_ACCEL]
    assert accel_writes == [HOMING_FINE_ACCEL, 0]
    controller.close()


def test_home_timeout_leaves_motor_deenergized(monkeypatch):
    controller, fake = _make_real_controller(monkeypatch)
    _fast_homing(monkeypatch)
    fake.di_script = [0]  # sensor never triggers; fake position never exceeds travel
    with pytest.raises(TimeoutError):
        controller.home(timeout_s=0.2)
    # Failure cleanup: stopped and de-energized, NOT left clamped.
    assert fake.control_word_writes()[-1] == CW_DISABLE
    controller.close()


def test_init_calibrates_factory_params(monkeypatch):
    # Fake reads return 0 for every parameter, so init must write the full factory
    # set (SingleMotor 2026-07-24/25 acceptance values) and persist it.
    controller, fake = _make_real_controller(monkeypatch)
    writes = fake.writes
    # min_speed must be written before max_speed: the firmware rejects a max-speed
    # write below the current min speed.
    assert writes.index((REG_MIN_SPEED, EXPECTED_MIN_SPEED)) < writes.index((REG_MAX_SPEED, EXPECTED_MAX_SPEED))
    assert (REG_CURRENT_RUN, EXPECTED_CURRENT_RUN) in writes
    assert (REG_CURRENT_OVERLOAD, EXPECTED_CURRENT_OVERLOAD) in writes
    # DI1 must be "origin switch" — a limit-mapped homing sensor faults FF0E.
    assert (REG_DI_FUNCTION, DI1_FUNCTION_ORIGIN_SWITCH) in writes
    # Direction is RAM-only: written after the EEPROM save so it is not persisted.
    assert writes.index((REG_SAVE_PARAMS, SAVE_PARAMS_MAGIC)) < writes.index((REG_DIRECTION, 1))
    controller.close()


def test_init_microstep_mismatch_writes_factory_value_and_raises(monkeypatch):
    # A wrong microstep invalidates calibrated pulses and the homing math, and the
    # register only takes effect after a power cycle: write it, save, fail fast.
    fake = _FakeModbus()
    fake.microstep_raw = 7
    monkeypatch.setattr(otc, "_find_port", lambda serial_number: "FAKE_PORT")
    monkeypatch.setattr(otc, "ModbusRTUClient", lambda **kwargs: fake)
    with pytest.raises(RuntimeError, match="[Pp]ower-cycle"):
        ObjectiveTurret4PosController(serial_number="SIM", stage=None)
    assert (REG_MICROSTEP, MICROSTEP_REG_VALUE) in fake.writes
    assert (REG_SAVE_PARAMS, SAVE_PARAMS_MAGIC) in fake.writes


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


# --- direction inversion (opposite-phase motor models) ---


def test_inverted_move_writes_negated_target(monkeypatch):
    # Slot targets stay logical; only the register write is negated. The move still
    # completes because the position readback is flipped back symmetrically.
    # Explicit offset/calibration keep the theoretical targets independent of any
    # machine .ini values loaded into _def.
    controller, fake = _make_real_controller(
        monkeypatch, direction_inverted=True, offset_pulses=0, calibrated_pulses={}
    )
    pp = controller.pulses_per_position
    fake.writes.clear()
    controller.move_to_objective("20x")  # slot 3 -> logical target 2*pp
    assert fake.target_position_writes() == [-2 * pp]
    controller.close()


def test_inverted_backlash_order_preserved_logically(monkeypatch):
    # Undershoot-then-target stays expressed in logical coordinates; both writes
    # come out negated but the logical approach-from-below order is unchanged.
    controller, fake = _make_real_controller(
        monkeypatch, backlash_deg=0.5, direction_inverted=True, offset_pulses=0, calibrated_pulses={}
    )
    pp = controller.pulses_per_position
    comp = round(0.5 / 360 * 4 * pp)
    fake.writes.clear()
    controller.move_to_objective("10x")  # slot 2 -> logical target 1*pp
    assert fake.target_position_writes() == [-(pp - comp), -pp]
    controller.close()


def test_inverted_position_readback_returns_logical(monkeypatch):
    controller, fake = _make_real_controller(monkeypatch, direction_inverted=True)
    fake._position = -1234  # physical counter
    assert controller.current_position_pulses == 1234
    controller.close()


def test_inverted_home_flips_sweep_and_fine_jog_direction_bits(monkeypatch):
    controller, fake = _make_real_controller(monkeypatch, direction_inverted=True)
    _fast_homing(monkeypatch)
    fake.writes.clear()
    # Off the window (0) -> sweep polls miss then hit (0, 1) -> backoff already
    # released (0) -> fine jog hits the edge (1).
    fake.di_script = [0, 0, 1, 0, 1]
    controller.home()
    assert (REG_RUN_MODE, MODE_SPEED) in fake.writes
    # Sweep is logical-negative -> physical bit 1; fine jog -2 -> physical bit 1.
    assert [v for (a, v) in fake.writes if a == REG_DIRECTION] == [1, 1]
    controller.close()


def test_inverted_backoff_jog_flips_direction_keeps_magnitude(monkeypatch):
    controller, fake = _make_real_controller(monkeypatch, direction_inverted=True)
    _fast_homing(monkeypatch)
    fake.writes.clear()
    # In the window (1) -> one backoff jog releases (1, 0) -> fine jog hits (1).
    fake.di_script = [1, 1, 0, 1]
    controller.home()
    # Backoff +60 -> physical bit 0; fine -2 -> physical bit 1. The relative-move
    # register still only ever receives the positive magnitude.
    assert [v for (a, v) in fake.writes if a == REG_DIRECTION] == [0, 1]
    assert (otc.REG_TARGET_POSITION, otc.HOMING_BACKOFF_STEP) in fake.writes
    controller.close()


def test_inverted_init_expects_direction_zero(monkeypatch):
    # The fake reads 0 from the direction register; inverted expects 0, so init must
    # NOT issue the non-inverted corrective write of 1.
    controller, fake = _make_real_controller(monkeypatch, direction_inverted=True)
    assert (REG_DIRECTION, 1) not in fake.writes
    controller.close()


def test_direction_inverted_falls_back_to_def_when_not_passed(monkeypatch):
    monkeypatch.setattr(control._def, "OBJECTIVE_TURRET_DIRECTION_INVERTED", True)
    controller, fake = _make_real_controller(monkeypatch, offset_pulses=0, calibrated_pulses={})
    fake.writes.clear()
    controller.move_to_objective("40x")  # slot 4 -> logical target 3*pp
    assert fake.target_position_writes() == [-3 * controller.pulses_per_position]
    controller.close()


@pytest.mark.parametrize("bad_inverted", [1, "true", 0.0], ids=["int", "str", "float"])
def test_non_bool_direction_inverted_raises(monkeypatch, bad_inverted):
    # .ini parsing can yield an int/str; only a real boolean is accepted.
    monkeypatch.setattr(otc, "_find_port", lambda serial_number: "FAKE_PORT")
    monkeypatch.setattr(otc, "ModbusRTUClient", lambda **kwargs: _FakeModbus())
    with pytest.raises(ValueError):
        ObjectiveTurret4PosController(serial_number="SIM", stage=None, direction_inverted=bad_inverted)


def test_default_not_inverted_keeps_current_behavior(monkeypatch):
    # Regression guard: with the default (False), targets, direction writes and
    # position readback are exactly the pre-inversion behavior.
    controller, fake = _make_real_controller(monkeypatch, offset_pulses=0, calibrated_pulses={})
    assert (REG_DIRECTION, 1) in fake.writes  # init still calibrates direction to 1
    pp = controller.pulses_per_position
    fake.writes.clear()
    controller.move_to_objective("20x")
    assert fake.target_position_writes() == [2 * pp]
    assert controller.current_position_pulses == fake._position
    controller.close()


def test_sim_accepts_direction_inverted_kwarg():
    sim = ObjectiveTurret4PosControllerSimulation(
        serial_number="SIM-001",
        positions=OBJECTIVE_TURRET_POSITIONS,
        direction_inverted=True,
    )
    assert sim.is_open
    sim.move_to_objective("10x")
    assert sim.current_objective == "10x"
    sim.close()


# --- DI polarity inversion (opposite-logic origin switches) ---
#
# With di_invert the raw DI level flips meaning: 1 = released, 0 = inside the
# sensor window (the exact inverse of the default logic). The scripts below are
# raw levels; the flipped verdicts drive the same state machine as the
# non-inverted homing tests.


def test_di_invert_home_from_inside_window_uses_inverted_levels(monkeypatch):
    # Raw [0, 0, 1, 0] -> verdicts [1, 1, 0, 1]: inside -> backoff releases after
    # one jog -> fine jog hits the edge. Same flow as the default-polarity case:
    # no velocity sweep, SET_ZERO at start and at the trigger edge, clamped at home.
    controller, fake = _make_real_controller(monkeypatch, di_invert=True, direction_inverted=False)
    _fast_homing(monkeypatch)
    fake.writes.clear()
    fake.di_script = [0, 0, 1, 0]
    controller.home()
    assert (REG_RUN_MODE, MODE_SPEED) not in fake.writes
    assert [v for (a, v) in fake.writes if a == REG_SET_ZERO] == [SET_ZERO_MAGIC, SET_ZERO_MAGIC]
    assert fake.control_word_writes()[-3:] == [CW_STARTUP, CW_ENABLE, CW_RUN_ABSOLUTE]
    controller.close()


def test_di_invert_home_sweeps_when_released_level_high(monkeypatch):
    # Raw level 1 = released -> the sweep starts; the sweep direction bit stays the
    # same (logical negative, toward the sensor) — only the trigger verdict flips.
    controller, fake = _make_real_controller(monkeypatch, di_invert=True, direction_inverted=False)
    _fast_homing(monkeypatch)
    fake.writes.clear()
    # released -> sweep polls miss then trigger (0) -> backoff released (1) ->
    # fine jog hits the edge (0).
    fake.di_script = [1, 1, 0, 1, 0]
    controller.home()
    assert (REG_RUN_MODE, MODE_SPEED) in fake.writes
    assert (REG_DIRECTION, 0) in fake.writes
    assert (REG_TARGET_SPEED, HOMING_SWEEP_SPEED) in fake.writes
    controller.close()


def test_di_invert_backoff_jog_direction_unchanged(monkeypatch):
    # Backoff still jogs positive (away from the sensor); the inversion applies to
    # the trigger verdict only, never to the jog direction.
    controller, fake = _make_real_controller(monkeypatch, di_invert=True, direction_inverted=False)
    _fast_homing(monkeypatch)
    fake.writes.clear()
    fake.di_script = [0, 0, 1, 0]
    controller.home()
    assert (otc.REG_TARGET_POSITION, otc.HOMING_BACKOFF_STEP) in fake.writes
    controller.close()


def test_di_invert_falls_back_to_def_when_not_passed(monkeypatch):
    monkeypatch.setattr(control._def, "OBJECTIVE_TURRET_DI_INVERT", True)
    controller, fake = _make_real_controller(monkeypatch, direction_inverted=False)
    _fast_homing(monkeypatch)
    fake.writes.clear()
    fake.di_script = [0, 0, 1, 0]  # raw 0 = inside the window under inverted logic
    controller.home()
    assert (REG_RUN_MODE, MODE_SPEED) not in fake.writes  # inside-window path taken
    controller.close()


@pytest.mark.parametrize("bad_invert", [1, "true", 0.0], ids=["int", "str", "float"])
def test_non_bool_di_invert_raises(monkeypatch, bad_invert):
    # .ini parsing can yield an int/str; only a real boolean is accepted.
    monkeypatch.setattr(otc, "_find_port", lambda serial_number: "FAKE_PORT")
    monkeypatch.setattr(otc, "ModbusRTUClient", lambda **kwargs: _FakeModbus())
    with pytest.raises(ValueError):
        ObjectiveTurret4PosController(
            serial_number="SIM", stage=None, direction_inverted=False, di_invert=bad_invert
        )


def test_sim_accepts_di_invert_kwarg():
    sim = ObjectiveTurret4PosControllerSimulation(
        serial_number="SIM-001",
        positions=OBJECTIVE_TURRET_POSITIONS,
        di_invert=True,
    )
    assert sim.is_open
    sim.move_to_objective("10x")
    assert sim.current_objective == "10x"
    sim.close()
