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


def test_conftest_patches_objectives_yaml_path_before_any_other_control_import():
    """F5: control._def reads objectives_config.OBJECTIVES_YAML_PATH at import time, so
    tests/conftest.py must patch it before any other control.* import pulls _def in
    transitively (e.g. control.microcontroller). Otherwise a real machine_configs/objectives.yaml
    on a dev/bench machine changes OBJECTIVES for the whole in-process suite, or sys.exit(1)s
    at collection."""
    conftest_path = Path(oc.__file__).resolve().parent.parent / "tests" / "conftest.py"
    tree = ast.parse(conftest_path.read_text())
    patch_lineno = None
    first_other_control_import_lineno = None
    for node in ast.walk(tree):
        names_and_modules = []
        if isinstance(node, ast.Import):
            names_and_modules = [(alias.name, node.lineno) for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names_and_modules = [(node.module, node.lineno)]
        for module, lineno in names_and_modules:
            if module == "control.objectives_config":
                patch_lineno = lineno
            elif module.startswith("control") and first_other_control_import_lineno is None:
                first_other_control_import_lineno = lineno
    assert patch_lineno is not None, "conftest.py must import control.objectives_config"
    assert first_other_control_import_lineno is not None, "conftest.py must import some other control.* module"
    assert patch_lineno < first_other_control_import_lineno


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

    def test_invalid_utf8_bytes_is_an_error(self, tmp_path):
        path = tmp_path / "objectives.yaml"
        path.write_bytes(b"version: 1\nchanger: {kind: none}\nobjectives:\n  - name: \xff\xfe bad\n")
        with pytest.raises(ObjectivesConfigError) as err:
            oc.load_objectives_config(path)
        assert "cannot be read" in err.value.reason

    def test_directory_at_the_path_is_an_error(self, tmp_path):
        path = tmp_path / "objectives.yaml"
        path.mkdir()
        with pytest.raises(ObjectivesConfigError) as err:
            oc.load_objectives_config(path)
        assert "cannot be read" in err.value.reason

    def test_utf8_model_string_round_trips(self, tmp_path):
        data = _data()
        data["objectives"][0]["model"] = "20× water"
        config = _valid(data)
        path = tmp_path / "objectives.yaml"
        oc.save_objectives_config(config, path)
        loaded = oc.load_objectives_config(path)
        assert loaded.objectives[0].model == "20× water"


class TestValidate:
    def test_at_least_one_objective(self):
        with pytest.raises(ObjectivesConfigError, match="at least one"):
            _valid(_data(objectives=[]))

    @pytest.mark.parametrize(
        "name",
        [
            "",
            " 4x",
            "4x ",
            "a/b",
            "a\\b",
            ".",
            "..",
            "general",
            "General",
            "20x:oil",
            "20x*",
            "20x?",
            "20x|w",
            'a"b',
            "a<b",
            "a>b",
            "a\tb",
            "CON",
            "nul",
            "Com1",
            "con.oil",
            "NUL.20x",
            "COM1.x",
            "CON .oil",
        ],
    )
    def test_bad_names(self, name):
        data = _data()
        data["objectives"][0]["name"] = name
        with pytest.raises(ObjectivesConfigError) as err:
            _valid(data)
        assert err.value.field == "objectives[0].name"

    @pytest.mark.parametrize("name", ["Condenser 10x", "Console.x", "con10x.oil"])
    def test_dotted_name_not_matching_a_reserved_prefix_is_accepted(self, name):
        # A name whose stem before the first dot is not itself (once stripped of surrounding
        # spaces) an exact reserved device name must not be rejected by the dotted-prefix check,
        # even when the stem merely starts with or contains one ("Console", "con10x").
        data = _data()
        data["objectives"][0]["name"] = name
        _valid(data)

    def test_case_insensitive_duplicate_names(self):
        data = _data()
        data["objectives"][1]["name"] = "4X"
        with pytest.raises(ObjectivesConfigError, match="case-insensitively"):
            _valid(data)

    @pytest.mark.parametrize(
        "field, value",
        [
            ("magnification", 0),
            ("na", 0),
            ("na", 1.6),
            ("tube_lens_f_mm", -1),
            ("magnification", 1e999),
            ("na", 1e999),
            ("tube_lens_f_mm", 1e999),
        ],
    )
    def test_optics_bounds(self, field, value):
        data = _data()
        data["objectives"][0][field] = value
        with pytest.raises(ObjectivesConfigError) as err:
            _valid(data)
        assert err.value.field == f"objectives[0].{field}"

    def test_version_other_than_1_rejected(self):
        data = _data()
        data["version"] = 7
        with pytest.raises(ObjectivesConfigError) as err:
            _valid(data)
        assert err.value.field == "version"

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

    def test_failed_publish_leaves_the_previous_file_untouched_and_no_temp_file(self, tmp_path, monkeypatch):
        # R6(b): save_objectives_config writes to a temp file and os.replace()s it onto the
        # real path; if the replace fails, the previous file must be byte-identical and no
        # temp file must be left behind.
        path = tmp_path / "objectives.yaml"
        oc.save_objectives_config(_valid(_data()), path)
        original_bytes = path.read_bytes()

        def _raise(*a, **k):
            raise OSError("disk full")

        new_config = _valid(
            _data(objectives=[{"name": "99x", "magnification": 99, "na": 1.0, "tube_lens_f_mm": 180, "slot": 1}])
        )
        monkeypatch.setattr(oc.os, "replace", _raise)
        with pytest.raises(OSError):
            oc.save_objectives_config(new_config, path)
        assert path.read_bytes() == original_bytes
        assert [p.name for p in tmp_path.iterdir()] == ["objectives.yaml"]  # no temp file left behind

    def test_non_oserror_during_publish_leaves_no_temp_file_and_propagates(self, tmp_path, monkeypatch):
        # The cleanup must run for ANY exception during the write/replace, not only OSError.
        path = tmp_path / "objectives.yaml"
        config = _valid(_data())

        def _raise(*a, **k):
            raise ValueError("boom")

        monkeypatch.setattr(oc.os, "replace", _raise)
        with pytest.raises(ValueError, match="boom"):
            oc.save_objectives_config(config, path)
        assert not path.exists()
        assert list(tmp_path.iterdir()) == []  # no temp file left behind

    def test_unlink_failure_during_cleanup_does_not_mask_the_original_error(self, tmp_path, monkeypatch):
        path = tmp_path / "objectives.yaml"
        config = _valid(_data())

        def _raise_replace(*a, **k):
            raise OSError("disk full")

        def _raise_unlink(self, missing_ok=False):
            raise OSError("cannot unlink temp file")

        monkeypatch.setattr(oc.os, "replace", _raise_replace)
        monkeypatch.setattr(oc.Path, "unlink", _raise_unlink)
        with pytest.raises(OSError, match="disk full"):
            oc.save_objectives_config(config, path)


class TestSerialRule:
    @pytest.mark.parametrize(
        "recorded, current, matches",
        [("", "", True), ("", "SN1", True), ("SN1", "SN1", True), ("SN1", "SN2", False), ("SN1", "", False)],
    )
    def test_serial_matches(self, recorded, current, matches):
        assert oc.serial_matches(recorded, current) is matches
