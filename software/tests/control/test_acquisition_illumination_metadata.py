"""acquisition parameters.json records what each channel's intensity % meant (design §9)."""

import json
import os

import tests.control.gui_test_stubs as gts
import control.microscope


def test_acquisition_parameters_record_the_intensity_unit():
    scope = control.microscope.Microscope.build_from_global_config(True)
    try:
        mpc = gts.get_test_qt_multi_point_controller(microscope=scope)
        channels = mpc.liveController.get_channels(mpc.objectiveStore.current_objective)
        names = [channel.name for channel in channels[:2]]
        mpc.set_selected_configurations(names)
        mpc.start_new_experiment("illumination metadata")

        with open(os.path.join(mpc.base_path, mpc.experiment_ID, "acquisition parameters.json")) as f:
            illumination = json.load(f)["illumination"]
        assert sorted(illumination) == sorted(names)
        for channel in mpc.selected_configurations:
            assert illumination[channel.name] == mpc.liveController.get_intensity_description(channel)
            assert illumination[channel.name]["intensity_unit"] in ("dac_percent", "power_percent", "source_percent")
    finally:
        scope.close()
