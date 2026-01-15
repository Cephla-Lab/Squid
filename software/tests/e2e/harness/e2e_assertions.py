"""
E2E-specific assertion helpers.

These assertions validate orchestrator workflows, output structure,
and experiment results for end-to-end tests.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from tests.harness.core.event_monitor import EventMonitor

from squid.backend.controllers.orchestrator import (
    OrchestratorStateChanged,
    OrchestratorRoundStarted,
    OrchestratorRoundCompleted,
    OrchestratorProgress,
)


def assert_orchestrator_completed(
    monitor: "EventMonitor",
    expected_rounds: Optional[int] = None,
) -> None:
    """
    Assert that the orchestrator completed successfully.

    Args:
        monitor: Event monitor with collected events
        expected_rounds: Expected number of completed rounds (optional)

    Raises:
        AssertionError: If orchestrator did not complete successfully
    """
    state_changes = monitor.get_events(OrchestratorStateChanged)
    final_states = [e for e in state_changes if e.new_state == "COMPLETED"]

    assert len(final_states) > 0, (
        f"Orchestrator did not reach COMPLETED state. "
        f"State transitions: {[e.new_state for e in state_changes]}"
    )

    if expected_rounds is not None:
        round_completed = monitor.get_events(OrchestratorRoundCompleted)
        successful_rounds = [e for e in round_completed if e.success]
        assert len(successful_rounds) == expected_rounds, (
            f"Expected {expected_rounds} completed rounds, got {len(successful_rounds)}"
        )


def assert_round_sequence(
    monitor: "EventMonitor",
    expected_rounds: List[str],
) -> None:
    """
    Assert that rounds executed in the expected order.

    Args:
        monitor: Event monitor with collected events
        expected_rounds: List of expected round names in order

    Raises:
        AssertionError: If round sequence doesn't match
    """
    round_started = monitor.get_events(OrchestratorRoundStarted)
    actual_names = [e.round_name for e in round_started]

    assert actual_names == expected_rounds, (
        f"Round sequence mismatch.\n"
        f"Expected: {expected_rounds}\n"
        f"Actual: {actual_names}"
    )


def assert_checkpoint_created(experiment_path: str) -> dict:
    """
    Assert that a checkpoint file was created and return its contents.

    Args:
        experiment_path: Path to experiment directory

    Returns:
        Checkpoint data as dict

    Raises:
        AssertionError: If checkpoint doesn't exist or is invalid
    """
    checkpoint_path = Path(experiment_path) / "checkpoint.json"

    assert checkpoint_path.exists(), (
        f"Checkpoint not found at {checkpoint_path}"
    )

    with open(checkpoint_path) as f:
        checkpoint_data = json.load(f)

    # Validate required fields
    required_fields = [
        "protocol_name",
        "experiment_id",
        "round_index",
    ]
    for field in required_fields:
        assert field in checkpoint_data, (
            f"Checkpoint missing required field: {field}"
        )

    return checkpoint_data


def assert_checkpoint_cleared(experiment_path: str) -> None:
    """
    Assert that the checkpoint file was cleared (deleted).

    Args:
        experiment_path: Path to experiment directory

    Raises:
        AssertionError: If checkpoint still exists
    """
    checkpoint_path = Path(experiment_path) / "checkpoint.json"

    assert not checkpoint_path.exists(), (
        f"Checkpoint should be cleared but exists at {checkpoint_path}"
    )


def assert_output_structure_valid(
    experiment_path: str,
    expected_rounds: int,
    check_images: bool = False,
) -> None:
    """
    Validate the output directory structure.

    Args:
        experiment_path: Path to experiment directory
        expected_rounds: Expected number of round directories
        check_images: If True, verify image files exist in each round

    Raises:
        AssertionError: If directory structure is invalid
    """
    exp_path = Path(experiment_path)

    assert exp_path.exists(), f"Experiment directory not found: {exp_path}"

    # Find round directories
    round_dirs = [
        p for p in exp_path.iterdir()
        if p.is_dir() and p.name.startswith("round_")
    ]

    assert len(round_dirs) == expected_rounds, (
        f"Expected {expected_rounds} round directories, found {len(round_dirs)}: "
        f"{[d.name for d in round_dirs]}"
    )

    # Check each round directory
    for round_dir in sorted(round_dirs):
        # Check for coordinates.csv if imaging round
        coordinates_path = round_dir / "coordinates.csv"
        if coordinates_path.exists():
            assert_coordinates_csv_valid(str(coordinates_path))

        if check_images:
            # Check for at least one image file
            image_files = list(round_dir.glob("*.tiff")) + list(round_dir.glob("*.bmp"))
            assert len(image_files) > 0, (
                f"No image files found in {round_dir}"
            )


def assert_coordinates_csv_valid(
    csv_path: str,
    expected_fovs: Optional[int] = None,
) -> List[Dict]:
    """
    Validate a coordinates.csv file.

    Args:
        csv_path: Path to coordinates.csv file
        expected_fovs: Expected number of FOV rows (optional)

    Returns:
        List of coordinate rows as dicts

    Raises:
        AssertionError: If CSV is invalid
    """
    path = Path(csv_path)

    assert path.exists(), f"Coordinates CSV not found: {path}"

    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames or []

    # Validate required columns
    required_columns = ["x (mm)", "y (mm)", "z (um)", "region"]
    if rows:
        for col in required_columns:
            assert col in fieldnames, (
                f"Coordinates CSV missing column: {col}. "
                f"Found columns: {fieldnames}"
            )

    if expected_fovs is not None:
        assert len(rows) == expected_fovs, (
            f"Expected {expected_fovs} FOV rows, got {len(rows)}"
        )

    return rows


def assert_no_errors(monitor: "EventMonitor") -> None:
    """
    Assert that no error events were published.

    Args:
        monitor: Event monitor with collected events

    Raises:
        AssertionError: If any error events were published
    """
    from squid.backend.controllers.orchestrator import OrchestratorError

    errors = monitor.get_events(OrchestratorError)

    assert len(errors) == 0, (
        f"Expected no errors, but got {len(errors)}: "
        f"{[e.error_message for e in errors]}"
    )


def assert_warnings_count(
    monitor: "EventMonitor",
    expected_count: int,
    category: Optional[str] = None,
) -> None:
    """
    Assert the expected number of warnings were raised.

    Args:
        monitor: Event monitor with collected events
        expected_count: Expected number of warnings
        category: Filter by warning category (optional)

    Raises:
        AssertionError: If warning count doesn't match
    """
    from squid.backend.controllers.orchestrator import WarningRaised

    warnings = monitor.get_events(WarningRaised)

    if category:
        warnings = [w for w in warnings if w.category == category]

    assert len(warnings) == expected_count, (
        f"Expected {expected_count} warnings"
        f"{f' with category {category}' if category else ''}, "
        f"got {len(warnings)}"
    )


def assert_state_transitions(
    monitor: "EventMonitor",
    expected_states: List[str],
) -> None:
    """
    Assert that state transitions occurred in the expected order.

    Args:
        monitor: Event monitor with collected events
        expected_states: List of expected state names in order

    Raises:
        AssertionError: If state transitions don't match
    """
    state_changes = monitor.get_events(OrchestratorStateChanged)
    actual_states = [e.new_state for e in state_changes]

    # Check that expected states appear in order (may have additional states between)
    expected_idx = 0
    for state in actual_states:
        if expected_idx < len(expected_states) and state == expected_states[expected_idx]:
            expected_idx += 1

    assert expected_idx == len(expected_states), (
        f"State transitions incomplete.\n"
        f"Expected states: {expected_states}\n"
        f"Actual states: {actual_states}\n"
        f"Only found {expected_idx} of {len(expected_states)} expected states"
    )


def assert_intervention_occurred(
    monitor: "EventMonitor",
    expected_message: Optional[str] = None,
) -> None:
    """
    Assert that an intervention was required during execution.

    Args:
        monitor: Event monitor with collected events
        expected_message: Expected intervention message (optional)

    Raises:
        AssertionError: If no intervention occurred or message doesn't match
    """
    from squid.backend.controllers.orchestrator import OrchestratorInterventionRequired

    interventions = monitor.get_events(OrchestratorInterventionRequired)

    assert len(interventions) > 0, "Expected at least one intervention, but none occurred"

    if expected_message:
        messages = [i.message for i in interventions]
        assert any(expected_message in msg for msg in messages), (
            f"Expected intervention message containing '{expected_message}'. "
            f"Actual messages: {messages}"
        )


def assert_orchestrator_progress_monotonic(monitor: "EventMonitor") -> None:
    """
    Assert that orchestrator progress is monotonically non-decreasing.

    Args:
        monitor: Event monitor with collected events

    Raises:
        AssertionError: If progress decreases or no progress events exist
    """
    progress_events = monitor.get_events(OrchestratorProgress)

    assert progress_events, "No orchestrator progress events received"

    last_progress = -1.0
    for event in progress_events:
        assert 0.0 <= event.progress_percent <= 100.0, (
            f"Progress out of bounds: {event.progress_percent}"
        )
        assert event.progress_percent + 1e-6 >= last_progress, (
            f"Progress decreased: {last_progress} -> {event.progress_percent}"
        )
        last_progress = event.progress_percent


def assert_round_events_match_protocol(
    monitor: "EventMonitor",
    expected_rounds: int,
) -> None:
    """
    Assert that round started/completed events match protocol round count.

    Args:
        monitor: Event monitor with collected events
        expected_rounds: Expected number of rounds

    Raises:
        AssertionError: If round events don't match expected count
    """
    round_started = monitor.get_events(OrchestratorRoundStarted)
    round_completed = monitor.get_events(OrchestratorRoundCompleted)

    assert len(round_started) == expected_rounds, (
        f"Expected {expected_rounds} round started events, got {len(round_started)}"
    )
    assert len(round_completed) == expected_rounds, (
        f"Expected {expected_rounds} round completed events, got {len(round_completed)}"
    )
    assert all(e.success for e in round_completed), "Not all rounds completed successfully"
