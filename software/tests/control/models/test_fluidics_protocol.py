import pytest
import yaml

from control.models.fluidics_protocol import (
    CoordinatesBlock,
    ProtocolFile,
    SettingsBlock,
    expand_rounds,
    folder_problems,
    load_protocol,
    parse_port_list,
    render_folder,
    save_protocol,
    split_into_steps,
    strip_for_library,
)

SETTINGS = {"channels": ["A", "B"], "z_stack": {"nz": 3, "delta_z_um": 0.5}, "autofocus": {"laser_af": True}}
COORDS = {"regions": [{"name": "A1", "fovs": [[1.0, 2.0, 3.0], [1.5, 2.0, 3.0]]}]}


def _protocol():
    return ProtocolFile(
        name="demo",
        imaging={"settings": {"cur": SETTINGS}, "coordinates": {"cur": COORDS}},
        sequences=[
            {
                "type": "priming",
                "round": "setup",
                "name": "prime",
                "fluidic_port": 25,
                "flow_rate": 5000,
                "volume": 800,
            },
            {
                "type": "flow_reagent",
                "round": "R01",
                "name": "probe",
                "fluidic_port": 1,
                "flow_rate": 2000,
                "volume": 500,
            },
            {
                "type": "flow_reagent",
                "round": "R01",
                "name": "wash",
                "fluidic_port": 25,
                "flow_rate": 5000,
                "volume": 1000,
            },
            {
                "type": "imaging",
                "round": "R01",
                "name": "image",
                "folder": "R01_image",
                "settings": "cur",
                "coordinates": "cur",
            },
            {
                "type": "flow_reagent",
                "round": "R01",
                "name": "cleave",
                "fluidic_port": 26,
                "flow_rate": 5000,
                "volume": 2000,
            },
            {
                "type": "clean_up",
                "round": "final",
                "name": "cleanup",
                "fluidic_port": 28,
                "flow_rate": 10000,
                "volume": 2000,
            },
        ],
    )


def test_blocks_accept_export_shaped_dicts():
    s = SettingsBlock.model_validate(SETTINGS)
    c = CoordinatesBlock.model_validate(COORDS)
    assert s.channels == ["A", "B"] and s.z_stack.nz == 3
    assert s.autofocus.laser_af is True and s.autofocus.contrast_af is False
    assert c.fov_count == 2 and c.regions[0].name == "A1"


def test_imaging_rows_are_validated_and_unknown_keys_rejected():
    with pytest.raises(ValueError):
        ProtocolFile(sequences=[{"type": "imaging", "folder": "x", "bogus": 1}])
    p = _protocol()
    assert [(i, r.folder) for i, r in p.imaging_rows()] == [(3, "R01_image")]


def test_strip_for_library_drops_imaging_rows_and_round_labels():
    rows = strip_for_library(_protocol().sequences)
    assert [r["type"] for r in rows] == ["priming", "flow_reagent", "flow_reagent", "flow_reagent", "clean_up"]
    assert all("round" not in r for r in rows)
    assert rows[1]["fluidic_port"] == 1  # other keys untouched


def test_split_into_steps_groups_contiguous_fluidics_rows_by_round():
    steps = split_into_steps(_protocol())
    assert [(s.kind, s.round) for s in steps] == [
        ("fluidics", "setup"),
        ("fluidics", "R01"),
        ("imaging", "R01"),
        ("fluidics", "R01"),
        ("fluidics", "final"),
    ]
    assert steps[1].row_indices == [1, 2] and steps[2].row_index == 3 and steps[3].row_indices == [4]
    assert [s.index for s in steps] == [0, 1, 2, 3, 4]


def test_split_into_steps_skips_excluded_rows():
    p = _protocol()
    p.sequences[3]["include"] = False
    p.sequences[2]["include"] = False
    steps = split_into_steps(p)
    assert [(s.kind, s.round) for s in steps] == [("fluidics", "setup"), ("fluidics", "R01"), ("fluidics", "final")]
    assert steps[1].row_indices == [1, 4]


def test_round_trip_as_a_plain_sequence_file(tmp_path):
    p = _protocol()
    path = tmp_path / "demo.yaml"
    save_protocol(p, str(path))
    raw = yaml.safe_load(path.read_text())
    assert list(raw) == ["version", "name", "imaging", "sequences"]
    assert list(raw["sequences"][0])[0] == "type"
    again = load_protocol(str(path))
    assert again == p
    # a standalone sequence file (no header) loads too
    (tmp_path / "plain.yaml").write_text(yaml.safe_dump({"sequences": p.sequences[:2]}))
    plain = load_protocol(str(tmp_path / "plain.yaml"))
    assert plain.name is None and len(plain.sequences) == 2 and plain.imaging.settings == {}


def test_render_folder_and_folder_problems():
    assert render_folder("{round}_{step}", round_label="R07", step_name="image", index=7) == "R07_image"
    assert render_folder("{index:02d}_{round}_{step}", round_label="R7", step_name="image", index=7) == "07_R7_image"
    p = _protocol()
    assert folder_problems(p) == []
    p.sequences.append({"type": "imaging", "round": "R02", "name": "image", "folder": "R01_image"})
    p.sequences.append({"type": "imaging", "round": "R03", "name": "image", "folder": "bad/name"})
    p.sequences.append({"type": "imaging", "round": "R04", "name": "image"})
    problems = folder_problems(p)
    assert any("duplicate" in m for m in problems)
    assert any("bad/name" in m for m in problems)
    assert any("no folder" in m for m in problems)


def test_expand_rounds_copies_a_round_with_new_labels_ports_and_folders():
    p = _protocol()
    out = expand_rounds(p, "R01", count=2, label_pattern="R{n:02d}", start=2, port_row_name="probe", ports=[2, 3])
    rounds = [r.get("round") for r in out.sequences]
    assert rounds.count("R02") == 4 and rounds.count("R03") == 4
    r02 = [r for r in out.sequences if r.get("round") == "R02"]
    assert [r["name"] for r in r02] == ["probe", "wash", "image", "cleave"]
    assert r02[0]["fluidic_port"] == 2 and r02[2]["folder"] == "R02_image"
    assert [r for r in out.sequences if r.get("round") == "R03"][0]["fluidic_port"] == 3
    assert out.sequences[-1]["round"] == "R03"  # generated rounds are appended at the end, in order
    assert len(p.sequences) == 6  # the input is untouched
    with pytest.raises(ValueError):
        expand_rounds(p, "R01", count=3, port_row_name="probe", ports=[2])


def test_parse_port_list():
    assert parse_port_list("2-4,7,9-10") == [2, 3, 4, 7, 9, 10]
    with pytest.raises(ValueError):
        parse_port_list("a-b")
