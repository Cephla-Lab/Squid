# stitcher_process.py
import os
import sys
import random
import json
import time
import math
import numpy as np
import pandas as pd
import cv2
import dask.array as da
from dask_image.imread import imread as dask_imread
from skimage.registration import phase_cross_correlation
from skimage import exposure
import ome_zarr
import zarr
import imageio
import pyvips
from aicsimageio.writers import OmeTiffWriter as aicsTiffWriter
from aicsimageio.writers import OmeZarrWriter as aicsZarrWriter
from aicsimageio import types as aics_types
from bioio.writers import OmeTiffWriter as bioioTiffWriter
from bioio.writers import OmeZarrWriter as bioioZarrWriter
from bioio.writers.ome_zarr_writer_2 import OmeZarrWriter as bioioZarrWriter2
from bioio.writers.ome_zarr_writer_2 import compute_level_shapes, compute_level_chunk_sizes_zslice
from bioio_base import types as bioio_types
from basicpy import BaSiC
from multiprocessing import Process, Queue, Event, Pool, cpu_count
from control.stitcher.stitcher_parameters import StitchingParameters

# Cephla-Lab: Squid Microscopy Image Stitcher (soham mukherjee)


def write_chunk_worker(args):
    """Worker function for parallel chunk writing.

    Args:
        args: Tuple containing (chunk_coords, path, data)

    Returns:
        Tuple: (success, chunk_info)
    """
    try:
        coords, zarr_path, data_slice = args
        z_idx, y_idx, x_idx = coords

        # Open zarr array
        store = zarr.DirectoryStore(zarr_path)
        root = zarr.group(store)
        dataset = root["0"]

        # Write data
        dataset[
            (slice(None),) * 2
            + (
                slice(z_idx, z_idx + 1),
                slice(y_idx, y_idx + data_slice.shape[3]),
                slice(x_idx, x_idx + data_slice.shape[4]),
            )
        ] = data_slice

        return True, coords

    except Exception as e:
        return False, f"Error at chunk {coords}: {str(e)}"


