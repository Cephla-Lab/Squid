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
    from control.models import AcquisitionChannel, CameraSettings, IlluminationSettings
    import squid.abc

    original_option = _def.FILE_SAVING_OPTION
    _def.FILE_SAVING_OPTION = FileSavingOption.OME_TIFF

    channels = [
        AcquisitionChannel(
            name=name,
            display_color="#FFFFFF",
            camera=1,  # v1.0: camera is int ID
            illumination_settings=IlluminationSettings(
                illumination_channel=name,
                intensity=5.0,
            ),
            camera_settings=CameraSettings(
                exposure_time_ms=10.0,
                gain_mode=1.0,
            ),
            z_offset_um=0.0,  # v1.0: at channel level
        )
        for name in ["DAPI", "GFP"]
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


def test_job_runner_injects_acquisition_info() -> None:
    """Test that JobRunner.dispatch() properly injects acquisition_info into SaveOMETiffJob."""
    from control.core.job_processing import SaveOMETiffJob, CaptureInfo, JobImage, AcquisitionInfo, JobRunner
    from control.models import AcquisitionChannel, CameraSettings, IlluminationSettings
    import squid.abc

    # Create test data
    acquisition_info = AcquisitionInfo(
        total_time_points=1,
        total_z_levels=1,
        total_channels=1,
        channel_names=["DAPI"],
        experiment_path=os.path.join(tempfile.gettempdir(), "test"),
        time_increment_s=1.0,
        physical_size_z_um=1.0,
        physical_size_x_um=0.5,
        physical_size_y_um=0.5,
    )

    channel = AcquisitionChannel(
        name="DAPI",
        display_color="#FFFFFF",
        camera=1,  # v1.0: camera is int ID
        illumination_settings=IlluminationSettings(
            illumination_channel="DAPI",
            intensity=5.0,
        ),
        camera_settings=CameraSettings(
            exposure_time_ms=10.0,
            gain_mode=1.0,
        ),
        z_offset_um=0.0,  # v1.0: at channel level
    )

    capture_info = CaptureInfo(
        position=squid.abc.Pos(x_mm=0.0, y_mm=0.0, z_mm=0.0, theta_rad=None),
        z_index=0,
        capture_time=time.time(),
        configuration=channel,
        save_directory=os.path.join(tempfile.gettempdir(), "test"),
        file_id="test_0_0_0",
        region_id=1,
        fov=0,
        configuration_idx=0,
        z_piezo_um=0.0,
        time_point=0,
    )

    image = np.zeros((32, 32), dtype=np.uint16)
    job = SaveOMETiffJob(
        capture_info=capture_info,
        capture_image=JobImage(image_array=image),
    )

    # Verify acquisition_info is None before dispatch
    assert job.acquisition_info is None

    # Create JobRunner with acquisition_info and dispatch
    runner = JobRunner(acquisition_info=acquisition_info)
    try:
        runner.dispatch(job)

        # Verify acquisition_info was injected
        assert job.acquisition_info is not None
        assert job.acquisition_info.total_time_points == 1
        assert job.acquisition_info.channel_names == ["DAPI"]
    finally:
        # Clean up - signal shutdown (don't call shutdown() since process wasn't started)
        runner._shutdown_event.set()


def test_stale_metadata_cleanup() -> None:
    """Test that cleanup_stale_metadata_files removes orphaned (unlocked) metadata files."""
    from control.core import utils_ome_tiff_writer as ome_tiff_writer

    # Create a fake orphaned metadata file in the system temp directory
    # (cleanup_stale_metadata_files looks in tempfile.gettempdir() for squid_ome_* files)
    orphaned_metadata_path = os.path.join(tempfile.gettempdir(), "squid_ome_teststale123_metadata.json")
    try:
        with open(orphaned_metadata_path, "w") as f:
            f.write("{}")

        # Run cleanup - file should be removed since it's not locked
        removed = ome_tiff_writer.cleanup_stale_metadata_files()

        # Verify the file was removed
        assert orphaned_metadata_path in removed
        assert not os.path.exists(orphaned_metadata_path)
    finally:
        # Clean up in case the test fails before cleanup runs
        if os.path.exists(orphaned_metadata_path):
            os.remove(orphaned_metadata_path)


def test_job_runner_cleanup_flag() -> None:
    """Test that JobRunner only runs cleanup when cleanup_stale_ome_files=True."""
    from unittest.mock import patch
    from control.core.job_processing import JobRunner, AcquisitionInfo

    acquisition_info = AcquisitionInfo(
        total_time_points=1,
        total_z_levels=1,
        total_channels=1,
        channel_names=["DAPI"],
    )

    # Test that cleanup is NOT called when flag is False (default)
    with patch("control.core.job_processing.ome_tiff_writer.cleanup_stale_metadata_files") as mock_cleanup:
        runner = JobRunner(acquisition_info=acquisition_info, cleanup_stale_ome_files=False)
        try:
            mock_cleanup.assert_not_called()
        finally:
            # Signal shutdown (don't call shutdown() since process wasn't started)
            runner._shutdown_event.set()

    # Test that cleanup IS called when flag is True
    with patch("control.core.job_processing.ome_tiff_writer.cleanup_stale_metadata_files") as mock_cleanup:
        mock_cleanup.return_value = []
        runner = JobRunner(acquisition_info=acquisition_info, cleanup_stale_ome_files=True)
        try:
            mock_cleanup.assert_called_once()
        finally:
            # Signal shutdown (don't call shutdown() since process wasn't started)
            runner._shutdown_event.set()


# ---------------------------------------------------------------------------
# Per-timepoint split (opt-in) helpers and tests
# ---------------------------------------------------------------------------

# Tiny images keep these tests cheap on disk (the split tests write real files).
SPLIT_IMAGE_SHAPE = (8, 8)
REGION_ID = 1
FOV = 0


def _build_channels(names):
    from control.models import AcquisitionChannel, CameraSettings, IlluminationSettings

    return [
        AcquisitionChannel(
            name=name,
            display_color="#FFFFFF",
            camera=1,
            illumination_settings=IlluminationSettings(illumination_channel=name, intensity=5.0),
            camera_settings=CameraSettings(exposure_time_ms=10.0, gain_mode=1.0),
            z_offset_um=0.0,
        )
        for name in names
    ]


def _make_capture_info(channel, channel_idx: int, time_point: int, save_directory: str, capture_time: float):
    from control.core.job_processing import CaptureInfo
    import squid.abc

    return CaptureInfo(
        position=squid.abc.Pos(x_mm=0.1, y_mm=0.2, z_mm=0.3, theta_rad=None),
        z_index=0,
        capture_time=capture_time,
        configuration=channel,
        save_directory=save_directory,
        file_id=f"test_{time_point}_{channel_idx}_0",
        region_id=REGION_ID,
        fov=FOV,
        configuration_idx=channel_idx,
        z_piezo_um=0.0,
        time_point=time_point,
    )


def _run_plane(acquisition_info, capture_info, fill_value: int):
    """Run one SaveOMETiffJob for a single 2D plane and return its SaveResult."""
    from control.core.job_processing import SaveOMETiffJob, JobImage

    image = np.full(SPLIT_IMAGE_SHAPE, fill_value=fill_value, dtype=np.uint16)
    job = SaveOMETiffJob(capture_info=capture_info, capture_image=JobImage(image_array=image))
    job.acquisition_info = acquisition_info
    return job.run()


def _read_stack(path):
    """Return (TZCYX shape, TZCYX data, Pixels element, Plane elements) of an OME-TIFF stack."""
    import tifffile

    with tifffile.TiffFile(path) as tif:
        series = tif.series[0]
        # squeeze=False keeps singleton T/Z dimensions, which these small stacks rely on.
        axes = series.get_axes(False).upper()
        shape = series.get_shape(False)
        if axes.endswith("S"):  # trailing samples-per-pixel axis
            axes, shape = axes[:-1], shape[:-1]
        assert axes == "TZCYX", axes
        data = series.asarray().reshape(shape)
        ome_xml = tif.ome_metadata or ""
    ns = {"ome": "http://www.openmicroscopy.org/Schemas/OME/2016-06"}
    root = ET.fromstring(ome_xml)
    return shape, data, root.find("ome:Image/ome:Pixels", ns), root.findall(".//ome:Plane", ns), root


def _remove_metadata_files(*metadata_paths):
    for metadata_path in metadata_paths:
        for path in (metadata_path, metadata_path + ".lock"):
            try:
                os.remove(path)
            except OSError:
                pass


def test_split_timepoints_round_trip(tmp_path) -> None:
    """With split_timepoints=True each timepoint gets its own single-T OME-TIFF file."""
    from control.core.job_processing import AcquisitionInfo
    from control.core import utils_ome_tiff_writer as ome_tiff_writer

    channels = _build_channels(["DAPI", "GFP"])
    start_time = time.time()
    acquisition_info = AcquisitionInfo(
        total_time_points=2,
        total_z_levels=1,
        total_channels=len(channels),
        channel_names=[c.name for c in channels],
        experiment_path=str(tmp_path),
        time_increment_s=1.5,
        physical_size_z_um=4.5,
        physical_size_x_um=0.75,
        physical_size_y_um=0.8,
        split_timepoints=True,
        acquisition_start_time=start_time,
    )

    results = {}
    capture_infos = {}
    metadata_paths = []
    try:
        for t in range(2):
            # FILE_ID_PADDING is 0 in the test config, so the worker's timepoint folder is "0"/"1".
            time_point_dir = tmp_path / f"{t}"
            time_point_dir.mkdir(parents=True, exist_ok=True)
            for c, channel in enumerate(channels):
                capture_info = _make_capture_info(
                    channel, c, t, str(time_point_dir), capture_time=start_time + 10.0 * t + c
                )
                capture_infos[t] = capture_info
                results[(t, c)] = _run_plane(acquisition_info, capture_info, fill_value=(t + 1) * 10 + c)

        base_name = ome_tiff_writer.ome_base_name(capture_infos[0])
        metadata_paths = [
            ome_tiff_writer.metadata_temp_path(acquisition_info, capture_infos[t], base_name) for t in range(2)
        ]

        # One file per timepoint, under that timepoint's folder.
        paths = [tmp_path / f"{t}" / "ome_tiff" / f"{base_name}.ome.tiff" for t in range(2)]
        for path in paths:
            assert path.exists(), f"expected per-timepoint stack at {path}"
        assert not (tmp_path / "ome_tiff").exists(), "split mode must not write the experiment-level stack"

        ns = {"ome": "http://www.openmicroscopy.org/Schemas/OME/2016-06"}
        delta_ts = []
        for t, path in enumerate(paths):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                shape, data, pixels, planes, root = _read_stack(path)
            assert not caught, [str(w.message) for w in caught]
            assert shape == (1, 1, len(channels), *SPLIT_IMAGE_SHAPE)
            # The file itself records which global timepoint it holds.
            description = root.find("ome:Image/ome:Description", ns)
            assert description is not None and f"Timepoint {t}" in description.text
            for c in range(len(channels)):
                np.testing.assert_array_equal(data[0, 0, c], (t + 1) * 10 + c)
            assert pixels is not None
            assert pixels.get("SizeT") == "1"
            assert pixels.get("SizeZ") == "1"
            assert pixels.get("SizeC") == str(len(channels))
            assert float(pixels.get("TimeIncrement", "nan")) == pytest.approx(1.5)
            assert len(planes) == len(channels)
            for plane in planes:
                # TheT is the index inside this file, which holds a single timepoint.
                assert plane.get("TheT") == "0"
            delta_ts.append(min(float(plane.get("DeltaT", "nan")) for plane in planes))

        # DeltaT stays relative to the acquisition start, so later files have later deltas.
        assert delta_ts[0] >= 0.0
        assert delta_ts[1] >= delta_ts[0]
        assert delta_ts[1] == pytest.approx(10.0, abs=1e-3)

        # Each timepoint tracks its own progress file.
        assert metadata_paths[0] != metadata_paths[1]

        # The last channel of each timepoint finalizes that timepoint's file.
        for t, path in enumerate(paths):
            assert results[(t, 0)].unit_complete is False
            assert results[(t, 1)].unit_complete is True
            assert results[(t, 1)].unit_paths == (os.path.abspath(str(path)),)
            assert results[(t, 1)].unit_kind == "file"

        # Progress files are removed once each stack completes.
        for metadata_path in metadata_paths:
            assert not os.path.exists(metadata_path), f"stale progress file left behind: {metadata_path}"
    finally:
        _remove_metadata_files(*metadata_paths)


def test_unsplit_two_timepoints_single_file(tmp_path) -> None:
    """Default (split_timepoints=False) still produces one multi-T stack per FOV."""
    from control.core.job_processing import AcquisitionInfo
    from control.core import utils_ome_tiff_writer as ome_tiff_writer

    channels = _build_channels(["DAPI", "GFP"])
    start_time = time.time()
    acquisition_info = AcquisitionInfo(
        total_time_points=2,
        total_z_levels=1,
        total_channels=len(channels),
        channel_names=[c.name for c in channels],
        experiment_path=str(tmp_path),
        time_increment_s=1.5,
    )
    assert acquisition_info.split_timepoints is False

    results = {}
    capture_info = None
    metadata_path = None
    try:
        for t in range(2):
            time_point_dir = tmp_path / f"{t}"
            time_point_dir.mkdir(parents=True, exist_ok=True)
            for c, channel in enumerate(channels):
                capture_info = _make_capture_info(
                    channel, c, t, str(time_point_dir), capture_time=start_time + 10.0 * t + c
                )
                results[(t, c)] = _run_plane(acquisition_info, capture_info, fill_value=(t + 1) * 10 + c)

        base_name = ome_tiff_writer.ome_base_name(capture_info)
        metadata_path = ome_tiff_writer.metadata_temp_path(acquisition_info, capture_info, base_name)

        output_path = tmp_path / "ome_tiff" / f"{base_name}.ome.tiff"
        assert output_path.exists()
        assert not (tmp_path / "0" / "ome_tiff").exists()

        shape, data, pixels, planes, root = _read_stack(output_path)
        assert shape == (2, 1, len(channels), *SPLIT_IMAGE_SHAPE)
        # No per-timepoint provenance is added to the unsplit stack.
        assert (
            root.find("ome:Image/ome:Description", {"ome": "http://www.openmicroscopy.org/Schemas/OME/2016-06"}) is None
        )
        for t in range(2):
            for c in range(len(channels)):
                np.testing.assert_array_equal(data[t, 0, c], (t + 1) * 10 + c)
        assert pixels.get("SizeT") == "2"
        assert sorted(plane.get("TheT") for plane in planes) == ["0", "0", "1", "1"]

        # Only the very last plane of the whole run finalizes the single file.
        assert results[(0, 1)].unit_complete is False
        assert results[(1, 1)].unit_complete is True
        assert results[(1, 1)].unit_paths == (os.path.abspath(str(output_path)),)
        assert not os.path.exists(metadata_path)
    finally:
        if metadata_path:
            _remove_metadata_files(metadata_path)


def test_split_timepoints_simulation_mode(tmp_path) -> None:
    """Simulated disk I/O with split enabled writes nothing but keeps the plane identity."""
    import control._def as _def
    from control.core.job_processing import AcquisitionInfo

    channels = _build_channels(["DAPI"])
    acquisition_info = AcquisitionInfo(
        total_time_points=2,
        total_z_levels=1,
        total_channels=1,
        channel_names=["DAPI"],
        experiment_path=str(tmp_path),
        split_timepoints=True,
        acquisition_start_time=time.time(),
    )

    original_enabled = _def.SIMULATED_DISK_IO_ENABLED
    original_speed = _def.SIMULATED_DISK_IO_SPEED_MB_S
    _def.SIMULATED_DISK_IO_ENABLED = True
    _def.SIMULATED_DISK_IO_SPEED_MB_S = 10000.0
    try:
        for t in range(2):
            time_point_dir = tmp_path / f"{t}"
            time_point_dir.mkdir(parents=True, exist_ok=True)
            capture_info = _make_capture_info(channels[0], 0, t, str(time_point_dir), capture_time=time.time())
            result = _run_plane(acquisition_info, capture_info, fill_value=t)

            assert result.unit_complete is False
            assert result.unit_paths == ()
            assert result.bytes_written > 0
            assert result.time_point == t
            assert result.region_id == str(REGION_ID)
            assert result.fov == FOV
            assert result.channel_idx == 0
            assert not (time_point_dir / "ome_tiff").exists()
    finally:
        _def.SIMULATED_DISK_IO_ENABLED = original_enabled
        _def.SIMULATED_DISK_IO_SPEED_MB_S = original_speed


def test_split_timepoints_abort_does_not_leak_into_next_timepoint(tmp_path) -> None:
    """An aborted timepoint leaves its own stack incomplete; the next timepoint still finalizes."""
    from control.core.job_processing import AcquisitionInfo
    from control.core import utils_ome_tiff_writer as ome_tiff_writer

    channels = _build_channels(["DAPI", "GFP"])
    start_time = time.time()
    acquisition_info = AcquisitionInfo(
        total_time_points=2,
        total_z_levels=1,
        total_channels=len(channels),
        channel_names=[c.name for c in channels],
        experiment_path=str(tmp_path),
        split_timepoints=True,
        acquisition_start_time=start_time,
    )

    metadata_paths = []
    try:
        # t=0 aborts after one of two channels.
        dir_t0 = tmp_path / "0"
        dir_t0.mkdir(parents=True, exist_ok=True)
        info_t0 = _make_capture_info(channels[0], 0, 0, str(dir_t0), capture_time=start_time)
        result_t0 = _run_plane(acquisition_info, info_t0, fill_value=1)
        assert result_t0.unit_complete is False

        # t=1 runs to completion.
        dir_t1 = tmp_path / "1"
        dir_t1.mkdir(parents=True, exist_ok=True)
        results_t1 = []
        for c, channel in enumerate(channels):
            info_t1 = _make_capture_info(channel, c, 1, str(dir_t1), capture_time=start_time + 10.0 + c)
            results_t1.append(_run_plane(acquisition_info, info_t1, fill_value=20 + c))

        base_name = ome_tiff_writer.ome_base_name(info_t0)
        metadata_paths = [
            ome_tiff_writer.metadata_temp_path(acquisition_info, info_t0, base_name),
            ome_tiff_writer.metadata_temp_path(acquisition_info, info_t1, base_name),
        ]

        path_t0 = dir_t0 / "ome_tiff" / f"{base_name}.ome.tiff"
        path_t1 = dir_t1 / "ome_tiff" / f"{base_name}.ome.tiff"
        assert path_t0.exists(), "the aborted timepoint's partial stack is still on disk"
        assert path_t1.exists()

        # t=1 finalized on its own progress file, t=0's is still pending.
        assert results_t1[-1].unit_complete is True
        assert results_t1[-1].unit_paths == (os.path.abspath(str(path_t1)),)
        assert not os.path.exists(metadata_paths[1])
        assert os.path.exists(metadata_paths[0]), "the aborted timepoint keeps its own progress file"

        t0_metadata = ome_tiff_writer.load_metadata(metadata_paths[0])
        assert t0_metadata[ome_tiff_writer.COMPLETED_KEY] is False
        assert t0_metadata[ome_tiff_writer.SAVED_COUNT_KEY] == 1
        assert t0_metadata[ome_tiff_writer.EXPECTED_COUNT_KEY] == len(channels)
    finally:
        _remove_metadata_files(*metadata_paths)
