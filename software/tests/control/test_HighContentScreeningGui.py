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
