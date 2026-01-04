"""Utilities for writing OME-TIFF stacks via tifffile memmaps."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import glob
from datetime import datetime
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import numpy as np
import tifffile

from control import utils

if TYPE_CHECKING:  # pragma: no cover - type-checking only
    from .job_processing import CaptureInfo, AcquisitionInfo

# Constants for metadata keys
WRITTEN_INDICES_KEY = "written_indices"
PLANES_KEY = "planes"
SAVED_COUNT_KEY = "saved_count"
EXPECTED_COUNT_KEY = "expected_count"
COMPLETED_KEY = "completed"
START_TIME_KEY = "start_time"
DTYPE_KEY = "dtype"
SHAPE_KEY = "shape"
AXES_KEY = "axes"
CHANNEL_NAMES_KEY = "channel_names"
TIME_INCREMENT_KEY = "time_increment"
TIME_INCREMENT_UNIT_KEY = "time_increment_unit"
PHYSICAL_SIZE_Z_KEY = "physical_size_z"
PHYSICAL_SIZE_Z_UNIT_KEY = "physical_size_z_unit"
PHYSICAL_SIZE_X_KEY = "physical_size_x"
PHYSICAL_SIZE_X_UNIT_KEY = "physical_size_x_unit"
PHYSICAL_SIZE_Y_KEY = "physical_size_y"
PHYSICAL_SIZE_Y_UNIT_KEY = "physical_size_y_unit"


def ome_output_folder(acq_info: "AcquisitionInfo", info: "CaptureInfo") -> str:
    base_dir = acq_info.experiment_path or os.path.dirname(info.save_directory)
    return os.path.join(base_dir, "ome_tiff")


def metadata_temp_path(acq_info: "AcquisitionInfo", info: "CaptureInfo", base_name: str) -> str:
    base_identifier = acq_info.experiment_path or info.save_directory
    key = f"{base_identifier}:{base_name}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    # Use squid_ome_ prefix to avoid conflicts with other OME-TIFF applications
    return os.path.join(tempfile.gettempdir(), f"squid_ome_{digest}_metadata.json")


def load_metadata(metadata_path: str) -> Optional[Dict[str, Any]]:
    if not os.path.exists(metadata_path):
        return None
    with open(metadata_path, "r", encoding="utf-8") as metadata_file:
        return json.load(metadata_file)


def write_metadata(metadata_path: str, metadata: Dict[str, Any]) -> None:
    with open(metadata_path, "w", encoding="utf-8") as metadata_file:
        json.dump(metadata, metadata_file)


def ome_base_name(info: "CaptureInfo") -> str:
    # Import here to avoid circular dependency: _def -> utils_ome_tiff_writer -> _def
    from control import _def

    return f"{info.region_id}_{info.fov:0{_def.FILE_ID_PADDING}}"


def validate_capture_info(info: "CaptureInfo", acq_info: "AcquisitionInfo", image: np.ndarray) -> None:
    """Validate that capture info and acquisition info have required fields for OME-TIFF saving.

    Note: The caller (SaveOMETiffJob.run) is responsible for checking that acq_info is not None.
    The acq_info fields total_time_points, total_z_levels, and total_channels are required (non-Optional)
    per the AcquisitionInfo dataclass definition.
    """
    if info.time_point is None:
        raise ValueError("CaptureInfo.time_point is required for OME-TIFF saving")
    if image.ndim != 2:
        raise NotImplementedError("OME-TIFF saving currently supports 2D grayscale images only")


def initialize_metadata(acq_info: "AcquisitionInfo", info: "CaptureInfo", image: np.ndarray) -> Dict[str, Any]:
    channel_names = acq_info.channel_names or []
    time_increment = float(acq_info.time_increment_s) if acq_info.time_increment_s is not None else None
    time_increment_unit = "s" if time_increment is not None else None
    physical_size_z = float(acq_info.physical_size_z_um) if acq_info.physical_size_z_um is not None else None
    physical_size_z_unit = "µm" if physical_size_z is not None else None
    physical_size_x = float(acq_info.physical_size_x_um) if acq_info.physical_size_x_um is not None else None
    physical_size_x_unit = "µm" if physical_size_x is not None else None
    physical_size_y = float(acq_info.physical_size_y_um) if acq_info.physical_size_y_um is not None else None
    physical_size_y_unit = "µm" if physical_size_y is not None else None
    return {
        DTYPE_KEY: np.dtype(image.dtype).str,
        AXES_KEY: "TZCYX",
        SHAPE_KEY: [
            int(acq_info.total_time_points),
            int(acq_info.total_z_levels),
            int(acq_info.total_channels),
            int(image.shape[-2]),
            int(image.shape[-1]),
        ],
        CHANNEL_NAMES_KEY: channel_names,
        WRITTEN_INDICES_KEY: [],
        SAVED_COUNT_KEY: 0,
        EXPECTED_COUNT_KEY: int(acq_info.total_time_points)
        * int(acq_info.total_z_levels)
        * int(acq_info.total_channels),
        PLANES_KEY: {},
        START_TIME_KEY: info.capture_time,
        COMPLETED_KEY: False,
        TIME_INCREMENT_KEY: time_increment,
        TIME_INCREMENT_UNIT_KEY: time_increment_unit,
        PHYSICAL_SIZE_Z_KEY: physical_size_z,
        PHYSICAL_SIZE_Z_UNIT_KEY: physical_size_z_unit,
        PHYSICAL_SIZE_X_KEY: physical_size_x,
        PHYSICAL_SIZE_X_UNIT_KEY: physical_size_x_unit,
        PHYSICAL_SIZE_Y_KEY: physical_size_y,
        PHYSICAL_SIZE_Y_UNIT_KEY: physical_size_y_unit,
    }


def update_plane_metadata(metadata: Dict[str, Any], info: "CaptureInfo") -> Dict[str, Any]:
    plane_key = f"{info.time_point}-{info.configuration_idx}-{info.z_index}"
    plane_data: Dict[str, Any] = {
        "TheT": int(info.time_point),
        "TheZ": int(info.z_index),
        "TheC": int(info.configuration_idx),
    }
    if info.position is not None:
        if getattr(info.position, "x_mm", None) is not None:
            plane_data["PositionX"] = float(info.position.x_mm)
            plane_data["PositionXUnit"] = "mm"
        if getattr(info.position, "y_mm", None) is not None:
            plane_data["PositionY"] = float(info.position.y_mm)
            plane_data["PositionYUnit"] = "mm"

    stepper_z_um: Optional[float] = None
    if info.position is not None and getattr(info.position, "z_mm", None) is not None:
        stepper_z_um = float(info.position.z_mm) * 1000.0

    piezo_z_um: Optional[float] = float(info.z_piezo_um) if info.z_piezo_um is not None else None
    if metadata.get(START_TIME_KEY) is not None and info.capture_time is not None:
        plane_data["DeltaT"] = float(info.capture_time - metadata[START_TIME_KEY])
    metadata.setdefault(PLANES_KEY, {})[plane_key] = plane_data

    if stepper_z_um is not None or piezo_z_um is not None:
        total_z_um = (stepper_z_um or 0.0) + (piezo_z_um or 0.0)
        plane_data["PositionZ"] = total_z_um
        plane_data["PositionZUnit"] = "µm"

    return metadata


def metadata_for_imwrite(metadata: Dict[str, Any]) -> Dict[str, Any]:
    channel_names = metadata.get(CHANNEL_NAMES_KEY) or []
    meta: Dict[str, Any] = {AXES_KEY: "TZCYX"}
    if channel_names:
        meta["Channel"] = {"Name": channel_names}
    if metadata.get(TIME_INCREMENT_KEY) is not None:
        meta["TimeIncrement"] = float(metadata[TIME_INCREMENT_KEY])
        meta["TimeIncrementUnit"] = metadata.get(TIME_INCREMENT_UNIT_KEY, "s")
    if metadata.get(PHYSICAL_SIZE_Z_KEY) is not None:
        meta["PhysicalSizeZ"] = float(metadata[PHYSICAL_SIZE_Z_KEY])
        meta["PhysicalSizeZUnit"] = metadata.get(PHYSICAL_SIZE_Z_UNIT_KEY, "µm")
    if metadata.get(PHYSICAL_SIZE_X_KEY) is not None:
        meta["PhysicalSizeX"] = float(metadata[PHYSICAL_SIZE_X_KEY])
        meta["PhysicalSizeXUnit"] = metadata.get(PHYSICAL_SIZE_X_UNIT_KEY, "µm")
    if metadata.get(PHYSICAL_SIZE_Y_KEY) is not None:
        meta["PhysicalSizeY"] = float(metadata[PHYSICAL_SIZE_Y_KEY])
        meta["PhysicalSizeYUnit"] = metadata.get(PHYSICAL_SIZE_Y_UNIT_KEY, "µm")
    return meta


def build_base_ome_xml(metadata: Dict[str, Any]) -> str:
    # Lazy import: xml.etree.ElementTree only needed for XML generation functions
    import xml.etree.ElementTree as ET

    ns = "http://www.openmicroscopy.org/Schemas/OME/2016-06"
    ET.register_namespace("", ns)

    dtype_map = {
        "uint8": "uint8",
        "uint16": "uint16",
        "uint32": "uint32",
        "int8": "int8",
        "int16": "int16",
        "int32": "int32",
        "float32": "float",
        "float64": "double",
    }

    dtype_str = np.dtype(metadata[DTYPE_KEY]).name
    ome_type = dtype_map.get(dtype_str, dtype_str)
    size_t, size_z, size_c, size_y, size_x = metadata[SHAPE_KEY]

    root = ET.Element("{ns}OME".format(ns="{" + ns + "}"), attrib={"Creator": "Squid"})
    image = ET.SubElement(root, "{ns}Image".format(ns="{" + ns + "}"), attrib={"ID": "Image:0"})
    if metadata.get(START_TIME_KEY) is not None:
        try:
            acq_time = datetime.fromtimestamp(metadata[START_TIME_KEY]).isoformat()
            image.set("AcquisitionDate", acq_time)
        except Exception:
            pass
    pixels = ET.SubElement(
        image,
        "{ns}Pixels".format(ns="{" + ns + "}"),
        attrib={
            "ID": "Pixels:0",
            "DimensionOrder": "TZCYX",
            "Type": ome_type,
            "SizeT": str(size_t),
            "SizeC": str(size_c),
            "SizeZ": str(size_z),
            "SizeY": str(size_y),
            "SizeX": str(size_x),
        },
    )

    channel_names = metadata.get(CHANNEL_NAMES_KEY) or []
    if not channel_names:
        channel_names = [f"Channel {idx}" for idx in range(size_c)]

    for idx, name in enumerate(channel_names):
        ET.SubElement(
            pixels,
            "{ns}Channel".format(ns="{" + ns + "}"),
            attrib={
                "ID": f"Channel:0:{idx}",
                "Name": name,
                "SamplesPerPixel": "1",
            },
        )

    xml_body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>' + xml_body


def augment_ome_xml(existing_xml: Optional[str], metadata: Dict[str, Any]) -> str:
    # Lazy import: xml.etree.ElementTree only needed for XML generation functions
    import xml.etree.ElementTree as ET

    if existing_xml:
        root = ET.fromstring(existing_xml)
    else:
        existing_xml = build_base_ome_xml(metadata)
        root = ET.fromstring(existing_xml)

    ns = {"ome": "http://www.openmicroscopy.org/Schemas/OME/2016-06"}
    ET.register_namespace("", ns["ome"])

    image = root.find("ome:Image", ns)
    if image is None:
        return existing_xml or ""

    if metadata.get(START_TIME_KEY) is not None:
        try:
            acq_time = datetime.fromtimestamp(metadata[START_TIME_KEY]).isoformat()
            image.set("AcquisitionDate", acq_time)
        except Exception:
            pass

    pixels = image.find("ome:Pixels", ns)
    if pixels is None:
        return existing_xml or ""

    if metadata.get(TIME_INCREMENT_KEY) is not None:
        pixels.set("TimeIncrement", str(metadata[TIME_INCREMENT_KEY]))
        pixels.set("TimeIncrementUnit", metadata.get(TIME_INCREMENT_UNIT_KEY, "s"))
    if metadata.get(PHYSICAL_SIZE_Z_KEY) is not None:
        pixels.set("PhysicalSizeZ", str(metadata[PHYSICAL_SIZE_Z_KEY]))
        pixels.set("PhysicalSizeZUnit", metadata.get(PHYSICAL_SIZE_Z_UNIT_KEY, "µm"))
    if metadata.get(PHYSICAL_SIZE_X_KEY) is not None:
        pixels.set("PhysicalSizeX", str(metadata[PHYSICAL_SIZE_X_KEY]))
        pixels.set("PhysicalSizeXUnit", metadata.get(PHYSICAL_SIZE_X_UNIT_KEY, "µm"))
    if metadata.get(PHYSICAL_SIZE_Y_KEY) is not None:
        pixels.set("PhysicalSizeY", str(metadata[PHYSICAL_SIZE_Y_KEY]))
        pixels.set("PhysicalSizeYUnit", metadata.get(PHYSICAL_SIZE_Y_UNIT_KEY, "µm"))

    channel_names = metadata.get(CHANNEL_NAMES_KEY) or []
    if channel_names:
        existing_channels = list(pixels.findall("ome:Channel", ns))
        if len(existing_channels) == len(channel_names):
            for elem, name in zip(existing_channels, channel_names):
                elem.set("Name", name)
        else:
            for elem in existing_channels:
                pixels.remove(elem)
            for idx, name in enumerate(channel_names):
                ET.SubElement(
                    pixels,
                    "{http://www.openmicroscopy.org/Schemas/OME/2016-06}Channel",
                    attrib={
                        "ID": f"Channel:0:{idx}",
                        "Name": name,
                        "SamplesPerPixel": "1",
                    },
                )

    for elem in list(pixels.findall("ome:Plane", ns)):
        pixels.remove(elem)

    planes = metadata.get(PLANES_KEY, {})
    ordered_planes = sorted(
        planes.values(),
        key=lambda p: (p.get("TheT", 0), p.get("TheC", 0), p.get("TheZ", 0)),
    )

    for plane in ordered_planes:
        ET.SubElement(
            pixels,
            "{http://www.openmicroscopy.org/Schemas/OME/2016-06}Plane",
            attrib={key: str(value) for key, value in plane.items()},
        )

    xml_body = ET.tostring(root, encoding="unicode")
    if not xml_body.startswith("<?xml"):
        xml_body = '<?xml version="1.0" encoding="UTF-8"?>' + xml_body
    return xml_body


def ensure_output_directory(path: str) -> None:
    utils.ensure_directory_exists(path)


def cleanup_stale_metadata_files() -> List[str]:
    """Remove orphaned OME-TIFF metadata files from the temp directory.

    Uses lock-based detection instead of time-based: attempts to acquire each
    file's lock with zero timeout. If the lock can be acquired, no active
    process is using the file, so it's safe to remove as orphaned.

    This approach is robust for any acquisition duration (even multi-week
    time-lapses) and any interval between image saves.

    Returns:
        List of paths that were successfully removed.
    """
    from filelock import FileLock, Timeout as FileLockTimeout

    removed: List[str] = []
    temp_dir = tempfile.gettempdir()
    pattern = os.path.join(temp_dir, "squid_ome_*_metadata.json")

    for metadata_path in glob.glob(pattern):
        lock_path = metadata_path + ".lock"
        lock = FileLock(lock_path, timeout=0)  # Non-blocking attempt

        metadata_removed = False
        try:
            with lock:
                # Lock acquired - no active process is using this file
                try:
                    os.remove(metadata_path)
                    removed.append(metadata_path)
                    metadata_removed = True
                except FileNotFoundError:
                    pass  # Already removed
        except FileLockTimeout:
            # Lock is held by another process - file is in active use, skip
            continue
        except OSError:
            # Other errors (permissions, etc.) - skip this file
            pass

        # Clean up lock file after releasing the lock
        if metadata_removed:
            try:
                os.remove(lock_path)
                removed.append(lock_path)
            except OSError:
                # On some platforms (notably Windows), filelock may still hold a handle
                # briefly after the context manager exits, causing os.remove to fail.
                # This is a best-effort cleanup, so such errors are safe to ignore.
                pass

    return removed
