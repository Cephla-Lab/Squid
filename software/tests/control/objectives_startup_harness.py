"""Run control._def in a fresh interpreter, from a scratch working directory.

control._def reads the machine .ini, cache/ and objective_and_sample_formats/ from the
current working directory at import time, so startup behavior can only be tested in a
subprocess whose cwd we control.
"""

import configparser
import json
import os
import shutil
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

SOFTWARE_DIR = Path(__file__).resolve().parents[2]
PRISTINE_INI = SOFTWARE_DIR / "configurations" / "configuration_Squid+.ini"
XERYON_INI = SOFTWARE_DIR / "configurations" / "configuration_Squid+_Kinetix_LDI_XLight_Xeryon.ini"
_MARKER = "@@DEF_REPORT@@"


@dataclass
class DefRun:
    returncode: int
    report: Optional[dict]
    output: str


def _write_ini(src: Path, dst: Path, overrides: Optional[dict]) -> None:
    if not overrides:
        shutil.copyfile(src, dst)
        return
    cfp = configparser.ConfigParser(interpolation=None)
    cfp.read(src)
    for key, value in overrides.items():
        cfp.set("GENERAL", key.lower(), value)
    with open(dst, "w") as f:
        cfp.write(f)


def run_def(
    tmp_path,
    *,
    ini=PRISTINE_INI,
    ini_general_overrides=None,
    cache_text=None,
    pre_imports=(),
    extra_report_expr="None",
    objectives_yaml=None,
    patch_yaml_path=True,
) -> DefRun:
    work = Path(tmp_path) / "work"
    (work / "cache").mkdir(parents=True)
    shutil.copytree(SOFTWARE_DIR / "objective_and_sample_formats", work / "objective_and_sample_formats")
    _write_ini(Path(ini), work / "configuration_test.ini", ini_general_overrides)
    if cache_text is not None:
        (work / "cache" / "objective_and_sample_format.txt").write_text(cache_text)

    lines = []
    if patch_yaml_path:
        # Always point the loader somewhere controlled, so the test never sees a real
        # machine_configs/objectives.yaml on the developer's machine: the tmp YAML when
        # objectives_yaml is given, otherwise a non-existent tmp path.
        yaml_path = Path(tmp_path) / "objectives.yaml"
        if objectives_yaml is not None:
            yaml_path.write_text(objectives_yaml)
        lines = [
            "import control.objectives_config as _oc",
            "import pathlib",
            f"_oc.OBJECTIVES_YAML_PATH = pathlib.Path({str(yaml_path)!r})",
        ]
    lines += [f"import {module}" for module in pre_imports]
    lines.append(
        textwrap.dedent(
            f"""
            import json
            import control._def as d
            # Report list/tuple values as lists; pass anything else (e.g. a raw string,
            # if the .ini value wasn't valid JSON) through unchanged.
            _raw = lambda v: list(v) if isinstance(v, (list, tuple)) else v
            print({_MARKER!r} + json.dumps({{
                "objectives": d.OBJECTIVES,
                "default_objective": d.DEFAULT_OBJECTIVE,
                "turret_positions": d.OBJECTIVE_TURRET_POSITIONS,
                "xeryon_pos_1": _raw(d.XERYON_OBJECTIVE_SWITCHER_POS_1),
                "xeryon_pos_2": _raw(d.XERYON_OBJECTIVE_SWITCHER_POS_2),
                "use_xeryon": d.USE_XERYON,
                "use_turret": d.USE_OBJECTIVE_TURRET,
                "extra": {extra_report_expr},
            }}))
            """
        )
    )
    env = {**os.environ, "PYTHONPATH": str(SOFTWARE_DIR)}
    proc = subprocess.run(
        [sys.executable, "-c", "\n".join(lines)],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    report = None
    for line in proc.stdout.splitlines():
        if line.startswith(_MARKER):
            report = json.loads(line[len(_MARKER) :])
    return DefRun(proc.returncode, report, proc.stdout + proc.stderr)
