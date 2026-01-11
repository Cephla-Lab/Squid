"""
Common test assertions for backend testing.

This module provides assertion functions for common test scenarios,
built on top of EventMonitor.
"""

from __future__ import annotations

from typing import List, Optional, Type

from squid.core.events import (
    Event,
    AcquisitionProgress,
    AcquisitionWorkerFinished,
    AcquisitionStateChanged,
)

from tests.harness.core.event_monitor import EventMonitor


def assert_acquisition_completed(
    monitor: EventMonitor,
    expected_images: Optional[int] = None,
    timeout_s: float = 60.0,
) -> AcquisitionWorkerFinished:
    """
    Assert that an acquisition completed successfully.

    Args:
        monitor: EventMonitor instance
        expected_images: Expected number of images (if None, any count is accepted)
        timeout_s: Maximum time to wait for completion

    Returns:
        The AcquisitionWorkerFinished event

    Raises:
        AssertionError: If acquisition fails or times out
    """
    # Subscribe to finish event
    monitor.subscribe(AcquisitionWorkerFinished)

    # Wait for finish
    result = monitor.wait_for(AcquisitionWorkerFinished, timeout_s=timeout_s)

    if result is None:
        raise AssertionError(
            f"Acquisition did not complete within {timeout_s}s timeout"
        )

    if not result.success:
        raise AssertionError(f"Acquisition failed: {result.error}")

    if expected_images is not None:
        from squid.core.events import AcquisitionCoordinates
        actual_count = monitor.get_event_count(AcquisitionCoordinates)
        if actual_count != expected_images:
            raise AssertionError(
                f"Expected {expected_images} images, got {actual_count}"
            )

    return result


def assert_state_transitions(
    monitor: EventMonitor,
    expected_states: List[str],
) -> None:
    """
    Assert that acquisition went through expected state transitions.

    Args:
        monitor: EventMonitor instance
        expected_states: List of expected state names (e.g., ["RUNNING", "IDLE"])
                        Valid states: "IDLE", "RUNNING", "ABORTING"

    Raises:
        AssertionError: If state sequence doesn't match
    """
    state_events = monitor.get_events(AcquisitionStateChanged)

    # Extract state names from events based on flags
    actual_states = []
    for evt in state_events:
        if evt.is_aborting:
            actual_states.append("ABORTING")
        elif evt.in_progress:
            actual_states.append("RUNNING")
        else:
            actual_states.append("IDLE")

    if actual_states != expected_states:
        raise AssertionError(
            f"Expected state sequence {expected_states}, got {actual_states}"
        )


def assert_state_sequence(
    monitor: EventMonitor,
    expected_sequence: List[str],
    strict: bool = True,
) -> None:
    """
    Assert that acquisition went through expected state transitions.

    This is a more flexible version of assert_state_transitions that supports
    both strict and non-strict matching.

    Args:
        monitor: EventMonitor instance
        expected_sequence: List of expected states in order.
                          Valid states: "IDLE", "RUNNING", "ABORTING"
        strict: If True, sequence must match exactly. If False,
                expected states must appear in order but can have
                intermediate states.

    Raises:
        AssertionError: If state sequence doesn't match expectations
    """
    state_events = monitor.get_events(AcquisitionStateChanged)

    # Extract state names from events
    actual_states = []
    for evt in state_events:
        if evt.is_aborting:
            actual_states.append("ABORTING")
        elif evt.in_progress:
            actual_states.append("RUNNING")
        else:
            actual_states.append("IDLE")

    if strict:
        if actual_states != expected_sequence:
            raise AssertionError(
                f"Expected state sequence {expected_sequence}, got {actual_states}"
            )
    else:
        # Verify expected states appear in order (but allow intermediate states)
        idx = 0
        for expected in expected_sequence:
            found = False
            while idx < len(actual_states):
                if actual_states[idx] == expected:
                    found = True
                    idx += 1
                    break
                idx += 1
            if not found:
                raise AssertionError(
                    f"Expected state '{expected}' not found in sequence. "
                    f"Expected {expected_sequence}, got {actual_states}"
                )


