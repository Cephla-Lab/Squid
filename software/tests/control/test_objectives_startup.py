"""Startup behavior of control._def's objective list (spec A §4.2-4.4, §6).

Every test runs control._def in a subprocess from a scratch directory (see
objectives_startup_harness). The no-YAML tests pin today's behavior: they pass on master
before any change, and must keep passing after it.
"""

import csv
import json

import pytest

from tests.control.objectives_startup_harness import SOFTWARE_DIR, XERYON_INI, run_def

CSV_OBJECTIVES = {
    row["name"]: {
        "magnification": float(row["magnification"]),
        "NA": float(row["NA"]),
        "tube_lens_f_mm": float(row["tube_lens_f_mm"]),
    }
    for row in csv.DictReader(open(SOFTWARE_DIR / "objective_and_sample_formats" / "objectives.csv"))
}


def _cache(objective):
    return json.dumps({"objective": objective, "wellplate_format": "96"})


class TestNoYamlIsUnchanged:
    def test_pristine_ini(self, tmp_path):
        run = run_def(tmp_path)
        assert run.returncode == 0, run.output
        assert run.report["objectives"] == CSV_OBJECTIVES
        assert run.report["turret_positions"] == {"4x": 1, "10x": 2, "20x": 3, "40x": 4}
        assert run.report["xeryon_pos_1"] == ["4x", "10x"]
        assert run.report["xeryon_pos_2"] == ["20x", "40x", "60x"]
        assert run.report["default_objective"] == "20x"

    def test_shipped_xeryon_ini(self, tmp_path):
        run = run_def(tmp_path, ini=XERYON_INI)
        assert run.returncode == 0, run.output
        assert run.report["use_xeryon"] is True
        assert run.report["objectives"] == CSV_OBJECTIVES
        # This shipped ini's lists use single quotes, which isn't valid JSON, so
        # conf_attribute_reader falls through to returning the raw config string.
        assert run.report["xeryon_pos_1"] == "['10x', '20x', '25x', '60x']"
        assert run.report["xeryon_pos_2"] == "['2x', '4x']"

    @pytest.mark.parametrize(
        "cache_text, expected",
        [
            (None, "20x"),  # no cache file
            ("{not json", "20x"),  # malformed cache
            (_cache("7x"), "20x"),  # names an objective that is not in OBJECTIVES
            (_cache("10x"), "10x"),  # valid
        ],
    )
    def test_default_objective_cache_cases(self, tmp_path, cache_text, expected):
        run = run_def(tmp_path, cache_text=cache_text)
        assert run.returncode == 0, run.output
        assert run.report["default_objective"] == expected


class TestImportOrder:
    @pytest.mark.parametrize(
        "pre_imports",
        [(), ("squid.config",), ("control.objective_turret_controller",), ("control._def",)],
    )
    def test_startup_succeeds_whatever_is_imported_first(self, tmp_path, pre_imports):
        if (SOFTWARE_DIR / "machine_configs" / "objectives.yaml").exists():
            pytest.skip("a real machine_configs/objectives.yaml exists; import-order tests need the default path empty")
        run = run_def(tmp_path, pre_imports=pre_imports, patch_yaml_path=False)
        assert run.returncode == 0, run.output
        assert run.report["objectives"] == CSV_OBJECTIVES


TURRET_YAML = """
version: 1
changer: {kind: nimotion_turret}
objectives:
  - {name: 4x, magnification: 4, na: 0.13, tube_lens_f_mm: 180, slot: 3}
  - {name: 10x, magnification: 10, na: 0.3, tube_lens_f_mm: 180, slot: 1, serial: SN10}
  - {name: 20x, magnification: 20, na: 0.8, tube_lens_f_mm: 180, slot: 2}
"""
NONE_YAML_WITHOUT_20X = """
version: 1
changer: {kind: none}
objectives:
  - {name: 10x, magnification: 10, na: 0.3, tube_lens_f_mm: 180}
  - {name: 4x, magnification: 4, na: 0.13, tube_lens_f_mm: 180}
"""
XERYON_YAML = """
version: 1
changer: {kind: xeryon}
objectives:
  - {name: 20x, magnification: 20, na: 0.8, tube_lens_f_mm: 180, slot: 1}
  - {name: 4x, magnification: 4, na: 0.13, tube_lens_f_mm: 180, slot: 2}
"""
TURRET_FLAGS = {"use_objective_turret": "True", "objective_turret_positions": '{"4x": 1, "10x": 2}'}


