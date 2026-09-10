import pytest

import control._def
import control.microscope


@pytest.fixture
def qt_controller(qtbot):
    """A simulated microscope + QtMultiPointController, shared by the imaging-path tests."""
    control._def.MERGE_CHANNELS = False
    from tests.control.gui_test_stubs import get_test_qt_multi_point_controller

    scope = control.microscope.Microscope.build_from_global_config(True)
    controller = get_test_qt_multi_point_controller(scope)
    yield scope, controller
    controller.close()
    scope.close()
