"""Tests for ObjectiveTurret4PosControllerSimulation (no hardware required)."""

from __future__ import annotations

import pytest

import control._def
import control.objective_turret_controller as otc
from control.modbus_rtu import ModbusError
from control._def import OBJECTIVE_TURRET_POSITIONS, OBJECTIVE_RETRACTED_POS_MM
from control.objective_turret_controller import (
    ObjectiveTurret4PosController,
    ObjectiveTurret4PosControllerSimulation,
    CW_DISABLE,
    CW_ENABLE,
    CW_STARTUP,
    CW_RUN_ABSOLUTE,
    CW_TRIGGER_ABSOLUTE,
    DI1_FUNCTION_ORIGIN_SWITCH,
    EXPECTED_CURRENT_OVERLOAD,
    EXPECTED_CURRENT_RUN,
    EXPECTED_MAX_SPEED,
    EXPECTED_MIN_SPEED,
    HOMING_ACCEL,
    HOMING_METHOD_SEARCH_NEGATIVE,
    HOMING_METHOD_SEARCH_POSITIVE,
    HOMING_SEARCH_SPEED,
    HOMING_ZERO_SPEED,
    MICROSTEP_REG_VALUE,
    MODE_HOMING,
    MODE_POSITION,
    REG_ACCEL,
    REG_CONTROL_WORD,
    REG_CURRENT_OVERLOAD,
    REG_CURRENT_POSITION,
    REG_CURRENT_RUN,
    REG_DI_FUNCTION,
    REG_DI_POLARITY,
    REG_DIRECTION,
    REG_HOMING_ACCEL,
    REG_HOMING_METHOD,
    REG_HOMING_SEARCH_SPEED,
    REG_HOMING_ZERO_SPEED,
    REG_MAX_SPEED,
    REG_MICROSTEP,
    REG_MIN_SPEED,
    REG_RUN_MODE,
    REG_SAVE_PARAMS,
    REG_SET_ZERO,
    REG_TARGET_POSITION,
    REG_ZERO_RETURN,
    SAVE_PARAMS_MAGIC,
    SET_ZERO_MAGIC,
    STATUS_BIT_FAULT,
    STATUS_BIT_RUNNING,
    ZERO_RETURN_ENABLED,
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
        # Scripted status words consumed one per status-snapshot read; the last value repeats.
        self.status_script = []
        self._status = None  # None until a status_script value has been consumed
        # Unscripted status behaves like the drive: RUNNING for one read after a move trigger.
        self._running_reads = 0
        self.alarm = 0  # alarm code reported in every status snapshot
        self.snapshot_reads = 0
        # A homing re-bases the drive's counter at the switch (62-63 pulses on the bench) once
        # the switch is found, i.e. on the read that reports the homing complete; `homes = False`
        # models a drive that did not run its homing at all.
        self._run_mode = None
        self.homes = True
        self.edge_counter = 63
        self._rebase_pending = False

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

    def read_input_register_32bit(self, slave_id, address, signed=False):
        return self._position if address == REG_CURRENT_POSITION else 0

    def _next_status_word(self):
        if self.status_script:
            self._status = self.status_script.pop(0)
        if self._status is not None:
            return self._status
        if self._running_reads > 0:
            self._running_reads -= 1
            return STATUS_BIT_RUNNING
        return 0  # idle, no fault

    def read_input_registers(self, slave_id, address, count):
        # Status snapshot (homing wait and move wait): status word at offset 8, the
        # commanded target as the live position at offsets 10..11 so the move-complete
        # tolerance check passes as soon as RUNNING clears, alarm code at offset 15.
        self.snapshot_reads += 1
        vals = [0] * count
        vals[8] = self._next_status_word()
        if self._rebase_pending and not (vals[8] & STATUS_BIT_RUNNING):
            self._position = self.edge_counter
            self._rebase_pending = False
        pos = self._position & 0xFFFFFFFF
        vals[10] = (pos >> 16) & 0xFFFF
        vals[11] = pos & 0xFFFF
        vals[15] = self.alarm
        return vals

    def write_register(self, slave_id, address, value):
        self.writes.append((address, value))
        if address == REG_RUN_MODE:
            self._run_mode = value
        if address == REG_CONTROL_WORD and value == CW_TRIGGER_ABSOLUTE:
            self._running_reads = 1
            self._rebase_pending = self._run_mode == MODE_HOMING and self.homes

    def write_register_32bit(self, slave_id, address, value, signed=False):
        self.writes.append((address, value))
        if address == REG_TARGET_POSITION:
            self._position = value

    def control_word_writes(self):
        return [value for (address, value) in self.writes if address == REG_CONTROL_WORD]

    def target_position_writes(self):
        return [value for (address, value) in self.writes if address == REG_TARGET_POSITION]


def _make_real_controller(monkeypatch, fake=None, stage=None, **controller_kwargs):
    fake = fake or _FakeModbus()
    monkeypatch.setattr(otc, "find_port", lambda serial_number: "FAKE_PORT")
    monkeypatch.setattr(otc, "ModbusRTUClient", lambda **kwargs: fake)
    controller = ObjectiveTurret4PosController(serial_number="SIM", stage=stage, **controller_kwargs)
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


# --- Z retract around a homing (PR #702's rule: a turret only homes with Z retracted) ---


def test_sim_home_retracts_z_and_leaves_it_there(monkeypatch):
    # Homing ends at the sensor reference, where no slot is in position: the focus height
    # of the objective that was selected must not be restored there.
    monkeypatch.setattr(control._def, "HOMING_ENABLED_Z", True)
    stage = FakeStage(z_mm=3.5)
    sim = _make_sim(stage=stage)
    sim.home()
    assert stage.z_moves == [OBJECTIVE_RETRACTED_POS_MM]
    sim.close()


def test_sim_reset_restores_z_only_after_the_rotation_back(monkeypatch):
    monkeypatch.setattr(control._def, "HOMING_ENABLED_Z", True)
    stage = FakeStage(z_mm=3.5)
    sim = _make_sim(stage=stage)
    sim.move_to_objective("10x")
    stage.z_moves.clear()
    sim.reset("10x")
    # One round trip: retract once, home, rotate (no restore of its own), restore once.
    assert stage.z_moves == [OBJECTIVE_RETRACTED_POS_MM, 3.5]
    assert sim.current_objective == "10x"
    stage.z_moves.clear()
    sim.reset(None)  # nothing selected: Z stays retracted
    assert stage.z_moves == [OBJECTIVE_RETRACTED_POS_MM]
    assert sim.current_objective is None
    sim.close()


@pytest.mark.parametrize("z_mm", [OBJECTIVE_RETRACTED_POS_MM, OBJECTIVE_RETRACTED_POS_MM + 0.00003, 0.0])
def test_home_sends_no_z_move_when_z_is_already_retracted(monkeypatch, z_mm):
    # Already at the retract position (within the microstep rounding of a position
    # readback) or below it (Z just homed to 0.0 at startup): no Z move at all. A move
    # from just above the retract position back onto it would be a move toward the Z
    # home switch, which the controller refuses right after a Z homing.
    monkeypatch.setattr(control._def, "HOMING_ENABLED_Z", True)
    stage = FakeStage(z_mm=z_mm)
    sim = _make_sim(stage=stage)
    sim.home()
    sim.move_to_objective("40x")
    assert stage.z_moves == []
    sim.close()


def test_sim_home_skips_z_without_a_stage_or_with_z_homing_disabled(monkeypatch):
    monkeypatch.setattr(control._def, "HOMING_ENABLED_Z", True)
    _make_sim(stage=None).home()  # must not raise
    monkeypatch.setattr(control._def, "HOMING_ENABLED_Z", False)
    stage = FakeStage(z_mm=3.5)
    sim = _make_sim(stage=stage)
    sim.home()
    assert stage.z_moves == []
    sim.close()


class _StageOnTheWire(FakeStage):
    """A FakeStage whose Z moves are also logged into the fake Modbus write list, so the
    order of Z moves and drive writes can be asserted."""

    def __init__(self, fake, z_mm):
        super().__init__(z_mm)
        self._fake = fake

    def move_z_to(self, abs_mm: float, blocking: bool = True):
        super().move_z_to(abs_mm, blocking)
        self._fake.writes.append(("Z", abs_mm))


def test_home_retracts_z_before_touching_the_drive_and_leaves_it_retracted(monkeypatch):
    monkeypatch.setattr(control._def, "HOMING_ENABLED_Z", True)
    fake = _FakeModbus()
    stage = _StageOnTheWire(fake, z_mm=3.5)
    controller, fake = _make_real_controller(monkeypatch, fake=fake, stage=stage)
    fake.writes.clear()
    controller.home()
    assert fake.writes[0] == ("Z", OBJECTIVE_RETRACTED_POS_MM)  # before the first register write
    assert fake.control_word_writes()[-3:] == [CW_STARTUP, CW_ENABLE, CW_RUN_ABSOLUTE]  # ends clamped at home
    assert stage.z_moves == [OBJECTIVE_RETRACTED_POS_MM]  # and Z is still retracted there
    controller.close()


def test_reset_restores_z_only_after_the_rotation_back(monkeypatch):
    # The operator's reset: Z comes back once, after the selected objective is back in
    # position - never at the sensor reference, where a longer objective may stand.
    monkeypatch.setattr(control._def, "HOMING_ENABLED_Z", True)
    fake = _FakeModbus()
    stage = _StageOnTheWire(fake, z_mm=3.5)
    controller, fake = _make_real_controller(monkeypatch, fake=fake, stage=stage)
    controller.move_to_objective("10x")
    fake.writes.clear()
    stage.z_moves.clear()
    controller.reset("10x")
    assert fake.writes[0] == ("Z", OBJECTIVE_RETRACTED_POS_MM)
    assert fake.writes[-1] == ("Z", 3.5)
    # The restore comes after the rotation's target write, which comes after the homing's SET_ZERO.
    i_zero = max(i for i, w in enumerate(fake.writes) if w == (REG_SET_ZERO, SET_ZERO_MAGIC))
    i_rotate = max(i for i, w in enumerate(fake.writes) if w[0] == REG_TARGET_POSITION)
    assert i_zero < i_rotate < len(fake.writes) - 1
    assert stage.z_moves == [OBJECTIVE_RETRACTED_POS_MM, 3.5]
    assert controller.current_objective == "10x"
    controller.close()


def test_home_failure_leaves_z_retracted(monkeypatch):
    # The turret's position is unknown after a failed homing: Z must not come back to the sample.
    monkeypatch.setattr(control._def, "HOMING_ENABLED_Z", True)
    _deterministic_clock(monkeypatch)
    stage = FakeStage(z_mm=3.5)
    controller, fake = _make_real_controller(monkeypatch, stage=stage)
    fake.status_script = [STATUS_BIT_RUNNING]  # the last scripted word repeats: never finishes
    with pytest.raises(TimeoutError):
        controller.home(timeout_s=1.0)
    assert stage.z_moves == [OBJECTIVE_RETRACTED_POS_MM]
    controller.close()


class _FakeTime:
    """Deterministic stand-in for the controller module's `time`: sleep advances the clock."""

    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        assert seconds >= 0, f"negative sleep {seconds}"
        self.sleeps.append(seconds)
        self.now += seconds


def _deterministic_clock(monkeypatch):
    """Install a fake clock on the controller module; call before constructing the controller."""
    clock = _FakeTime()
    monkeypatch.setattr(otc, "time", clock)
    return clock


# --- homing: the drive runs it (origin-switch method), the host waits ---


def test_home_runs_the_drive_homing_mode_then_zeroes_and_clamps(monkeypatch):
    controller, fake = _make_real_controller(monkeypatch)
    fake.writes.clear()
    controller.home()
    # Homing mode is selected while disabled, then the 0x0F -> 0x1F start the manual
    # prescribes; no velocity sweep, no relative jogs.
    assert fake.writes.index((REG_RUN_MODE, MODE_HOMING)) > fake.writes.index((REG_CONTROL_WORD, CW_DISABLE))
    cws = fake.control_word_writes()
    assert cws[:5] == [CW_DISABLE, CW_STARTUP, CW_ENABLE, CW_RUN_ABSOLUTE, CW_TRIGGER_ABSOLUTE]
    # The counter is zeroed once, after the drive has returned to the switch edge.
    assert [v for (a, v) in fake.writes if a == REG_SET_ZERO] == [SET_ZERO_MAGIC]
    assert fake.writes.index((REG_SET_ZERO, SET_ZERO_MAGIC)) > fake.writes.index(
        (REG_CONTROL_WORD, CW_TRIGGER_ABSOLUTE)
    )
    # Ends clamped at home in position mode (0x06/0x07/0x0F, no trigger), not de-energized.
    assert cws[-3:] == [CW_STARTUP, CW_ENABLE, CW_RUN_ABSOLUTE]
    assert fake.writes.index((REG_RUN_MODE, MODE_POSITION)) > fake.writes.index((REG_RUN_MODE, MODE_HOMING))
    assert controller.current_objective is None
    controller.close()


def test_home_does_not_touch_the_move_speed_or_acceleration(monkeypatch):
    # The software homing lowered max speed / accel for its jogs and had to restore them
    # afterwards; the drive's homing has its own speed registers, so a home must leave
    # the move set alone.
    controller, fake = _make_real_controller(monkeypatch)
    fake.writes.clear()
    controller.home()
    assert not [a for (a, _v) in fake.writes if a in (REG_MAX_SPEED, REG_ACCEL)]
    controller.close()


def test_home_waits_for_running_to_clear(monkeypatch):
    clock = _deterministic_clock(monkeypatch)
    controller, fake = _make_real_controller(monkeypatch)
    fake.status_script = [STATUS_BIT_RUNNING, STATUS_BIT_RUNNING, STATUS_BIT_RUNNING, 0]
    controller.home()
    assert fake.snapshot_reads == 4  # three RUNNING polls, then the idle word that ends the wait
    assert clock.now < otc.MOVE_START_GRACE_S
    controller.close()


def test_home_holds_the_idle_verdict_until_running_seen_or_grace(monkeypatch):
    # A short creep (switch already active) can finish before the drive ever shows RUNNING:
    # an idle word is only accepted as done after MOVE_START_GRACE_S, as for moves.
    clock = _deterministic_clock(monkeypatch)
    controller, fake = _make_real_controller(monkeypatch)
    fake.status_script = [0]
    controller.home()
    assert clock.now >= otc.MOVE_START_GRACE_S
    assert fake.control_word_writes()[-3:] == [CW_STARTUP, CW_ENABLE, CW_RUN_ABSOLUTE]
    controller.close()


def test_home_timeout_leaves_motor_deenergized(monkeypatch):
    _deterministic_clock(monkeypatch)
    controller, fake = _make_real_controller(monkeypatch)
    fake.status_script = [STATUS_BIT_RUNNING]  # the last scripted word repeats: never finishes
    with pytest.raises(TimeoutError):
        controller.home(timeout_s=1.0)
    # Failure cleanup: stopped and de-energized, NOT zeroed, NOT left clamped.
    assert fake.control_word_writes()[-1] == CW_DISABLE
    assert (REG_SET_ZERO, SET_ZERO_MAGIC) not in fake.writes
    controller.close()


def test_home_raises_on_a_drive_alarm(monkeypatch):
    # An alarm (undervoltage etc.) interrupts the homing; waiting for the timeout would
    # only hide the cause.
    controller, fake = _make_real_controller(monkeypatch)
    fake.alarm = 0xFF0E
    with pytest.raises(RuntimeError, match="alarm"):
        controller.home()
    assert fake.control_word_writes()[-1] == CW_DISABLE
    controller.close()


def test_home_raises_on_the_fault_bit(monkeypatch):
    controller, fake = _make_real_controller(monkeypatch)
    fake.status_script = [STATUS_BIT_FAULT]
    with pytest.raises(RuntimeError, match="fault"):
        controller.home()
    assert fake.control_word_writes()[-1] == CW_DISABLE
    controller.close()


def test_init_calibrates_the_drive_homing_params(monkeypatch):
    # Fake reads 0 everywhere, so every non-zero homing register must be written, and
    # before the EEPROM save so it persists with the rest of the factory set.
    controller, fake = _make_real_controller(monkeypatch)
    writes = fake.writes
    save = writes.index((REG_SAVE_PARAMS, SAVE_PARAMS_MAGIC))
    for row in [
        (REG_HOMING_METHOD, HOMING_METHOD_SEARCH_NEGATIVE),
        (REG_HOMING_SEARCH_SPEED, HOMING_SEARCH_SPEED),
        (REG_HOMING_ZERO_SPEED, HOMING_ZERO_SPEED),
        (REG_HOMING_ACCEL, HOMING_ACCEL),
        (REG_ZERO_RETURN, ZERO_RETURN_ENABLED),
    ]:
        assert row in writes
        assert writes.index(row) < save
    controller.close()


def test_move_wait_holds_the_idle_verdict_until_running_seen_or_grace(monkeypatch):
    # Idle-at-target before RUNNING was ever seen must not end the move early (see MOVE_START_GRACE_S).
    clock = _deterministic_clock(monkeypatch)
    controller, fake = _make_real_controller(monkeypatch)
    fake.status_script = [0]  # never reports RUNNING; position tracks the target
    controller.move_to_objective("10x")
    assert clock.now >= otc.MOVE_START_GRACE_S
    assert fake.control_word_writes()[-1] == CW_DISABLE
    controller.close()


def test_move_wait_returns_as_soon_as_running_clears(monkeypatch):
    clock = _deterministic_clock(monkeypatch)
    controller, fake = _make_real_controller(monkeypatch)
    fake.status_script = [STATUS_BIT_RUNNING, 0]
    controller.move_to_objective("10x")
    assert clock.now < otc.MOVE_START_GRACE_S
    controller.close()


def test_move_wait_raises_when_the_motor_stops_short(monkeypatch):
    class _StalledModbus(_FakeModbus):
        def write_register_32bit(self, slave_id, address, value, signed=False):
            self.writes.append((address, value))  # the position counter never follows the target

    _deterministic_clock(monkeypatch)
    controller, fake = _make_real_controller(monkeypatch, fake=_StalledModbus())
    fake.status_script = [STATUS_BIT_RUNNING, 0]
    with pytest.raises(RuntimeError, match="stopped at"):
        controller.move_to_objective("10x")
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


def test_init_microstep_mismatch_raises_without_writing(monkeypatch):
    # The register reads back the pending value, so a corrective write would let
    # the next start pass this check on the wrong active scale: raise, never write.
    fake = _FakeModbus()
    fake.microstep_raw = 7
    monkeypatch.setattr(otc, "find_port", lambda serial_number: "FAKE_PORT")
    monkeypatch.setattr(otc, "ModbusRTUClient", lambda **kwargs: fake)
    with pytest.raises(RuntimeError, match="[Pp]ower-cycle"):
        ObjectiveTurret4PosController(serial_number="SIM", stage=None)
    assert not any(addr == REG_MICROSTEP for (addr, _) in fake.writes)
    assert not any(addr == REG_SAVE_PARAMS for (addr, _) in fake.writes)


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
    monkeypatch.setattr(otc, "find_port", lambda serial_number: "FAKE_PORT")
    monkeypatch.setattr(otc, "ModbusRTUClient", lambda **kwargs: _FakeModbus())
    with pytest.raises(ValueError):
        ObjectiveTurret4PosController(serial_number="SIM", stage=None, offset_pulses=bad_offset)


def test_out_of_range_offset_raises(monkeypatch):
    # An offset beyond one slot (the 90-degree spacing) is a misconfiguration and must be
    # rejected. With the fake's microstep 4 -> 16 microsteps, pulses/position = 2200, so
    # 5000 is over one slot (but under a full rev) — it must still be rejected.
    monkeypatch.setattr(otc, "find_port", lambda serial_number: "FAKE_PORT")
    monkeypatch.setattr(otc, "ModbusRTUClient", lambda **kwargs: _FakeModbus())
    with pytest.raises(ValueError):
        ObjectiveTurret4PosController(serial_number="SIM", stage=None, offset_pulses=5_000)


# --- slot targets ---


def test_pulses_per_slot_is_2200():
    # Pin the derived value so an accidental edit to a mechanics constant fails
    # loudly. Slot targets are covered by test_move_targets_apply_offset.
    assert otc.PULSES_PER_SLOT == 2200


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
    monkeypatch.setattr(otc, "find_port", lambda serial_number: "FAKE_PORT")
    monkeypatch.setattr(otc, "ModbusRTUClient", lambda **kwargs: _FakeModbus())
    with pytest.raises(ValueError):
        ObjectiveTurret4PosController(serial_number="SIM", stage=None, backlash_deg=bad_deg)


def test_sim_accepts_offset_and_backlash_kwargs():
    # Constructor parity: microscope.py builds one turret_kwargs dict for both twins.
    sim = ObjectiveTurret4PosControllerSimulation(
        serial_number="SIM-001",
        positions=OBJECTIVE_TURRET_POSITIONS,
        offset_pulses=42,
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
    # An explicit offset keeps the theoretical targets independent of any machine
    # .ini values loaded into _def.
    controller, fake = _make_real_controller(monkeypatch, direction_inverted=True, offset_pulses=0)
    pp = controller.pulses_per_position
    fake.writes.clear()
    controller.move_to_objective("20x")  # slot 3 -> logical target 2*pp
    assert fake.target_position_writes() == [-2 * pp]
    controller.close()


def test_inverted_backlash_order_preserved_logically(monkeypatch):
    # Undershoot-then-target stays expressed in logical coordinates; both writes
    # come out negated but the logical approach-from-below order is unchanged.
    controller, fake = _make_real_controller(monkeypatch, backlash_deg=0.5, direction_inverted=True, offset_pulses=0)
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


def test_inverted_direction_selects_the_mirror_homing_method(monkeypatch):
    # The drive's homing methods fix the search direction physically. Method 21 searches
    # in the physical negative direction; on a motor wired the other way round the
    # logical-negative search is physical-positive, which is method 19.
    controller, fake = _make_real_controller(monkeypatch, direction_inverted=True)
    assert (REG_HOMING_METHOD, HOMING_METHOD_SEARCH_POSITIVE) in fake.writes
    assert (REG_HOMING_METHOD, HOMING_METHOD_SEARCH_NEGATIVE) not in fake.writes
    controller.close()


def test_inverted_init_expects_direction_zero(monkeypatch):
    # The fake reads 0 from the direction register; inverted expects 0, so init must
    # NOT issue the non-inverted corrective write of 1.
    controller, fake = _make_real_controller(monkeypatch, direction_inverted=True)
    assert (REG_DIRECTION, 1) not in fake.writes
    controller.close()


def test_direction_inverted_falls_back_to_def_when_not_passed(monkeypatch):
    monkeypatch.setattr(control._def, "OBJECTIVE_TURRET_DIRECTION_INVERTED", True)
    controller, fake = _make_real_controller(monkeypatch, offset_pulses=0)
    fake.writes.clear()
    controller.move_to_objective("40x")  # slot 4 -> logical target 3*pp
    assert fake.target_position_writes() == [-3 * controller.pulses_per_position]
    controller.close()


@pytest.mark.parametrize("bad_inverted", [1, "true", 0.0], ids=["int", "str", "float"])
def test_non_bool_direction_inverted_raises(monkeypatch, bad_inverted):
    # .ini parsing can yield an int/str; only a real boolean is accepted.
    monkeypatch.setattr(otc, "find_port", lambda serial_number: "FAKE_PORT")
    monkeypatch.setattr(otc, "ModbusRTUClient", lambda **kwargs: _FakeModbus())
    with pytest.raises(ValueError):
        ObjectiveTurret4PosController(serial_number="SIM", stage=None, direction_inverted=bad_inverted)


def test_default_not_inverted_keeps_current_behavior(monkeypatch):
    # Regression guard: with the default (False), targets, direction writes and
    # position readback are exactly the pre-inversion behavior.
    controller, fake = _make_real_controller(monkeypatch, offset_pulses=0)
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
# The drive runs the homing, so it has to see the switch with the right polarity: the
# option is applied to the drive's DI polarity register (0x2E, bit 0 = DI1) at connect
# and persisted with the factory set. Nothing is flipped host-side any more.


def test_di_invert_sets_the_drive_polarity_bit_for_di1(monkeypatch):
    controller, fake = _make_real_controller(monkeypatch, di_invert=True, direction_inverted=False)
    assert (REG_DI_POLARITY, 0x0001) in fake.writes
    assert fake.writes.index((REG_DI_POLARITY, 0x0001)) < fake.writes.index((REG_SAVE_PARAMS, SAVE_PARAMS_MAGIC))
    controller.close()


class _PolarityPresetModbus(_FakeModbus):
    """Reports a preset DI polarity register, so the DI1 bit must be merged, not overwritten."""

    def __init__(self, polarity: int):
        super().__init__()
        self.polarity = polarity

    def read_register(self, slave_id, address):
        if address == REG_DI_POLARITY:
            return self.polarity
        return super().read_register(slave_id, address)


def test_di_invert_preserves_the_other_inputs_polarity_bits(monkeypatch):
    fake = _PolarityPresetModbus(0x0006)  # DI2 and DI3 inverted on this drive
    controller, fake = _make_real_controller(monkeypatch, fake=fake, di_invert=True, direction_inverted=False)
    assert (REG_DI_POLARITY, 0x0007) in fake.writes
    controller.close()


def test_default_polarity_clears_di1_and_keeps_the_rest(monkeypatch):
    fake = _PolarityPresetModbus(0x0003)  # DI1 left inverted by an earlier setup, DI2 inverted
    controller, fake = _make_real_controller(monkeypatch, fake=fake, di_invert=False, direction_inverted=False)
    assert (REG_DI_POLARITY, 0x0002) in fake.writes
    controller.close()


def test_default_polarity_writes_nothing_on_a_clean_drive(monkeypatch):
    controller, fake = _make_real_controller(monkeypatch, di_invert=False, direction_inverted=False)
    assert REG_DI_POLARITY not in [a for (a, _v) in fake.writes]
    controller.close()


def test_di_invert_falls_back_to_def_when_not_passed(monkeypatch):
    monkeypatch.setattr(control._def, "OBJECTIVE_TURRET_DI_INVERT", True)
    controller, fake = _make_real_controller(monkeypatch, direction_inverted=False)
    assert (REG_DI_POLARITY, 0x0001) in fake.writes
    controller.close()


@pytest.mark.parametrize("bad_invert", [1, "true", 0.0], ids=["int", "str", "float"])
def test_non_bool_di_invert_raises(monkeypatch, bad_invert):
    # .ini parsing can yield an int/str; only a real boolean is accepted.
    monkeypatch.setattr(otc, "find_port", lambda serial_number: "FAKE_PORT")
    monkeypatch.setattr(otc, "ModbusRTUClient", lambda **kwargs: _FakeModbus())
    with pytest.raises(ValueError):
        ObjectiveTurret4PosController(serial_number="SIM", stage=None, direction_inverted=False, di_invert=bad_invert)


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


# --- review findings on the drive-side homing (2026-10-10) ---


class _TriggerRejectingModbus(_FakeModbus):
    """The homing trigger write fails on the wire (reply lost); every other write is recorded."""

    def write_register(self, slave_id, address, value):
        if address == REG_CONTROL_WORD and value == CW_TRIGGER_ABSOLUTE:
            raise ModbusError("reply lost")
        super().write_register(slave_id, address, value)


def test_home_start_failure_deenergizes(monkeypatch):
    # The drive may have accepted the trigger although the reply was lost: a failed start must
    # end with the motor disabled, like a failed move, not with an unsupervised homing running.
    controller, fake = _make_real_controller(monkeypatch, fake=_TriggerRejectingModbus())
    with pytest.raises(ModbusError):
        controller.home()
    assert fake.control_word_writes()[-1] == CW_DISABLE
    assert (REG_SET_ZERO, SET_ZERO_MAGIC) not in fake.writes
    controller.close()


class _RunawayModbus(_FakeModbus):
    """The drive keeps searching: RUNNING forever and the counter moves one step per read."""

    def __init__(self, step):
        super().__init__()
        self.step = step
        self.status_script = [STATUS_BIT_RUNNING]

    def read_input_registers(self, slave_id, address, count):
        vals = super().read_input_registers(slave_id, address, count)
        self._position += self.step
        return vals


@pytest.mark.parametrize("step", [-3000, 3000], ids=["negative", "positive"])
def test_home_raises_when_the_search_exceeds_one_revolution(monkeypatch, step):
    # A switch the drive never sees (polarity, wiring, a dead sensor) must not spin the turret
    # for the whole 30 s timeout: the counter is in every snapshot, so bound the travel.
    _deterministic_clock(monkeypatch)
    controller, fake = _make_real_controller(monkeypatch, fake=_RunawayModbus(step))
    with pytest.raises(RuntimeError, match="one revolution"):
        controller.home()
    assert fake.control_word_writes()[-1] == CW_DISABLE
    assert (REG_SET_ZERO, SET_ZERO_MAGIC) not in fake.writes
    controller.close()


def test_home_raises_when_the_drive_did_not_reach_the_switch_reference(monkeypatch):
    # The drive re-bases its counter at the switch (62-63 pulses at the stop on the bench);
    # an idle drive still at a slot position did not run its homing and must not be zeroed.
    controller, fake = _make_real_controller(monkeypatch)
    fake.homes = False
    fake._position = 6050  # slot 3 on the bench unit
    with pytest.raises(RuntimeError, match="switch reference"):
        controller.home()
    assert (REG_SET_ZERO, SET_ZERO_MAGIC) not in fake.writes
    assert fake.control_word_writes()[-1] == CW_DISABLE
    controller.close()


def test_home_accepts_the_bench_edge_counter(monkeypatch):
    controller, fake = _make_real_controller(monkeypatch)
    fake.edge_counter = 63
    controller.home()
    assert (REG_SET_ZERO, SET_ZERO_MAGIC) in fake.writes
    controller.close()


def test_move_raises_on_a_drive_alarm(monkeypatch):
    # The shared wait raises on an alarm code for moves too (previously only the fault bit).
    controller, fake = _make_real_controller(monkeypatch)
    fake.alarm = 0xFF0E
    with pytest.raises(RuntimeError, match="alarm"):
        controller.move_to_objective("10x")
    assert fake.control_word_writes()[-1] == CW_DISABLE
    controller.close()


def test_homing_params_are_keyword_only():
    # Two booleans swapped silently would select the mirror method by the polarity flag.
    with pytest.raises(TypeError):
        otc.homing_params(True, False)  # type: ignore[misc]
    assert otc.homing_params(direction_inverted=False, di_invert=False)


def test_zero_speed_is_not_below_the_start_speed():
    # The drive's start speed (REG_MIN_SPEED) bounds every homing phase from below; a creep
    # slower than it cannot happen, so raising EXPECTED_MIN_SPEED must be caught here.
    assert otc.HOMING_ZERO_SPEED >= otc.EXPECTED_MIN_SPEED


def test_init_warns_when_a_per_machine_homing_row_is_changed(monkeypatch):
    # The .ini wins over whatever the drive carried (e.g. a polarity inverted on the drive by
    # SingleMotor on a unit whose .ini still says False), so a changed row is said out loud.
    warnings = []
    monkeypatch.setattr(otc.logger, "warning", lambda msg, *args: warnings.append(msg % args))
    controller, fake = _make_real_controller(monkeypatch, di_invert=True, direction_inverted=False)
    assert any("DI1_polarity" in w and "homing_method" in w for w in warnings), warnings
    controller.close()


def test_init_does_not_warn_when_the_drive_already_matches(monkeypatch):
    class _ConfiguredModbus(_FakeModbus):
        def read_register(self, slave_id, address):
            if address == REG_HOMING_METHOD:
                return HOMING_METHOD_SEARCH_NEGATIVE
            if address == REG_ZERO_RETURN:
                return ZERO_RETURN_ENABLED
            return super().read_register(slave_id, address)

        def read_register_32bit(self, slave_id, address, signed=False):
            return {
                REG_HOMING_SEARCH_SPEED: HOMING_SEARCH_SPEED,
                REG_HOMING_ZERO_SPEED: HOMING_ZERO_SPEED,
                REG_HOMING_ACCEL: HOMING_ACCEL,
            }.get(address, 0)

    warnings = []
    monkeypatch.setattr(otc.logger, "warning", lambda msg, *args: warnings.append(msg % args))
    fake = _ConfiguredModbus()
    fake.status_script = []
    controller, fake = _make_real_controller(monkeypatch, fake=fake)
    assert not [w for w in warnings if "homing" in w.lower()], warnings
    controller.close()


def test_home_after_a_failed_search_is_not_tripped_by_the_counter_rebase(monkeypatch):
    # A search that found no switch leaves the counter beyond one revolution (-10080 here); on the
    # retry the drive finds the switch and re-bases to 63. That jump is the re-base, not travel.
    controller, fake = _make_real_controller(monkeypatch)
    fake._position = -10080
    controller.home()
    assert (REG_SET_ZERO, SET_ZERO_MAGIC) in fake.writes
    assert fake.control_word_writes()[-3:] == [CW_STARTUP, CW_ENABLE, CW_RUN_ABSOLUTE]
    controller.close()
