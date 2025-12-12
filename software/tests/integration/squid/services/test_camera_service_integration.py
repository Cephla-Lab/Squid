"""Integration tests for CameraService with simulated camera."""

import pytest

from squid.core.events import (
    EventBus,
    ExposureTimeChanged,
    SetExposureTimeCommand,
    BinningChanged,
    ROIChanged,
    PixelFormatChanged,
)
from squid.mcs.services import CameraService
from squid.core.config import CameraPixelFormat


@pytest.mark.integration
def test_set_exposure_publishes_and_updates_camera(simulated_camera):
    bus = EventBus()
    service = CameraService(simulated_camera, bus)

    received = []
    bus.subscribe(ExposureTimeChanged, lambda e: received.append(e))

    service.set_exposure_time(50.0)

    assert simulated_camera.get_exposure_time() == pytest.approx(50.0)
    assert received and received[0].exposure_time_ms == pytest.approx(50.0)


@pytest.mark.integration
def test_exposure_command_is_clamped(simulated_camera):
    bus = EventBus()
    service = CameraService(simulated_camera, bus)

    bus.publish(SetExposureTimeCommand(exposure_time_ms=2000.0))

    min_exp, max_exp = simulated_camera.get_exposure_limits()
    assert simulated_camera.get_exposure_time() == pytest.approx(max_exp)


@pytest.mark.integration
def test_binning_and_roi_events(simulated_camera):
    bus = EventBus()
    service = CameraService(simulated_camera, bus)

    binning_events = []
    roi_events = []
    bus.subscribe(BinningChanged, lambda e: binning_events.append(e))
    bus.subscribe(ROIChanged, lambda e: roi_events.append(e))

    service.set_binning(2, 2)
    service.set_region_of_interest(10, 20, 100, 200)

    assert simulated_camera.get_binning() == (2, 2)
    assert binning_events and binning_events[0].binning_x == 2
    assert binning_events[0].binning_y == 2

    assert simulated_camera.get_region_of_interest() == (10, 20, 100, 200)
    assert roi_events and roi_events[0].x_offset == 10
    assert roi_events[0].y_offset == 20
    assert roi_events[0].width == 100
    assert roi_events[0].height == 200


@pytest.mark.integration
def test_pixel_format_event(simulated_camera):
    bus = EventBus()
    service = CameraService(simulated_camera, bus)

    pixel_events = []
    bus.subscribe(PixelFormatChanged, lambda e: pixel_events.append(e))

    service.set_pixel_format(CameraPixelFormat.MONO8)

    assert simulated_camera.get_pixel_format() == CameraPixelFormat.MONO8
    assert pixel_events and pixel_events[0].pixel_format == CameraPixelFormat.MONO8
