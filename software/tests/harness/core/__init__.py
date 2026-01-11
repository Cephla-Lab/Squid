"""Core test harness components."""

from tests.harness.core.event_monitor import EventMonitor
from tests.harness.core.backend_context import BackendContext
from tests.harness.core.assertions import (
    assert_acquisition_completed,
    assert_state_transitions,
    assert_state_sequence,
    assert_progress_monotonic,
    assert_no_errors,
    assert_fov_count,
    assert_image_count,
    assert_abort_behavior,
    assert_timelapse_timing,
)
from tests.harness.core.image_validation import ImageValidator, ImageSpec
from tests.harness.core.fault_injection import FaultInjector

__all__ = [
    "EventMonitor",
    "BackendContext",
    "assert_acquisition_completed",
    "assert_state_transitions",
    "assert_state_sequence",
    "assert_progress_monotonic",
    "assert_no_errors",
    "assert_fov_count",
    "assert_image_count",
    "assert_abort_behavior",
    "assert_timelapse_timing",
    "ImageValidator",
    "ImageSpec",
    "FaultInjector",
]
