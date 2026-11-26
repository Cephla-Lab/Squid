"""The setup scripts for Ubuntu 22.04, Ubuntu 24.04 and Windows install the same Python packages."""

import re
import shlex
from pathlib import Path

import pytest

SOFTWARE = Path(__file__).resolve().parents[1]

_SH_PIP = re.compile(r"^\s*pip3 install (.+?)(\s*\|\|.*)?$")
_PS_PIP = re.compile(r"^\s*(?:try \{ )?Invoke-Pip install (.+?)(?: \} catch.*)?$")


def _pip_installs(name: str, pattern: re.Pattern) -> list:
    """Each `pip install` in the script, as its argument list, in order (pip's own upgrade left out)."""
    installs = []
    for line in (SOFTWARE / name).read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if match:
            args = shlex.split(match.group(1))
            if args != ["--upgrade", "pip"]:
                installs.append(args)
    return installs


def test_the_linux_script_has_pip_installs_to_compare():
    installs = _pip_installs("setup_22.04.sh", _SH_PIP)
    assert len(installs) >= 5
    assert any("napari[pyqt6]==0.7.1" in args for args in installs)


@pytest.mark.parametrize(
    "name, pattern",
    [("setup_24.04.sh", _SH_PIP), ("setup_windows.ps1", _PS_PIP)],
)
def test_installs_the_same_packages_as_ubuntu_22_04(name, pattern):
    assert _pip_installs(name, pattern) == _pip_installs("setup_22.04.sh", _SH_PIP)
