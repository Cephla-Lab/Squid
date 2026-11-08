"""Declared use through Squid's lighting layer (IlluminationController.use / LightSource.use), for any light source."""

from unittest.mock import MagicMock

from control.lighting import IlluminationController, IntensityControlMode, LightSourceType, ShutterControlMode
from squid.abc import LightSource, LightSourceHold


class _RecordingLightSource(LightSource):
    """A light source whose channels are the wavelengths' names; records what is declared on it."""

    def __init__(self):
        self.channel_mappings = {488: "blue", 561: "yellow"}
        self.calls = []

    def initialize(self):
        return True

    def set_intensity_control_mode(self, mode):
        pass

    def get_intensity_control_mode(self):
        return IntensityControlMode.Software

    def set_shutter_control_mode(self, mode):
        pass

    def get_shutter_control_mode(self):
        return ShutterControlMode.TTL

    def set_shutter_state(self, channel, on):
        pass

    def get_shutter_state(self, channel):
        return False

    def set_intensity(self, channel, intensity):
        pass

    def get_intensity(self, channel) -> float:
        return 0.0

    def shut_down(self):
        pass

    def use(self, name, channels):
        source = self

        class Hold(LightSourceHold):
            def update(self, channels):
                source.calls.append(("update", name, list(channels)))

            def release(self):
                source.calls.append(("release", name))

            def wait_until_ready(self, timeout_s=None, cancel_fn=lambda: False):
                source.calls.append(("wait", name, timeout_s))
                return False

        self.calls.append(("use", name, list(channels)))
        return Hold()


def _controller(light_source):
    return IlluminationController(
        MagicMock(),
        IntensityControlMode.Software,
        ShutterControlMode.TTL,
        LightSourceType.CephlaLaserEngineV2,
        light_source,
    )


def test_use_maps_wavelengths_to_the_light_sources_channels_and_skips_the_others():
    source = _RecordingLightSource()
    controller = _controller(source)
    with controller.use("live", [561, 730]) as hold:  # 730: no channel on this light source
        hold.update([488])
        assert hold.wait_until_ready(5.0) is False
    assert source.calls == [
        ("use", "live", ["yellow"]),
        ("update", "live", ["blue"]),
        ("wait", "live", 5.0),
        ("release", "live"),
    ]


def test_without_a_light_source_use_does_nothing():
    controller = IlluminationController(MagicMock(), IntensityControlMode.Software, ShutterControlMode.TTL)
    with controller.use("snap", [561]) as hold:
        hold.update([488])
        assert hold.wait_until_ready() is True


def test_a_light_source_does_nothing_on_use_by_default():
    source = _RecordingLightSource()
    hold = LightSource.use(source, "live", ["yellow"])  # the base class's
    assert type(hold) is LightSourceHold and hold.wait_until_ready() is True
    hold.update(["blue"])
    hold.release()
    assert LightSource.mechanical_shutter(source, "yellow") is None
