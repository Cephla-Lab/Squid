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
        run = run_def(tmp_path, pre_imports=pre_imports)
        assert run.returncode == 0, run.output
        assert run.report["objectives"] == CSV_OBJECTIVES
