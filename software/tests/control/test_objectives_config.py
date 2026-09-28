import ast
from pathlib import Path

import pytest

import control.objectives_config as oc
from control.objectives_config import ChangerKind, ObjectivesConfigError


def _data(kind="nimotion_turret", objectives=None):
    if objectives is None:
        objectives = [
            {"name": "4x", "magnification": 4, "na": 0.13, "tube_lens_f_mm": 180, "slot": 1},
            {"name": "10x", "magnification": 10, "na": 0.3, "tube_lens_f_mm": 180, "slot": 2},
            {"name": "20x", "magnification": 20, "na": 0.8, "tube_lens_f_mm": 180, "slot": 3},
        ]
    return {"version": 1, "changer": {"kind": kind}, "objectives": objectives}


def _valid(data, *, use_xeryon=False, use_turret=True):
    config = oc.parse_objectives_config(data)
    oc.validate_objectives_config(config, use_xeryon=use_xeryon, use_turret=use_turret)
    return config


def test_module_imports_only_allowed_modules():
    tree = ast.parse(Path(oc.__file__).read_text())
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module)
    for module in modules:
        assert not module.startswith("squid"), module
        if module.startswith("control"):
            assert module == "control.objective_changer_constants", module


class TestParse:
    def test_valid_turret(self):
        config = _valid(_data())
        assert config.changer.kind is ChangerKind.NIMOTION_TURRET
        assert [o.name for o in config.objectives] == ["4x", "10x", "20x"]

    def test_integer_optics_coerce_to_float(self):
        config = _valid(_data())
        assert config.objectives[0].magnification == 4.0
        assert isinstance(config.objectives[0].magnification, float)

    def test_unknown_key_rejected(self):
        data = _data()
        data["objectives"][0]["colour"] = "red"
        with pytest.raises(ObjectivesConfigError) as err:
            oc.parse_objectives_config(data)
        assert "objectives.0.colour" in err.value.field

    def test_blank_optics_rejected(self):
        data = _data()
        data["objectives"][0]["magnification"] = None
        with pytest.raises(ObjectivesConfigError) as err:
            oc.parse_objectives_config(data)
        assert "magnification" in err.value.field

    def test_empty_file_is_an_error(self, tmp_path):
        path = tmp_path / "objectives.yaml"
        path.write_text("   \n")
        with pytest.raises(ObjectivesConfigError) as err:
            oc.load_objectives_config(path)
        assert err.value.path == path

    def test_malformed_yaml_is_an_error(self, tmp_path):
        path = tmp_path / "objectives.yaml"
        path.write_text("objectives: [\n")
        with pytest.raises(ObjectivesConfigError) as err:
            oc.load_objectives_config(path)
        assert "YAML" in err.value.reason

    def test_missing_file_is_none(self, tmp_path):
        assert oc.load_objectives_config(tmp_path / "absent.yaml") is None

    def test_error_message_names_file_field_and_remedy(self, tmp_path):
        err = ObjectivesConfigError(tmp_path / "objectives.yaml", "objectives[0].slot", "must be 1..4")
        text = str(err)
        assert "objectives.yaml" in text and "objectives[0].slot" in text and "delete" in text


