"""
Protocol-driven smoke tests for orchestrator workflows.

Runs each protocol YAML in configs/protocols through the orchestrator
to ensure end-to-end execution and output structure.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from squid.core.protocol import ProtocolLoader
from tests.harness import BackendContext
from tests.e2e.harness import (
    OrchestratorSimulator,
    assert_output_structure_valid,
    assert_round_events_match_protocol,
)


PROTOCOL_DIR = Path(__file__).resolve().parents[1] / "configs" / "protocols"
PROTOCOL_PATHS = sorted(PROTOCOL_DIR.glob("*.yaml"))


@pytest.mark.e2e
@pytest.mark.orchestrator
@pytest.mark.parametrize("protocol_path", PROTOCOL_PATHS, ids=lambda p: p.name)
def test_protocol_smoke(
    e2e_orchestrator: OrchestratorSimulator,
    e2e_backend_ctx: BackendContext,
    protocol_path: Path,
):
    """Run each protocol end-to-end and validate outputs."""
    sim = e2e_orchestrator
    sim.load_protocol(str(protocol_path))

    protocol = ProtocolLoader().load(str(protocol_path))
    if protocol.total_imaging_steps() > 0 and protocol.fov_file is None and not protocol.fov_sets:
        center = e2e_backend_ctx.get_stage_center()
        sim.add_single_fov("region_1", x=center[0], y=center[1], z=center[2])
    expected_rounds = len(protocol.rounds)
    expected_round_dirs = protocol.total_imaging_steps()

    result = sim.run_and_wait(timeout_s=180)

    assert result.success, f"Experiment failed: {result.error}"
    assert result.completed_rounds == expected_rounds
    assert_round_events_match_protocol(sim.monitor, expected_rounds=expected_rounds)

    if result.experiment_path:
        check_images = protocol_path.name == "single_round_imaging_save.yaml"
        assert_output_structure_valid(
            result.experiment_path,
            expected_rounds=expected_round_dirs,
            check_images=check_images,
        )
