import cv2
import matplotlib.pyplot as plt

from tests.tools import get_repo_root
from control.utils import find_spot_location, SpotDetectionMode
from control._def import LASER_AF_FILTER_SIGMA


def check_image_from_disk(image_path: str):
    """Test spot detection on a real image file.

    Args:
        image_path: Path to the image file to test
    """
    # Load image
    image = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ValueError(f"Failed to load image from {image_path}")

    print(f"Loaded image shape: {image.shape}")

    # Try different detection modes
    modes = [SpotDetectionMode.SINGLE, SpotDetectionMode.DUAL_LEFT, SpotDetectionMode.DUAL_RIGHT]

    # Test parameters to try
    param_sets = [
        {
            "y_window": 96,
            "x_window": 20,
            "min_peak_width": 10,
            "min_peak_distance": 10,
            "min_peak_prominence": 0.25,
        },
        {
            "y_window": 96,
            "x_window": 20,
            "min_peak_width": 5,
            "min_peak_distance": 20,
            "min_peak_prominence": 0.25,
        },
    ]

    # Create figure for results
    plt.figure(figsize=(15, 10))

    # Try each mode and parameter set
    for i, mode in enumerate(modes):
        print(f"\nTesting {mode.name}:")

        for j, params in enumerate(param_sets):
            print(f"\nParameters set {j+1}:")
            print(params)

            result = find_spot_location(
                image, mode=mode, params=params, filter_sigma=LASER_AF_FILTER_SIGMA, debug_plot=True
            )

            if result is not None:
                x, y = result
                print(f"Found spot at: ({x:.1f}, {y:.1f})")
            else:
                print("No spot detected")

            # Wait for user to review plots
            input("Press Enter to continue to next test...")
            plt.close("all")


if __name__ == "__main__":
    # Replace with path to your test image
    repo_root = get_repo_root()
    image_path = repo_root / "tests/data/laser_af_camera.png"
    check_image_from_disk(image_path)
