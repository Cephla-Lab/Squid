# /// script
# requires-python = ">=3.12"
# dependencies = ["ngff-zarr[validate]==0.50.0", "iohub==0.4.0"]
# ///
"""Open the samples from write_samples.py with ngff-zarr and iohub.

Downstream pipelines read Squid's Zarr output with these libraries. Both need a
newer Python than the Squid environment, so CI runs this script with uv, which
reads the dependencies above and fetches its own Python:

    uv run tests/ngff_readers/read_samples.py <out_dir>

Exits non-zero if either library fails to open a sample or reads it differently
from expected.json.
"""

import json
import os
import sys
import traceback

import ngff_zarr
from iohub import open_ome_zarr
from ngff_zarr.hcs import from_hcs_zarr


def check_ngff_zarr(expected: dict) -> None:
    plate = from_hcs_zarr(expected["plate"], validate=True)
    wells = sorted(w.path for w in plate.metadata.wells)
    assert wells == sorted({f.rsplit("/", 1)[0] for f in expected["plate_fields"]}), wells
    for field in expected["plate_fields"]:
        check_ngff_zarr_image(os.path.join(expected["plate"], field), expected)
    for image in expected["images"]:
        check_ngff_zarr_image(image, expected)


def check_ngff_zarr_image(path: str, expected: dict) -> None:
    multiscales = ngff_zarr.from_ngff_zarr(path, validate=True)
    image = multiscales.images[0]
    assert list(image.dims) == ["t", "c", "z", "y", "x"], image.dims
    assert list(image.data.shape) == expected["shape"], image.data.shape


def check_iohub(expected: dict) -> None:
    with open_ome_zarr(expected["plate"], mode="r", layout="hcs") as plate:
        # iohub lists fields in name order ("B/3/10" before "B/3/2"), so compare them as sets
        fields = [name for name, _ in plate.positions()]
        assert sorted(fields) == sorted(expected["plate_fields"]), fields
        assert plate.channel_names == expected["channels"], plate.channel_names
        for _, position in plate.positions():
            assert list(position["0"].shape) == expected["shape"], position["0"].shape
    for image in expected["images"]:
        with open_ome_zarr(image, mode="r", layout="fov") as position:
            assert position.channel_names == expected["channels"], position.channel_names
            assert list(position["0"].shape) == expected["shape"], position["0"].shape


def main(out_dir: str) -> int:
    with open(os.path.join(out_dir, "expected.json")) as f:
        expected = json.load(f)
    failures = 0
    for name, check in [("ngff-zarr", check_ngff_zarr), ("iohub", check_iohub)]:
        try:
            check(expected)
            print(f"PASS {name}")
        except Exception:
            failures += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
