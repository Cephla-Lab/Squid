# /// script
# requires-python = ">=3.12"
# dependencies = ["ngff-zarr[validate]==0.50.0", "iohub==0.4.0"]
#
# [tool.uv]
# exclude-newer = "2026-10-30T00:00:00Z"
# ///
"""Open the samples from write_samples.py with ngff-zarr and iohub.

Downstream pipelines read Squid's Zarr output with these libraries. Both need a
newer Python than the Squid environment, so CI runs this script with uv, which
reads the dependencies above and fetches its own Python:

    uv run --python 3.12 tests/ngff_readers/read_samples.py <out_dir>

Python 3.12 (iohub's minimum) has wheels for every dependency; uv would
otherwise pick the newest Python and compile some of them from source.
exclude-newer makes uv ignore releases after that date, so the packages these
two pull in do not change from run to run; move it forward with the pins.

Exits non-zero if either library fails to open a sample or reads it differently
from expected.json: fields, axes, channels, shape, known pixel values, the pixel
size and z step with their units, and, for the aborted plate, the field list
(exactly the written fields) and each array's acquisition_complete flag.
"""

import json
import math
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
    aborted = expected["aborted_plate"]
    plate = from_hcs_zarr(aborted["path"], validate=True)
    fields = []
    for well in plate.metadata.wells:
        row, column = well.path.split("/")
        fields += [f"{well.path}/{image.path}" for image in plate.get_well(row, column).images]
    assert sorted(fields) == sorted(aborted["fields"]), fields


def check_ngff_zarr_image(path: str, expected: dict) -> None:
    multiscales = ngff_zarr.from_ngff_zarr(path, validate=True)
    image = multiscales.images[0]
    assert list(image.dims) == ["t", "c", "z", "y", "x"], image.dims
    assert list(image.data.shape) == expected["shape"], image.data.shape
    for pixel in expected["pixels"]:
        check_pixel(image.data[tuple(pixel["index"])].compute(), pixel)
    check_scale(image.scale, image.axes_units, expected)


def check_pixel(value, pixel: dict) -> None:
    """The pixel read back is the one written, so the reader decoded the image chunks, not just the metadata."""
    assert int(value) == pixel["value"], (int(value), pixel)


def check_scale(scale: dict, units: dict, expected: dict) -> None:
    """The reader puts the pixel size and z step on the right axes, in micrometers."""
    for axis, step_um in expected["scale"].items():
        assert math.isclose(scale[axis], step_um) and units[axis] == "micrometer", (axis, scale, units)


def check_iohub(expected: dict) -> None:
    with open_ome_zarr(expected["plate"], mode="r", layout="hcs") as plate:
        # iohub lists fields in name order ("B/3/10" before "B/3/2"), so compare them as sets
        fields = [name for name, _ in plate.positions()]
        assert sorted(fields) == sorted(expected["plate_fields"]), fields
        assert plate.channel_names == expected["channels"], plate.channel_names
        for _, position in plate.positions():
            check_iohub_position(position, expected)
    for image in expected["images"]:
        with open_ome_zarr(image, mode="r", layout="fov") as position:
            assert position.channel_names == expected["channels"], position.channel_names
            check_iohub_position(position, expected)
    aborted = expected["aborted_plate"]
    with open_ome_zarr(aborted["path"], mode="r", layout="hcs") as plate:
        fields = [name for name, _ in plate.positions()]
        assert sorted(fields) == sorted(aborted["fields"]), fields


def check_squid_flags(expected: dict) -> None:
    """Each field of the aborted plate says whether every planned plane was written (plain JSON, no library)."""
    aborted = expected["aborted_plate"]
    for field, complete in aborted["complete"].items():
        with open(os.path.join(aborted["path"], field, "zarr.json")) as f:
            squid = json.load(f)["attributes"]["_squid"]
        assert squid["acquisition_complete"] is complete, (field, squid)
        assert (squid["planes_written"] == squid["planes_expected"]) is complete, (field, squid)


def check_iohub_position(position, expected: dict) -> None:
    array = position["0"]
    assert list(array.shape) == expected["shape"], array.shape
    for pixel in expected["pixels"]:
        check_pixel(array[tuple(pixel["index"])], pixel)
    names = [axis.name for axis in position.axes]
    # Only spatial axes carry a unit in iohub (its channel axis has no unit attribute)
    units = {axis.name: axis.unit for axis in position.axes if axis.name in expected["scale"]}
    check_scale(dict(zip(names, position.scale)), units, expected)


def main(out_dir: str) -> int:
    with open(os.path.join(out_dir, "expected.json")) as f:
        expected = json.load(f)
    failures = 0
    for name, check in [("ngff-zarr", check_ngff_zarr), ("iohub", check_iohub), ("squid-flags", check_squid_flags)]:
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
