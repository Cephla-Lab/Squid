import pytest

from control.core.fluidics_protocol.resolve import ProtocolProblems, resolve_protocol
from control.models.fluidics_protocol import CoordinatesBlock, ProtocolFile

SETTINGS = {"channels": ["A"], "z_stack": {"nz": 2, "delta_z_um": 1.0}}
COORDS = {"regions": [{"name": "A1", "fovs": [[1.0, 2.0, 3.0]]}]}
ACQ_YAML = """acquisition:
  widget_type: wellplate
objective:
  name: 20x
z_stack:
  nz: 5
  delta_z_mm: 0.002
channels:
  - name: B
wellplate_scan:
  regions:
    - name: C4
      center_mm: [38.3, 28.7, 1.2]
"""


def _protocol(**imaging_row):
    row = {
        "type": "imaging",
        "round": "R01",
        "name": "image",
        "folder": "R01_image",
        "settings": "cur",
        "coordinates": "cur",
    }
    row.update(imaging_row)
    return ProtocolFile(
        imaging={"settings": {"cur": SETTINGS}, "coordinates": {"cur": COORDS}},
        sequences=[{"type": "flow_reagent", "round": "R01", "fluidic_port": 1, "flow_rate": 500, "volume": 500}, row],
    )


class RecordingPort:
    def __init__(self, fail=None):
        self.validated = []
        self.fail = fail

    def validate(self, rows):
        self.validated.append(rows)
        if self.fail:
            raise ValueError(self.fail)

    def plan(self, rows):
        class E:
            duration_seconds = 60.0

        return tuple(E() for _ in rows)


def test_header_keys_resolve_and_fluidics_rows_are_validated(tmp_path):
    port = RecordingPort()
    resolved = resolve_protocol(_protocol(), tmp_path, fluidics=port)
    assert [s.kind for s in resolved.steps] == ["fluidics", "imaging"]
    assert resolved.imaging[1].settings.z_stack.nz == 2 and resolved.imaging[1].coordinates.fov_count == 1
    assert port.validated == [[{"type": "flow_reagent", "fluidic_port": 1, "flow_rate": 500, "volume": 500}]]
    assert resolved.fluidics_estimate_s == 60.0


def test_file_sources_are_inlined_relative_to_base_dir(tmp_path):
    acq = tmp_path / "acquisitions" / "R24"
    acq.mkdir(parents=True)
    (acq / "acquisition.yaml").write_text(ACQ_YAML)
    (acq / "coordinates.csv").write_text("region,x (mm),y (mm),z (mm)\nC4,38.0,28.0,1.2\nC4,38.5,28.0,1.2\n")

    resolved = resolve_protocol(
        _protocol(settings="acquisitions/R24", coordinates="acquisitions/R24/coordinates.csv"), tmp_path
    )

    img = resolved.imaging[1]
    assert img.settings.channels == ["B"] and img.settings.z_stack.nz == 5
    assert img.settings.z_stack.delta_z_um == pytest.approx(2.0)
    assert img.settings.source_path.endswith("acquisitions/R24")
    assert img.coordinates.fov_count == 2 and img.coordinates.regions[0].name == "C4"
    row = resolved.protocol.sequences[1]
    assert row["settings"] == "file:acquisitions/R24" and row["coordinates"] == "file:acquisitions/R24/coordinates.csv"
    assert "file:acquisitions/R24" in resolved.protocol.imaging.settings


def test_every_problem_is_reported_at_once(tmp_path):
    p = _protocol(settings="missing_key", coordinates="nowhere.csv")
    p.imaging.coordinates["empty"] = CoordinatesBlock(regions=[])
    p.sequences.append(
        {
            "type": "imaging",
            "round": "R02",
            "name": "image",
            "folder": "R01_image",
            "settings": "cur",
            "coordinates": "empty",
        }
    )
    p.sequences.append({"type": "imaging", "round": "R03", "name": "image", "folder": "R03_image", "settings": "cur"})

    with pytest.raises(ProtocolProblems) as info:
        resolve_protocol(p, tmp_path, fluidics=RecordingPort(fail="fluidic_port=99 out of range"))

    text = "\n".join(info.value.problems)
    assert "missing_key" in text and "nowhere.csv" in text and "duplicate folder" in text
    assert "no FOVs" in text and "no coordinates" in text and "fluidic_port=99" in text


def test_excluded_imaging_rows_are_not_resolved(tmp_path):
    p = _protocol(include=False, settings="missing_key")
    resolved = resolve_protocol(p, tmp_path)
    assert resolved.imaging == {} and [s.kind for s in resolved.steps] == ["fluidics"]
