import time

import control._def

import control.gui_hcs


def test_create_simulated_hcs_with_or_without_piezo(qtbot):
    # This just tests to make sure we can successfully create a simulated hcs gui with or without
    # the piezo objective.
    control._def.HAS_OBJECTIVE_PIEZO = True
    with_piezo = control.gui_hcs.HighContentScreeningGui(is_simulation=True)
    qtbot.add_widget(with_piezo)

    control._def.HAS_OBJECTIVE_PIEZO = False
    without_piezo = control.gui_hcs.HighContentScreeningGui(is_simulation=True)
    qtbot.add_widget(without_piezo)


def test_create_and_acquire_with_or_without_laser_af(qtbot):
    control._def.SUPPORT_LASER_AUTOFOCUS = False
    without_laser_autofocus = control.gui_hcs.HighContentScreeningGui(is_simulation=True)
    qtbot.add_widget(without_laser_autofocus)

    without_laser_autofocus.scanCoordinates.add_region("A1", 1, 2, 1, 10, shape="Square")
    without_laser_autofocus.multipointController.set_selected_configurations("BF LED matrix full")
    without_laser_autofocus.multipointController.run_acquisition()

    timeout_time = time.time() + 5
    while time.time() < timeout_time:
        if not without_laser_autofocus.multipointController.acquisition_in_progress():
            time.sleep(0.1)

    assert not without_laser_autofocus.multipointController.acquisition_in_progress()
