"""
Contains helper functions for use in acquisitions such as saving images, converting
images, etc.
"""

import os

import numpy as np
import cv2
import imageio

import _def
from squid.core.utils.config_utils import ChannelMode


def get_image_filepath(save_directory: str, file_id: str, config_name: str, dtype) -> str:
    """Construct the filepath for a saved image.

    This is used by both save_image() and NDViewer registration to ensure
    consistent filepath construction.

    Args:
        save_directory: Directory where images are saved
        file_id: Base file ID (e.g., "0_0_0" for region_fov_z)
        config_name: Channel configuration name (e.g., "BF LED matrix full")
        dtype: numpy dtype of the image (e.g., np.uint16)

    Returns:
        Full filepath string
    """
    channel_name_safe = str(config_name).replace(" ", "_")
    if dtype == np.uint16:
        extension = "tiff"
    else:
        extension = _def.Acquisition.IMAGE_FORMAT
    return os.path.join(save_directory, f"{file_id}_{channel_name_safe}.{extension}")


def save_image(
    image: np.ndarray,
    file_id: str,
    save_directory: str,
    config: ChannelMode,
    is_color: bool,
) -> np.ndarray:
    saving_path = get_image_filepath(save_directory, file_id, config.name, image.dtype)

    if is_color:
        if "BF LED matrix" in config.name:
            if _def.MULTIPOINT_BF_SAVING_OPTION == "RGB2GRAY":
                image = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
            elif _def.MULTIPOINT_BF_SAVING_OPTION == "Green Channel Only":
                image = image[:, :, 1]

    if _def.SAVE_IN_PSEUDO_COLOR:
        image = return_pseudo_colored_image(image, config)

    imageio.imwrite(saving_path, image)

    return image


def grayscale_to_rgb(image: np.ndarray, hex_color: int) -> np.ndarray:
    rgb_ratios = (
        np.array([(hex_color >> 16) & 0xFF, (hex_color >> 8) & 0xFF, hex_color & 0xFF])
        / 255
    )
    rgb = np.stack([image] * 3, axis=-1) * rgb_ratios
    return rgb.astype(image.dtype)


def return_pseudo_colored_image(image: np.ndarray, config: ChannelMode) -> np.ndarray:
    if "405 nm" in config.name:
        image = grayscale_to_rgb(image, _def.CHANNEL_COLORS_MAP["405"]["hex"])
    elif "488 nm" in config.name:
        image = grayscale_to_rgb(image, _def.CHANNEL_COLORS_MAP["488"]["hex"])
    elif "561 nm" in config.name:
        image = grayscale_to_rgb(image, _def.CHANNEL_COLORS_MAP["561"]["hex"])
    elif "638 nm" in config.name:
        image = grayscale_to_rgb(image, _def.CHANNEL_COLORS_MAP["638"]["hex"])
    elif "730 nm" in config.name:
        image = grayscale_to_rgb(image, _def.CHANNEL_COLORS_MAP["730"]["hex"])
    else:
        image = np.stack([image] * 3, axis=-1)

    return image
