"""Integration tests for saving/loading ImagingProtocol via ConfigRepository."""

import pytest

from qtpy.QtWidgets import QApplication, QWidget

from squid.core.config.repository import ConfigRepository
from squid.core.events import EventBus
from squid.core.protocol.imaging_protocol import ImagingProtocol
from squid.ui.widgets.acquisition.flexible_multipoint import FlexibleMultiPointWidget


@pytest.fixture(scope="module")
def qapp():
    """Ensure a QApplication exists for the test module."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def config_repo(tmp_path):
    """Create a ConfigRepository with a test profile."""
    repo = ConfigRepository(base_path=tmp_path / "profiles")
    (tmp_path / "profiles" / "machine_configs").mkdir(parents=True)
    repo.create_profile("test_profile")
    repo.set_profile("test_profile")
    return repo


@pytest.fixture(scope="module")
def shared_event_bus():
    """Module-scoped EventBus to avoid Qt segfaults from GC across tests."""
    bus = EventBus()
    yield bus
    bus.stop()


@pytest.fixture
def multipoint_widget(qapp, config_repo, shared_event_bus):
    """Create a FlexibleMultiPointWidget with config_repo."""
    channels = [
        "BF LED matrix full",
        "Fluorescence 405 nm Ex",
        "Fluorescence 488 nm Ex",
    ]
    focus_map = QWidget()

    widget = FlexibleMultiPointWidget(
        focus_map,
        shared_event_bus,
        initial_channel_configs=channels,
        config_repo=config_repo,
    )
    return widget


class TestProtocolSaveLoad:
    """Integration tests for protocol save/load via ConfigRepository."""

    def test_save_and_load_protocol_via_config_repo(self, multipoint_widget, config_repo):
        """Build protocol from widget -> save -> load -> assert equal."""
        w = multipoint_widget

        # Configure widget
        w.list_configurations.item(0).setSelected(True)
        w.list_configurations.item(1).setSelected(True)
        w.list_configurations.item(2).setSelected(False)
        w.entry_NZ.setValue(5)
        w.entry_deltaZ.setValue(0.5)
        w.combobox_z_stack.setCurrentIndex(1)  # from_center
        w.checkbox_withAutofocus.setChecked(False)
        w.checkbox_withReflectionAutofocus.setChecked(True)
        w.spinbox_af_interval.setValue(3)
        w.checkbox_skipSaving.setChecked(False)

        # Build and save
        protocol = w.build_imaging_protocol()
        config_repo.save_imaging_protocol("test_profile", "test_protocol", protocol)

        # Load and compare
        loaded = config_repo.get_imaging_protocol("test_protocol")
        assert loaded is not None
        assert loaded == protocol

    def test_save_protocol_creates_yaml_file(self, multipoint_widget, config_repo):
        """Save protocol -> verify YAML file exists with correct content."""
        w = multipoint_widget

        w.list_configurations.item(0).setSelected(True)
        w.entry_NZ.setValue(3)
        w.entry_deltaZ.setValue(1.0)
        w.checkbox_withAutofocus.setChecked(False)
        w.checkbox_withReflectionAutofocus.setChecked(False)

        protocol = w.build_imaging_protocol()
        config_repo.save_imaging_protocol("test_profile", "my_protocol", protocol)

        # Check file exists
        profile_path = config_repo.get_profile_path("test_profile")
        yaml_path = profile_path / "imaging_protocols" / "my_protocol.yaml"
        assert yaml_path.exists()

        # Verify content by re-loading
        loaded = config_repo.get_imaging_protocol("my_protocol")
        assert loaded is not None
        assert loaded.channels == ["BF LED matrix full"]
        assert loaded.z_stack.planes == 3
        assert loaded.z_stack.step_um == 1.0

    def test_load_protocol_applies_to_widget(self, multipoint_widget, config_repo):
        """Save protocol -> change widget -> load -> verify widget matches original."""
        w = multipoint_widget

        # Configure and build
        w.list_configurations.item(0).setSelected(False)
        w.list_configurations.item(1).setSelected(True)
        w.list_configurations.item(2).setSelected(True)
        w.entry_NZ.setValue(10)
        w.entry_deltaZ.setValue(2.0)
        w.combobox_z_stack.setCurrentIndex(2)  # from_top
        w.checkbox_withAutofocus.setChecked(True)
        w.checkbox_withReflectionAutofocus.setChecked(False)
        w.spinbox_af_interval.setValue(7)
        w.checkbox_skipSaving.setChecked(True)

        original_protocol = w.build_imaging_protocol()
        config_repo.save_imaging_protocol("test_profile", "roundtrip_test", original_protocol)

        # Change widget state to something different
        w.list_configurations.item(0).setSelected(True)
        w.list_configurations.item(1).setSelected(False)
        w.list_configurations.item(2).setSelected(False)
        w.entry_NZ.setValue(1)
        w.checkbox_skipSaving.setChecked(False)

        # Load and apply
        loaded = config_repo.get_imaging_protocol("roundtrip_test")
        assert loaded is not None
        w.apply_imaging_protocol(loaded)

        # Verify widget state matches original
        rebuilt = w.build_imaging_protocol()
        assert rebuilt == original_protocol
