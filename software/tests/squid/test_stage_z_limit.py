"""Z soft-limit clamping for CephlaStage.move_z_to.

Regression tests for the objective-turret startup TimeoutError: after homing the
firmware reports Z=0.0, which is below the configured Z soft floor (MIN_POSITION,
0.05mm by default). The turret's retract/restore dance then commands an absolute
move back to 0.0. The firmware's absolute MOVETO_Z stores that target unclamped
while parking the motor at the clamped limit, so the move never reports complete
and wait_till_operation_is_completed times out.

The host-side fix clamps the final absolute Z target to [MIN_POSITION,
MAX_POSITION] in CephlaStage.move_z_to, mirroring the clamp already applied to the
backlash pre-move. Relative MOVE_Z is already clamped firmware-side, so only the
absolute path needs this guard.
"""

import pytest

import squid.config
import squid.stage.cephla
from control._def import CMD_EXECUTION_STATUS, CMD_SET
from control.microcontroller import Microcontroller, SimSerial


class _ZSoftLimitDeadlockSerial(SimSerial):
    """SimSerial that reproduces the firmware's below-soft-limit Z deadlock.

    The stock SimSerial accepts any absolute MOVETO_Z and immediately reports
    COMPLETED at the commanded position, so it cannot reproduce the hang. This
    subclass models the real Teensy behavior: do_focus_control drives the motor
    to the clamped soft limit, but check_position compares the live position
    against the *unclamped* commanded target, so a below-limit target never
    matches and the command stays IN_PROGRESS forever.
    """

    def __init__(self, z_axis_config):
        super().__init__()
        lo = z_axis_config.convert_real_units_to_ustep(z_axis_config.MIN_POSITION)
        hi = z_axis_config.convert_real_units_to_ustep(z_axis_config.MAX_POSITION)
        # MOVEMENT_SIGN can invert ustep ordering, so sort to get the reachable range.
        self._z_lo_ust, self._z_hi_ust = sorted((lo, hi))

    def _respond_to(self, write_bytes):
        if write_bytes[1] == CMD_SET.MOVETO_Z:
            target = self.unpack_position(write_bytes[2:6])
            if not (self._z_lo_ust <= target <= self._z_hi_ust):
                # Park at the clamped limit but never acknowledge the unclamped
                # target -> perpetual IN_PROGRESS, exactly as the firmware wedges.
                self.z = min(max(target, self._z_lo_ust), self._z_hi_ust)
                self.response_buffer.extend(
                    SimSerial.response_bytes_for(
                        write_bytes[0],
                        CMD_EXECUTION_STATUS.IN_PROGRESS,
                        self.x,
                        self.y,
                        self.z,
                        self.theta,
                        self.joystick_button,
                        self.switch,
                        firmware_version=(SimSerial.FIRMWARE_VERSION_MAJOR, SimSerial.FIRMWARE_VERSION_MINOR),
                    )
                )
                self._update_internal_state()
                return
        super()._respond_to(write_bytes)


@pytest.fixture
def make_stage():
    """Build a CephlaStage on a given SimSerial, closing the microcontroller at teardown.

    tests/squid/ has no autouse Microcontroller-cleanup fixture (that lives in
    tests/control/conftest.py), so close it here to stop the background
    serial-read thread, per the CLAUDE.md segfault note.
    """
    created = []

    def _make(serial=None):
        mc = Microcontroller(serial if serial is not None else SimSerial(), True)
        created.append(mc)
        return squid.stage.cephla.CephlaStage(mc, squid.config.get_stage_config())

    yield _make

    for mc in created:
        mc.close()


def test_move_z_to_below_min_clamps_to_min(make_stage):
    stage = make_stage()
    z_min = stage.get_config().Z_AXIS.MIN_POSITION
    # Mirror the turret restore: retract up, then attempt to restore below the floor.
    stage.move_z_to(0.1)
    stage.move_z_to(0.0)
    assert stage.get_pos().z_mm == pytest.approx(z_min, abs=1e-3)


def test_move_z_to_above_max_clamps_to_max(make_stage):
    stage = make_stage()
    z_max = stage.get_config().Z_AXIS.MAX_POSITION
    stage.move_z_to(z_max + 5.0)
    assert stage.get_pos().z_mm == pytest.approx(z_max, abs=1e-3)


def test_move_z_to_in_range_is_not_clamped(make_stage):
    stage = make_stage()
    stage.move_z_to(1.0)
    assert stage.get_pos().z_mm == pytest.approx(1.0, abs=1e-3)


def test_move_z_to_below_soft_limit_does_not_deadlock(make_stage, monkeypatch):
    # Keep the pre-fix failing path fast: a below-limit move would otherwise wait
    # the full computed timeout before raising.
    monkeypatch.setattr(
        squid.stage.cephla.CephlaStage,
        "_calc_move_timeout",
        staticmethod(lambda distance, max_speed: 0.5),
    )
    cfg = squid.config.get_stage_config()
    stage = make_stage(_ZSoftLimitDeadlockSerial(cfg.Z_AXIS))
    z_min = cfg.Z_AXIS.MIN_POSITION
    # Reproduce the turret startup sequence against firmware-faithful limits.
    stage.move_z_to(0.1)
    stage.move_z_to(0.0)  # pre-fix: TimeoutError; post-fix: clamped to the floor
    assert stage.get_pos().z_mm == pytest.approx(z_min, abs=1e-3)
