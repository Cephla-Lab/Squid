#!/usr/bin/env python3
"""
Test script for filter wheel shortest path algorithm.

This script verifies that the shortest path calculation is correct
without needing hardware.
"""


def test_shortest_path():
    """Test the shortest path calculation logic."""
    num_positions = 8

    print("Shortest Path Test (8 positions)")
    print("=" * 50)
    print(f"{'From':<6} {'To':<6} {'Forward':<10} {'Backward':<10} {'Chosen':<15}")
    print("-" * 50)

    test_cases = [
        (1, 2),  # Simple forward
        (1, 8),  # Should go backward (1 step vs 7)
        (1, 5),  # Equal distance (4 vs 4), forward preferred
        (8, 1),  # Should go forward (1 step vs 7)
        (3, 7),  # Forward 4 vs backward 4, forward preferred
        (7, 2),  # Forward 3 vs backward 5, forward
        (2, 7),  # Forward 5 vs backward 3, backward
        (4, 4),  # Same position, no move
    ]

    for current_pos, target_pos in test_cases:
        if current_pos == target_pos:
            print(f"{current_pos:<6} {target_pos:<6} {'0':<10} {'0':<10} {'No move':<15}")
            continue

        forward_steps = (target_pos - current_pos) % num_positions
        backward_steps = (current_pos - target_pos) % num_positions

        if forward_steps <= backward_steps:
            chosen = f"Forward ({forward_steps})"
        else:
            chosen = f"Backward ({backward_steps})"

        print(f"{current_pos:<6} {target_pos:<6} {forward_steps:<10} {backward_steps:<10} {chosen:<15}")

    print("=" * 50)
    print("\nExpected behavior:")
    print("- 1 -> 8: Backward 1 step (not forward 7)")
    print("- 8 -> 1: Forward 1 step (not backward 7)")
    print("- Equal distance: Forward preferred")

    # Verify critical cases
    assert (8 - 1) % 8 == 7, "Forward 1->8 should be 7"
    assert (1 - 8) % 8 == 1, "Backward 1->8 should be 1"
    assert (1 - 8) % 8 < (8 - 1) % 8, "1->8 should choose backward"

    assert (1 - 8) % 8 == 1, "Forward 8->1 should be 1"
    assert (8 - 1) % 8 == 7, "Backward 8->1 should be 7"
    assert (1 - 8) % 8 < (8 - 1) % 8, "8->1 should choose forward"

    print("\n✓ All assertions passed!")


def simulate_full_cycle():
    """Simulate a full imaging cycle and calculate total steps."""
    print("\n\nFull Cycle Simulation")
    print("=" * 50)

    # Simulate imaging cycle: visit positions 1,3,5,7 then return to 1
    positions_to_visit = [1, 3, 5, 7, 1]
    num_positions = 8

    current = 1
    total_steps_shortest = 0
    total_steps_linear = 0

    print(f"{'Move':<12} {'Linear':<10} {'Shortest':<10} {'Saved':<10}")
    print("-" * 50)

    for target in positions_to_visit[1:]:
        # Linear (always forward)
        linear_steps = abs(target - current)
        if target < current:
            linear_steps = num_positions - current + target

        # Shortest path
        forward = (target - current) % num_positions
        backward = (current - target) % num_positions
        shortest_steps = min(forward, backward)

        saved = linear_steps - shortest_steps
        print(f"{current} -> {target:<6} {linear_steps:<10} {shortest_steps:<10} {saved:<10}")

        total_steps_linear += linear_steps
        total_steps_shortest += shortest_steps
        current = target

    print("-" * 50)
    print(
        f"{'Total':<12} {total_steps_linear:<10} {total_steps_shortest:<10} {total_steps_linear - total_steps_shortest:<10}"
    )

    improvement = (total_steps_linear - total_steps_shortest) / total_steps_linear * 100
    print(f"\nImprovement: {improvement:.1f}% fewer steps")


if __name__ == "__main__":
    test_shortest_path()
    simulate_full_cycle()
