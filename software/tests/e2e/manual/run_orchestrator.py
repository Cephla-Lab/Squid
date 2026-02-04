#!/usr/bin/env python3
"""
Manual orchestrator workflow testing script.

This script allows interactive testing of multi-round orchestrated experiments
including fluidics and imaging workflows.

Usage:
    # From software directory:
    python -m tests.e2e.manual.run_orchestrator --protocol tests/e2e/configs/protocols/multi_round_fish.yaml

    # With specific grid configuration
    python -m tests.e2e.manual.run_orchestrator --protocol tests/e2e/configs/protocols/tiled_zstack.yaml \\
        --grid-size 3 --regions 2

    # Test pause at specific round
    python -m tests.e2e.manual.run_orchestrator --protocol tests/e2e/configs/protocols/multi_round_fish.yaml \\
        --pause-at-round 2

    # Resume from checkpoint (not yet implemented)
    python -m tests.e2e.manual.run_orchestrator --resume /path/to/experiment
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src"))

from tests.harness import BackendContext
from tests.e2e.harness import OrchestratorSimulator
from squid.backend.controllers.orchestrator import OrchestratorState


def main():
    parser = argparse.ArgumentParser(
        description="Manual orchestrator workflow testing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--protocol", "-p",
        type=str,
        required=True,
        help="Path to protocol YAML file",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output directory (uses temp dir if not specified)",
    )
    parser.add_argument(
        "--grid-size",
        type=int,
        default=1,
        help="Grid size NxN for each region",
    )
    parser.add_argument(
        "--regions",
        type=int,
        default=1,
        help="Number of FOV regions",
    )
    parser.add_argument(
        "--pause-at-round",
        type=int,
        default=None,
        help="Pause before this round index (0-based)",
    )
    parser.add_argument(
        "--auto-acknowledge",
        action="store_true",
        default=True,
        help="Auto-acknowledge intervention requests",
    )
    parser.add_argument(
        "--no-auto-acknowledge",
        action="store_false",
        dest="auto_acknowledge",
        help="Don't auto-acknowledge interventions (will wait)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="Timeout in seconds",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose output",
    )

    args = parser.parse_args()

    # Validate protocol path
    protocol_path = Path(args.protocol)
    if not protocol_path.exists():
        print(f"ERROR: Protocol file not found: {protocol_path}")
        return 1

    # Setup output directory
    if args.output:
        output_dir = args.output
        Path(output_dir).mkdir(parents=True, exist_ok=True)
    else:
        output_dir = tempfile.mkdtemp(prefix="squid_orchestrator_test_")

    print("=" * 60)
    print("MANUAL ORCHESTRATOR WORKFLOW TEST")
    print("=" * 60)
    print(f"Protocol: {args.protocol}")
    print(f"Output: {output_dir}")
    print(f"Grid size: {args.grid_size}x{args.grid_size}")
    print(f"Regions: {args.regions}")
    print(f"Auto-acknowledge interventions: {args.auto_acknowledge}")
    if args.pause_at_round is not None:
        print(f"Pause at round: {args.pause_at_round}")
    print("=" * 60)
    print()

    print("Starting simulated backend...")

    with BackendContext(simulation=True, base_path=output_dir) as ctx:
        sim = OrchestratorSimulator(ctx)

        # Get stage center
        center = ctx.get_stage_center()
        print(f"Stage center: {center}")

        # Load protocol
        sim.load_protocol(str(protocol_path))
        print(f"Protocol loaded: {protocol_path.name}")

        # Add regions
        for i in range(args.regions):
            x_offset = (i - args.regions // 2) * 2.0
            region_center = (center[0] + x_offset, center[1], center[2])

            if args.grid_size == 1:
                sim.add_single_fov(
                    f"region_{i+1}",
                    x=region_center[0],
                    y=region_center[1],
                    z=region_center[2],
                )
            else:
                sim.add_grid_region(
                    f"region_{i+1}",
                    center=region_center,
                    n_x=args.grid_size,
                    n_y=args.grid_size,
                    overlap_pct=10.0,
                )

            print(f"Added region_{i+1} at {region_center}")

        print()
        print("Starting experiment...")
        print("-" * 40)

        # Start experiment
        started = sim.start(base_path=output_dir)
        if not started:
            print("ERROR: Failed to start experiment")
            return 1

        # Monitoring loop
        start_time = time.time()
        last_state = None
        last_round = -1

        while sim.orchestrator.is_running:
            elapsed = time.time() - start_time

            if elapsed > args.timeout:
                print(f"\nTIMEOUT after {args.timeout}s")
                sim.abort()
                break

            current_state = sim.orchestrator.state

            # Print state changes
            if current_state != last_state:
                print(f"[{elapsed:6.1f}s] State: {current_state.value}")
                last_state = current_state

            # Handle pause at round
            if args.pause_at_round is not None:
                progress = sim.orchestrator._progress
                if progress and progress.current_round_index == args.pause_at_round:
                    if last_round != args.pause_at_round:
                        print(f"[{elapsed:6.1f}s] Pausing at round {args.pause_at_round}...")
                        sim.pause()
                        print("Press Enter to resume or 'q' to quit...")
                        user_input = input()
                        if user_input.lower() == 'q':
                            sim.abort()
                            break
                        sim.resume()
                        last_round = args.pause_at_round

            # Handle interventions
            if current_state == OrchestratorState.WAITING_INTERVENTION:
                if args.auto_acknowledge:
                    time.sleep(0.2)
                    print(f"[{elapsed:6.1f}s] Auto-acknowledging intervention...")
                    sim.acknowledge_intervention()
                else:
                    print(f"[{elapsed:6.1f}s] Waiting for intervention acknowledgment...")
                    print("Press Enter to acknowledge or 'q' to quit...")
                    user_input = input()
                    if user_input.lower() == 'q':
                        sim.abort()
                        break
                    sim.acknowledge_intervention()

            time.sleep(0.1)

        # Allow final events
        time.sleep(0.3)

        print("-" * 40)
        print()

        # Collect results
        elapsed = time.time() - start_time
        final_state = sim.orchestrator.state
        monitor = ctx.event_monitor

        from squid.backend.controllers.orchestrator import (
            OrchestratorRoundStarted,
            OrchestratorRoundCompleted,
            WarningRaised,
            OrchestratorError,
        )

        round_started = monitor.get_events(OrchestratorRoundStarted)
        round_completed = monitor.get_events(OrchestratorRoundCompleted)
        warnings = monitor.get_events(WarningRaised)
        errors = monitor.get_events(OrchestratorError)

        # Print results
        print("=" * 60)
        print("RESULTS")
        print("=" * 60)
        print(f"Final state: {final_state.value}")
        print(f"Elapsed time: {elapsed:.2f}s")
        print(f"Rounds started: {len(round_started)}")
        print(f"Rounds completed: {len([r for r in round_completed if r.success])}")
        print(f"Warnings: {len(warnings)}")
        print(f"Errors: {len(errors)}")

        if args.verbose:
            print()
            print("Round sequence:")
            for r in round_started:
                print(f"  [{r.round_index}] {r.round_name}")

            if warnings:
                print()
                print("Warnings:")
                for w in warnings[:5]:
                    print(f"  [{w.category}] {w.message}")
                if len(warnings) > 5:
                    print(f"  ... and {len(warnings) - 5} more")

            if errors:
                print()
                print("Errors:")
                for e in errors:
                    print(f"  {e.message}")

        print()
        success = final_state == OrchestratorState.COMPLETED

        if success:
            print("ORCHESTRATOR TEST PASSED")
            return 0
        else:
            print(f"ORCHESTRATOR TEST ENDED IN STATE: {final_state.value}")
            return 1 if final_state == OrchestratorState.FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
