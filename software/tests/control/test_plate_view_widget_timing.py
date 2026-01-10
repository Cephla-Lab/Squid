"""Tests to measure NapariPlateViewWidget update timing."""

import time
import numpy as np
import pytest


@pytest.fixture
def plate_view_widget(qtbot):
    """Create a NapariPlateViewWidget for testing."""
    from control.widgets import NapariPlateViewWidget
    from control.core.contrast_manager import ContrastManager

    contrast_manager = ContrastManager()
    widget = NapariPlateViewWidget(contrast_manager)
    qtbot.addWidget(widget)
    try:
        yield widget
    finally:
        # Ensure the embedded napari viewer is properly closed after tests
        viewer = getattr(widget, "viewer", None)
        if viewer is not None:
            close_method = getattr(viewer, "close", None)
            if callable(close_method):
                close_method()


class TestNapariPlateViewWidgetTiming:
    """Measure timing of NapariPlateViewWidget operations."""

    def test_init_plate_layout_timing(self, plate_view_widget, qtbot):
        """Measure time for initPlateLayout."""
        num_rows = 8
        num_cols = 12
        well_slot_shape = (500, 500)

        start = time.time()
        plate_view_widget.initPlateLayout(
            num_rows=num_rows,
            num_cols=num_cols,
            well_slot_shape=well_slot_shape,
            fov_grid_shape=(2, 2),
            channel_names=["BF", "Fluorescence 488"],
        )
        qtbot.wait(10)  # Process events
        elapsed_ms = (time.time() - start) * 1000

        print(f"\ninitPlateLayout ({num_rows}x{num_cols}, {well_slot_shape}): {elapsed_ms:.1f} ms")
        # Should be fast - just drawing boundaries
        # Threshold increased from 500ms to 750ms due to CI runner variability
        assert elapsed_ms < 750, f"initPlateLayout too slow: {elapsed_ms:.1f} ms"

    def test_first_layer_creation_timing(self, plate_view_widget, qtbot):
        """Measure time for first layer creation (add_image)."""
        # Initialize plate layout
        num_rows = 8
        num_cols = 12
        well_slot_shape = (500, 500)
        plate_height = num_rows * well_slot_shape[0]
        plate_width = num_cols * well_slot_shape[1]

        plate_view_widget.initPlateLayout(
            num_rows=num_rows,
            num_cols=num_cols,
            well_slot_shape=well_slot_shape,
            fov_grid_shape=(2, 2),
            channel_names=["BF"],
        )

        # Create plate image
        plate_image = np.zeros((plate_height, plate_width), dtype=np.uint16)
        plate_image[0:500, 0:500] = np.random.randint(0, 65535, (500, 500), dtype=np.uint16)

        # Time first update (creates layer)
        start = time.time()
        plate_view_widget.updatePlateView(0, "BF", plate_image)
        qtbot.wait(10)
        elapsed_ms = (time.time() - start) * 1000

        print(f"\nFirst layer creation ({plate_height}x{plate_width}): {elapsed_ms:.1f} ms")
        # First layer creation can be slow, but should be reasonable
        assert elapsed_ms < 3000, f"First layer creation too slow: {elapsed_ms:.1f} ms"

    def test_layer_update_timing(self, plate_view_widget, qtbot):
        """Measure time for subsequent layer updates."""
        # Initialize plate layout
        num_rows = 8
        num_cols = 12
        well_slot_shape = (500, 500)
        plate_height = num_rows * well_slot_shape[0]
        plate_width = num_cols * well_slot_shape[1]

        plate_view_widget.initPlateLayout(
            num_rows=num_rows,
            num_cols=num_cols,
            well_slot_shape=well_slot_shape,
            fov_grid_shape=(2, 2),
            channel_names=["BF"],
        )

        # Create initial plate image
        plate_image = np.zeros((plate_height, plate_width), dtype=np.uint16)
        plate_image[0:500, 0:500] = 32768

        # First update (creates layer)
        plate_view_widget.updatePlateView(0, "BF", plate_image)
        qtbot.wait(10)

        # Time subsequent updates
        update_times = []
        for i in range(5):
            plate_image[0:500, 500 * (i + 1) : 500 * (i + 2)] = np.random.randint(0, 65535, (500, 500), dtype=np.uint16)

            start = time.time()
            plate_view_widget.updatePlateView(0, "BF", plate_image.copy())
            qtbot.wait(10)
            elapsed_ms = (time.time() - start) * 1000
            update_times.append(elapsed_ms)

        mean_time = np.mean(update_times)
        max_time = np.max(update_times)
        print(f"\nLayer update timing (5 updates):")
        print(f"  Mean: {mean_time:.1f} ms")
        print(f"  Max: {max_time:.1f} ms")
        print(f"  Individual: {[f'{t:.1f}' for t in update_times]}")

        # Updates should be fast
        assert mean_time < 500, f"Layer updates too slow: {mean_time:.1f} ms mean"

    def test_multi_channel_update_timing(self, plate_view_widget, qtbot):
        """Measure time for multi-channel updates (what happens per well completion)."""
        # Initialize plate layout
        num_rows = 8
        num_cols = 12
        well_slot_shape = (500, 500)
        plate_height = num_rows * well_slot_shape[0]
        plate_width = num_cols * well_slot_shape[1]
        channel_names = ["BF", "Fluorescence 488", "Fluorescence 561"]

        plate_view_widget.initPlateLayout(
            num_rows=num_rows,
            num_cols=num_cols,
            well_slot_shape=well_slot_shape,
            fov_grid_shape=(2, 2),
            channel_names=channel_names,
        )

        # Create plate images for each channel
        plate_images = [np.zeros((plate_height, plate_width), dtype=np.uint16) for _ in channel_names]

        # First well (creates all layers)
        print("\nFirst well (creates layers):")
        for i, (name, plate_image) in enumerate(zip(channel_names, plate_images)):
            plate_image[0:500, 0:500] = np.random.randint(0, 65535, (500, 500), dtype=np.uint16)
            start = time.time()
            plate_view_widget.updatePlateView(i, name, plate_image)
            qtbot.wait(10)
            elapsed_ms = (time.time() - start) * 1000
            print(f"  {name}: {elapsed_ms:.1f} ms")

        # Subsequent wells
        print("\nSubsequent wells:")
        well_times = []
        for well_idx in range(1, 6):  # Wells A2-A6
            well_start = time.time()
            for i, (name, plate_image) in enumerate(zip(channel_names, plate_images)):
                plate_image[0:500, 500 * well_idx : 500 * (well_idx + 1)] = np.random.randint(
                    0, 65535, (500, 500), dtype=np.uint16
                )
                plate_view_widget.updatePlateView(i, name, plate_image.copy())
            qtbot.wait(10)
            well_time = (time.time() - well_start) * 1000
            well_times.append(well_time)
            print(f"  Well {well_idx+1}: {well_time:.1f} ms")

        mean_well_time = np.mean(well_times)
        print(f"\nMean time per well ({len(channel_names)} channels): {mean_well_time:.1f} ms")

        # Each well update should be reasonable
        assert mean_well_time < 1000, f"Well updates too slow: {mean_well_time:.1f} ms mean"

    def test_large_plate_update_timing(self, plate_view_widget, qtbot):
        """Measure timing with larger plate dimensions (1536-well)."""
        # 1536-well plate: 32x48
        num_rows = 32
        num_cols = 48
        well_slot_shape = (100, 100)  # Smaller wells
        plate_height = num_rows * well_slot_shape[0]
        plate_width = num_cols * well_slot_shape[1]

        print(f"\nLarge plate test ({num_rows}x{num_cols} = {num_rows*num_cols} wells)")
        print(f"Plate size: {plate_height}x{plate_width} pixels")

        # Time initPlateLayout
        start = time.time()
        plate_view_widget.initPlateLayout(
            num_rows=num_rows,
            num_cols=num_cols,
            well_slot_shape=well_slot_shape,
            fov_grid_shape=(1, 1),
            channel_names=["BF"],
        )
        qtbot.wait(10)
        init_time = (time.time() - start) * 1000
        print(f"initPlateLayout: {init_time:.1f} ms")

        # Create plate image
        plate_image = np.zeros((plate_height, plate_width), dtype=np.uint16)

        # Time first layer creation
        plate_image[0:100, 0:100] = 32768
        start = time.time()
        plate_view_widget.updatePlateView(0, "BF", plate_image)
        qtbot.wait(10)
        first_time = (time.time() - start) * 1000
        print(f"First layer creation: {first_time:.1f} ms")

        # Time rapid updates
        start = time.time()
        num_updates = 20
        for i in range(num_updates):
            row = i // num_cols
            col = i % num_cols
            y0 = row * well_slot_shape[0]
            x0 = col * well_slot_shape[1]
            plate_image[y0 : y0 + 100, x0 : x0 + 100] = np.random.randint(0, 65535, (100, 100), dtype=np.uint16)
            plate_view_widget.updatePlateView(0, "BF", plate_image.copy())
        qtbot.wait(10)
        total_time = (time.time() - start) * 1000
        print(f"{num_updates} updates: {total_time:.1f} ms ({total_time/num_updates:.1f} ms/update)")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
