"""Regression tests for illumination config editing.

Edits saved from the illumination dialogs must be visible to the running app
(single shared ConfigRepository), edits must not leak into shared state before
Save (dialogs edit a deep copy), and the laser TTL mapping must follow
port-mapping saves without reconstructing the IlluminationController.
"""

import pytest

import tests.control.gui_test_stubs  # noqa: F401 - ensures GUI modules import cleanly
import control.microscope
import control.widgets
from control.core.config import ConfigRepository
from control.lighting import IlluminationController
from tests.tools import get_test_microcontroller

ILLUMINATION_YAML = """\
version: 1
controller_port_mapping:
  D1: 11
  D2: 12
  USB1: 0
channels:
  - name: BF LED matrix full
    type: transillumination
    controller_port: USB1
    wavelength_nm: null
    max_output: 0.2
  - name: Fluorescence 405 nm Ex
    type: epi_illumination
    controller_port: D1
    wavelength_nm: 405
"""


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "machine_configs").mkdir()
    (tmp_path / "machine_configs" / "illumination_channel_config.yaml").write_text(ILLUMINATION_YAML)
    return ConfigRepository(base_path=tmp_path)


@pytest.fixture
def confirm_yes(monkeypatch):
    """Auto-answer Yes to QMessageBox.question confirmation prompts."""
    monkeypatch.setattr(
        control.widgets.QMessageBox, "question", lambda *args, **kwargs: control.widgets.QMessageBox.Yes
    )


def _set_port_mapping_in_dialog(dialog, port, source_code):
    for row in range(dialog.table.rowCount()):
        if dialog.table.item(row, 0).text() == port:
            widget = dialog.table.cellWidget(row, 1)
            assert isinstance(widget, control.widgets.SourceCodeWidget)
            widget.set_source_code(source_code)
            return
    raise AssertionError(f"Port {port} not found in dialog table")


def test_port_mapping_dialog_save_updates_shared_repo_cache(qtbot, repo, confirm_yes):
    """Saving the port mapping dialog must update the repo other readers share."""
    dialog = control.widgets.ControllerPortMappingDialog(repo)
    qtbot.addWidget(dialog)
    _set_port_mapping_in_dialog(dialog, "D1", 16)

    dialog._save_changes()

    assert repo.get_illumination_config().controller_port_mapping["D1"] == 16


def test_port_mapping_save_keeps_channels_saved_since_dialog_opened(qtbot, repo, confirm_yes):
    """The mapping save is partial: it must not clobber channel edits saved after the dialog loaded."""
    dialog = control.widgets.ControllerPortMappingDialog(repo)
    qtbot.addWidget(dialog)
    _set_port_mapping_in_dialog(dialog, "D1", 16)

    # Another editor saves a channel change while the port dialog is open
    concurrent = repo.get_illumination_config(for_edit=True)
    del concurrent.channels[0]
    repo.save_illumination_config(concurrent)

    dialog._save_changes()

    saved = repo.get_illumination_config()
    assert saved.controller_port_mapping["D1"] == 16
    assert len(saved.channels) == 1, "Port-mapping save must not restore the removed channel"


def test_configurator_cancel_leaves_shared_config_unchanged(qtbot, repo, confirm_yes):
    """Removing a channel then cancelling must not mutate the shared cached config."""
    shared_before = repo.get_illumination_config()
    assert len(shared_before.channels) == 2

    dialog = control.widgets.IlluminationChannelConfiguratorDialog(repo)
    qtbot.addWidget(dialog)
    dialog.table.selectRow(0)
    dialog._remove_channel()
    dialog.reject()

    shared_after = repo.get_illumination_config()
    assert len(shared_after.channels) == 2, "Cancel must leave the shared config untouched"


def test_configurator_save_publishes_edits_to_shared_repo(qtbot, repo, confirm_yes):
    """Removing a channel and saving must update the shared cache and the YAML."""
    dialog = control.widgets.IlluminationChannelConfiguratorDialog(repo)
    qtbot.addWidget(dialog)
    dialog.table.selectRow(0)
    dialog._remove_channel()
    dialog._save_changes()

    shared = repo.get_illumination_config()
    assert len(shared.channels) == 1
    assert shared.channels[0].name == "Fluorescence 405 nm Ex"

    fresh = ConfigRepository(base_path=repo.base_path).get_illumination_config()
    assert len(fresh.channels) == 1


def test_ttl_mapping_reflects_port_mapping_saved_after_construction(repo):
    """channel_mappings_TTL must follow port-mapping saves without reconstruction."""
    micro = get_test_microcontroller()
    controller = IlluminationController(micro, config_repo=repo)
    assert controller.channel_mappings_TTL[405] == 11

    updated = repo.get_illumination_config(for_edit=True)
    updated.controller_port_mapping["D1"] = 16
    repo.save_illumination_config(updated)

    assert controller.channel_mappings_TTL[405] == 16


def test_configurator_shows_and_saves_max_output(qtbot, repo, confirm_yes):
    """The configurator must show each channel's max_output and persist edits."""
    dialog = control.widgets.IlluminationChannelConfiguratorDialog(repo)
    qtbot.addWidget(dialog)

    led_spin = dialog.table.cellWidget(0, dialog.COL_MAX_OUTPUT)
    laser_spin = dialog.table.cellWidget(1, dialog.COL_MAX_OUTPUT)
    assert led_spin.value() == pytest.approx(0.2)
    assert laser_spin.value() == pytest.approx(1.0)

    laser_spin.setValue(0.5)
    dialog._save_changes()

    saved = repo.get_illumination_config()
    assert saved.channels[1].max_output == pytest.approx(0.5)


def test_add_channel_dialog_defaults_max_output_by_type(qtbot, repo):
    """New channels default to 1.0 max output for epi, 0.2 for transillumination."""
    config = repo.get_illumination_config()
    dialog = control.widgets.AddIlluminationChannelDialog(config)
    qtbot.addWidget(dialog)

    dialog.type_combo.setCurrentText("epi_illumination")
    assert dialog.get_channel_data()["max_output"] == pytest.approx(1.0)

    dialog.type_combo.setCurrentText("transillumination")
    assert dialog.get_channel_data()["max_output"] == pytest.approx(0.2)


def test_simulated_microscope_shares_config_repo_with_illumination_controller():
    """The microscope and its illumination controller must use the same repository,
    including after config_repo is reassigned (the setter propagates)."""
    scope = control.microscope.Microscope.build_from_global_config(True)
    try:
        assert scope.illumination_controller.config_repo is scope.config_repo

        replacement = ConfigRepository()
        scope.config_repo = replacement
        assert scope.illumination_controller.config_repo is replacement
    finally:
        scope.close()
