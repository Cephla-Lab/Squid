"""
Pytest fixtures for control module tests.

This module provides fixtures to ensure proper cleanup of Microcontroller instances,
preventing background threads from causing segfaults in subsequent tests.
"""

import logging
from unittest.mock import patch

import pytest

import control.microcontroller

logger = logging.getLogger(__name__)


def _make_tracking_init(original_init, instances_list):
    """Create a wrapper that tracks Microcontroller instances."""

    def _tracking_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        instances_list.append(self)

    return _tracking_init


@pytest.fixture(autouse=True)
def cleanup_microcontrollers():
    """
    Fixture that automatically cleans up all Microcontroller instances after each test.

    This prevents background threads from causing segfaults when subsequent tests run,
    especially those involving Qt event loops. The Microcontroller.read_received_packet
    method runs in a background thread that must be stopped via close().
    """
    # Track instances created during this test (scoped to this fixture invocation)
    active_microcontrollers = []

    # Capture original __init__ at fixture runtime, not module load time
    original_init = control.microcontroller.Microcontroller.__init__

    with patch.object(
        control.microcontroller.Microcontroller, "__init__", _make_tracking_init(original_init, active_microcontrollers)
    ):
        yield

    # Clean up all tracked instances
    for micro in active_microcontrollers:
        try:
            if hasattr(micro, "terminate_reading_received_packet_thread"):
                if not micro.terminate_reading_received_packet_thread:
                    micro.close()
        except Exception as e:
            logger.warning(f"Failed to close Microcontroller in test cleanup: {e}")
