import tempfile
import time

from PyQt5.QtWidgets import QApplication

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

    temp_dir = tempfile.mkdtemp()

    without_laser_autofocus.multipointController.set_base_path(temp_dir)
    without_laser_autofocus.multipointController.start_new_experiment("test_experiment_no_laser_af")
    without_laser_autofocus.multipointController.set_selected_configurations(["BF LED matrix full"])
    without_laser_autofocus.scanCoordinates.add_region("current", 3, 3, 3, 10, shape="Circle")
    # without_laser_autofocus.scanCoordinates.add_manual_region([(1, 1), (1, 2), (2, 1)], 10)
    # TODO(imo): The without_laser_autofocus.multipointController.acquisition_in_progress() check does
    # not work.  I verified that it never returns False after an acquisition starts, even after
    # the run() of the MultiPointWorker returns.  Needs to be figured out, but just do this to
    # have some test that works!
    acquisition_finished = False

    def acquisition_complete():
        nonlocal acquisition_finished
        acquisition_finished = True

    without_laser_autofocus.multipointController.acquisitionFinished.connect(acquisition_complete)

    print("calling run_acquisitions...")
    without_laser_autofocus.multipointController.run_acquisition()
    print("Called, polling...")

    timeout_time = time.time() + 10
    while time.time() < timeout_time:
        # This is bad practice, but how we have things setup (either in these tests, or the application itself)
        # means we need it for the eventual acquisitionFinished signal to make it to our callback.
        QApplication.processEvents()

        # TODO(imo): acquisition_in_progress doesn't return False, even when
        if not acquisition_finished:
            time.sleep(0.1)
        else:
            break

    assert acquisition_finished
