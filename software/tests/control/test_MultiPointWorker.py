import tests.control.gui_test_stubs as gts
import pytest

import control.microscope


# Make sure we can create a multi point controller and worker with out default config
@pytest.mark.skip(
    "This fails because of the QApplicateion.processEvents() in _on_acqusition_complete.  Not sure why we need that."
)
def test_multi_point_worker_with_default_config(qtbot):
    scope = control.microscope.Microscope.build_from_global_config(True)
    multi_point_controller = gts.get_test_qt_multi_point_controller(microscope=scope)
    multi_point_controller.run_acquisition()
    multi_point_controller.request_abort_aquisition()
    scope.close()


@pytest.mark.skip(
    "This fails because of the QApplicateion.processEvents() in _on_acqusition_complete.  Not sure why we need that."
)
def test_multi_point_worker_init_bugs(qtbot):
    # We don't always init all our fields in __init__, which leads to some paths
    # for some configs whereby we use instance attributes before initialization.  This
    # test documents cases of those by writing tests that hit them (then subsequent PR that
    # fix them to make this test pass).

    # The init_napari_layers field is dependent on USE_NAPARI_FOR_MULTIPOINT,
    # so make sure that it is initialized regardless of that config value.

    USE_NAPARI_FOR_MULTIPOINT = False
    scope_false = control.microscope.Microscope.build_from_global_config(True)
    multi_point_controller_for_false = gts.get_test_qt_multi_point_controller(microscope=scope_false)
    multi_point_controller_for_false.run_acquisition()
    multi_point_controller_for_false.request_abort_aquisition()
    # This will throw if the attribute doesn't exist
    napari_layer_for_false = multi_point_controller_for_false.multiPointWorker.init_napari_layers

    USE_NAPARI_FOR_MULTIPOINT = True
    scope_true = control.microscope.Microscope.build_from_global_config(True)
    multi_point_controller_for_true = gts.get_test_qt_multi_point_controller(microscope=scope_true)
    multi_point_controller_for_true.run_acquisition()
    multi_point_controller_for_true.request_abort_aquisition()
    # This will throw if the attribute doesn't exist
    napari_layer_for_true = multi_point_controller_for_true.multiPointWorker.init_napari_layers


@pytest.mark.parametrize("use_pi, expected_s", [(False, 0.02), (True, 0.0)], ids=["stepper", "v308"])
def test_z_stack_moves_pause_for_the_stage_specific_stabilization_time(monkeypatch, use_pi, expected_s):
    # The V-308's blocking move already waits for on-target inside its window, so its Z-stack steps
    # must not add the stepper's fixed 20 ms pause; the stepper keeps it.
    import types
    import unittest.mock

    import control._def
    from control.core.multi_point_worker import MultiPointWorker

    monkeypatch.setattr(control._def, "USE_PI_FOCUS_STAGE", use_pi)
    monkeypatch.setattr(control._def, "PI_FOCUS_ON_TARGET_TOL_MIN_UM", 0.1)
    monkeypatch.setattr(control._def, "SCAN_STABILIZATION_TIME_MS_Z", 20)
    monkeypatch.setattr(control._def, "PI_FOCUS_SCAN_STABILIZATION_TIME_MS_Z", 0)
    worker = types.SimpleNamespace(
        use_piezo=False,
        stage=unittest.mock.MagicMock(),
        deltaZ=0.0015,
        NZ=3,
        z_stacking_config="FROM CENTER",
        _log=unittest.mock.MagicMock(),
    )
    sleeps = []
    worker._sleep = sleeps.append

    MultiPointWorker.move_z_for_stack(worker)
    MultiPointWorker.prepare_z_stack(worker)
    MultiPointWorker.move_to_z_level(worker, 1.0)

    assert sleeps == pytest.approx([expected_s] * 4)
