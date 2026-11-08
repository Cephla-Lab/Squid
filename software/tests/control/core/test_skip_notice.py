"""Skip notices: the frames of a relative z-stack left out because they are outside the actuator's travel.

The worker sends one notice per lock-on group, in order with the image jobs. The Zarr and OME-TIFF jobs create a
FOV's array or file from it when no image did (so a wholly skipped FOV still exists, as an empty tile), record the
skipped frames in their Squid metadata, and count them toward completion. Individual TIFFs write nothing.

The jobs run in-process, with the injected writer info set by hand (JobRunner.dispatch does it in a run).
"""

import json
import os
import time
import xml.etree.ElementTree as ET

import numpy as np
import pytest
import tifffile

import squid.abc
from control.core import utils_ome_tiff_writer as ome_tiff_writer
from control.core.job_processing import (
    AcquisitionInfo,
    CaptureInfo,
    JobImage,
    SaveImageJob,
    SaveOMETiffJob,
    SaveZarrJob,
    SkippedFrames,
    ZarrWriterInfo,
)
from control.models import AcquisitionChannel, CameraSettings, IlluminationSettings

OME_NS = {"ome": "http://www.openmicroscopy.org/Schemas/OME/2016-06"}
SHAPE = (30, 50)  # (height, width): not square, so a swapped Y / X shows
CHANNELS = ["A", "B"]


def _channel(name):
    return AcquisitionChannel(
        name=name,
        display_color="#FFFFFF",
        camera=1,
        illumination_settings=IlluminationSettings(illumination_channel=name, intensity=5.0),
        camera_settings=CameraSettings(exposure_time_ms=10.0, gain_mode=1.0),
        z_offset_um=0.0,
    )


def _info(save_directory, region_id="A1", fov=0, t=0, c=0, z=0, skipped=None):
    return CaptureInfo(
        position=squid.abc.Pos(x_mm=1.0, y_mm=2.0, z_mm=0.5, theta_rad=None),
        z_index=z,
        capture_time=time.time(),
        configuration=_channel(CHANNELS[c]),
        save_directory=str(save_directory),
        file_id=f"{region_id}_{fov}_{z}",
        region_id=region_id,
        fov=fov,
        configuration_idx=c,
        time_point=t,
        skipped_frames=skipped,
    )


def _notice(frames, shape=SHAPE, dtype="uint16"):
    return SkippedFrames(frames=tuple(frames), frame_shape=shape, dtype=dtype)


def _image(value, shape=SHAPE):
    return np.full(shape, value, dtype=np.uint16)


# --- Zarr ----------------------------------------------------------------------------------------------------


@pytest.fixture
def zarr_writers():
    pytest.importorskip("tensorstore")
    SaveZarrJob.clear_writers()
    yield
    SaveZarrJob.clear_writers()


def _zarr_info(base_path, layout, t_size=1, z_size=3, fov_count=2):
    return ZarrWriterInfo(
        base_path=str(base_path),
        t_size=t_size,
        c_size=len(CHANNELS),
        z_size=z_size,
        is_hcs=layout == "hcs",
        use_6d_fov=layout == "6d",
        region_fov_counts={"A1": fov_count},
        channel_names=list(CHANNELS),
    )


def _run_zarr(zarr_info, info, image=None):
    job = SaveZarrJob(capture_info=info, capture_image=JobImage(image_array=image))
    job.zarr_writer_info = zarr_info
    return job.run()


def _squid_attrs(zarr_info, fov):
    path = zarr_info.get_output_path("A1", fov)
    if not zarr_info.use_6d_fov or zarr_info.is_hcs:
        path = os.path.dirname(path)  # the OME-NGFF group holds the metadata
    with open(os.path.join(path, "zarr.json")) as f:
        return json.load(f)["attributes"]["_squid"]


def _read_zarr(zarr_info, fov):
    import tensorstore as ts

    path = zarr_info.get_output_path("A1", fov)
    return ts.open({"driver": "zarr3", "kvstore": {"driver": "file", "path": path}}).result().read().result()


