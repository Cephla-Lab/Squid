"""Saved-acquisition fixtures shared by the gallery tests: folders as MultiPointController and the protocol runner lay them out."""

import json

import numpy as np
import pytest
import tifffile
import yaml


def write_acquisition(
    root,
    name,
    *,
    start_time=None,
    widget_type="wellplate",
    channels=("BF LED matrix full", "Fluorescence 488 nm Ex"),
    nz=3,
    nt=2,
    regions=(("A1", 4), ("B2", 4)),
    images="tiff",
    with_yaml=True,
    with_overview=False,
):
    """A folder like <base_path>/<experiment_ID>: metadata files and a first-timepoint folder of images."""
    folder = root / name
    folder.mkdir()
    params = {"Nz": nz, "Nt": nt, "objective": {"name": "20x", "magnification": 20.0}}
    (folder / "acquisition parameters.json").write_text(json.dumps(params))
    if with_yaml:
        data = {
            "acquisition": {"experiment_id": name, "start_time": start_time, "widget_type": widget_type},
            "objective": {"name": "20x", "magnification": 20.0, "pixel_size_um": 0.325},
            "sample": {"wellplate_format": "96 well plate"},
            "z_stack": {"nz": nz, "delta_z_mm": 0.001},
            "time_series": {"nt": nt, "delta_t_s": 0.0},
            "channels": [{"name": c} for c in channels],
            "wellplate_scan" if widget_type == "wellplate" else "flexible_scan": {
                "regions" if widget_type == "wellplate" else "positions": [
                    {"name": r, "center_mm": [1.0, 2.0], "fovs": [[1.0, 2.0]] * n} for r, n in regions
                ]
            },
        }
        with open(folder / "acquisition.yaml", "w", encoding="utf-8") as f:
            f.write(f"# Acquisition Parameters - {name}\n\n")
            yaml.dump(data, f, sort_keys=False)
    with open(folder / "coordinates.csv", "w") as f:
        f.write("region,x (mm),y (mm),z (mm)\n")
        for region, n in regions:
            for _ in range(n):
                f.write(f"{region},1.0,2.0,3.0\n")
    tp = folder / "0"
    tp.mkdir()
    image = (np.arange(64 * 48, dtype=np.uint16).reshape(48, 64) * 100).astype(np.uint16)
    if images == "tiff":
        for region, n in regions:
            for fov in range(n):
                for ch in channels:
                    safe = ch.replace(" ", "_")
                    tifffile.imwrite(tp / f"{region}_{fov}_0_{safe}.tiff", image)
    elif images == "multipage":
        # As SaveImageJob.save_image (MULTI_PAGE_TIFF) appends them: one page per (z, channel), z outermost, the
        # channel in PageName and channel / z_level in the page description JSON. Pages above z=0 are black.
        for region, n in regions:
            for fov in range(n):
                for z in range(nz):
                    for ci, ch in enumerate(channels):
                        page = image if z == 0 else np.zeros_like(image)
                        metadata = {"z_level": z, "channel": ch, "channel_index": ci, "region_id": region, "fov": fov}
                        with tifffile.TiffWriter(tp / f"{region}_{fov}_stack.tiff", append=True) as writer:
                            writer.write(
                                page,
                                metadata=metadata,
                                description=json.dumps(metadata),
                                extratags=[(285, "s", 0, ch, False)],
                            )
    elif images == "ome":
        # As SaveOMETiffJob writes them: ome_tiff/<region>_<fov>.ome.tiff, a TZCYX stack with the OME Channel names.
        # Only t=0, z=0 is lit.
        ome = folder / "ome_tiff"
        ome.mkdir()
        stack = np.zeros((nt, nz, len(channels), *image.shape), dtype=np.uint16)
        stack[0, 0] = image
        for region, n in regions:
            for fov in range(n):
                tifffile.imwrite(
                    ome / f"{region}_{fov}.ome.tiff",
                    stack,
                    metadata={"axes": "TZCYX", "Channel": {"Name": list(channels)}},
                    ome=True,
                )
    elif images == "zarr":
        (folder / "zarr" / "A1" / "fov_0.ome.zarr").mkdir(parents=True)
        (folder / "zarr" / "A1" / "fov_0.ome.zarr" / "zarr.json").write_text("{}")
    elif images == "bmp":
        import cv2

        cv2.imwrite(str(tp / "A1_0_0_BF_LED_matrix_full.bmp"), (image >> 8).astype(np.uint8))
    for t in range(1, 2 if nt > 1 else 1):  # a second timepoint folder with its own images
        later = folder / str(t)
        later.mkdir()
        if images == "tiff":
            for region, n in regions:
                for fov in range(n):
                    for ch in channels:
                        tifffile.imwrite(later / f"{region}_{fov}_0_{ch.replace(' ', '_')}.tiff", image)
    if with_overview:
        for t in range(2 if nt > 1 else 1):
            mosaic = folder / str(t) / "mosaic_view"
            mosaic.mkdir()
            stack = np.zeros((2, 40, 60), dtype=np.uint16)
            stack[0, :20, :] = 4000  # top half lit in channel 0
            stack[1, 20:, :] = 4000  # bottom half lit in channel 1
            tifffile.imwrite(mosaic / "mosaic_plate_5um.ome.tiff", stack, metadata={"axes": "CYX"})
            with open(mosaic / "mosaic_plate_5um.yaml", "w", encoding="utf-8") as f:
                yaml.dump({"channel_names": list(channels)}, f)
    return folder


@pytest.fixture
def saving_root(tmp_path):
    root = tmp_path / "saved"
    root.mkdir()
    return root


def write_protocol_run(root, run_name, rounds, status="finished"):
    """A protocol run folder <root>/<run_name> as the fluidics protocol runner lays it out: run_manifest.json (none when
    status is None) and one round_<n>_image acquisition folder per imaging round, each acquisition.yaml carrying a
    `protocol` section. rounds is a count, or (round, attempt) pairs; attempts after the first get an _attempt<k> suffix.
    """
    run = root / run_name
    run.mkdir()
    pairs = [(n, 1) for n in range(1, rounds + 1)] if isinstance(rounds, int) else list(rounds)
    for n, attempt in pairs:
        folder_name = f"round_{n}_image" + (f"_attempt{attempt}" if attempt > 1 else "")
        folder = write_acquisition(run, folder_name, start_time=1762596000.0 + 100 * n + attempt)
        yaml_path = folder / "acquisition.yaml"
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        data["protocol"] = {
            "protocol": "stain",
            "run_name": run_name,
            "round": n,
            "step": "image",
            "row_index": n,
            "step_index": 0,
            "attempt": attempt,
        }
        yaml_path.write_text(yaml.dump(data, sort_keys=False), encoding="utf-8")
    if status is not None:
        manifest = {"run_name": run_name, "status": status, "started_at": 1762596000.0, "ended_at": 1762599000.0}
        (run / "run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return run
