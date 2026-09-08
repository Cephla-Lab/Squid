"""After a Z homing the stage is parked at the Z soft floor when the machine asks for it.

Stages whose actuator homes below the stage's stop have a gap above home where the stage does not follow
(0.64 mm on the second bench controller). The policy is: the floor (SOFTWARE_POS_LIMIT.Z_NEGATIVE, i.e.
Z_AXIS.MIN_POSITION) applies to every move, homing alone may go below it, and after a homing Z is brought
to the floor so every later move starts from a coupled position.
"""

from unittest.mock import MagicMock, patch

import control._def as _def
import squid.config
from squid.stage.cephla import CephlaStage


def _stage():
    mc = MagicMock()
    mc.firmware_version = (1, 6)
    mc.get_pos.return_value = (0, 0, 0, 0)
    cfg = squid.config.get_stage_config()
    return CephlaStage(mc, cfg), mc, cfg


def test_homing_z_parks_at_the_floor_when_enabled():
    stage, mc, cfg = _stage()
    with patch.object(_def, "Z_PARK_AT_MIN_AFTER_HOMING", True):
        stage.home(x=False, y=False, z=True, theta=False, blocking=True)
    mc.home_z.assert_called_once()
    expected = cfg.Z_AXIS.convert_real_units_to_ustep(cfg.Z_AXIS.MIN_POSITION)
    targets = [c.args[0] for c in mc.move_z_to_usteps.call_args_list]
    assert expected in targets, f"park target {expected} not among MOVETO_Z targets {targets}"
    # the park is the last Z command, after the homing wait
    names = [c[0] for c in mc.method_calls if c[0] in ("home_z", "move_z_to_usteps")]
    assert names[0] == "home_z" and names[-1] == "move_z_to_usteps"


def test_homing_z_does_not_park_by_default():
    stage, mc, _ = _stage()
    with patch.object(_def, "Z_PARK_AT_MIN_AFTER_HOMING", False):
        stage.home(x=False, y=False, z=True, theta=False, blocking=True)
    mc.home_z.assert_called_once()
    mc.move_z_to_usteps.assert_not_called()


def test_homing_xy_only_never_parks_z():
    stage, mc, _ = _stage()
    with patch.object(_def, "Z_PARK_AT_MIN_AFTER_HOMING", True):
        stage.home(x=True, y=True, z=False, theta=False, blocking=True)
    mc.home_z.assert_not_called()
    mc.move_z_to_usteps.assert_not_called()


def test_non_blocking_homing_skips_the_park():
    stage, mc, _ = _stage()
    with patch.object(_def, "Z_PARK_AT_MIN_AFTER_HOMING", True):
        stage.home(x=False, y=False, z=True, theta=False, blocking=False)
    mc.home_z.assert_called_once()
    mc.move_z_to_usteps.assert_not_called()
