import pytest

import control._def
import control.asi_objective_turret as aot
from squid.stage.asi import MS2000Serial
from tests.tools import FakeSerialConn as _FakeSerialConn, FakeZStage as FakeStage

POSITIONS = {"2x": 1, "4x": 2, "10x": 3, "20x": 4, "40x": 5, "60x": 6}


def _shared_turret(replies, stage=None, positions=POSITIONS, **kwargs):
    """Turret on a shared (not owned) scripted transport; first reply feeds the ctor W F probe."""
    conn = _FakeSerialConn(replies=replies)
    turret = aot.ASIObjectiveTurret(shared_serial=MS2000Serial(conn), positions=positions, stage=stage, **kwargs)
    return turret, conn


# --- simulation ---------------------------------------------------------------


def test_sim_init_open():
    sim = aot.ASIObjectiveTurretSimulation(positions=POSITIONS)
    assert sim.is_open is True
    assert sim.current_objective is None
    assert sim.current_slot is None  # unknown until commanded


@pytest.mark.parametrize("name", sorted(POSITIONS))
def test_sim_move_to_each_known_objective(name):
    sim = aot.ASIObjectiveTurretSimulation(positions=POSITIONS)
    sim.move_to_objective(name)
    assert sim.current_objective == name
    assert sim.current_slot == POSITIONS[name]


def test_sim_unknown_objective_raises_key_error():
    sim = aot.ASIObjectiveTurretSimulation(positions=POSITIONS)
    with pytest.raises(KeyError, match="Valid names"):
        sim.move_to_objective("95x")


def test_sim_home_is_motionless(monkeypatch):
    # The ASI turret has no homing; home() must not move the turret or Z.
    monkeypatch.setattr(control._def, "HOMING_ENABLED_Z", True, raising=False)
    stage = FakeStage()
    sim = aot.ASIObjectiveTurretSimulation(positions=POSITIONS, stage=stage)
    sim.move_to_objective("10x")
    sim.home()
    assert sim.current_objective is None  # tracked name cleared
    assert sim.current_slot == 3  # slot unchanged: no rotation happened
    assert stage.z_moves == [control._def.OBJECTIVE_RETRACTED_POS_MM, 3.5]  # only the earlier move's dance


def test_sim_retract_and_restore_z(monkeypatch):
    monkeypatch.setattr(control._def, "HOMING_ENABLED_Z", True, raising=False)
    stage = FakeStage(z_mm=3.5)
    sim = aot.ASIObjectiveTurretSimulation(positions=POSITIONS, stage=stage)
    sim.move_to_objective("4x")
    assert stage.z_moves == [control._def.OBJECTIVE_RETRACTED_POS_MM, 3.5]


def test_sim_skips_z_without_stage(monkeypatch):
    monkeypatch.setattr(control._def, "HOMING_ENABLED_Z", True, raising=False)
    sim = aot.ASIObjectiveTurretSimulation(positions=POSITIONS, stage=None)
    sim.move_to_objective("4x")  # must not raise


def test_sim_skips_z_when_homing_z_disabled(monkeypatch):
    monkeypatch.setattr(control._def, "HOMING_ENABLED_Z", False, raising=False)
    stage = FakeStage()
    sim = aot.ASIObjectiveTurretSimulation(positions=POSITIONS, stage=stage)
    sim.move_to_objective("4x")
    assert stage.z_moves == []


def test_sim_alias_same_slot_skips_dance(monkeypatch):
    monkeypatch.setattr(control._def, "HOMING_ENABLED_Z", True, raising=False)
    stage = FakeStage()
    positions = {"4x": 1, "4x-phase": 1, "10x": 2}
    sim = aot.ASIObjectiveTurretSimulation(positions=positions, stage=stage)
    sim.move_to_objective("4x")
    moves_after_first = list(stage.z_moves)
    sim.move_to_objective("4x-phase")  # same slot: tracked-name-only no-op
    assert sim.current_objective == "4x-phase"
    assert stage.z_moves == moves_after_first


def test_sim_restore_z_false_skips_restore(monkeypatch):
    monkeypatch.setattr(control._def, "HOMING_ENABLED_Z", True, raising=False)
    stage = FakeStage()
    sim = aot.ASIObjectiveTurretSimulation(positions=POSITIONS, stage=stage)
    sim.move_to_objective("4x", restore_z=False)
    assert stage.z_moves == [control._def.OBJECTIVE_RETRACTED_POS_MM]


def test_sim_ops_after_close_raise():
    sim = aot.ASIObjectiveTurretSimulation(positions=POSITIONS)
    sim.close()
    with pytest.raises(RuntimeError):
        sim.move_to_objective("4x")


def test_sim_close_idempotent_and_context_manager():
    with aot.ASIObjectiveTurretSimulation(positions=POSITIONS) as sim:
        assert sim.is_open
    assert not sim.is_open
    sim.close()  # second close must not raise


def test_invalid_slot_positions_raise():
    for bad in ({"4x": 0}, {"4x": 7}, {"4x": "one"}):
        with pytest.raises(ValueError):
            aot.ASIObjectiveTurretSimulation(positions=bad)
        with pytest.raises(ValueError):
            aot.ASIObjectiveTurret(shared_serial=MS2000Serial(_FakeSerialConn()), positions=bad)


# --- real controller over a scripted transport --------------------------------