class StitcherProcess(Process):
    def __init__(
        self,
        params: StitchingParameters,
        progress_queue: Queue,
        status_queue: Queue,
        complete_queue: Queue,
        stop_event: Event,
    ):
        """Initialize the StitcherProcess.
        Args:
            params (StitchingParameters): Configuration parameters for stitching
            progress_queue (Queue): Queue for progress updates
            status_queue (Queue): Queue for status messages
            complete_queue (Queue): Queue for completion notification
            stop_event (Event): Event for process termination
        """
        super().__init__()

        # Store queues and event for inter-process communication
        self.progress_queue = progress_queue
        self.status_queue = status_queue
        self.complete_queue = complete_queue
        self.stop_event = stop_event

        # Validate and store parameters
        self.params = params
        params.validate()  # comment out for save_region_test.py script to bypass parameter validation

        # Core attributes from parameters
        self.input_folder = params.input_folder
        self.output_folder = params.stitched_folder
        self.output_format = params.output_format

        # Default merge parameters to False
        self.merge_timepoints = params.merge_timepoints if hasattr(params, "merge_timepoints") else False
        self.merge_hcs_regions = params.merge_hcs_regions if hasattr(params, "merge_hcs_regions") else False

        # Setup output paths using the standardized base folder
        self.per_timepoint_region_output_template = os.path.join(
            self.output_folder, "{timepoint}_stitched", "{region}_stitched" + self.output_format
        )

        if self.merge_timepoints:
            self.region_time_series_dir = os.path.join(self.output_folder, "region_time_series")
            os.makedirs(self.region_time_series_dir, exist_ok=True)
            self.merged_timepoints_output_template = os.path.join(
                self.region_time_series_dir, "{region}_time_series" + self.output_format
            )

        if self.merge_hcs_regions:
            self.hcs_timepoints_dir = os.path.join(self.output_folder, "hcs_timepoints")
            os.makedirs(self.hcs_timepoints_dir, exist_ok=True)
            self.merged_hcs_output_template = os.path.join(
                self.hcs_timepoints_dir, "{timepoint}_hcs" + self.output_format
            )

        if self.merge_timepoints and self.merge_hcs_regions:
            self.complete_hcs_output_path = os.path.join(self.hcs_timepoints_dir, "complete_hcs" + self.output_format)

        # Other processing parameters
        self.apply_flatfield = params.apply_flatfield
        self.use_registration = params.use_registration

        if self.use_registration:
            self.registration_channel = params.registration_channel
            self.registration_z_level = params.registration_z_level
            self.dynamic_registration = params.dynamic_registration

        # Initialize state
        self.scan_pattern = params.scan_pattern
        self.init_stitching_parameters()

    def __enter__(self):
        """Initialize the process when entering the context."""
        self.start()  # Start the process
        return self  # Return the instance for use inside the context

    def __exit__(self, exc_type, exc_value, traceback):
        """Clean up the process when exiting the context."""
        if self.is_alive():
            self.terminate()  # Terminate the process if it's still running
            self.join()  # Wait for the process to fully terminate

    def init_stitching_parameters(self):
        """Initialize core stitching parameters and state variables."""
        self.pixel_size_um = None
        self.acquisition_params = None
        self.timepoints = []
        self.regions = []
        self.channel_names = []
        self.monochrome_channels = []
        self.monochrome_colors = []
        self.num_z = self.num_c = self.num_t = 1
        self.input_height = self.input_width = 0
        self.num_pyramid_levels = 5
        self.flatfields = {}
        self.acquisition_metadata = {}
        self.dtype = np.uint16
        self.chunks = (1, 1, 1, 2048, 2048)  # (1, 1, 1, 4096, 4096)
        self.h_shift = (0, 0)
        if self.scan_pattern == "S-Pattern":
            self.h_shift_rev = (0, 0)
            self.h_shift_rev_odd = 0
        self.v_shift = (0, 0)
        self.x_positions = set()
        self.y_positions = set()

    def emit_progress(self, current: int, total: int):
        """Send progress update through queue.
        Args:
            current (int): Current progress value
            total (int): Total progress value
        """
        if self.progress_queue is None:
            print(f"PROGRESS: {(current, total)}")
        else:
            self.progress_queue.put(("progress", (current, total)))

    def emit_status(self, status: str, is_saving: bool = False):
        """Send status update through queue.
        Args:
            status (str): Status message
            is_saving (bool): Whether the status is about saving data
        """
        if self.status_queue is None:
            print(f"STATUS: {status}")
        else:
            self.status_queue.put(("status", (status, is_saving)))

    def emit_complete(self, output_path: str, dtype):
        """Send completion status through queue.
        Args:
            output_path (str): Path to the output file
            dtype: Data type of the output
        """
        if self.complete_queue is None:
            print(f"COMPLETE:")
        else:
            self.complete_queue.put(("complete", (output_path, dtype)))

    def check_stop(self):
        """Check if processing should stop and terminate if needed."""
        if self.stop_event.is_set():
            print("Stop event detected, terminating process...")
            self.cleanup()
            sys.exit(0)
            return

    def cleanup(self):
        """Clean up resources before termination."""
        try:
            # Clear queues
            for queue in [self.progress_queue, self.status_queue, self.complete_queue]:
                if queue:
                    while not queue.empty():
                        try:
                            queue.get_nowait()
                        except Empty:
                            pass

            import gc

            gc.collect()  # Force garbage collection

            self.emit_status("Process Stopped...")

        except Exception as e:
            print(f"Error during cleanup: {str(e)}")
            # Continue with termination even if cleanup fails

    def get_timepoints(self):
        """Get list of timepoints from input directory.

        Returns:
            list: List of timepoint folder names
        """
        self.timepoints = [
            d
            for d in os.listdir(self.input_folder)
            if os.path.isdir(os.path.join(self.input_folder, d)) and d.isdigit()
        ]
        self.timepoints.sort(key=int)
        return self.timepoints

    def extract_acquisition_parameters(self):
        """Extract acquisition parameters from JSON file."""
        acquisition_params_path = os.path.join(self.input_folder, "acquisition parameters.json")
        with open(acquisition_params_path, "r") as file:
            self.acquisition_params = json.load(file)

    def get_pixel_size(self):
        """Calculate pixel size based on acquisition parameters."""
        obj_mag = self.acquisition_params["objective"]["magnification"]
        obj_tube_lens_mm = self.acquisition_params["objective"]["tube_lens_f_mm"]
        sensor_pixel_size_um = self.acquisition_params["sensor_pixel_size_um"]
        tube_lens_mm = self.acquisition_params["tube_lens_mm"]
        self.pixel_binning = self.acquisition_params.get("pixel_binning", 1)
        obj_focal_length_mm = obj_tube_lens_mm / obj_mag
        actual_mag = tube_lens_mm / obj_focal_length_mm
        self.pixel_size_um = sensor_pixel_size_um / actual_mag
        print("pixel_size_um:", self.pixel_size_um)

    def parse_acquisition_metadata(self):
        """Parse image filenames and match them to coordinates for stitching.
        Handles multiple channels, regions, timepoints, z levels.
        """
        self.acquisition_metadata = {}
        self.regions = set()
        self.channel_names = set()
        max_z = 0
        max_fov = 0

        # Iterate over each timepoint
        for timepoint in self.timepoints:

            image_folder = os.path.join(self.input_folder, str(timepoint))
            coordinates_path = os.path.join(self.input_folder, timepoint, "coordinates.csv")
            try:
                coordinates_df = pd.read_csv(coordinates_path)
            except FileNotFoundError:
                print(f"Warning: coordinates.csv not found for timepoint {timepoint}")
                continue

            # Process each image file
            image_files = sorted(
                [
                    f
                    for f in os.listdir(image_folder)
                    if f.endswith((".bmp", ".tiff", "tif", "jpg", "jpeg", "png"))
                    and not f.startswith(".")
                    and "focus_camera" not in f
                ]
            )
            for file in image_files:
                parts = file.split("_", 3)
                region, fov, z_level = parts[0], int(parts[1]), int(parts[2])
                channel = os.path.splitext(parts[3])[0].replace("_", " ").replace("full ", "full_")
                coord_row = coordinates_df[
                    (coordinates_df["region"] == region)
                    & (coordinates_df["fov"] == fov)
                    & (coordinates_df["z_level"] == z_level)
                ]

                if coord_row.empty:
                    print(f"Warning: No coordinates for {file}")
                    continue

                coord_row = coord_row.iloc[0]

                # Create key with actual timepoint value
                key = (int(timepoint), region, fov, z_level, channel)
                print(key)

                self.acquisition_metadata[key] = {
                    "filepath": os.path.join(image_folder, file),
                    "x": coord_row["x (mm)"],
                    "y": coord_row["y (mm)"],
                    "z": coord_row["z (um)"],
                    "channel": channel,
                    "z_level": z_level,
                    "region": region,
                    "fov_idx": fov,
                    "t": int(timepoint),
                }

                # Update metadata sets
                self.regions.add(region)
                self.channel_names.add(channel)
                max_z = max(max_z, z_level)
                max_fov = max(max_fov, fov)

        # Finalize metadata
        self.regions = sorted(self.regions)
        self.channel_names = sorted(self.channel_names)

        # Calculate dimensions
        self.num_t = len(self.timepoints)
        self.num_z = max_z + 1
        self.num_fovs_per_region = max_fov + 1

        # Set up image parameters based on the first image
        first_key = list(self.acquisition_metadata.keys())[0]
        first_image = dask_imread(self.acquisition_metadata[first_key]["filepath"])[0]

        self.dtype = first_image.dtype
        if len(first_image.shape) == 2:
            self.input_height, self.input_width = first_image.shape
        elif len(first_image.shape) == 3:
            self.input_height, self.input_width = first_image.shape[:2]
        else:
            raise ValueError(f"Unexpected image shape: {first_image.shape}")

        # Set up final monochrome channels
        self.monochrome_channels = []
        first_timepoint = self.acquisition_metadata[first_key]["t"]
        first_region = self.acquisition_metadata[first_key]["region"]
        first_fov = self.acquisition_metadata[first_key]["fov_idx"]
        first_z_level = self.acquisition_metadata[first_key]["z_level"]

        for channel in self.channel_names:
            channel_key = (first_timepoint, first_region, first_fov, first_z_level, channel)
            channel_image = dask_imread(self.acquisition_metadata[channel_key]["filepath"])[0]
            if len(channel_image.shape) == 3 and channel_image.shape[2] == 3:
                channel = channel.split("_")[0]
                self.monochrome_channels.extend([f"{channel}_R", f"{channel}_G", f"{channel}_B"])
            else:
                self.monochrome_channels.append(channel)

        self.num_c = len(self.monochrome_channels)
        self.monochrome_colors = [self.get_channel_color(name) for name in self.monochrome_channels]

        # Print dataset information
        print(f"{self.num_t} timepoints")
        print(f"{self.num_z} z-levels")
        print(f"{self.num_c} channels: {self.monochrome_channels}")
        print(f"{len(self.regions)} regions: {self.regions}\n")

    def get_region_data(self, t, region):
        """Get region data with consistent filtering.
        Args:
            t (int): Timepoint
            region (str): Region identifier

        Returns:
            dict: Filtered metadata for the specified timepoint and region
        """
        t = int(t)

        # Filter data with explicit type matching
        data = {}
        for key, value in self.acquisition_metadata.items():
            key_t, key_region, _, _, _ = key
            if key_t == t and key_region == region:
                data[key] = value

        if not data:
            available_t = sorted(set(k[0] for k in self.acquisition_metadata.keys()))
            available_r = sorted(set(k[1] for k in self.acquisition_metadata.keys()))
            print(f"\nAvailable timepoints in data: {available_t}")
            print(f"Available regions in data: {available_r}")
            raise ValueError(f"No data found for timepoint {t}, region {region}")

        return data

    def get_channel_color(self, channel_name):
        """Get the color for a given channel.
        Args:
            channel_name (str): Name of the channel

        Returns:
            int: Hex color value for the channel
        """
        color_map = {
            "405": 0x0000FF,  # Blue
            "488": 0x00FF00,  # Green
            "561": 0xFFCF00,  # Yellow
            "638": 0xFF0000,  # Red
            "730": 0x770000,  # Dark Red
            "_B": 0x0000FF,  # Blue
            "_G": 0x00FF00,  # Green
            "_R": 0xFF0000,  # Red
        }
        for key in color_map:
            if key in channel_name:
                return color_map[key]
        return 0xFFFFFF  # Default to white if no match found

    def calculate_output_dimensions(self, timepoint, region):
        """Calculate dimensions for the output image.
        Args:
            timepoint (int/str): The timepoint to process
            region (str): The region identifier

        Returns:
            tuple: (width_pixels, height_pixels)
        """
        # Convert timepoint to int
        t = int(timepoint)

        # Get region data
        region_data = self.get_region_data(t, region)
        # Extract positions
        self.x_positions = sorted(set(tile_info["x"] for tile_info in region_data.values()))
        self.y_positions = sorted(set(tile_info["y"] for tile_info in region_data.values()))

        if self.use_registration:
            # Calculate dimensions with registration shifts
            num_cols = len(self.x_positions)
            num_rows = len(self.y_positions)

            # Handle different scanning patterns
            if self.scan_pattern == "S-Pattern":
                max_h_shift = (
                    max(abs(self.h_shift[0]), abs(self.h_shift_rev[0])),
                    max(abs(self.h_shift[1]), abs(self.h_shift_rev[1])),
                )
            else:
                max_h_shift = (abs(self.h_shift[0]), abs(self.h_shift[1]))

            # Calculate dimensions including overlaps and shifts
            width_pixels = int(self.input_width + ((num_cols - 1) * (self.input_width - max_h_shift[1])))
            width_pixels += abs((num_rows - 1) * self.v_shift[1])  # Add horizontal shift from vertical registration

            height_pixels = int(self.input_height + ((num_rows - 1) * (self.input_height - self.v_shift[0])))
            height_pixels += abs((num_cols - 1) * max_h_shift[0])  # Add vertical shift from horizontal registration

        else:
            # Calculate dimensions based on physical coordinates
            width_mm = max(self.x_positions) - min(self.x_positions) + (self.input_width * self.pixel_size_um / 1000)
            height_mm = max(self.y_positions) - min(self.y_positions) + (self.input_height * self.pixel_size_um / 1000)

            width_pixels = int(np.ceil(width_mm * 1000 / self.pixel_size_um))
            height_pixels = int(np.ceil(height_mm * 1000 / self.pixel_size_um))

        # Calculate pyramid levels based on dimensions and number of regions
        if len(self.regions) > 1:
            rows, columns = self.get_rows_and_columns()
            max_dimension = max(len(rows), len(columns))
        else:
            max_dimension = 1

        self.num_pyramid_levels = max(1, math.ceil(np.log2(max(width_pixels, height_pixels) / 1024 * max_dimension)))

        return width_pixels, height_pixels

    def get_rows_and_columns(self):
        """Get unique rows and columns from regions.

        Returns:
            tuple: (rows, columns) where each is a sorted list of unique values
        """
        rows = sorted(set(region[0] for region in self.regions))
        columns = sorted(set(region[1:] for region in self.regions))
        return rows, columns

    def init_output(self, timepoint, region):
        """Initialize output array for a region.
        Args:
            timepoint: The timepoint to process
            region: The region identifier

        Returns:
            dask.array: Initialized output array
        """
        # Get region dimensions
        width, height = self.calculate_output_dimensions(timepoint, region)
        # Create zeros with the right shape/dtype per timepoint per region
        output_shape = (1, self.num_c, self.num_z, height, width)
        print(f"Region {region}, Timepoint {timepoint} output dimensions: {output_shape}")
        return da.zeros(output_shape, dtype=self.dtype, chunks=self.chunks)

    def get_flatfields(self):
        """Calculate flatfields for each channel using BaSiC."""

        def process_images(images, channel_name):
            """Process a set of images to calculate flatfield correction.

            Args:
                images: Array of images to process
                channel_name: Name of the channel being processed
            """
            if images.size == 0:
                print(f"Warning: No images found for channel {channel_name}")
                return

            if images.ndim != 3 and images.ndim != 4:
                raise ValueError(
                    f"Images must be 3 or 4-dimensional array, with dimension of (T, Y, X) or (T, Z, Y, X). Got shape {images.shape}"
                )

            basic = BaSiC(get_darkfield=False, smoothness_flatfield=1)
            basic.fit(images)
            channel_index = self.monochrome_channels.index(channel_name)
            self.flatfields[channel_index] = basic.flatfield
            self.emit_progress(channel_index + 1, self.num_c)

        self.emit_progress(0, self.num_c)
        for channel in self.channel_names:
            self.check_stop()
            self.emit_status(f"Calculating Flatfield... ({channel})")
            images = []

            for t in self.timepoints:
                time_images = [
                    dask_imread(tile["filepath"])[0]
                    for key, tile in self.acquisition_metadata.items()
                    if tile["channel"] == channel and key[0] == int(t)
                ]

                if not time_images:
                    print(f"Warning: No images found for channel {channel} at timepoint {t}")
                    continue

                random.shuffle(time_images)
                selected_tiles = time_images[: min(32, len(time_images))]
                images.extend(selected_tiles)

                if len(images) > 48:
                    break

            if not images:
                print(f"Warning: No images found for channel {channel} across all timepoints")
                continue

            images = np.array(images)

            if images.ndim == 3:
                # Images are in the shape (N, Y, X)
                process_images(images, channel)
            elif images.ndim == 4:
                if images.shape[-1] == 3:
                    # Images are in the shape (N, Y, X, 3) for RGB images
                    images_r = images[..., 0]
                    images_g = images[..., 1]
                    images_b = images[..., 2]
                    channel = channel.split("_")[0]
                    process_images(images_r, channel + "_R")
                    process_images(images_g, channel + "_G")
                    process_images(images_b, channel + "_B")
                else:
                    # Images are in the shape (N, Z, Y, X)
                    process_images(images, channel)
            else:
                raise ValueError(f"Unexpected number of dimensions in images array: {images.ndim}")

    def calculate_shifts(self, t, region):
        """Calculate registration shifts between tiles.
        Args:
            t (int): Timepoint
            region (str): Region identifier
        """
        region_data = self.get_region_data(t, region)
        # Get unique x and y positions
        x_positions = sorted(set(tile_info["x"] for tile_info in region_data.values()))
        y_positions = sorted(set(tile_info["y"] for tile_info in region_data.values()))

        # Initialize shifts
        self.h_shift = (0, 0)
        self.v_shift = (0, 0)

        # Set registration channel if not already set
        if not self.registration_channel:
            self.registration_channel = self.channel_names[0]
        elif self.registration_channel not in self.channel_names:
            print(f"Warning: Registration channel '{self.registration_channel}' not found")
            print(f"Using {self.channel_names[0]} for registration")
            self.registration_channel = self.channel_names[0]
        self.emit_status(f"Calculating Registration Shifts...")

        self.calculate_output_dimensions(int(t), region)
        x_pos_list = sorted(list(self.x_positions))
        y_pos_list = sorted(list(self.y_positions))

        # Calculate spacing between positions
        dx_mm = x_pos_list[1] - x_pos_list[0]
        dy_mm = y_pos_list[1] - y_pos_list[0]

        dx_pixels = dx_mm * 1000 / self.pixel_size_um
        dy_pixels = dy_mm * 1000 / self.pixel_size_um

        max_x_overlap = round(abs(self.input_width - dx_pixels) * 1.05) // 2 * self.pixel_binning
        max_y_overlap = round(abs(self.input_height - dy_pixels) * 1.05) // 2 * self.pixel_binning
        ## print("objective calculated - vertical overlap:", max_y_overlap, ", horizontal overlap:", max_x_overlap)

        # Find center positions
        center_tile_x_index = (len(x_positions) - 1) // 2
        center_tile_y_index = (len(y_positions) - 1) // 2

        center_tile_x = x_positions[center_tile_x_index]
        center_tile_y = y_positions[center_tile_y_index]

        right_tile_x = None
        bottom_tile_y = None

        # Calculate horizontal shift
        if center_tile_x_index + 1 < len(x_positions):
            right_tile_x = x_positions[center_tile_x_index + 1]
            center_tile = self.get_tile(
                t, region, center_tile_x, center_tile_y, self.registration_channel, self.registration_z_level
            )
            right_tile = self.get_tile(
                t, region, right_tile_x, center_tile_y, self.registration_channel, self.registration_z_level
            )

            if center_tile is not None and right_tile is not None:
                self.h_shift = self.calculate_horizontal_shift(center_tile, right_tile, max_x_overlap)
            else:
                print(f"Warning: Missing tiles for horizontal shift calculation in region {region}.")

        # Calculate vertical shift
        if center_tile_y_index + 1 < len(y_positions):
            bottom_tile_y = y_positions[center_tile_y_index + 1]
            center_tile = self.get_tile(
                t, region, center_tile_x, center_tile_y, self.registration_channel, self.registration_z_level
            )
            bottom_tile = self.get_tile(
                t, region, center_tile_x, bottom_tile_y, self.registration_channel, self.registration_z_level
            )

            if center_tile is not None and bottom_tile is not None:
                self.v_shift = self.calculate_vertical_shift(center_tile, bottom_tile, max_y_overlap)
            else:
                print(f"Warning: Missing tiles for vertical shift calculation in region {region}.")

        # Handle S-Pattern scanning
        if self.scan_pattern == "S-Pattern" and right_tile_x and bottom_tile_y:
            center_tile = self.get_tile(
                t, region, center_tile_x, bottom_tile_y, self.registration_channel, self.registration_z_level
            )
            right_tile = self.get_tile(
                t, region, right_tile_x, bottom_tile_y, self.registration_channel, self.registration_z_level
            )

            if center_tile is not None and right_tile is not None:
                self.h_shift_rev = self.calculate_horizontal_shift(center_tile, right_tile, max_x_overlap)
                self.h_shift_rev_odd = center_tile_y_index % 2 == 0
                print(f"Bi-Directional Horizontal Shift - Reverse Horizontal: {self.h_shift_rev}")
            else:
                print(f"Warning: Missing tiles for reverse horizontal shift calculation in region {region}.")

        print(f"Calculated Shifts - Horizontal: {self.h_shift}, Vertical: {self.v_shift}")

    def calculate_horizontal_shift(self, img_left, img_right, max_overlap):
        """Calculate horizontal shift between two images using phase correlation.
        Args:
            img_left: Left image
            img_right: Right image
            max_overlap: Maximum overlap to consider

        Returns:
            tuple: (vertical_shift, horizontal_shift)
        """
        img_left = self.normalize_image(img_left)
        img_right = self.normalize_image(img_right)

        margin = int(img_left.shape[0] * 0.25)  # 25% margin
        img_left_overlap = img_left[margin:-margin, -max_overlap:]
        img_right_overlap = img_right[margin:-margin, :max_overlap]

        self.visualize_image(img_left_overlap, img_right_overlap, "horizontal")

        shift, error, diffphase = phase_cross_correlation(img_left_overlap, img_right_overlap, upsample_factor=10)
        return round(shift[0]), round(shift[1] - img_left_overlap.shape[1])

    def calculate_vertical_shift(self, img_top, img_bot, max_overlap):
        """Calculate vertical shift between two images using phase correlation.
        Args:
            img_top: Top image
            img_bot: Bottom image
            max_overlap: Maximum overlap to consider

        Returns:
            tuple: (vertical_shift, horizontal_shift)
        """
        img_top = self.normalize_image(img_top)
        img_bot = self.normalize_image(img_bot)

        margin = int(img_top.shape[1] * 0.25)  # 25% margin
        img_top_overlap = img_top[-max_overlap:, margin:-margin]
        img_bot_overlap = img_bot[:max_overlap, margin:-margin]

        self.visualize_image(img_top_overlap, img_bot_overlap, "vertical")

        shift, error, diffphase = phase_cross_correlation(img_top_overlap, img_bot_overlap, upsample_factor=10)
        return round(shift[0] - img_top_overlap.shape[0]), round(shift[1])

    def get_tile(self, t, region, x, y, channel, z_level):
        """Get a specific tile using standardized data access.
        Args:
            t: Timepoint
            region: Region identifier
            x: X coordinate
            y: Y coordinate
            channel: Channel name
            z_level: Z-level

        Returns:
            array: Tile image data or None if not found
        """
        region_data = self.get_region_data(int(t), str(region))

        for key, value in region_data.items():
            if value["x"] == x and value["y"] == y and value["channel"] == channel and value["z_level"] == z_level:
                try:
                    return dask_imread(value["filepath"])[0]
                except FileNotFoundError:
                    print(f"Warning: Tile file not found: {value['filepath']}")
                    return None

        print(f"Warning: No matching tile found for region {region}, x={x}, y={y}, channel={channel}, z={z_level}")
        return None

    def place_tile(self, stitched_region, tile, x_pixel, y_pixel, z_level, channel, t):
        """Place a tile in the stitched region.
        Args:
            stitched_region: Output array
            tile: Tile image data
            x_pixel: X position in pixels
            y_pixel: Y position in pixels
            z_level: Z-level
            channel: Channel name
            t: Timepoint
        """
        if len(tile.shape) == 2:
            # Handle 2D grayscale image
            channel_idx = self.monochrome_channels.index(channel)
            self.place_single_channel_tile(stitched_region, tile, x_pixel, y_pixel, z_level, channel_idx, 0)

        elif len(tile.shape) == 3:
            if tile.shape[2] == 3:
                # Handle RGB image
                channel = channel.split("_")[0]
                for i, color in enumerate(["R", "G", "B"]):
                    channel_idx = self.monochrome_channels.index(f"{channel}_{color}")
                    self.place_single_channel_tile(
                        stitched_region, tile[:, :, i], x_pixel, y_pixel, z_level, channel_idx, 0
                    )
            elif tile.shape[0] == 1:
                channel_idx = self.monochrome_channels.index(channel)
                self.place_single_channel_tile(stitched_region, tile[0], x_pixel, y_pixel, z_level, channel_idx, 0)
        else:
            raise ValueError(f"Unexpected tile shape: {tile.shape}")

    def place_single_channel_tile(self, stitched_region, tile, x_pixel, y_pixel, z_level, channel_idx, t):
        """Place a single channel tile in the stitched region.
        Args:
            stitched_region: Output array
            tile: Single channel tile data
            x_pixel: X position in pixels
            y_pixel: Y position in pixels
            z_level: Z-level
            channel_idx: Channel index
            t: Timepoint
        """
        if len(stitched_region.shape) != 5:
            raise ValueError(
                f"Unexpected stitched_region shape: {stitched_region.shape}. " f"Expected 5D array (t, c, z, y, x)."
            )

        if self.apply_flatfield:
            tile = self.apply_flatfield_correction(tile, channel_idx)

        if self.use_registration:
            if self.scan_pattern == "S-Pattern" and self.row_index % 2 == self.h_shift_rev_odd:
                h_shift = self.h_shift_rev
            else:
                h_shift = self.h_shift

            # Determine crop for tile edges
            top_crop = max(0, (-self.v_shift[0] // 2) - abs(h_shift[0]) // 2) if self.row_index > 0 else 0
            bottom_crop = (
                max(0, (-self.v_shift[0] // 2) - abs(h_shift[0]) // 2)
                if self.row_index < len(self.y_positions) - 1
                else 0
            )
            left_crop = max(0, (-h_shift[1] // 2) - abs(self.v_shift[1]) // 2) if self.col_index > 0 else 0
            right_crop = (
                max(0, (-h_shift[1] // 2) - abs(self.v_shift[1]) // 2)
                if self.col_index < len(self.x_positions) - 1
                else 0
            )

            # Apply cropping to the tile
            tile = tile[top_crop : tile.shape[0] - bottom_crop, left_crop : tile.shape[1] - right_crop]

            # Adjust x_pixel and y_pixel based on cropping
            x_pixel += left_crop
            y_pixel += top_crop

        # Calculate end points based on stitched_region shape
        y_end = min(y_pixel + tile.shape[0], stitched_region.shape[3])
        x_end = min(x_pixel + tile.shape[1], stitched_region.shape[4])

        # Extract the tile slice we'll use
        tile_slice = tile[: y_end - y_pixel, : x_end - x_pixel]

        try:
            # Place the tile slice - use t=0 since we're working with 1-timepoint arrays
            stitched_region[0, channel_idx, z_level, y_pixel:y_end, x_pixel:x_end] = tile_slice
        except Exception as e:
            print(f"ERROR: Failed to place tile. Details: {str(e)}")
            print(
                f"DEBUG: t:0, channel_idx:{channel_idx}, z_level:{z_level}, "
                f"y:{y_pixel}-{y_end}, x:{x_pixel}-{x_end}"
            )
            print(f"DEBUG: tile slice shape: {tile_slice.shape}")
            print(f"DEBUG: stitched_region shape: {stitched_region.shape}")
            print(
                f"DEBUG: output location shape: "
                f"{stitched_region[0, channel_idx, z_level, y_pixel:y_end, x_pixel:x_end].shape}"
            )
            raise

    def apply_flatfield_correction(self, tile, channel_idx):
        """Apply flatfield correction to a tile.
        Args:
            tile: Tile image data
            channel_idx: Channel index

        Returns:
            array: Corrected tile data
        """
        if channel_idx in self.flatfields:
            return (
                (tile / self.flatfields[channel_idx])
                .clip(min=np.iinfo(self.dtype).min, max=np.iinfo(self.dtype).max)
                .astype(self.dtype)
            )
        return tile

    def normalize_image(self, img):
        """Normalize image to full dynamic range.
        Args:
            img: Input image

        Returns:
            array: Normalized image
        """
        img_min, img_max = img.min(), img.max()
        img_normalized = (img - img_min) / (img_max - img_min)
        scale_factor = np.iinfo(self.dtype).max if np.issubdtype(self.dtype, np.integer) else 1
        return (img_normalized * scale_factor).astype(self.dtype)

    def visualize_image(self, img1, img2, title):
        """Save visualization of image overlaps for debugging.
        Args:
            img1: First image
            img2: Second image
            title: Title for the visualization
        """
        try:
            # Ensure images are numpy arrays
            img1 = np.asarray(img1)
            img2 = np.asarray(img2)

            if title == "horizontal":
                combined_image = np.hstack((img1, img2))
            else:
                combined_image = np.vstack((img1, img2))

            # Convert to uint8 for saving as PNG
            combined_image_uint8 = (combined_image / np.iinfo(self.dtype).max * 255).astype(np.uint8)

            cv2.imwrite(f"{self.output_folder}/{title}.png", combined_image_uint8)

            print(f"Saved {title}.png successfully")
        except Exception as e:
            print(f"Error in visualize_image: {e}")

    def stitch_region(self, timepoint, region):
        """Stitch and save single region for a specific timepoint.
        Args:
            timepoint: The timepoint to process
            region: The region identifier

        Returns:
            array: Stitched region data or None if stopped
        """
        start_time = time.time()

        try:
            # Initialize output array
            region_data = self.get_region_data(int(timepoint), region)
            stitched_region = self.init_output(timepoint, region)
            x_min = min(self.x_positions)
            y_min = min(self.y_positions)

            total_tiles = len(region_data)
            processed_tiles = 0

            print(f"Stitching {total_tiles} Images... (Timepoint:{timepoint} Region:{region})")
            self.emit_status(f"Stitching... (Timepoint:{timepoint} Region:{region})")

            # Process each tile with progress tracking
            for key, tile_info in region_data.items():
                t, _, fov, z_level, channel = key

                self.check_stop()
                try:
                    tile = dask_imread(tile_info["filepath"])[0]
                except Exception as e:
                    self.emit_status(f"Error Loading Image {tile_info['filepath']}: {str(e)}")
                    continue

                # Calculate pixel positions
                if self.use_registration:
                    self.col_index = self.x_positions.index(tile_info["x"])
                    self.row_index = self.y_positions.index(tile_info["y"])

                    if self.scan_pattern == "S-Pattern" and self.row_index % 2 == self.h_shift_rev_odd:
                        h_shift = self.h_shift_rev
                    else:
                        h_shift = self.h_shift

                    x_pixel = int(self.col_index * (self.input_width + h_shift[1]))
                    y_pixel = int(self.row_index * (self.input_height + self.v_shift[0]))

                    if h_shift[0] < 0:
                        y_pixel += int((len(self.x_positions) - 1 - self.col_index) * abs(h_shift[0]))
                    else:
                        y_pixel += int(self.col_index * h_shift[0])

                    if self.v_shift[1] < 0:
                        x_pixel += int((len(self.y_positions) - 1 - self.row_index) * abs(self.v_shift[1]))
                    else:
                        x_pixel += int(self.row_index * self.v_shift[1])
                else:
                    x_pixel = int((tile_info["x"] - x_min) * 1000 / self.pixel_size_um)
                    y_pixel = int((tile_info["y"] - y_min) * 1000 / self.pixel_size_um)

                # Place the tile
                self.place_tile(stitched_region, tile, x_pixel, y_pixel, z_level, channel, t)

                # Update progress
                processed_tiles += 1
                self.emit_progress(processed_tiles, total_tiles)

            print(f"(Timepoint:{timepoint}, Region:{region}) Complete Stitching in {time.time() - start_time:.1f}s\n")
            return stitched_region

        except Exception as e:
            self.status_queue.put(("error", f"Error stitching region {region}: {str(e)}"))
            raise

    def save_region_aics(self, timepoint, region, stitched_region):
        """Save stitched region data as OME-ZARR or OME-TIFF using aicsimageio.
        Args:
            timepoint: Timepoint being saved
            region: Region identifier
            stitched_region: Stitched image data

        Returns:
            str: Path to saved output file
        """
        start_time = time.time()
        self.emit_status(f"Saving... (Timepoint:{timepoint} Region:{region})", is_saving=True)

        # Ensure output directory exists
        output_path = os.path.join(
            self.output_folder, f"{timepoint}_stitched", f"{region}_stitched{self.output_format}"
        )
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        # Create physical pixel sizes object
        physical_pixel_sizes = aics_types.PhysicalPixelSizes(
            Z=self.acquisition_params.get("dz(um)", 1.0), Y=self.pixel_size_um, X=self.pixel_size_um
        )

        # Prepare channel intensity ranges
        channel_minmax = [(np.iinfo(self.dtype).min, np.iinfo(self.dtype).max) for _ in range(self.num_c)]

        # Convert colors to RGB lists for OME format
        rgb_colors = [[c >> 16, (c >> 8) & 0xFF, c & 0xFF] for c in self.monochrome_colors]

        if self.output_format.endswith(".zarr"):
            print(f"Writing OME-ZARR to: {output_path}\n")
            writer = aicsZarrWriter(output_path)

            # Write the image with metadata
            writer.write_image(
                image_data=stitched_region,
                image_name=f"{region}_t{timepoint}",
                physical_pixel_sizes=physical_pixel_sizes,
                channel_names=self.monochrome_channels,
                channel_colors=self.monochrome_colors,
                channel_minmax=channel_minmax,
                chunk_dims=self.chunks,
                scale_num_levels=self.num_pyramid_levels,
                scale_factor=2.0,
                dimension_order="TCZYX",
            )

        else:  # .tiff
            print(f"Writing OME-TIFF to: {output_path}\n")

            # Build OME metadata for TIFF
            ome_meta = aicsTiffWriter.build_ome(
                data_shapes=[stitched_region.shape],
                data_types=[stitched_region.dtype],
                dimension_order=["TCZYX"],
                channel_names=[self.monochrome_channels],
                image_name=[f"{region}_t{timepoint}"],
                physical_pixel_sizes=[physical_pixel_sizes],
                channel_colors=[rgb_colors],
            )

            # Write the image with metadata
            aicsTiffWriter.save(
                data=stitched_region,
                uri=output_path,
                dim_order="TCZYX",
                ome_xml=ome_meta,
                channel_names=self.monochrome_channels,
                image_name=f"{region}_t{timepoint}",
                physical_pixel_sizes=physical_pixel_sizes,
                channel_colors=rgb_colors,
            )

        print(f"Successfully saved to: {output_path}")
        print(f"Time to save region {region} timepoint {timepoint}: {time.time() - start_time}")
        return output_path

    def save_region_ome_zarr(self, timepoint, region, stitched_region):
        """Save stitched region data as OME-ZARR using direct pyramid writing.
        Args:
            timepoint: Timepoint being saved
            region: Region identifier
            stitched_region: Stitched image data

        Returns:
            str: Path to saved OME-ZARR file
        """
        start_time = time.time()
        output_path = os.path.join(self.output_folder, f"{timepoint}_stitched", f"{region}_stitched.ome.zarr")
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        self.emit_status(f"Saving... (Timepoint:{timepoint} Region:{region})", is_saving=True)
        print(f"Writing OME-ZARR to: {output_path}\n")

        # Create zarr store and root group
        store = ome_zarr.io.parse_url(output_path, mode="w").store
        root = zarr.group(store=store)

        # Calculate pyramid using scaler
        scaler = ome_zarr.scale.Scaler(max_layer=self.num_pyramid_levels - 1)
        pyramid = scaler.nearest(stitched_region)

        # Define physical coordinates with proper micrometer scaling
        transforms = []
        for level in range(self.num_pyramid_levels):
            scale = 2**level
            transforms.append(
                [
                    {
                        "type": "scale",
                        "scale": [
                            1,  # time
                            1,  # channels
                            float(self.acquisition_params.get("dz(um)", 1.0)),  # z in microns
                            float(self.pixel_size_um * scale),  # y with pyramid scaling
                            float(self.pixel_size_um * scale),  # x with pyramid scaling
                        ],
                    }
                ]
            )

        # Configure storage options with optimal chunking
        storage_opts = {"chunks": self.chunks, "compressor": zarr.storage.default_compressor}

        # Write pyramid data with full metadata
        ome_zarr.writer.write_multiscale(
            pyramid=pyramid,
            group=root,
            axes=[
                {"name": "t", "type": "time", "unit": "second"},
                {"name": "c", "type": "channel"},
                {"name": "z", "type": "space", "unit": "micrometer"},
                {"name": "y", "type": "space", "unit": "micrometer"},
                {"name": "x", "type": "space", "unit": "micrometer"},
            ],
            coordinate_transformations=transforms,
            storage_options=storage_opts,
            name=f"{region}_t{timepoint}",
            fmt=ome_zarr.format.CurrentFormat(),
        )

        # Add complete OMERO metadata for visualization
        root.attrs["omero"] = {
            "id": 1,
            "name": f"{region}_t{timepoint}",
            "version": "0.4",
            "channels": [
                {
                    "label": name,
                    "color": f"{color:06X}",
                    "window": {"start": 0, "end": np.iinfo(self.dtype).max, "min": 0, "max": np.iinfo(self.dtype).max},
                    "active": True,
                    "coefficient": 1,
                    "family": "linear",
                }
                for name, color in zip(self.monochrome_channels, self.monochrome_colors)
            ],
        }

        print(f"Successfully saved OME-ZARR to: {output_path}")
        print(f"Time to save region {region} timepoint {timepoint}: {time.time() - start_time}")
        return output_path

    def save_region_parallel(self, timepoint, region, stitched_region):
        """Save stitched region data using Zarr with parallel chunk writing.

        Args:
            timepoint: Current timepoint
            region: Region identifier
            stitched_region: Data array to save

        Returns:
            str: Path to saved output file
        """
        start_time = time.time()

        # Configure output path
        output_path = os.path.join(
            self.output_folder, f"{timepoint}_stitched", f"{region}_stitched{self.output_format}"
        )
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        self.emit_status(f"Saving... (Timepoint:{timepoint} Region:{region})", is_saving=True)

        # Initialize zarr array
        store = zarr.DirectoryStore(output_path)
        root = zarr.group(store, overwrite=True)

        # Use zstd compression
        compressor = zarr.Blosc(cname="zstd", clevel=1, shuffle=zarr.Blosc.SHUFFLE)

        # Create dataset
        dataset = root.create_dataset(
            "0", shape=stitched_region.shape, chunks=self.chunks, dtype=stitched_region.dtype, compressor=compressor
        )

        # Write initial data
        small_slice = tuple(slice(0, min(c, s)) for c, s in zip(self.chunks, stitched_region.shape))
        dataset[small_slice] = stitched_region[small_slice]

        # Prepare chunks for parallel processing
        chunk_args = []
        for z in range(0, stitched_region.shape[2], self.chunks[2]):
            for y in range(0, stitched_region.shape[3], self.chunks[3]):
                for x in range(0, stitched_region.shape[4], self.chunks[4]):
                    # Calculate chunk boundaries
                    z_end = min(z + self.chunks[2], stitched_region.shape[2])
                    y_end = min(y + self.chunks[3], stitched_region.shape[3])
                    x_end = min(x + self.chunks[4], stitched_region.shape[4])

                    # Extract chunk data
                    chunk_data = stitched_region[:, :, z:z_end, y:y_end, x:x_end]
                    if isinstance(chunk_data, da.Array):
                        chunk_data = chunk_data.compute()

                    # Create argument tuple
                    args = ((z, y, x), output_path, chunk_data)
                    chunk_args.append(args)

        # Process chunks in parallel
        num_workers = min(cpu_count() // 2, 8)
        errors = []
        total_chunks = len(chunk_args)
        chunks_processed = 0

        print(f"\nProcessing {total_chunks} chunks using {num_workers} workers...")

        with Pool(num_workers) as pool:
            try:
                for result in pool.imap_unordered(write_chunk_worker, chunk_args):
                    chunks_processed += 1

                    if not result[0]:
                        errors.append(result[1])

                    if chunks_processed % 10 == 0:
                        print(f"Progress: {chunks_processed}/{total_chunks} chunks complete")

                    if self.progress_queue:
                        self.emit_progress(chunks_processed, total_chunks)

            except Exception as e:
                errors.append(str(e))

        # Handle any errors
        if errors:
            error_msg = f"Errors occurred while saving chunks: {errors}"
            print(error_msg)
            if self.status_queue:
                self.status_queue.put(("error", error_msg))
            raise RuntimeError(error_msg)

        # Add metadata
        root.attrs["multiscales"] = [
            {
                "datasets": [
                    {
                        "path": "0",
                        "coordinateTransformations": [
                            {
                                "type": "scale",
                                "scale": [
                                    1,  # Time
                                    1,  # Channels
                                    float(self.acquisition_params.get("dz(um)", 1.0)),  # Z
                                    float(self.pixel_size_um),  # Y
                                    float(self.pixel_size_um),  # X
                                ],
                            }
                        ],
                    }
                ],
                "version": "0.4",
            }
        ]

        # Add OMERO metadata
        root.attrs["omero"] = {
            "id": 1,
            "name": f"{region}_t{timepoint}",
            "version": "0.4",
            "channels": [
                {
                    "label": name,
                    "color": f"{color:06X}",
                    "window": {"start": 0, "end": np.iinfo(self.dtype).max, "min": 0, "max": np.iinfo(self.dtype).max},
                }
                for name, color in zip(self.monochrome_channels, self.monochrome_colors)
            ],
        }

        elapsed = time.time() - start_time
        print(f"\nSaved {output_path} in {elapsed:.1f}s using {num_workers} processes")
        return output_path

    def save_region_bioio(self, timepoint, region, stitched_region):
        """Save stitched region using BioIO's OmeZarrWriter or OmeTiffWriter."""
        start_time = time.time()
        output_path = os.path.join(
            self.output_folder, f"{timepoint}_stitched", f"{region}_stitched{self.output_format}"
        )
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        self.emit_status(f"Saving... (Timepoint:{timepoint} Region:{region})", is_saving=True)

        try:
            if self.output_format.endswith(".zarr"):
                writer = bioioZarrWriter(output_path)
                physical_pixel_sizes = bioio_types.PhysicalPixelSizes(
                    Z=self.acquisition_params.get("dz(um)", 1.0),
                    Y=self.pixel_size_um,
                    X=self.pixel_size_um,
                )

                writer.write_image(
                    image_data=stitched_region,
                    image_name=f"{region}_t{timepoint}",
                    physical_pixel_sizes=physical_pixel_sizes,
                    channel_names=self.monochrome_channels,
                    channel_colors=self.monochrome_colors,
                    chunk_dims=self.chunks,
                    scale_num_levels=self.num_pyramid_levels,
                    scale_factor=2.0,
                    dimension_order="TCZYX",
                )
            else:  # Save as TIFF
                physical_pixel_sizes = bioio_types.PhysicalPixelSizes(
                    Z=self.acquisition_params.get("dz(um)", 1.0),
                    Y=self.pixel_size_um,
                    X=self.pixel_size_um,
                )

                bioioTiffWriter.save(
                    data=stitched_region,
                    uri=output_path,
                    dim_order="TCZYX",
                    physical_pixel_sizes=physical_pixel_sizes,
                    channel_names=self.monochrome_channels,
                )

            elapsed = time.time() - start_time
            print(f"Saved {output_path} in {elapsed:.1f}s")
            return output_path
        except Exception as e:
            self.status_queue.put(("error", f"Error saving region {region}: {str(e)}"))
            raise

    def save_region_bioio_2(self, timepoint, region, stitched_region):
        """Save stitched region using bioio.ome_zarr_writer_2 for OME-Zarr."""
        start_time = time.time()
        output_path = os.path.join(self.output_folder, f"{timepoint}_stitched", f"{region}_stitched.ome.zarr")
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        self.emit_status(f"Saving... (Timepoint:{timepoint} Region:{region})", is_saving=True)

        try:
            shapes = compute_level_shapes(stitched_region.shape, (1, 1, 1, 2, 2), self.num_pyramid_levels)
            chunk_sizes = compute_level_chunk_sizes_zslice(shapes)

            writer = bioioZarrWriter2()
            writer.init_store(output_path, shapes, chunk_sizes, stitched_region.dtype)

            writer.write_t_batches_array(stitched_region, tbatch=4)

            metadata = writer.generate_metadata(
                image_name=f"{region}_t{timepoint}",
                channel_names=self.monochrome_channels,
                channel_colors=self.monochrome_colors,
                physical_dims={"x": self.pixel_size_um, "y": self.pixel_size_um, "z": 1.0},
                physical_units={"x": "micrometer", "y": "micrometer", "z": "micrometer"},
            )
            writer.write_metadata(metadata)

            elapsed = time.time() - start_time
            print(f"Saved {output_path} in {elapsed:.1f}s")
            return output_path
        except Exception as e:
            self.status_queue.put(("error", f"Error saving region {region}: {str(e)}"))
            raise

    def save_region_tiffile(self, timepoint, region, stitched_region):
        """
        Save a stitched region as a multi-resolution (pyramidal), multi-series OME-TIFF file.

        Args:
            timepoint: Current timepoint being saved.
            region: Region identifier.
            stitched_region: Image data in TCZYX format.

        Returns:
            str: Path to the saved file.
        """
        from skimage.transform import downscale_local_mean
        from tifffile import TiffWriter

        # Output path
        output_path = f"{self.output_folder}/{timepoint}_stitched/{region}_stitched.ome.tif"

        # Ensure metadata and configuration values are available from self
        pixel_size_um = self.pixel_size_um
        channel_names = self.monochrome_channels
        channel_colors = self.monochrome_colors
        num_pyramid_levels = self.num_pyramid_levels
        dtype = self.dtype

        # Metadata
        metadata = {
            "axes": "TCZYX",
            "SignificantBits": 8 if dtype == np.uint8 else 16,
            "PhysicalSizeX": pixel_size_um,
            "PhysicalSizeXUnit": "µm",
            "PhysicalSizeY": pixel_size_um,
            "PhysicalSizeYUnit": "µm",
            "Channel": {"Name": channel_names},
            "Description": f"Region {region}, Timepoint {timepoint}",
            "MapAnnotation": {  # For OMERO metadata
                "Namespace": "openmicroscopy.org/PyramidResolution",
                **{
                    f"{i}": f"{stitched_region.shape[4] // (2 ** i)} {stitched_region.shape[3] // (2 ** i)}"
                    for i in range(num_pyramid_levels)
                },
            },
        }

        # Options for TiffWriter
        options = dict(
            photometric="minisblack",
            tile=(128, 128),
            compression="lzw",
            resolutionunit="CENTIMETER",
            maxworkers=2,
        )

        # Save the OME-TIFF
        with TiffWriter(output_path, bigtiff=True) as tif:
            # Write the base resolution
            tif.write(
                stitched_region,
                subifds=num_pyramid_levels,
                resolution=(1e4 / pixel_size_um, 1e4 / pixel_size_um),
                metadata=metadata,
                **options,
            )
            # Write pyramid levels
            for level in range(1, num_pyramid_levels + 1):
                mag = 2**level
                downsampled = downscale_local_mean(
                    stitched_region,
                    factors=(1, 1, 1, mag, mag),
                ).astype(dtype)
                tif.write(
                    downsampled,
                    subfiletype=1,
                    resolution=(1e4 / (mag * pixel_size_um), 1e4 / (mag * pixel_size_um)),
                    **options,
                )

            # Generate and write a thumbnail (optional)
            thumbnail = (stitched_region[0, 0, 0, ::8, ::8] >> (8 if dtype == np.uint16 else 0)).astype(np.uint8)
            tif.write(
                thumbnail,
                metadata={"Name": f"{region}_t{timepoint}_thumbnail"},
            )

        print(f"Saved OME-TIFF to {output_path}")
        return output_path

    def save_region_vips(self, timepoint, region, stitched_region):
        """
        Save stitched region data using pyvips for efficient tiled pyramidal TIFF output.

        Args:
            timepoint: Timepoint being saved
            region: Region identifier
            stitched_region: Stitched image data

        Returns:
            str: Path to saved output file
        """
        start_time = time.time()

        # Configure output path
        output_path = os.path.join(
            self.output_folder, f"{timepoint}_stitched", f"{region}_stitched{self.output_format}"
        )
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        self.emit_status(f"Saving with pyvips... (Timepoint:{timepoint} Region:{region})", is_saving=True)

        try:
            # Convert dask array to numpy if needed
            if isinstance(stitched_region, da.Array):
                data = stitched_region.compute()
            else:
                data = stitched_region

            # Ensure data is 5D: (T, C, Z, Y, X)
            if len(data.shape) != 5:
                raise ValueError(f"Expected data with 5 dimensions (TCZYX), got shape: {data.shape}")

            # Create a list of pyvips images for each z-level
            z_level_images = []
            for z in range(self.num_z):
                # Create a list of vips bands (one per channel) for this z-level
                channel_bands = []
                for c in range(self.num_c):
                    # Extract the slice and ensure it's contiguous
                    slice_data = np.ascontiguousarray(data[0, c, z])

                    # Create a vips image for the channel
                    vips_band = pyvips.Image.new_from_memory(
                        slice_data.tobytes(),
                        width=slice_data.shape[1],
                        height=slice_data.shape[0],
                        bands=1,
                        format=pyvips.BandFormat.USHORT if self.dtype == np.uint16 else pyvips.BandFormat.UCHAR,
                    )
                    channel_bands.append(vips_band)

                # Band join the channels for this z-level
                z_image = channel_bands[0].bandjoin(channel_bands[1:])
                z_level_images.append(z_image)

            # Page join the z-levels
            final_image = z_level_images[0].pagejoin(z_level_images[1:])

            # Set page height for proper pyramid generation
            final_image = final_image.copy()
            final_image.set_type(pyvips.GValue.gint_type, "page-height", data.shape[3])

            # Set OME-TIFF metadata
            ome_metadata = f"""<?xml version="1.0" encoding="UTF-8"?>
            <OME xmlns="http://www.openmicroscopy.org/Schemas/OME/2016-06"
                xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                xsi:schemaLocation="http://www.openmicroscopy.org/Schemas/OME/2016-06 http://www.openmicroscopy.org/Schemas/OME/2016-06/ome.xsd">
                <Image ID="Image:0" Name="{region}_t{timepoint}">
                    <Pixels DimensionOrder="ZCYX"
                            ID="Pixels:0"
                            PhysicalSizeX="{self.pixel_size_um}"
                            PhysicalSizeY="{self.pixel_size_um}"
                            PhysicalSizeZ="{self.acquisition_params.get('dz(um)', 1.0)}"
                            PhysicalSizeXUnit="µm"
                            PhysicalSizeYUnit="µm"
                            PhysicalSizeZUnit="µm"
                            SizeC="{self.num_c}"
                            SizeT="1"
                            SizeX="{data.shape[4]}"
                            SizeY="{data.shape[3]}"
                            SizeZ="{self.num_z}"
                            Type="{str(self.dtype)[4:]}">
                    </Pixels>
                </Image>
            </OME>"""
            final_image.set_type(pyvips.GValue.gstr_type, "image-description", ome_metadata)

            # Calculate resolution in pixels/mm
            pixels_per_mm = 1000 / self.pixel_size_um

            # Save as pyramidal OME-TIFF
            final_image.tiffsave(
                output_path,
                compression="jpeg",  # Use JPEG compression
                bigtiff=True,  # Enable BigTIFF for large files
                pyramid=True,  # Enable pyramidal TIFF
                subifd=True,  # Store pyramid levels in sub-IFDs
                tile=True,  # Enable tiling
                tile_width=256,  # Tile width
                tile_height=256,  # Tile height
                depth="onetile",  # Use single tile depth
                xres=pixels_per_mm,  # X resolution in pixels/mm
                yres=pixels_per_mm,  # Y resolution in pixels/mm
                resunit="cm",  # Resolution unit (e.g., cm, mm)
                predictor="horizontal",  # Use horizontal predictor for compression
            )

            elapsed = time.time() - start_time
            print(f"\nSaved {output_path} in {elapsed:.1f}s using pyvips")
            print(f"Dimensions: {data.shape[4]}x{data.shape[3]}, {self.num_c} channels, {self.num_z} z-levels")
            print(f"Pixel size: {self.pixel_size_um}µm, Resolution: {pixels_per_mm} pixels/mm")
            return output_path

        except Exception as e:
            error_msg = f"Error saving with pyvips: {str(e)}"
            print(error_msg)
            if self.status_queue:
                self.status_queue.put(("error", error_msg))
            raise

    def _save_debug_slice(self, stitched_region, zarr_path):
        """Save a debug RGB image slice for verification.
        Args:
            stitched_region: Stitched image data
            zarr_path: Path where debug image will be saved
        """
        try:
            # Get up to first 3 channels and convert to numpy if needed
            channels = stitched_region[0, :3, 0]
            if isinstance(channels, da.Array):
                channels = channels.compute()

            # Reshape to [y, x, c]
            rgb_image = np.moveaxis(channels, 0, -1)

            # Normalize to 0-255 range
            rgb_min, rgb_max = rgb_image.min(axis=(0, 1)), rgb_image.max(axis=(0, 1))
            mask = rgb_max > rgb_min
            rgb_uint8 = np.zeros_like(rgb_image, dtype=np.uint8)
            rgb_uint8[..., mask] = (
                (rgb_image[..., mask] - rgb_min[mask]) * 255 / (rgb_max[mask] - rgb_min[mask])
            ).astype(np.uint8)

            # Save as TIFF
            tiff_path = zarr_path.replace(".zarr", "_debug_rgb.tiff")
            imageio.imwrite(tiff_path, rgb_uint8)
            print(f"Saved RGB debug image to: {tiff_path}")

        except Exception as e:
            print(f"Warning: Could not save debug image: {str(e)}")

    def generate_pyramid(self, image, num_levels):
        """Generate image pyramid for efficient visualization.
        Args:
            image: Input image data
            num_levels: Number of pyramid levels

        Returns:
            list: List of pyramid levels
        """
        pyramid = [image]
        for level in range(1, num_levels):
            scale_factor = 2**level
            factors = {0: 1, 1: 1, 2: 1, 3: scale_factor, 4: scale_factor}
            if isinstance(image, da.Array):
                downsampled = da.coarsen(np.mean, image, factors, trim_excess=True)
            else:
                block_size = (1, 1, 1, scale_factor, scale_factor)
                downsampled = block_reduce(image, block_size=block_size, func=np.mean)
            pyramid.append(downsampled)
        return pyramid

    def merge_timepoints_per_region(self):
        """Merge all timepoints for each region into a single dataset.(all timepoints per region)"""
        for region in self.regions:
            self.check_stop()
            self.emit_status(f"Merging Timepoints... (Region:{region})", is_saving=True)
            output_path = self.merged_timepoints_output_template.format(region=region)
            store = ome_zarr.io.parse_url(output_path, mode="w").store
            root = zarr.group(store=store)

            # Load and merge data
            merged_data = self.load_and_merge_timepoints(region)

            # Create region group and write metadata
            region_group = root.create_group(region)

            # Prepare dataset and transformation metadata
            datasets = [
                {
                    "path": str(i),
                    "coordinateTransformations": [
                        {
                            "type": "scale",
                            "scale": [
                                1,
                                1,
                                self.acquisition_params.get("dz(um)", 1),
                                self.pixel_size_um * (2**i),
                                self.pixel_size_um * (2**i),
                            ],
                        }
                    ],
                }
                for i in range(self.num_pyramid_levels)
            ]

            axes = [
                {"name": "t", "type": "time", "unit": "second"},
                {"name": "c", "type": "channel"},
                {"name": "z", "type": "space", "unit": "micrometer"},
                {"name": "y", "type": "space", "unit": "micrometer"},
                {"name": "x", "type": "space", "unit": "micrometer"},
            ]

            # Write multiscales metadata
            ome_zarr.writer.write_multiscales_metadata(region_group, datasets, axes=axes, name=region)

            # Generate and write pyramid
            pyramid = self.generate_pyramid(merged_data, self.num_pyramid_levels)
            storage_options = {"chunks": self.chunks}
            self.emit_status(f"Writing Time Series... (Region:{region})")
            ome_zarr.writer.write_multiscale(
                pyramid=pyramid,
                group=region_group,
                axes=axes,
                coordinate_transformations=[d["coordinateTransformations"] for d in datasets],
                storage_options=storage_options,
                name=region,
            )

            # Add OMERO metadata
            region_group.attrs["omero"] = {
                "name": f"Region_{region}",
                "version": "0.4",
                "channels": [
                    {"label": name, "color": f"{color:06X}", "window": {"start": 0, "end": np.iinfo(self.dtype).max}}
                    for name, color in zip(self.monochrome_channels, self.monochrome_colors)
                ],
            }

        self.emit_complete(output_path, self.dtype)

    def load_and_merge_timepoints(self, region):
        """Load and merge all timepoints for a specific region.
        Args:
            region: Region identifier

        Returns:
            array: Merged timepoint data
        """
        t_data = []
        t_shapes = []

        for t in self.timepoints:
            self.check_stop()
            zarr_path = os.path.join(self.output_folder, f"{t}_stitched", f"{region}_stitched" + self.output_format)
            print(f"Loading t:{t} region:{region}, path:{zarr_path}")

            try:
                z = zarr.open(zarr_path, mode="r")
                t_array = da.from_array(z["0"], chunks=self.chunks)
                t_data.append(t_array)
                t_shapes.append(t_array.shape)
            except Exception as e:
                print(f"Error loading timepoint {t}, region {region}: {e}")
                continue

        if not t_data:
            raise ValueError(f"No data loaded from any timepoints for region {region}")

        # Handle single vs multiple timepoints
        if len(t_data) == 1:
            return t_data[0]

        # Pad arrays to largest size and concatenate
        max_shape = tuple(max(s) for s in zip(*t_shapes))
        padded_data = [self.pad_to_largest(t, max_shape) for t in t_data]
        merged_data = da.concatenate(padded_data, axis=0)
        print(f"Merged timepoints shape for region {region}: {merged_data.shape}")
        return merged_data

    def pad_to_largest(self, array, target_shape):
        """Pad array to match target shape.
        Args:
            array: Input array
            target_shape: Desired output shape

        Returns:
            array: Padded array
        """
        if array.shape == target_shape:
            return array
        pad_widths = [(0, max(0, ts - s)) for s, ts in zip(array.shape, target_shape)]
        return da.pad(array, pad_widths, mode="constant", constant_values=0)

    def create_hcs_ome_zarr_per_timepoint(self):
        """Create separate HCS OME-ZARR files for each timepoint.(all regions per timpoint)"""
        for t in self.timepoints:
            self.check_stop()
            self.emit_status(f"Creating HCS OME-ZARR... (Timepoint:{t})", is_saving=True)

            output_path = self.merged_hcs_output_template.format(timepoint=t)

            store = ome_zarr.io.parse_url(output_path, mode="w").store
            root = zarr.group(store=store)

            # Write plate metadata
            rows = sorted(set(region[0] for region in self.regions))
            columns = sorted(set(region[1:] for region in self.regions))
            well_paths = [f"{well_id[0]}/{well_id[1:]}" for well_id in sorted(self.regions)]

            acquisitions = [{"id": 0, "maximumfieldcount": 1, "name": f"Timepoint {t} Acquisition"}]

            ome_zarr.writer.write_plate_metadata(
                root,
                rows=rows,
                columns=[str(col) for col in columns],
                wells=well_paths,
                acquisitions=acquisitions,
                name=f"HCS Dataset - Timepoint {t}",
                field_count=1,
            )

            # Process each region (well) for this timepoint
            for region in self.regions:
                self.check_stop()

                # Load existing timepoint-region data
                region_path = os.path.join(
                    self.output_folder, f"{t}_stitched", f"{region}_stitched{self.output_format}"
                )

                if not os.path.exists(region_path):
                    print(f"Warning: Missing data for timepoint {t}, region {region}")
                    continue

                # Load data from existing zarr
                z = zarr.open(region_path, mode="r")
                data = da.from_array(z["0"])

                # Create well hierarchy
                row, col = region[0], region[1:]
                row_group = root.require_group(row)
                well_group = row_group.require_group(col)

                # Write well metadata
                ome_zarr.writer.write_well_metadata(well_group, images=[{"path": "0", "acquisition": 0}])

                # Write image data
                image_group = well_group.require_group("0")

                # Prepare dataset and transformation metadata
                datasets = [
                    {
                        "path": str(i),
                        "coordinateTransformations": [
                            {
                                "type": "scale",
                                "scale": [
                                    1,
                                    1,
                                    self.acquisition_params.get("dz(um)", 1),
                                    self.pixel_size_um * (2**i),
                                    self.pixel_size_um * (2**i),
                                ],
                            }
                        ],
                    }
                    for i in range(self.num_pyramid_levels)
                ]

                axes = [
                    {"name": "t", "type": "time", "unit": "second"},
                    {"name": "c", "type": "channel"},
                    {"name": "z", "type": "space", "unit": "micrometer"},
                    {"name": "y", "type": "space", "unit": "micrometer"},
                    {"name": "x", "type": "space", "unit": "micrometer"},
                ]

                # Write multiscales metadata
                ome_zarr.writer.write_multiscales_metadata(image_group, datasets, axes=axes, name=f"Well_{region}_t{t}")

                # Generate and write pyramid
                pyramid = self.generate_pyramid(data, self.num_pyramid_levels)
                storage_options = {"chunks": self.chunks}

                ome_zarr.writer.write_multiscale(
                    pyramid=pyramid,
                    group=image_group,
                    axes=axes,
                    coordinate_transformations=[d["coordinateTransformations"] for d in datasets],
                    storage_options=storage_options,
                    name=f"Well_{region}_t{t}",
                )

                # Add OMERO metadata
                image_group.attrs["omero"] = {
                    "name": f"Well_{region}_t{t}",
                    "version": "0.4",
                    "channels": [
                        {
                            "label": name,
                            "color": f"{color:06X}",
                            "window": {"start": 0, "end": np.iinfo(self.dtype).max},
                        }
                        for name, color in zip(self.monochrome_channels, self.monochrome_colors)
                    ],
                }

            if t == self.timepoints[-1]:
                self.emit_complete(output_path, self.dtype)

    def create_complete_hcs_ome_zarr(self):
        """Create complete HCS OME-ZARR with merged timepoints. (all regions all timpoints)"""
        self.emit_status(f"Creating HCS OME-ZARR for Complete Acquisition...")
        output_path = self.complete_hcs_output_path

        store = ome_zarr.io.parse_url(output_path, mode="w").store
        root = zarr.group(store=store)

        # Write plate metadata with correct parameters
        rows = sorted(set(region[0] for region in self.regions))
        columns = sorted(set(region[1:] for region in self.regions))
        well_paths = [f"{well_id[0]}/{well_id[1:]}" for well_id in sorted(self.regions)]

        acquisitions = [{"id": 0, "maximumfieldcount": 1, "name": "Stitched Acquisition"}]

        ome_zarr.writer.write_plate_metadata(
            root,
            rows=rows,
            columns=[str(col) for col in columns],
            wells=well_paths,
            acquisitions=acquisitions,
            name="Complete HCS Dataset",
            field_count=1,
        )

        # Process each region (well)
        for region in self.regions:
            self.check_stop()

            # Load and merge timepoints for this region
            merged_data = self.load_and_merge_timepoints(region)
            if merged_data is None:  # Stopped
                return

            # Create well hierarchy
            row, col = region[0], region[1:]
            row_group = root.require_group(row)
            well_group = row_group.require_group(col)

            # Write well metadata
            ome_zarr.writer.write_well_metadata(well_group, images=[{"path": "0", "acquisition": 0}])

            # Write image data
            image_group = well_group.require_group("0")

            # Write multiscales metadata first
            datasets = [
                {
                    "path": str(i),
                    "coordinateTransformations": [
                        {
                            "type": "scale",
                            "scale": [
                                1,
                                1,
                                self.acquisition_params.get("dz(um)", 1),
                                self.pixel_size_um * (2**i),
                                self.pixel_size_um * (2**i),
                            ],
                        }
                    ],
                }
                for i in range(self.num_pyramid_levels)
            ]

            axes = [
                {"name": "t", "type": "time", "unit": "second"},
                {"name": "c", "type": "channel"},
                {"name": "z", "type": "space", "unit": "micrometer"},
                {"name": "y", "type": "space", "unit": "micrometer"},
                {"name": "x", "type": "space", "unit": "micrometer"},
            ]

            ome_zarr.writer.write_multiscales_metadata(image_group, datasets, axes=axes, name=f"Well_{region}")

            # Generate and write pyramid data
            pyramid = self.generate_pyramid(merged_data, self.num_pyramid_levels)
            storage_options = {"chunks": self.chunks}

            ome_zarr.writer.write_multiscale(
                pyramid=pyramid,
                group=image_group,
                axes=axes,
                coordinate_transformations=[d["coordinateTransformations"] for d in datasets],
                storage_options=storage_options,
                name=f"Well_{region}",
            )

            # Add OMERO metadata
            image_group.attrs["omero"] = {
                "name": f"Well_{region}",
                "version": "0.4",
                "channels": [
                    {"label": name, "color": f"{color:06X}", "window": {"start": 0, "end": np.iinfo(self.dtype).max}}
                    for name, color in zip(self.monochrome_channels, self.monochrome_colors)
                ],
            }

        self.emit_complete(output_path, self.dtype)

    def print_zarr_structure(self, path, indent=""):
        """Print the structure of a ZARR file for debugging.
        Args:
            path: Path to ZARR file
            indent: Indentation string
        """
        root = zarr.open(path, mode="r")
        print(f"Zarr Tree and Metadata for: {path}")
        print(root.tree())
        print(dict(root.attrs))

    def run(self):
        """Main execution method handling timepoints and regions."""
        stime = time.time()
        try:
            # Initial setup
            self.emit_status("Extracting Acquisition Metadata...")
            self.get_timepoints()
            self.extract_acquisition_parameters()
            self.get_pixel_size()
            self.parse_acquisition_metadata()
            os.makedirs(self.output_folder, exist_ok=True)
            last_path = ""

            if self.apply_flatfield:
                self.get_flatfields()

            if self.use_registration:
                self.calculate_shifts(self.timepoints[0], self.regions[0])

            # Main stitching loop
            for timepoint in self.timepoints:
                ttime = time.time()
                self.check_stop()

                os.makedirs(os.path.join(self.output_folder, f"{timepoint}_stitched"), exist_ok=True)

                for region in self.regions:
                    rtime = time.time()
                    self.check_stop()

                    # Stitch region
                    stitched_region = self.stitch_region(timepoint, region)

                    # Convert dask array to numpy if needed
                    if isinstance(stitched_region, da.Array):
                        stitched_region = stitched_region.compute()

                    # Save region
                    if self.output_format.endswith(".zarr"):
                        #    output_path = self.save_region_ome_zarr(timepoint, region, stitched_region) # zarr
                        output_path = self.save_region_aics(timepoint, region, stitched_region)  # zarr or tiff
                    #    output_path = self.save_region_bioio(timepoint, region, stitched_region) # zarr or tiff
                    #     output_path = self.save_region_bioio_2(timepoint, region, stitched_region) # zarr
                    #     output_path = self.save_region_parallel(timepoint, region, stitched_region) # zarr # TO FIX

                    else:
                        output_path = self.save_region_aics(timepoint, region, stitched_region)  # zarr or tiff
                    #     output_path = self.save_region_bioio(timepoint, region, stitched_region) # zarr or tiff
                    #     output_path = self.save_region_tiffile(timepoint, region, stitched_region) # tiff
                    #     output_path = self.save_region_vips(timepoint, region, stitched_region) # tiff

                    last_path = output_path
                    print(f"Completed region {region} in {time.time() - rtime:.1f}s")

                print(f"Completed timepoint {timepoint} in {time.time() - ttime:.1f}s")

            # Final steps
            self.check_stop()

            # Post-processing based on merge settings
            if self.merge_timepoints and self.merge_hcs_regions:
                self.create_complete_hcs_ome_zarr()

            elif self.merge_timepoints:
                self.merge_timepoints_per_region()

            elif self.merge_hcs_regions:
                self.create_hcs_ome_zarr_per_timepoint()

            else:
                self.print_zarr_structure(last_path)
                self.emit_complete(last_path, self.dtype)

            print(f"Processing complete. Total time: {time.time() - stime:.1f}s")

        except Exception as e:
            print(f"Error in StitcherProcess: {e}")
            self.status_queue.put(("error", str(e)))
            raise