@pytest.mark.parametrize("layout", ["hcs", "5d", "6d"])
def test_wholly_skipped_first_fov_creates_its_zarr_array(tmp_path, zarr_writers, layout):
    zarr_info = _zarr_info(tmp_path, layout)
    frames = [(c, k) for c in range(2) for k in range(3)]

    result = _run_zarr(zarr_info, _info(tmp_path, fov=0, skipped=_notice(frames)))

    assert result is True  # not a ZarrWriteResult: the viewer is told of written frames only
    data = _read_zarr(zarr_info, 0)
    if layout == "6d":
        assert data.shape == (2, 1, 2, 3, *SHAPE)  # the region array, every FOV
        expected_skips = [[0, 0, c, k] for c, k in frames]
    else:
        assert data.shape == (1, 2, 3, *SHAPE)
        expected_skips = [[0, c, k] for c, k in frames]
    assert data.dtype == np.uint16
    assert not data.any()
    assert "skipped_frames" not in _squid_attrs(zarr_info, 0)  # written once, at finalize
    if layout == "hcs":
        with open(os.path.join(zarr_info.get_well_path("A1"), "zarr.json")) as f:
            well = json.load(f)["attributes"]["ome"]["well"]
        assert [image["path"] for image in well["images"]] == ["0", "1"]
        assert os.path.exists(os.path.join(zarr_info.get_plate_path(), "zarr.json"))

    assert SaveZarrJob.finalize_all_writers()
    squid_attrs = _squid_attrs(zarr_info, 0)
    assert squid_attrs["skipped_frames"] == expected_skips
    assert squid_attrs["acquisition_complete"] is True


@pytest.mark.parametrize("notice_first", [True, False])
def test_partly_skipped_fov_writes_images_and_records_the_rest(tmp_path, zarr_writers, notice_first):
    # The notice precedes its group's frames, except a notice deferred until the run's first frame.
    zarr_info = _zarr_info(tmp_path, "5d")
    skipped = [(0, 0), (1, 2)]
    imaged = [(c, k) for c in range(2) for k in range(3) if (c, k) not in skipped]
    notice = _info(tmp_path, skipped=_notice(skipped))
    images = [(_info(tmp_path, c=c, z=k), _image(10 * c + k + 1)) for c, k in imaged]

    if notice_first:
        assert _run_zarr(zarr_info, notice) is True
    for info, image in images:
        assert _run_zarr(zarr_info, info, image).z_index == info.z_index
    if not notice_first:
        assert _run_zarr(zarr_info, notice) is True

    data = _read_zarr(zarr_info, 0)
    for c, k in imaged:
        assert (data[0, c, k] == 10 * c + k + 1).all()
    for c, k in skipped:
        assert not data[0, c, k].any()
    assert SaveZarrJob.finalize_all_writers()
    squid_attrs = _squid_attrs(zarr_info, 0)
    assert squid_attrs["skipped_frames"] == [[0, 0, 0], [0, 1, 2]]
    assert squid_attrs["acquisition_complete"] is True


def test_a_zarr_run_without_skips_writes_no_skipped_frames_attribute(tmp_path, zarr_writers):
    zarr_info = _zarr_info(tmp_path, "5d", z_size=1)
    for c in range(2):
        _run_zarr(zarr_info, _info(tmp_path, c=c), _image(c + 1))
    assert SaveZarrJob.finalize_all_writers()
    assert "skipped_frames" not in _squid_attrs(zarr_info, 0)


# --- OME-TIFF ------------------------------------------------------------------------------------------------


def _acq_info(experiment_path, t=1, z=3):
    return AcquisitionInfo(
        total_time_points=t,
        total_z_levels=z,
        total_channels=len(CHANNELS),
        channel_names=list(CHANNELS),
        experiment_path=str(experiment_path),
    )


def _run_ome(acq_info, info, image=None):
    job = SaveOMETiffJob(capture_info=info, capture_image=JobImage(image_array=image))
    job.acquisition_info = acq_info
    return job.run()


