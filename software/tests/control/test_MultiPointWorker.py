import control.core.core
from control._def import *

import tests.control.gui_test_stubs as gts


# Make sure we can create a multi point controller and worker with out default config
def test_multi_point_worker_with_default_config(qtbot):
    multi_point_controller = gts.get_test_multi_point_controller()
    multi_point_controller.run_acquisition()
    multi_point_controller.request_abort_aquisition()


def test_multi_point_worker_init_bugs(qtbot):
    # We don't always init all our fields in __init__, which leads to some paths
    # for some configs whereby we use instance attributes before initialization.  This
    # test documents cases of those by writing tests that hit them (then subsequent PR that
    # fix them to make this test pass).

    # The init_napari_layers field is dependent on USE_NAPARI_FOR_MULTIPOINT,
    # so make sure that it is initialized regardless of that config value.

    USE_NAPARI_FOR_MULTIPOINT = False
    multi_point_controller_for_false = gts.get_test_multi_point_controller()
    multi_point_controller_for_false.run_acquisition()
    multi_point_controller_for_false.request_abort_aquisition()
    # This will throw if the attribute doesn't exist
    napari_layer_for_false = multi_point_controller_for_false.multiPointWorker.init_napari_layers

    USE_NAPARI_FOR_MULTIPOINT = True
    multi_point_controller_for_true = gts.get_test_multi_point_controller()
    multi_point_controller_for_true.run_acquisition()
    multi_point_controller_for_true.request_abort_aquisition()
    # This will throw if the attribute doesn't exist
    napari_layer_for_true = multi_point_controller_for_true.multiPointWorker.init_napari_layers
