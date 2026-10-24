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


@pytest.fixture
def initialized_display_tab(qtbot, tmp_path, monkeypatch):
    """Build a FluidicsDisplayTab over a simulated system initialized from the given config text."""
    pytest.importorskip("fluidics")
    from control.fluidics_system import FluidicsService
    from control.widgets_fluidics.display_tab import FluidicsDisplayTab
    import control.widgets_fluidics.system_panel as system_panel_module
    from control.widgets_fluidics.system_panel import SystemPanel

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(SystemPanel, "_INITIALIZE_KWARGS", {"instant": True})
    # a failed Initialize raises a modal, which would hang the offscreen run; fail the test instead
    monkeypatch.setattr(system_panel_module.QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    built = []

    def build(config_text, shown_at=None):
        config = tmp_path / "fluidics_config.yaml"
        config.write_text(config_text)
        service = FluidicsService(default_config_path=str(config), simulated=True)
        tab = FluidicsDisplayTab(service)
        qtbot.addWidget(tab)
        built.append((tab, service))
        if shown_at is not None:  # the application's order: on screen first, Initialize later
            tab.resize(*shown_at)
            tab.show()
            qtbot.waitExposed(tab)
        outcome = []
        tab.system_ready.connect(lambda: outcome.append(None))
        tab.system_panel.initialize_failed.connect(outcome.append)
        tab.system_panel.initialize_button.click()
        qtbot.waitUntil(lambda: bool(outcome), timeout=15000)
        assert outcome == [None], f"Initialize failed: {outcome[0]}"
        return tab

    yield build
    for tab, service in built:
        tab.shutdown()
        assert service.close() == []