def test_move_frames_raw_slot_command():
    # Slot index is sent RAW ('M F=3'), not scaled by the 0.1 um unit factor.
    turret, conn = _shared_turret([b":A 1\r\n", b":A\r\n", b"N\r\n", b":A 3\r\n"])
    assert turret.current_slot == 1  # seeded from the ctor W F probe
    turret.move_to_objective("10x")
    assert conn.written == [b"W F\r", b"M F=3\r", b"/\r", b"W F\r"]
    assert turret.current_objective == "10x"
    assert turret.current_slot == 3


def test_wait_idle_polls_global_busy():
    turret, conn = _shared_turret([b":A 1\r\n", b":A\r\n", b"B\r\n", b"B\r\n", b"N\r\n", b":A 2\r\n"])
    turret.move_to_objective("4x")
    assert conn.written.count(b"/\r") == 3


def test_move_timeout_raises_and_still_restores_z(monkeypatch):
    monkeypatch.setattr(control._def, "HOMING_ENABLED_Z", True, raising=False)
    stage = FakeStage(z_mm=2.0)
    conn = _FakeSerialConn(replies=[b":A 1\r\n"], default=b"B\r\n")  # never idle
    turret = aot.ASIObjectiveTurret(shared_serial=MS2000Serial(conn), positions=POSITIONS, stage=stage)
    with pytest.raises(RuntimeError, match="idle"):
        turret.move_to_objective("4x", timeout_s=0.12)
    assert stage.z_moves == [control._def.OBJECTIVE_RETRACTED_POS_MM, 2.0]  # finally restored


def test_probe_seed_short_circuits_move():
    # Ctor W F said slot 4; moving to the slot-4 objective must not rotate.
    turret, conn = _shared_turret([b":A 4\r\n"])
    turret.move_to_objective("20x")
    assert conn.written == [b"W F\r"]  # no M command
    assert turret.current_objective == "20x"


def test_probe_garbage_means_unknown_slot():
    turret, conn = _shared_turret([b":N-1\r\n", b":A\r\n", b"N\r\n"])
    assert turret.current_slot is None
    turret.move_to_objective("2x")  # unknown never short-circuits: rotation commanded
    assert b"M F=1\r" in conn.written


def test_unknown_objective_keyerror_real():
    turret, _ = _shared_turret([b":A 1\r\n"])
    with pytest.raises(KeyError, match="Valid names"):
        turret.move_to_objective("95x")


def test_shared_serial_not_closed_on_close():
    turret, conn = _shared_turret([b":A 1\r\n"])
    assert turret.owns_serial is False
    turret.close()
    assert conn.closed is False  # the Z stage owns the shared transport


def test_owned_serial_closed_on_close(monkeypatch):
    conn = _FakeSerialConn(replies=[b":A 1\r\n"])
    monkeypatch.setattr(MS2000Serial, "open", classmethod(lambda cls, *a, **k: MS2000Serial(conn)))
    turret = aot.ASIObjectiveTurret(serial_port="/dev/FAKE", positions=POSITIONS)
    assert turret.owns_serial is True
    turret.close()
    assert conn.closed is True


def test_owned_ctor_probe_failure_closes_serial(monkeypatch):
    conn = _FakeSerialConn(replies=[])  # dead air, both probe attempts
    monkeypatch.setattr(MS2000Serial, "open", classmethod(lambda cls, *a, **k: MS2000Serial(conn)))
    with pytest.raises(RuntimeError):
        aot.ASIObjectiveTurret(serial_port="/dev/FAKE", positions=POSITIONS)
    assert conn.closed is True  # no leaked handle


def test_home_is_motionless_real():
    turret, conn = _shared_turret([b":A 2\r\n", b":A 2\r\n"])
    turret.home()
    assert all(not w.startswith(b"M ") for w in conn.written)  # W F probes only, no motion
    assert turret.current_slot == 2


def test_error_ack_on_move_raises():
    turret, _ = _shared_turret([b":A 1\r\n", b":N-4\r\n"])
    with pytest.raises(RuntimeError, match="N-4"):
        turret.move_to_objective("4x")


def test_turret_without_port_or_sn_raises():
    with pytest.raises(RuntimeError, match="ASI_OBJECTIVE_TURRET"):
        aot.ASIObjectiveTurret(positions=POSITIONS)


# --- simulated end-to-end through Microscope -----------------------------------


def _build_asi_scope(monkeypatch, skip_init):
    import control.microscope

    monkeypatch.setattr(control._def, "USE_ASI_Z_STAGE", True, raising=False)
    # find-zero (default on) derives its overdrive from the physical travel; a configured
    # machine always sets this.
    monkeypatch.setattr(control._def, "ASI_Z_TRAVEL_MM", 50.0, raising=False)
    monkeypatch.setattr(control._def, "USE_ASI_OBJECTIVE_TURRET", True, raising=False)
    monkeypatch.setattr(control._def, "SIMULATE_OBJECTIVE_CHANGER", True, raising=False)
    return control.microscope.Microscope.build_from_global_config(simulated=True, skip_init=skip_init)


def test_microscope_builds_asi_turret_simulated(monkeypatch):
    scope = _build_asi_scope(monkeypatch, skip_init=True)
    changer = scope.addons.objective_changer
    assert isinstance(changer, aot.ASIObjectiveTurretSimulation)
    changer.move_to_objective(control._def.DEFAULT_OBJECTIVE)
    assert changer.current_objective == control._def.DEFAULT_OBJECTIVE
    scope.close()


def test_microscope_startup_selects_default_objective(monkeypatch):
    scope = _build_asi_scope(monkeypatch, skip_init=False)
    assert scope.addons.objective_changer.current_objective == control._def.DEFAULT_OBJECTIVE
    scope.close()
