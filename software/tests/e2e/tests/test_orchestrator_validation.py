"""
E2E tests for protocol validation and warning handling.
"""

from __future__ import annotations

import time

import pytest

from squid.backend.controllers.orchestrator import (
    AddWarningCommand,
    ClearWarningsCommand,
    OrchestratorState,
    ProtocolValidationComplete,
    SetWarningThresholdsCommand,
    ValidateProtocolCommand,
    WarningRaised,
    WarningsCleared,
)
from squid.core.protocol import ProtocolLoader
from tests.harness import BackendContext
from tests.e2e.harness import OrchestratorSimulator


@pytest.mark.e2e
@pytest.mark.orchestrator
class TestProtocolValidation:
    """End-to-end tests for protocol validation events."""

    def test_protocol_validation_events(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        multi_round_fish_protocol: str,
        experiment_output_dir,
    ):
        """Validate protocol and assert validation summary matches protocol."""
        sim = e2e_orchestrator
        center = e2e_backend_ctx.get_stage_center()

        sim.add_single_fov("region_1", x=center[0], y=center[1], z=center[2])
        sim.monitor.subscribe(ProtocolValidationComplete)

        sim.publish(
            ValidateProtocolCommand(
                protocol_path=multi_round_fish_protocol,
                base_path=str(experiment_output_dir),
            )
        )

        validation = sim.monitor.wait_for(ProtocolValidationComplete, timeout_s=30)
        assert validation is not None, "Protocol validation did not complete"

        protocol = ProtocolLoader().load(multi_round_fish_protocol)
        assert validation.valid
        assert validation.total_rounds == len(protocol.rounds)
        assert not validation.errors


@pytest.mark.e2e
@pytest.mark.orchestrator
class TestWarningHandling:
    """End-to-end tests for warning thresholds and clearing."""

    def test_warning_threshold_pause_and_clear(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        multi_round_fish_protocol: str,
    ):
        """Trigger a warning-based pause and clear warnings."""
        sim = e2e_orchestrator
        center = e2e_backend_ctx.get_stage_center()

        sim.load_protocol(multi_round_fish_protocol)
        sim.add_grid_region("region_1", center=center, n_x=2, n_y=2)

        sim.monitor.subscribe(WarningRaised, WarningsCleared)

        started = sim.start()
        assert started

        # Wait until the orchestrator starts running
        deadline = time.time() + 30
        while time.time() < deadline:
            if sim.orchestrator.state in (
                OrchestratorState.RUNNING_IMAGING,
                OrchestratorState.RUNNING_FLUIDICS,
            ):
                break
            time.sleep(0.1)
        assert sim.orchestrator.state in (
            OrchestratorState.RUNNING_IMAGING,
            OrchestratorState.RUNNING_FLUIDICS,
        ), "Orchestrator did not enter a running state"

        sim.publish(
            SetWarningThresholdsCommand(
                pause_after_count=None,
                pause_on_critical=True,
                pause_on_high=True,
                max_stored_warnings=100,
            )
        )

        sim.publish(
            AddWarningCommand(
                category="SYSTEM",
                severity="HIGH",
                message="Injected high severity warning",
                round_index=0,
                round_name="Round 1 - Hybridization",
            )
        )

        # Wait for pause
        deadline = time.time() + 30
        while time.time() < deadline:
            if sim.orchestrator.state == OrchestratorState.PAUSED:
                break
            time.sleep(0.1)

        assert sim.orchestrator.state == OrchestratorState.PAUSED
        assert sim.monitor.get_events(WarningRaised)

        sim.publish(ClearWarningsCommand(experiment_id=sim.orchestrator._experiment_id))
        cleared = sim.monitor.wait_for(WarningsCleared, timeout_s=10)
        assert cleared is not None

        resumed = sim.resume()
        assert resumed

        # Wait for completion
        deadline = time.time() + 120
        while sim.orchestrator.is_running and time.time() < deadline:
            time.sleep(0.1)

        assert sim.orchestrator.state == OrchestratorState.COMPLETED
