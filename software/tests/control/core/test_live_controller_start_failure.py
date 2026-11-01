"""A start_live() that fails partway leaves the controller as stop_live() would: not live, the live channel released.

Before, a camera that would not stream left is_live True and the live channel held: live view showed nothing,
snap() did nothing (it ignores a call while live), and the light source stayed held until live was stopped.
"""

from unittest.mock import MagicMock, patch

import pytest

import control.microscope
import tests.control.test_stubs as ts


@pytest.fixture
def live():
    scope = control.microscope.Microscope.build_from_global_config(True)
    controller = ts.get_test_live_controller(scope, scope.objective_store.default_objective)
    yield controller
    controller.stop_live()
    scope.close()


def _holds_made_by(live, release_error=None):
    """The light-source holds use() hands out from now on (the live channel takes one)."""
    holds = []

    def use(*args, **kwargs):
        hold = MagicMock()
        if release_error is not None:
            hold.release.side_effect = release_error
        holds.append(hold)
        return hold

    return holds, patch.object(live.microscope.illumination_controller, "use", side_effect=use)


def test_a_camera_that_will_not_stream_leaves_live_off_and_the_channel_released(live):
    holds, holds_patch = _holds_made_by(live)
    with holds_patch, patch.object(live.camera, "start_streaming", side_effect=RuntimeError("camera gone")):
        with pytest.raises(RuntimeError, match="camera gone"):
            live.start_live()

    assert live.is_live is False
    assert [hold.release.call_count for hold in holds] == [1]


def test_snap_works_after_live_failed_to_start(live):
    with patch.object(live.camera, "start_streaming", side_effect=RuntimeError("camera gone")):
        with pytest.raises(RuntimeError):
            live.start_live()

    assert live.snap() is True  # not ignored as "live is running"


def test_a_failed_undo_does_not_hide_why_live_would_not_start(live, caplog):
    holds, holds_patch = _holds_made_by(live, release_error=RuntimeError("light source gone"))
    with holds_patch, patch.object(live.camera, "start_streaming", side_effect=RuntimeError("camera gone")):
        with pytest.raises(RuntimeError, match="camera gone"):
            live.start_live()

    assert live.is_live is False
    assert "light source gone" in caplog.text