class TestValidate:
    def test_at_least_one_objective(self):
        with pytest.raises(ObjectivesConfigError, match="at least one"):
            _valid(_data(objectives=[]))

    @pytest.mark.parametrize("name", ["", " 4x", "4x ", "a/b", "a\\b", ".", "..", "general", "General"])
    def test_bad_names(self, name):
        data = _data()
        data["objectives"][0]["name"] = name
        with pytest.raises(ObjectivesConfigError) as err:
            _valid(data)
        assert err.value.field == "objectives[0].name"

    def test_case_insensitive_duplicate_names(self):
        data = _data()
        data["objectives"][1]["name"] = "4X"
        with pytest.raises(ObjectivesConfigError, match="case-insensitively"):
            _valid(data)

    @pytest.mark.parametrize("field, value", [("magnification", 0), ("na", 0), ("na", 1.6), ("tube_lens_f_mm", -1)])
    def test_optics_bounds(self, field, value):
        data = _data()
        data["objectives"][0][field] = value
        with pytest.raises(ObjectivesConfigError) as err:
            _valid(data)
        assert err.value.field == f"objectives[0].{field}"

    def test_duplicate_slot(self):
        data = _data()
        data["objectives"][1]["slot"] = 1
        with pytest.raises(ObjectivesConfigError, match="already used"):
            _valid(data)

    @pytest.mark.parametrize("slot", [0, 5])
    def test_turret_slot_range(self, slot):
        data = _data()
        data["objectives"][0]["slot"] = slot
        with pytest.raises(ObjectivesConfigError, match="1..4"):
            _valid(data)

    def test_xeryon_one_objective_per_position(self):
        objectives = [
            {"name": "10x", "magnification": 10, "na": 0.3, "tube_lens_f_mm": 180, "slot": 1},
            {"name": "20x", "magnification": 20, "na": 0.8, "tube_lens_f_mm": 180, "slot": 1},
        ]
        with pytest.raises(ObjectivesConfigError, match="already used"):
            _valid(_data("xeryon", objectives), use_xeryon=True, use_turret=False)

    def test_xeryon_slot_range(self):
        objectives = [{"name": "10x", "magnification": 10, "na": 0.3, "tube_lens_f_mm": 180, "slot": 3}]
        with pytest.raises(ObjectivesConfigError, match="1..2"):
            _valid(_data("xeryon", objectives), use_xeryon=True, use_turret=False)

    def test_missing_slot_on_a_changer(self):
        data = _data()
        data["objectives"][0]["slot"] = None
        with pytest.raises(ObjectivesConfigError, match="required"):
            _valid(data)

    def test_slot_given_without_a_changer(self):
        with pytest.raises(ObjectivesConfigError, match="empty"):
            _valid(_data("none"), use_xeryon=False, use_turret=False)

    @pytest.mark.parametrize(
        "kind, use_xeryon, use_turret",
        [
            ("nimotion_turret", True, False),
            ("xeryon", False, True),
            ("none", False, True),
            ("nimotion_turret", False, False),
        ],
    )
    def test_kind_must_match_ini_flags(self, kind, use_xeryon, use_turret):
        with pytest.raises(ObjectivesConfigError) as err:
            _valid(_data(kind), use_xeryon=use_xeryon, use_turret=use_turret)
        assert err.value.field == "changer.kind"


class TestDerivedAndRoundTrip:
    def test_objectives_dict_shape(self):
        assert oc.to_objectives_dict(_valid(_data()))["10x"] == {
            "magnification": 10.0,
            "NA": 0.3,
            "tube_lens_f_mm": 180.0,
        }

    def test_turret_positions(self):
        assert oc.to_turret_positions(_valid(_data())) == {"4x": 1, "10x": 2, "20x": 3}

    def test_xeryon_lists(self):
        objectives = [
            {"name": "10x", "magnification": 10, "na": 0.3, "tube_lens_f_mm": 180, "slot": 1},
            {"name": "4x", "magnification": 4, "na": 0.13, "tube_lens_f_mm": 180, "slot": 2},
        ]
        config = _valid(_data("xeryon", objectives), use_xeryon=True, use_turret=False)
        assert oc.to_xeryon_lists(config) == (["10x"], ["4x"])

    def test_save_then_load(self, tmp_path):
        config = _valid(_data())
        path = tmp_path / "sub" / "objectives.yaml"
        oc.save_objectives_config(config, path)
        assert oc.load_objectives_config(path) == config


class TestSerialRule:
    @pytest.mark.parametrize(
        "recorded, current, matches",
        [("", "", True), ("", "SN1", True), ("SN1", "SN1", True), ("SN1", "SN2", False), ("SN1", "", False)],
    )
    def test_serial_matches(self, recorded, current, matches):
        assert oc.serial_matches(recorded, current) is matches
