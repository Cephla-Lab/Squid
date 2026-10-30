"""Write small Zarr v3 samples through SaveZarrJob for the NGFF reader check in CI.

Writes an HCS plate (wells B3 with two fields and D5 with one, so rows and
columns have gaps) and a non-HCS per-FOV image, plus expected.json describing
what a reader should find. read_samples.py then opens them with ngff-zarr and
iohub, which need a newer Python than the Squid environment.

Usage (from software/): python3 -m tests.ngff_readers.write_samples <out_dir>
"""

import json
import os
import sys
import time

import numpy as np

import squid.abc
from control.core.job_processing import CaptureInfo, JobImage, SaveZarrJob, ZarrWriterInfo
from control.models import AcquisitionChannel, CameraSettings, IlluminationSettings

CHANNELS = ["BF LED matrix full", "Fluorescence 488 nm Ex"]
T_SIZE, Z_SIZE, IMAGE_SHAPE = 1, 3, (64, 64)


def capture_info(region_id: str, fov: int, z_index: int, config_idx: int) -> CaptureInfo:
    return CaptureInfo(
        position=squid.abc.Pos(x_mm=0.0, y_mm=0.0, z_mm=0.0, theta_rad=None),
        z_index=z_index,
        capture_time=time.time(),
        configuration=AcquisitionChannel(
            name=CHANNELS[config_idx],
            illumination_settings=IlluminationSettings(illumination_channel="LED", intensity=50.0),
            camera_settings=CameraSettings(exposure_time_ms=10.0, gain_mode=1.0),
        ),
        save_directory="unused",
        file_id=f"{region_id}_{fov}_{z_index}",
        region_id=region_id,
        fov=fov,
        configuration_idx=config_idx,
        time_point=0,
    )


def write_acquisition(base_path: str, is_hcs: bool, region_fov_counts: dict) -> None:
    info = ZarrWriterInfo(
        base_path=base_path,
        t_size=T_SIZE,
        c_size=len(CHANNELS),
        z_size=Z_SIZE,
        is_hcs=is_hcs,
        region_fov_counts=region_fov_counts,
        pixel_size_um=0.65,
        z_step_um=1.5,
        channel_names=CHANNELS,
        channel_colors=["#FFFFFF", "#1FFF00"],
        channel_wavelengths=[None, 488],
    )
    for region_id, fov_count in region_fov_counts.items():
        for fov in range(fov_count):
            for c in range(len(CHANNELS)):
                for z in range(Z_SIZE):
                    job = SaveZarrJob(
                        capture_info=capture_info(region_id, fov, z, c),
                        capture_image=JobImage(image_array=np.full(IMAGE_SHAPE, 100 * c + z, dtype=np.uint16)),
                    )
                    job.zarr_writer_info = info
                    job.run()
    if not SaveZarrJob.finalize_all_writers():
        raise RuntimeError(f"Failed to finalize zarr writers under {base_path}")


def main(out_dir: str) -> None:
    hcs_base = os.path.join(out_dir, "hcs")
    per_fov_base = os.path.join(out_dir, "per_fov")
    write_acquisition(hcs_base, is_hcs=True, region_fov_counts={"B3": 2, "D5": 1})
    write_acquisition(per_fov_base, is_hcs=False, region_fov_counts={"region_0": 1})

    expected = {
        "plate": os.path.join(hcs_base, "plate.ome.zarr"),
        "plate_fields": ["B/3/0", "B/3/1", "D/5/0"],
        "images": [os.path.join(per_fov_base, "zarr", "region_0", "fov_0.ome.zarr")],
        "channels": CHANNELS,
        "shape": [T_SIZE, len(CHANNELS), Z_SIZE, *IMAGE_SHAPE],
    }
    with open(os.path.join(out_dir, "expected.json"), "w") as f:
        json.dump(expected, f, indent=2)
    print(f"Wrote samples to {out_dir}")


if __name__ == "__main__":
    main(sys.argv[1])