class TestWithYaml:
    def test_turret_yaml_wins_over_conflicting_ini_maps(self, tmp_path):
        run = run_def(tmp_path, ini_general_overrides=TURRET_FLAGS, objectives_yaml=TURRET_YAML)
        assert run.returncode == 0, run.output
        assert sorted(run.report["objectives"]) == ["10x", "20x", "4x"]
        assert run.report["turret_positions"] == {"4x": 3, "10x": 1, "20x": 2}
        assert "ignored" in run.output

    def test_xeryon_yaml_wins_over_the_shipped_lists(self, tmp_path):
        run = run_def(tmp_path, ini=XERYON_INI, objectives_yaml=XERYON_YAML)
        assert run.returncode == 0, run.output
        assert run.report["xeryon_pos_1"] == ["20x"]
        assert run.report["xeryon_pos_2"] == ["4x"]

    @pytest.mark.parametrize(
        "cache_text, expected",
        [(None, "4x"), ("{not json", "4x"), (_cache("20x"), "4x"), (_cache("10x"), "10x")],
    )
    def test_default_objective_is_lowest_mounted_when_cache_is_unusable(self, tmp_path, cache_text, expected):
        run = run_def(tmp_path, objectives_yaml=NONE_YAML_WITHOUT_20X, cache_text=cache_text)
        assert run.returncode == 0, run.output
        assert run.report["default_objective"] == expected

    @pytest.mark.parametrize(
        "yaml_text, overrides, field",
        [
            ("   \n", None, "(file)"),
            (TURRET_YAML.replace("slot: 1", "slot: 3"), TURRET_FLAGS, "slot"),  # 10x now shares slot 3 with 4x
            (TURRET_YAML, None, "changer.kind"),  # the pristine ini selects no changer
        ],
    )
    def test_invalid_yaml_stops_startup(self, tmp_path, yaml_text, overrides, field):
        run = run_def(tmp_path, objectives_yaml=yaml_text, ini_general_overrides=overrides)
        # returncode == 1 and no traceback distinguish the clean log.error(...) + sys.exit(1)
        # path from an uncaught ObjectivesConfigError (which would also give a nonzero
        # returncode and mention the path/field/"delete" text, via the exception message).
        assert run.returncode == 1
        assert "Traceback" not in run.output
        assert " - ERROR - " in run.output
        assert run.report is None
        assert "objectives.yaml" in run.output and field in run.output and "delete" in run.output

    def test_lookups_with_yaml(self, tmp_path):
        run = run_def(
            tmp_path,
            ini_general_overrides=TURRET_FLAGS,
            objectives_yaml=TURRET_YAML,
            extra_report_expr='[list(d.get_mounting("10x")), d.get_declared("10x")]',
        )
        assert run.returncode == 0, run.output
        mounting, declared = run.report["extra"]
        assert mounting == ["nimotion_turret", 1]
        assert declared == {"magnification": 10.0, "na": 0.3, "tube_lens_f_mm": 180.0, "model": "", "serial": "SN10"}


class TestLookupsWithoutYaml:
    def test_pristine_ini(self, tmp_path):
        run = run_def(tmp_path, extra_report_expr='[list(d.get_mounting("20x")), d.get_declared("20x")["serial"]]')
        assert run.returncode == 0, run.output
        assert run.report["extra"] == [["none", None], ""]

    def test_xeryon_ini_name_in_neither_list(self, tmp_path):
        run = run_def(
            tmp_path,
            ini=XERYON_INI,
            extra_report_expr='[list(d.get_mounting("40x")), list(d.get_mounting("4x"))]',
        )
        assert run.returncode == 0, run.output
        assert run.report["extra"] == [["xeryon", None], ["xeryon", 2]]

    def test_unknown_name_raises_key_error(self, tmp_path):
        expr = '(lambda: (d.get_mounting("7x")))()'
        run = run_def(tmp_path, extra_report_expr=expr)
        assert run.returncode != 0 and "KeyError" in run.output


class TestNonDictCache:
    @pytest.mark.parametrize("objectives_yaml", [None, NONE_YAML_WITHOUT_20X])
    def test_non_dict_cache_behaves_as_today(self, tmp_path, objectives_yaml):
        """Today a JSON cache that is not an object crashes at cached_settings.get. Out of scope; pinned."""
        run = run_def(tmp_path, cache_text="[]", objectives_yaml=objectives_yaml)
        assert run.returncode != 0 and "AttributeError" in run.output
