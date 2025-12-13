from __future__ import annotations

from dataclasses import dataclass

from squid.core.events import EventBus, SetIlluminationCommand
from squid.mcs.services.illumination_service import IlluminationService


class FakeLegacyIlluminationController:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, float | None]] = []

    def set_intensity(self, channel: int, intensity: float) -> None:
        self.calls.append(("set_intensity", channel, intensity))

    def turn_on_illumination(self, channel: int | None = None) -> None:
        assert channel is not None
        self.calls.append(("turn_on_illumination", channel, None))

    def turn_off_illumination(self, channel: int | None = None) -> None:
        assert channel is not None
        self.calls.append(("turn_off_illumination", channel, None))


def test_illumination_service_accepts_legacy_controller_for_any_channel() -> None:
    bus = EventBus()
    bus.start()
    controller = FakeLegacyIlluminationController()
    svc = IlluminationService(controller, bus)

    # Command path
    bus.publish(SetIlluminationCommand(channel=12, intensity=33.0, on=True))

    bus.drain(timeout_s=1.0)
    bus.stop(timeout_s=1.0)

    assert ("set_intensity", 12, 33.0) in controller.calls
    assert ("turn_on_illumination", 12, None) in controller.calls