def _metadata_path(acq_info, info):
    return ome_tiff_writer.metadata_temp_path(acq_info, info, ome_tiff_writer.ome_base_name(info))


def _skips_in_file(path):
    """The (t, c, z) planes the finished file lists as skipped, after checking the Image references them."""
    with tifffile.TiffFile(path) as tif:
        root = ET.fromstring(tif.ome_metadata)
    annotations = root.findall("ome:StructuredAnnotations/ome:MapAnnotation", OME_NS)
    skipped = [a for a in annotations if a.get("Namespace") == "squid.skipped_frames"]
    assert len(skipped) == 1
    refs = [ref.get("ID") for ref in root.findall("ome:Image/ome:AnnotationRef", OME_NS)]
    assert skipped[0].get("ID") in refs
    entries = skipped[0].findall("ome:Value/ome:M", OME_NS)
    assert {m.text for m in entries} == {"out of travel"}
    return sorted(tuple(int(n) for n in m.get("K").split(",")) for m in entries)


def test_wholly_skipped_fov_completes_its_ome_tiff(tmp_path):
    experiment = tmp_path / "exp"
    acq_info = _acq_info(experiment)
    frames = [(c, k) for c in range(2) for k in range(3)]
    info = _info(experiment / "000", skipped=_notice(frames))

    assert _run_ome(acq_info, info) is True

    # saved 0 + skipped 6 == expected 6: complete, so the temporary metadata JSON is gone.
    assert not os.path.exists(_metadata_path(acq_info, info))
    path = experiment / "ome_tiff" / "A1_0.ome.tiff"
    with tifffile.TiffFile(path) as tif:
        assert tif.series[0].get_axes(False) == "TZCYXS"
        data = tif.asarray(squeeze=False)[..., 0]  # one sample per pixel
    assert data.shape == (1, 3, 2, *SHAPE)  # T, Z, C, Y, X as planned
    assert data.dtype == np.uint16
    assert not data.any()
    assert _skips_in_file(path) == sorted((0, c, k) for c, k in frames)


def test_partly_skipped_ome_tiff_keeps_its_skips_in_the_finished_file(tmp_path):
    experiment = tmp_path / "exp"
    acq_info = _acq_info(experiment, t=2)
    skipped = {0: [(0, 0)], 1: [(1, 2)]}  # per time point: (c, k)
    jobs = []
    for t in range(2):
        save_directory = experiment / f"{t:03d}"
        jobs.append((_info(save_directory, t=t, skipped=_notice(skipped[t])), None))
        for c in range(2):
            for k in range(3):
                if (c, k) not in skipped[t]:
                    jobs.append((_info(save_directory, t=t, c=c, z=k), _image(100 * t + 10 * c + k + 1)))

    metadata_path = _metadata_path(acq_info, jobs[0][0])
    for info, image in jobs[:-1]:
        assert _run_ome(acq_info, info, image) is True
    assert os.path.exists(metadata_path)  # 9 written + 2 skipped of 12: not complete yet
    assert _run_ome(acq_info, *jobs[-1]) is True
    assert not os.path.exists(metadata_path)

    path = experiment / "ome_tiff" / "A1_0.ome.tiff"
    with tifffile.TiffFile(path) as tif:
        assert tif.series[0].get_axes(False) == "TZCYXS"
        data = tif.asarray(squeeze=False)[..., 0]  # one sample per pixel
    assert data.shape == (2, 3, 2, *SHAPE)
    for t in range(2):
        for c in range(2):
            for k in range(3):
                expected = 0 if (c, k) in skipped[t] else 100 * t + 10 * c + k + 1
                assert (data[t, k, c] == expected).all()
    assert _skips_in_file(path) == [(0, 0, 0), (1, 1, 2)]


# --- Individual TIFFs ----------------------------------------------------------------------------------------


def test_individual_tiff_job_ignores_a_skip_notice(tmp_path):
    job = SaveImageJob(
        capture_info=_info(tmp_path, skipped=_notice([(0, 0), (1, 0)])), capture_image=JobImage(image_array=None)
    )

    assert job.run() is True
    assert os.listdir(tmp_path) == []
