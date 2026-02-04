"""E2E test harness extensions."""

from tests.e2e.harness.orchestrator_simulator import OrchestratorSimulator, OrchestratorResult
from tests.e2e.harness.e2e_assertions import (
    assert_orchestrator_completed,
    assert_round_sequence,
    assert_checkpoint_created,
    assert_checkpoint_cleared,
    assert_output_structure_valid,
    assert_coordinates_csv_valid,
    assert_no_errors,
    assert_warnings_count,
    assert_state_transitions,
    assert_intervention_occurred,
    assert_orchestrator_progress_monotonic,
    assert_round_events_match_protocol,
)

__all__ = [
    "OrchestratorSimulator",
    "OrchestratorResult",
    "assert_orchestrator_completed",
    "assert_round_sequence",
    "assert_checkpoint_created",
    "assert_checkpoint_cleared",
    "assert_output_structure_valid",
    "assert_coordinates_csv_valid",
    "assert_no_errors",
    "assert_warnings_count",
    "assert_state_transitions",
    "assert_intervention_occurred",
    "assert_orchestrator_progress_monotonic",
    "assert_round_events_match_protocol",
]
