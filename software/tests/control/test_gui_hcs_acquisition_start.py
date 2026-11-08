"""The acquisition-start handler in gui_hcs must size the display tabs from the run's parameters."""

from unittest.mock import MagicMock

import pytest

from control.core.multi_point_utils import AcquisitionParameters, ScanPositionInformation
from control.gui_hcs import QtMultiPointController


class _StopHere(Exception):
    pass


def test_display_tabs_get_the_derived_nz():
    # The controller's NZ field says 3, but the run's parameters carry the Nz derived from a relative range.
    ctrl = QtMultiPointController.__new__(QtMultiPointController)
    ctrl.NZ = 3
    ctrl.selected_configurations = ["BF"]
    ctrl.xy_mode = "Select Wells"
    ctrl.run_acquisition_current_fov = False
    ctrl.signal_set_display_tabs = MagicMock()
    ctrl.signal_acquisition_start = MagicMock()
    ctrl.signal_acquisition_start.emit.side_effect = _StopHere  # nothing past the display-tab emit is under test

    params = AcquisitionParameters(
        experiment_ID=None,
        base_path=None,
        selected_configurations=[],
        acquisition_start_time=0.0,
        scan_position_information=ScanPositionInformation(
            scan_region_coords_mm=[], scan_region_names=[], scan_region_fov_coords_mm={}
        ),
        NX=1,
        deltaX=0,
        NY=1,
        deltaY=0,
        NZ=11,
        deltaZ=0.002,
        Nt=1,
        deltat=0,
        do_autofocus=False,
        do_reflection_autofocus=False,
        use_piezo=True,
        display_resolution_scaling=1.0,
        z_stacking_config="FROM BOTTOM",
        z_range=(0.0, 0.0),
        laser_af_relative_range_um=(-5.0, 15.0),
    )
    with pytest.raises(_StopHere):
        ctrl._signal_acquisition_start_fn(params)

    ctrl.signal_set_display_tabs.emit.assert_called_once_with(["BF"], 11, "Select Wells")
