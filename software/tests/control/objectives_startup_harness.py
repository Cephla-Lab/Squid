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
) -> DefRun:
    work = Path(tmp_path) / "work"
    (work / "cache").mkdir(parents=True)
    shutil.copytree(SOFTWARE_DIR / "objective_and_sample_formats", work / "objective_and_sample_formats")
    _write_ini(Path(ini), work / "configuration_test.ini", ini_general_overrides)
    if cache_text is not None:
        (work / "cache" / "objective_and_sample_format.txt").write_text(cache_text)

    lines = [f"import {module}" for module in pre_imports]
    lines.append(
        textwrap.dedent(
            f"""
            import json
            import control._def as d
            print({_MARKER!r} + json.dumps({{
                "objectives": d.OBJECTIVES,
                "default_objective": d.DEFAULT_OBJECTIVE,
                "turret_positions": d.OBJECTIVE_TURRET_POSITIONS,
                "xeryon_pos_1": list(d.XERYON_OBJECTIVE_SWITCHER_POS_1),
                "xeryon_pos_2": list(d.XERYON_OBJECTIVE_SWITCHER_POS_2),
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