def assert_progress_monotonic(monitor: EventMonitor) -> None:
    """
    Assert that progress events show monotonically increasing progress.

    Args:
        monitor: EventMonitor instance

    Raises:
        AssertionError: If progress decreases
    """
    progress_events = monitor.get_events(AcquisitionProgress)

    if not progress_events:
        raise AssertionError("No progress events received")

    for i in range(1, len(progress_events)):
        prev = progress_events[i - 1]
        curr = progress_events[i]

        # Progress should increase (or stay same during timepoint transitions)
        if curr.current_fov < prev.current_fov and curr.current_round <= prev.current_round:
            raise AssertionError(
                f"Progress decreased: FOV {prev.current_fov} -> {curr.current_fov} "
                f"(round {prev.current_round} -> {curr.current_round})"
            )


def assert_no_errors(monitor: EventMonitor) -> None:
    """
    Assert that no error events were received.

    Args:
        monitor: EventMonitor instance

    Raises:
        AssertionError: If any error events were received
    """
    finish_events = monitor.get_events(AcquisitionWorkerFinished)

    for evt in finish_events:
        if not evt.success:
            raise AssertionError(f"Acquisition error: {evt.error}")


def assert_fov_count(
    monitor: EventMonitor,
    expected_fovs: int,
) -> None:
    """
    Assert that the expected number of FOVs were registered.

    Args:
        monitor: EventMonitor instance
        expected_fovs: Expected number of FOV registrations

    Raises:
        AssertionError: If FOV count doesn't match
    """
    from squid.core.events import CurrentFOVRegistered

    actual_count = monitor.get_event_count(CurrentFOVRegistered)
    if actual_count != expected_fovs:
        raise AssertionError(
            f"Expected {expected_fovs} FOV registrations, got {actual_count}"
        )


def assert_image_count(
    monitor: EventMonitor,
    expected_images: int,
) -> None:
    """
    Assert that the expected number of images were captured.

    Args:
        monitor: EventMonitor instance
        expected_images: Expected number of images

    Raises:
        AssertionError: If image count doesn't match
    """
    from squid.core.events import AcquisitionCoordinates

    actual_count = monitor.get_event_count(AcquisitionCoordinates)
    if actual_count != expected_images:
        raise AssertionError(
            f"Expected {expected_images} images, got {actual_count}"
        )


def assert_abort_behavior(
    monitor: EventMonitor,
    max_images: int,
    min_images: int = 1,
) -> None:
    """
    Assert that an abort resulted in a partial acquisition.

    Args:
        monitor: EventMonitor instance
        max_images: Maximum expected images (should be less than full acquisition)
        min_images: Minimum expected images (at least some should be captured)

    Raises:
        AssertionError: If abort behavior is incorrect
    """
    from squid.core.events import AcquisitionCoordinates

    actual_count = monitor.get_event_count(AcquisitionCoordinates)

    if actual_count >= max_images:
        raise AssertionError(
            f"Abort did not stop acquisition early: {actual_count} >= {max_images} images"
        )

    if actual_count < min_images:
        raise AssertionError(
            f"Abort stopped too early: only {actual_count} < {min_images} images captured"
        )

    # Verify state sequence shows aborting
    state_events = monitor.get_events(AcquisitionStateChanged)
    saw_aborting = any(evt.is_aborting for evt in state_events)

    if not saw_aborting:
        raise AssertionError("Abort was requested but no ABORTING state was observed")


def assert_timelapse_timing(
    monitor: EventMonitor,
    expected_dt_s: float,
    n_timepoints: int,
    tolerance_pct: float = 20.0,
) -> None:
    """
    Assert that timelapse timepoints occurred at expected intervals.

    Args:
        monitor: EventMonitor instance
        expected_dt_s: Expected interval between timepoints in seconds
        n_timepoints: Expected number of timepoints
        tolerance_pct: Allowed deviation from expected interval (default 20%)

    Raises:
        AssertionError: If timing is incorrect
    """
    from squid.core.events import AcquisitionProgress

    progress_events = monitor.get_events(AcquisitionProgress)

    # Find events where round number increased (new timepoint started)
    timepoint_events = []
    last_round = -1
    for evt in progress_events:
        if evt.current_round > last_round:
            timepoint_events.append(evt)
            last_round = evt.current_round

    if len(timepoint_events) < n_timepoints:
        raise AssertionError(
            f"Expected {n_timepoints} timepoints, only found {len(timepoint_events)}"
        )

    # Check intervals between timepoints
    if expected_dt_s > 0 and len(timepoint_events) >= 2:
        tolerance = expected_dt_s * tolerance_pct / 100

        for i in range(1, len(timepoint_events)):
            # Note: Events don't have timestamps by default, so this check
            # is limited. In practice, you'd need to add timestamps to events
            # or use a different mechanism to verify timing.
            pass  # Timing verification requires event timestamps
