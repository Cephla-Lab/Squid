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
        "folder": "image",
        "settings": "cur",
        "coordinates": "cur",
    }
    row.update(imaging_row)
    return ProtocolFile(
        imaging={"settings": {"cur": SETTINGS}, "coordinates": {"cur": COORDS}},
        sequences=[{"type": "flow_reagent", "round": "R01", "fluidic_port": 1, "flow_rate": 500, "volume": 500}, row],
    )


class RecordingPort:
    def __init__(self, fail=None, reservoirs_refusal=None):
        self.validated = []
        self.fail = fail
        self.reservoir_checks = []
        self.reservoirs_refusal = reservoirs_refusal

    def validate(self, rows):
        self.validated.append(rows)
        if self.fail:
            raise ValueError(self.fail)

    def plan(self, rows):
        class E:
            duration_seconds = 60.0

        return tuple(E() for _ in rows)

    def check_reservoirs(self, plan):
        self.reservoir_checks.append(plan)
        if self.reservoirs_refusal:
            raise ValueError(self.reservoirs_refusal)
        return [("probes/A1", 100.0, 250.0)]


def _two_rounds():
    flow = {"type": "flow_reagent", "fluidic_port": 1, "flow_rate": 500, "volume": 500}
    return ProtocolFile(sequences=[{**flow, "round": "R01"}, {**flow, "round": "R02"}, {**flow, "round": "R02"}])


def test_the_volumes_are_checked_once_over_every_fluidics_step(tmp_path):
    port = RecordingPort()
    resolved = resolve_protocol(_two_rounds(), tmp_path, fluidics=port)
    assert len(port.validated) == 2  # two steps, two library runs
    assert [len(plan) for plan in port.reservoir_checks] == [3]  # one check, every step's plan end to end
    assert resolved.reservoirs == [("probes/A1", 100.0, 250.0)]

    refusing = RecordingPort(reservoirs_refusal="R02: probes/A1 cannot supply 500 uL")
    with pytest.raises(ProtocolProblems) as refused:
        resolve_protocol(_two_rounds(), tmp_path, fluidics=refusing)
    assert refused.value.problems == ["reservoirs: R02: probes/A1 cannot supply 500 uL"]

    unchecked = RecordingPort(reservoirs_refusal="never asked")
    assert resolve_protocol(_two_rounds(), tmp_path, fluidics=unchecked, check_reservoirs=False).reservoirs == []
    assert unchecked.reservoir_checks == []


def test_no_volume_check_while_a_step_is_invalid(tmp_path):
    port = RecordingPort(fail="port 99 is not on this rig")
    with pytest.raises(ProtocolProblems):
        resolve_protocol(_two_rounds(), tmp_path, fluidics=port)
    assert port.reservoir_checks == []  # the rows' own problems first; a replay of invalid rows says nothing


def test_header_keys_resolve_and_fluidics_rows_are_validated(tmp_path):
    port = RecordingPort()
    resolved = resolve_protocol(_protocol(), tmp_path, fluidics=port)
    assert [s.kind for s in resolved.steps] == ["fluidics", "imaging"]
    assert resolved.imaging[1].settings.z_stack.nz == 2 and resolved.imaging[1].coordinates.fov_count == 1
    assert port.validated == [
        [{"type": "flow_reagent", "round": "R01", "fluidic_port": 1, "flow_rate": 500, "volume": 500}]
    ]  # round passes through - the library accepts it natively
    assert resolved.fluidics_estimate_s == 60.0
    # imaging is priced at a rough 1 s/FOV; one FOV here, so the total is fluidics + 1 s
    assert resolved.imaging_estimate_s == 1.0
    assert resolved.total_estimate_s == 61.0


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
            "folder": "image",
            "settings": "cur",
            "coordinates": "empty",
        }
    )
    # R03 duplicates R02's derived output folder (both R... no — same round+base collides)
    p.sequences.append({"type": "imaging", "round": "R02", "name": "image", "folder": "image", "settings": "cur"})

    with pytest.raises(ProtocolProblems) as info:
        resolve_protocol(p, tmp_path, fluidics=RecordingPort(fail="fluidic_port=99 out of range"))

    text = "\n".join(info.value.problems)
    assert "missing_key" in text and "nowhere.csv" in text and "collides" in text
    assert "no FOVs" in text and "no coordinates" in text and "fluidic_port=99" in text


def test_excluded_imaging_rows_are_not_resolved(tmp_path):
    p = _protocol(include=False, settings="missing_key")
    resolved = resolve_protocol(p, tmp_path)
    assert resolved.imaging == {} and [s.kind for s in resolved.steps] == ["fluidics"]
