"""Tests for AcquisitionYAMLDropMixin._restore_channel_settings.

Dropping a previous acquisition onto a multipoint widget must restore the
per-channel settings (exposure, analog gain, illumination intensity) recorded
in its acquisition.yaml, persisting them through ConfigRepository the same way
live-control edits do. Channels missing from the current configuration and
out-of-range values are skipped: update_channel_setting bypasses pydantic
assignment validation, so persisting a bad value would corrupt the profile.
Z-offset is sample-dependent and must never be restored.
"""

from unittest.mock import MagicMock, call

from control.acquisition_yaml_loader import AcquisitionYAMLData, ChannelYAMLSettings
from control.widgets import AcquisitionYAMLDropMixin


class _Stub(AcquisitionYAMLDropMixin):
    """Minimal host for the mixin with mocked controller/repo plumbing."""

    def __init__(self, known_channels, confocal_mode=False):
        self._log = MagicMock()
        self.objectiveStore = MagicMock()
        self.objectiveStore.current_objective = "20x"
        self.multipointController = MagicMock()
        live = self.multipointController.liveController
        live.is_confocal_mode.return_value = confocal_mode
        live.get_channel_by_name.side_effect = lambda objective, name: (
            MagicMock() if name in known_channels else None
        )
        self.repo = live.microscope.config_repo
        self.repo.update_channel_setting.return_value = True


def _yaml_data(channel_settings):
    return AcquisitionYAMLData(widget_type="wellplate", channel_settings=channel_settings)


def test_restores_all_settings_for_known_channel():
    stub = _Stub(known_channels={"BF"})
    data = _yaml_data(
        [ChannelYAMLSettings(name="BF", exposure_time_ms=12.5, analog_gain=2.0, illumination_intensity=30.0)]
    )

    assert stub._restore_channel_settings(data) == 1
    stub.repo.update_channel_setting.assert_has_calls(
        [
            call("20x", "BF", "ExposureTime", 12.5, confocal_mode=False),
            call("20x", "BF", "AnalogGain", 2.0, confocal_mode=False),
            call("20x", "BF", "IlluminationIntensity", 30.0, confocal_mode=False),
        ]
    )


def test_passes_current_confocal_mode():
    stub = _Stub(known_channels={"BF"}, confocal_mode=True)
    data = _yaml_data([ChannelYAMLSettings(name="BF", exposure_time_ms=10.0)])

    stub._restore_channel_settings(data)
    stub.repo.update_channel_setting.assert_called_once_with("20x", "BF", "ExposureTime", 10.0, confocal_mode=True)


def test_skips_channel_missing_from_current_configuration():
    stub = _Stub(known_channels={"BF"})
    data = _yaml_data(
        [
            ChannelYAMLSettings(name="Removed Channel", exposure_time_ms=50.0),
            ChannelYAMLSettings(name="BF", exposure_time_ms=12.0),
        ]
    )

    assert stub._restore_channel_settings(data) == 1
    updated_channels = {c.args[1] for c in stub.repo.update_channel_setting.call_args_list}
    assert updated_channels == {"BF"}


def test_skips_none_values_from_older_yamls():
    stub = _Stub(known_channels={"BF"})
    data = _yaml_data([ChannelYAMLSettings(name="BF")])  # name-only channel entry

    assert stub._restore_channel_settings(data) == 0
    stub.repo.update_channel_setting.assert_not_called()


def test_skips_out_of_range_values():
    stub = _Stub(known_channels={"BF"})
    data = _yaml_data(
        [ChannelYAMLSettings(name="BF", exposure_time_ms=0.0, analog_gain=-1.0, illumination_intensity=150.0)]
    )

    assert stub._restore_channel_settings(data) == 0
    stub.repo.update_channel_setting.assert_not_called()


def test_repo_failure_does_not_count_channel():
    stub = _Stub(known_channels={"BF"})
    stub.repo.update_channel_setting.return_value = False
    data = _yaml_data([ChannelYAMLSettings(name="BF", exposure_time_ms=12.0)])

    assert stub._restore_channel_settings(data) == 0


def test_emits_signal_only_when_settings_restored():
    stub = _Stub(known_channels={"BF"})
    stub.signal_channel_settings_restored = MagicMock()
    data = _yaml_data([ChannelYAMLSettings(name="BF", exposure_time_ms=12.0)])

    stub._restore_channel_settings(data)
    stub.signal_channel_settings_restored.emit.assert_called_once()

    stub.signal_channel_settings_restored.emit.reset_mock()
    stub._restore_channel_settings(_yaml_data([ChannelYAMLSettings(name="BF")]))
    stub.signal_channel_settings_restored.emit.assert_not_called()


def test_no_signal_attribute_is_tolerated():
    stub = _Stub(known_channels={"BF"})  # no signal_channel_settings_restored defined
    data = _yaml_data([ChannelYAMLSettings(name="BF", exposure_time_ms=12.0)])

    assert stub._restore_channel_settings(data) == 1
