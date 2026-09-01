"""Light wiring checks for the Fluidics tabs in the HCS GUI.

The full-GUI test (test_HighContentScreeningGui.py) stays out of CI, so these assert the
integration points at the source level instead of instantiating the window.
"""

import inspect

import control.gui_hcs as gui_hcs

GUI = gui_hcs.HighContentScreeningGui


def _source(name):
    return inspect.getsource(getattr(GUI, name))


def test_the_fluidics_tab_name_and_hooks_exist():
    assert GUI.FLUIDICS_TAB_NAME == "Fluidics"
    for hook in (
        "_setup_fluidics_widgets",
        "_set_fluidics_protocol_active",
        "_handle_fluidics_notification",
        "_fluidics_run_line",
    ):
        assert callable(getattr(GUI, hook))


def test_load_widgets_builds_the_fluidics_widgets_when_fluidics_is_present():
    src = _source("loadWidgets" if hasattr(GUI, "loadWidgets") else "load_widgets")
    assert "self._setup_fluidics_widgets()" in src
    assert src.index("_setup_fluidics_widgets") < src.index("setupRecordTabWidget")


def test_the_display_and_record_tabs_are_added():
    assert "self.imageDisplayTabs.addTab(self.fluidicsDisplayTab, self.FLUIDICS_TAB_NAME)" in _source(
        "_setup_fluidics_widgets"
    )
    assert 'self.recordTabWidget.addTab(self.fluidicsProtocolWidget, "Fluidics Protocol")' in _source(
        "setupRecordTabWidget"
    )


def test_napari_toggle_exempts_the_fluidics_tab():
    assert "self.FLUIDICS_TAB_NAME" in _source("toggleNapariTabs")


def test_signals_are_connected_and_the_imaging_port_feeds_napari():
    src = _source("make_connections")
    assert "signal_acquisition_started.connect(self.toggleAcquisitionStart)" in src
    assert "signal_protocol_active.connect(self._set_fluidics_protocol_active)" in src
    assert "signal_run_notification.connect(self._handle_fluidics_notification)" in src
    assert "signal_reagent_rows.connect(self.fluidicsDisplayTab.reagents_table.set_rows)" in src
    assert "signal_step_label.connect(self.fluidicsDisplayTab.recorder.set_step_label)" in src
    napari_src = _source("makeNapariConnections")
    assert "self.qtImagingPort.signal_acquisition_channels" in napari_src
    assert "self.qtImagingPort.signal_acquisition_shape" in napari_src


def test_a_running_protocol_freezes_tab_and_region_behavior():
    assert "_fluidics_protocol_active" in _source("setAcquisitionDisplayTabs").split("performance_mode")[0]
    tab_src = _source("onTabChanged")
    assert "self.recordTabWidget.indexOf(" in tab_src
    assert tab_src.index("fluidicsProtocolWidget") < tab_src.index("is_flexible_acquisition")


def test_close_and_show_events_cover_the_protocol_lifecycle():
    close_src = _source("closeEvent")
    assert "is_run_active()" in close_src and "end_run_for_exit(15)" in close_src
    assert close_src.index("is_run_active") < close_src.index("Confirm Exit")
    # Ending the run is irreversible: it must come only after exit is confirmed.
    assert close_src.index("event.ignore()") < close_src.index("end_run_for_exit")
    restart_src = _source("restart_application")
    assert "is_run_active()" in restart_src and "end_run_for_exit(15)" in restart_src
    assert "offer_recovery(startup=True)" in _source("showEvent")
    assert "disconnect_logging" in _source("_cleanup_common")
