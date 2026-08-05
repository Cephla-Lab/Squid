import control.microscope
import tests.control.test_stubs as ts
from squid.abc import CameraError


def test_trigger_acquisition_returns_false_on_transient_camera_error():
    """A CameraError from send_trigger (e.g. streaming paused by a concurrent
    sensor mode / ROI / pixel format change) must not propagate out of
    trigger_acquisition: the trigger timer runs on its own thread, and an
    uncaught exception there kills the live trigger loop. It should report
    failure so the timer's retry path handles it.
    """
    scope = control.microscope.Microscope.build_from_global_config(True)
    try:
        live = ts.get_test_live_controller(microscope=scope, starting_objective=scope.objective_store.default_objective)

        def failing_send_trigger(*args, **kwargs):
            raise CameraError("Camera is not streaming, cannot send trigger.")

        scope.camera.send_trigger = failing_send_trigger

        assert live.trigger_acquisition() is False
    finally:
        scope.close()
