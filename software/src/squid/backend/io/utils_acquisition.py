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


def save_image(
    image: np.ndarray,
    file_id: str,
    save_directory: str,
    config: ChannelMode,
    is_color: bool,
) -> np.ndarray:
    if image.dtype == np.uint16:
        saving_path = os.path.join(
            save_directory, file_id + "_" + str(config.name).replace(" ", "_") + ".tiff"
        )
    else:
        saving_path = os.path.join(
            save_directory,
            file_id
            + "_"
            + str(config.name).replace(" ", "_")
            + "."
            + _def.Acquisition.IMAGE_FORMAT,
        )

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
