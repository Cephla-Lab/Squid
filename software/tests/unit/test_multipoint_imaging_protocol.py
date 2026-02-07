"""Unit tests for FlexibleMultiPointWidget build/apply ImagingProtocol."""

import pytest

from qtpy.QtWidgets import QApplication, QWidget

from squid.core.events import EventBus
from squid.core.protocol.imaging_protocol import (
    ImagingProtocol,
    ZStackConfig,
    FocusConfig,
)
from squid.ui.widgets.acquisition.flexible_multipoint import FlexibleMultiPointWidget


@pytest.fixture(scope="module")
def qapp():
    """Ensure a QApplication exists for the test module."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture(scope="module")
def multipoint_widget(qapp):
    """Create a single FlexibleMultiPointWidget shared across tests."""
    event_bus = EventBus()
    channels = [
        "BF LED matrix full",
        "Fluorescence 405 nm Ex",
        "Fluorescence 488 nm Ex",
    ]
    focus_map = QWidget()

    widget = FlexibleMultiPointWidget(
        focus_map,
        event_bus,
        initial_channel_configs=channels,
    )
    yield widget
    event_bus.stop()


class TestBuildImagingProtocol:
    """Tests for FlexibleMultiPointWidget.build_imaging_protocol()."""

    def test_build_imaging_protocol_basic(self, multipoint_widget):
        """Select 2 channels, set NZ=5, deltaZ=0.5, enable laser AF -> correct protocol."""
        w = multipoint_widget

        # Select channels 0 and 1
        w.list_configurations.item(0).setSelected(True)
        w.list_configurations.item(1).setSelected(True)
        w.list_configurations.item(2).setSelected(False)

        # Set z-stack params
        w.entry_NZ.setValue(5)
        w.entry_deltaZ.setValue(0.5)

        # Enable laser AF (reflection AF)
        w.checkbox_withAutofocus.setChecked(False)
        w.checkbox_withReflectionAutofocus.setChecked(True)

        # Set z-stack direction to "From Center" (index 1)
        w.combobox_z_stack.setCurrentIndex(1)

        protocol = w.build_imaging_protocol()
        assert isinstance(protocol, ImagingProtocol)
        assert protocol.channels == ["BF LED matrix full", "Fluorescence 405 nm Ex"]
        assert protocol.z_stack.planes == 5
        assert protocol.z_stack.step_um == 0.5
        assert protocol.z_stack.direction == "from_center"
        assert protocol.focus.enabled is True
        assert protocol.focus.method == "laser"
        assert protocol.acquisition_order == "channel_first"
        assert protocol.skip_saving is False

    def test_build_imaging_protocol_no_channels_raises(self, multipoint_widget):
        """No channels selected -> ValueError."""
        w = multipoint_widget

        # Deselect all
        for i in range(w.list_configurations.count()):
            w.list_configurations.item(i).setSelected(False)

        with pytest.raises(ValueError, match="channel"):
            w.build_imaging_protocol()

    def test_build_imaging_protocol_z_directions(self, multipoint_widget):
        """Test all 3 z-stack directions map correctly."""
        w = multipoint_widget

        # Select at least one channel
        w.list_configurations.item(0).setSelected(True)

        direction_map = {
            0: "from_bottom",
            1: "from_center",
            2: "from_top",
        }

        for idx, expected_dir in direction_map.items():
            w.combobox_z_stack.setCurrentIndex(idx)
            protocol = w.build_imaging_protocol()
            assert protocol.z_stack.direction == expected_dir, (
                f"Index {idx}: expected {expected_dir}, got {protocol.z_stack.direction}"
            )

    def test_build_imaging_protocol_contrast_af(self, multipoint_widget):
        """Enable contrast AF (not laser) -> focus.method == 'contrast'."""
        w = multipoint_widget

        w.list_configurations.item(0).setSelected(True)
        w.checkbox_withAutofocus.setChecked(True)
        w.checkbox_withReflectionAutofocus.setChecked(False)

        protocol = w.build_imaging_protocol()
        assert protocol.focus.enabled is True
        assert protocol.focus.method == "contrast"

    def test_build_imaging_protocol_no_af(self, multipoint_widget):
        """Both AF checkboxes unchecked -> focus.enabled == False."""
        w = multipoint_widget

        w.list_configurations.item(0).setSelected(True)
        w.checkbox_withAutofocus.setChecked(False)
        w.checkbox_withReflectionAutofocus.setChecked(False)

        protocol = w.build_imaging_protocol()
        assert protocol.focus.enabled is False
        assert protocol.focus.method == "none"

    def test_build_imaging_protocol_af_interval(self, multipoint_widget):
        """Set AF interval spinbox to 5 -> focus.interval_fovs == 5."""
        w = multipoint_widget

        w.list_configurations.item(0).setSelected(True)
        w.checkbox_withReflectionAutofocus.setChecked(True)
        w.spinbox_af_interval.setValue(5)

        protocol = w.build_imaging_protocol()
        assert protocol.focus.interval_fovs == 5

    def test_build_imaging_protocol_skip_saving(self, multipoint_widget):
        """Check skip saving -> skip_saving == True."""
        w = multipoint_widget

        w.list_configurations.item(0).setSelected(True)
        w.checkbox_skipSaving.setChecked(True)

        protocol = w.build_imaging_protocol()
        assert protocol.skip_saving is True

        # Clean up
        w.checkbox_skipSaving.setChecked(False)

    def test_apply_imaging_protocol_sets_controls(self, multipoint_widget):
        """Create a protocol, apply it, verify widget controls match."""
        w = multipoint_widget

        protocol = ImagingProtocol(
            channels=["Fluorescence 405 nm Ex", "Fluorescence 488 nm Ex"],
            z_stack=ZStackConfig(planes=7, step_um=1.5, direction="from_top"),
            acquisition_order="channel_first",
            focus=FocusConfig(enabled=True, method="laser", interval_fovs=4),
            skip_saving=True,
        )

        w.apply_imaging_protocol(protocol)

        # Verify channel selection
        selected = [
            w.list_configurations.item(i).text()
            for i in range(w.list_configurations.count())
            if w.list_configurations.item(i).isSelected()
        ]
        assert set(selected) == {"Fluorescence 405 nm Ex", "Fluorescence 488 nm Ex"}

        # Verify z-stack
        assert w.entry_NZ.value() == 7
        assert w.entry_deltaZ.value() == 1.5
        assert w.combobox_z_stack.currentIndex() == 2  # from_top

        # Verify AF
        assert w.checkbox_withReflectionAutofocus.isChecked() is True
        assert w.checkbox_withAutofocus.isChecked() is False
        assert w.spinbox_af_interval.value() == 4

        # Verify skip saving
        assert w.checkbox_skipSaving.isChecked() is True

    def test_roundtrip_build_apply_build(self, multipoint_widget):
        """Configure -> build -> change -> apply -> build -> assert equal."""
        w = multipoint_widget

        # Configure panel
        w.list_configurations.item(0).setSelected(True)
        w.list_configurations.item(1).setSelected(True)
        w.list_configurations.item(2).setSelected(False)
        w.entry_NZ.setValue(3)
        w.entry_deltaZ.setValue(0.8)
        w.combobox_z_stack.setCurrentIndex(0)  # from_bottom
        w.checkbox_withAutofocus.setChecked(True)
        w.checkbox_withReflectionAutofocus.setChecked(False)
        w.spinbox_af_interval.setValue(2)
        w.checkbox_skipSaving.setChecked(False)

        protocol1 = w.build_imaging_protocol()

        # Change the panel to something different
        w.list_configurations.item(0).setSelected(False)
        w.entry_NZ.setValue(10)
        w.checkbox_skipSaving.setChecked(True)

        # Apply original protocol
        w.apply_imaging_protocol(protocol1)

        protocol2 = w.build_imaging_protocol()

        assert protocol1 == protocol2

    def test_apply_protocol_channels_not_in_list(self, multipoint_widget):
        """Protocol references channels not in widget's list -> silently skipped."""
        w = multipoint_widget

        protocol = ImagingProtocol(
            channels=["Fluorescence 405 nm Ex", "NonExistent Channel"],
            z_stack=ZStackConfig(planes=1, step_um=0.5),
            focus=FocusConfig(enabled=False, method="none"),
        )

        w.apply_imaging_protocol(protocol)

        selected = [
            w.list_configurations.item(i).text()
            for i in range(w.list_configurations.count())
            if w.list_configurations.item(i).isSelected()
        ]
        # Only the channel that exists should be selected
        assert selected == ["Fluorescence 405 nm Ex"]
