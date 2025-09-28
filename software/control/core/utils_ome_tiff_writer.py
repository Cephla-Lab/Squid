"""Utilities for writing OME-TIFF stacks via tifffile memmaps."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime
from typing import Any, Dict, Optional, TYPE_CHECKING

import numpy as np
import tifffile

from control import utils

if TYPE_CHECKING:  # pragma: no cover - type-checking only
    from .job_processing import CaptureInfo


def ome_output_folder(info: "CaptureInfo") -> str:
    base_dir = info.experiment_path or os.path.dirname(info.save_directory)
    return os.path.join(base_dir, "ome_tiff")


def metadata_temp_path(info: "CaptureInfo", base_name: str) -> str:
    base_identifier = info.experiment_path or info.save_directory
    key = f"{base_identifier}:{base_name}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return os.path.join(tempfile.gettempdir(), f"ome_{digest}_metadata.json")


def load_metadata(metadata_path: str) -> Optional[Dict[str, Any]]:
    if not os.path.exists(metadata_path):
        return None
    with open(metadata_path, "r", encoding="utf-8") as metadata_file:
        return json.load(metadata_file)


def write_metadata(metadata_path: str, metadata: Dict[str, Any]) -> None:
    with open(metadata_path, "w", encoding="utf-8") as metadata_file:
        json.dump(metadata, metadata_file)


def ome_base_name(info: "CaptureInfo") -> str:
    from control import _def

    return f"{info.region_id}_{info.fov:0{_def.FILE_ID_PADDING}}"


def validate_capture_info(info: "CaptureInfo", image: np.ndarray) -> None:
    if info.time_point is None:
        raise ValueError("CaptureInfo.time_point is required for OME-TIFF saving")
    if info.total_time_points is None:
        raise ValueError("CaptureInfo.total_time_points is required for OME-TIFF saving")
    if info.total_z_levels is None:
        raise ValueError("CaptureInfo.total_z_levels is required for OME-TIFF saving")
    if info.total_channels is None:
        raise ValueError("CaptureInfo.total_channels is required for OME-TIFF saving")
    if image.ndim != 2:
        raise NotImplementedError("OME-TIFF saving currently supports 2D grayscale images only")


def initialize_metadata(info: "CaptureInfo", image: np.ndarray) -> Dict[str, Any]:
    channel_names = info.channel_names or []
    time_increment = float(info.time_increment_s) if info.time_increment_s is not None else None
    time_increment_unit = "s" if time_increment is not None else None
    physical_size_z = float(info.physical_size_z_um) if info.physical_size_z_um is not None else None
    physical_size_z_unit = "µm" if physical_size_z is not None else None
    physical_size_x = float(info.physical_size_x_um) if info.physical_size_x_um is not None else None
    physical_size_x_unit = "µm" if physical_size_x is not None else None
    physical_size_y = float(info.physical_size_y_um) if info.physical_size_y_um is not None else None
    physical_size_y_unit = "µm" if physical_size_y is not None else None
    return {
        "dtype": np.dtype(image.dtype).str,
        "axes": "TZCYX",
        "shape": [
            int(info.total_time_points),
            int(info.total_z_levels),
            int(info.total_channels),
            int(image.shape[-2]),
            int(image.shape[-1]),
        ],
        "channel_names": channel_names,
        "written_indices": [],
        "saved_count": 0,
        "expected_count": int(info.total_time_points) * int(info.total_z_levels) * int(info.total_channels),
        "planes": {},
        "start_time": info.capture_time,
        "completed": False,
        "time_increment": time_increment,
        "time_increment_unit": time_increment_unit,
        "physical_size_z": physical_size_z,
        "physical_size_z_unit": physical_size_z_unit,
        "physical_size_x": physical_size_x,
        "physical_size_x_unit": physical_size_x_unit,
        "physical_size_y": physical_size_y,
        "physical_size_y_unit": physical_size_y_unit,
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

    piezo_z_um = float(info.z_piezo_um) if info.z_piezo_um is not None else None
    if metadata.get("start_time") is not None and info.capture_time is not None:
        plane_data["DeltaT"] = float(info.capture_time - metadata["start_time"])
    metadata.setdefault("planes", {})[plane_key] = plane_data

    if stepper_z_um is not None or piezo_z_um is not None:
        total_z_um = (stepper_z_um or 0.0) + (piezo_z_um or 0.0)
        plane_data["PositionZ"] = total_z_um
        plane_data["PositionZUnit"] = "µm"

    return metadata


def metadata_for_imwrite(metadata: Dict[str, Any]) -> Dict[str, Any]:
    channel_names = metadata.get("channel_names") or []
    meta: Dict[str, Any] = {"axes": "TZCYX"}
    if channel_names:
        meta["Channel"] = {"Name": channel_names}
    if metadata.get("time_increment") is not None:
        meta["TimeIncrement"] = float(metadata["time_increment"])
        meta["TimeIncrementUnit"] = metadata.get("time_increment_unit", "s")
    if metadata.get("physical_size_z") is not None:
        meta["PhysicalSizeZ"] = float(metadata["physical_size_z"])
        meta["PhysicalSizeZUnit"] = metadata.get("physical_size_z_unit", "µm")
    if metadata.get("physical_size_x") is not None:
        meta["PhysicalSizeX"] = float(metadata["physical_size_x"])
        meta["PhysicalSizeXUnit"] = metadata.get("physical_size_x_unit", "µm")
    if metadata.get("physical_size_y") is not None:
        meta["PhysicalSizeY"] = float(metadata["physical_size_y"])
        meta["PhysicalSizeYUnit"] = metadata.get("physical_size_y_unit", "µm")
    return meta


def build_base_ome_xml(metadata: Dict[str, Any]) -> str:
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

    dtype_str = np.dtype(metadata["dtype"]).name
    ome_type = dtype_map.get(dtype_str, dtype_str)
    size_t, size_z, size_c, size_y, size_x = metadata["shape"]

    root = ET.Element("{ns}OME".format(ns="{" + ns + "}"), attrib={"Creator": "Squid"})
    image = ET.SubElement(root, "{ns}Image".format(ns="{" + ns + "}"), attrib={"ID": "Image:0"})
    if metadata.get("start_time") is not None:
        try:
            acq_time = datetime.fromtimestamp(metadata["start_time"]).isoformat()
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

    channel_names = metadata.get("channel_names") or []
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

    if metadata.get("start_time") is not None:
        try:
            acq_time = datetime.fromtimestamp(metadata["start_time"]).isoformat()
            image.set("AcquisitionDate", acq_time)
        except Exception:
            pass

    pixels = image.find("ome:Pixels", ns)
    if pixels is None:
        return existing_xml or ""

    if metadata.get("time_increment") is not None:
        pixels.set("TimeIncrement", str(metadata["time_increment"]))
        pixels.set("TimeIncrementUnit", metadata.get("time_increment_unit", "s"))
    if metadata.get("physical_size_z") is not None:
        pixels.set("PhysicalSizeZ", str(metadata["physical_size_z"]))
        pixels.set("PhysicalSizeZUnit", metadata.get("physical_size_z_unit", "µm"))
    if metadata.get("physical_size_x") is not None:
        pixels.set("PhysicalSizeX", str(metadata["physical_size_x"]))
        pixels.set("PhysicalSizeXUnit", metadata.get("physical_size_x_unit", "µm"))
    if metadata.get("physical_size_y") is not None:
        pixels.set("PhysicalSizeY", str(metadata["physical_size_y"]))
        pixels.set("PhysicalSizeYUnit", metadata.get("physical_size_y_unit", "µm"))

    channel_names = metadata.get("channel_names") or []
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

    planes = metadata.get("planes", {})
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
