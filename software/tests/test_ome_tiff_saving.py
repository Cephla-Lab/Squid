from __future__ import annotations

MM_TO_UM = 1000.0
PIEZO_STEP_UM = 10.0

"""Tests for the OME-TIFF memmap saving pipeline."""

import os
import sys
import tempfile
import warnings
import xml.etree.ElementTree as ET
import time
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Ensure relative resources in control._def resolve as expected
os.chdir(PROJECT_ROOT)


@pytest.mark.parametrize("shape", [(64, 48), (32, 32)])
def test_ome_tiff_memmap_roundtrip(shape: tuple[int, int]) -> None:
    # Imports that rely on the stubs and project path
    import control._def as _def
    from control._def import FileSavingOption
    from control.core.job_processing import SaveOMETiffJob, CaptureInfo, JobImage, AcquisitionInfo
    from control.utils_config import ChannelMode
    import squid.abc

    original_option = _def.FILE_SAVING_OPTION
    _def.FILE_SAVING_OPTION = FileSavingOption.OME_TIFF

    channels = [
        ChannelMode(
            id=str(idx),
            name=name,
            exposure_time=10.0,
            analog_gain=1.0,
            illumination_source=idx,
            illumination_intensity=5.0,
            z_offset=0.0,
        )
        for idx, name in enumerate(["DAPI", "GFP"], start=1)
    ]

    total_timepoints = 2
    total_channels = len(channels)
    total_z = 3

    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            experiment_dir = Path(tmp_dir) / "experiment"
            positions = [
                squid.abc.Pos(x_mm=float(t), y_mm=float(c), z_mm=float(z), theta_rad=None)
                for t in range(total_timepoints)
                for z in range(total_z)
                for c in range(total_channels)
            ]

            pos_iter = iter(positions)

            channel_names = [channel.name for channel in channels]

            acquisition_info = AcquisitionInfo(
                total_time_points=total_timepoints,
                total_z_levels=total_z,
                total_channels=total_channels,
                channel_names=channel_names,
                experiment_path=str(experiment_dir),
                time_increment_s=1.5,
                physical_size_z_um=4.5,
                physical_size_x_um=0.75,
                physical_size_y_um=0.8,
            )

            for t in range(total_timepoints):
                time_point_dir = experiment_dir / f"{t:03d}"
                time_point_dir.mkdir(parents=True, exist_ok=True)
                for z in range(total_z):
                    for c, channel in enumerate(channels):
                        image = np.full(shape, fill_value=(t + 1) * 10 + z + c, dtype=np.uint16)
                        capture_info = CaptureInfo(
                            position=next(pos_iter),
                            z_index=z,
                            capture_time=time.time(),
                            configuration=channel,
                            save_directory=str(time_point_dir),
                            file_id=f"test_{t}_{c}_{z}",
                            region_id=1,
                            fov=0,
                            configuration_idx=c,
                            z_piezo_um=float(z) * PIEZO_STEP_UM,
                            time_point=t,
                        )
                        job = SaveOMETiffJob(
                            capture_info=capture_info,
                            capture_image=JobImage(image_array=image),
                        )
                        # Manually inject acquisition_info (normally done by JobRunner)
                        job.acquisition_info = acquisition_info
                        assert job.run()

            output_path = experiment_dir / "ome_tiff" / "1_0.ome.tiff"
            assert output_path.exists(), "Stack file should be created after all planes are written"

            import tifffile

            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                with tifffile.TiffFile(output_path) as tif:
                    series = tif.series[0]
                    assert series.axes.upper() == "TZCYX"
                    data = series.asarray()
                    assert data.shape == (total_timepoints, total_z, total_channels, *shape)
                    for t in range(total_timepoints):
                        for z in range(total_z):
                            for c in range(total_channels):
                                expected = (t + 1) * 10 + z + c
                                np.testing.assert_array_equal(data[t, z, c], expected)

                    ome_xml = tif.ome_metadata or ""
                    assert 'DimensionOrder="XYCZT"' in ome_xml

                    ns = {"ome": "http://www.openmicroscopy.org/Schemas/OME/2016-06"}
                    root = ET.fromstring(ome_xml)
                    pixels = root.find("ome:Image/ome:Pixels", ns)
                    assert pixels is not None
                    assert pixels.get("SizeT") == str(total_timepoints)
                    assert pixels.get("SizeC") == str(total_channels)
                    assert pixels.get("SizeZ") == str(total_z)
                    assert float(pixels.get("TimeIncrement", "nan")) == pytest.approx(1.5)
                    assert float(pixels.get("PhysicalSizeZ", "nan")) == pytest.approx(4.5)
                    assert float(pixels.get("PhysicalSizeX", "nan")) == pytest.approx(0.75)
                    assert float(pixels.get("PhysicalSizeY", "nan")) == pytest.approx(0.8)
                    assert pixels.get("PhysicalSizeXUnit") == "µm"
                    assert pixels.get("PhysicalSizeYUnit") == "µm"
                    assert pixels.get("PhysicalSizeZUnit") == "µm"
                    plane_map = {
                        (
                            int(plane.get("TheT", "0")),
                            int(plane.get("TheZ", "0")),
                            int(plane.get("TheC", "0")),
                        ): plane
                        for plane in root.findall(".//ome:Plane", ns)
                    }
                    assert len(plane_map) == total_timepoints * total_z * total_channels

                    for t in range(total_timepoints):
                        for z in range(total_z):
                            for c in range(total_channels):
                                plane = plane_map[(t, z, c)]
                                if "PositionX" in plane:
                                    assert float(plane.get("PositionX", "nan")) == pytest.approx(float(t))
                                    assert plane.get("PositionXUnit") == "mm"
                                if "PositionY" in plane:
                                    assert float(plane.get("PositionY", "nan")) == pytest.approx(float(c))
                                    assert plane.get("PositionYUnit") == "mm"
                                expected_stage_um = float(z) * MM_TO_UM
                                expected_piezo_um = float(z) * PIEZO_STEP_UM
                                expected_total_um = expected_stage_um + expected_piezo_um
                                assert float(plane.get("PositionZ", "nan")) == pytest.approx(
                                    expected_total_um, rel=1e-6
                                )
                                assert plane.get("PositionZUnit") == "µm"
                                assert float(plane.get("DeltaT", "nan")) >= 0.0

            assert not caught

            ome_dir_contents = list((experiment_dir / "ome_tiff").iterdir())
            assert all(path.suffix != ".json" for path in ome_dir_contents)
            assert all(not path.name.endswith("_tczyx.dat") for path in ome_dir_contents)
    finally:
        _def.FILE_SAVING_OPTION = original_option
