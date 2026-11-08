import os
import subprocess
import sys

import pytest

import control._def
from control.ndviewer_launcher import LauncherResolution, launch_ndviewer_light, resolve_ndviewer_light

PY = "python-for-tests"


def _resolve(preference="", *, which=lambda name: None, package=False, submodule="", python=PY):
    return resolve_ndviewer_light(
        preference,
        which=which,
        package_importable=lambda: package,
        submodule_main=submodule,
        python=python,
    )


def test_preference_executable_wins(tmp_path):
    exe = tmp_path / "ndviewer-light.exe"
    exe.write_text("")
    res = _resolve(str(exe), which=lambda name: "/usr/bin/ndviewer-light", package=True)
    assert res.command == [str(exe)]
    assert res.source == "preference"
    assert res.env is None and res.cwd is None


def test_preference_directory_runs_the_module_from_it(tmp_path):
    pkg = tmp_path / "ndviewer_light"
    pkg.mkdir()
    (pkg / "__main__.py").write_text("")
    res = _resolve(str(tmp_path))
    assert res.command == [PY, "-m", "ndviewer_light"]
    assert res.source == "preference"
    assert res.env["PYTHONPATH"].startswith(str(tmp_path))


def test_preference_command_line_is_split(tmp_path):
    res = _resolve("somewhere-not-a-file --flag")
    assert res.source == "preference"
    assert res.command[-1] == "--flag"


def test_path_command_then_package_then_submodule_then_none(tmp_path):
    main = tmp_path / "__main__.py"
    main.write_text("")

    res = _resolve(which=lambda name: "/bin/ndviewer-light" if name == "ndviewer-light" else None, package=True)
    assert (res.source, res.command) == ("path", ["/bin/ndviewer-light"])

    res = _resolve(package=True, submodule=str(main))
    assert (res.source, res.command) == ("package", [PY, "-m", "ndviewer_light"])

    res = _resolve(submodule=str(main))
    assert (res.source, res.command) == ("submodule", [PY, "-m", "control.ndviewer_light"])
    software = os.path.dirname(os.path.dirname(os.path.abspath(control._def.__file__)))
    assert res.cwd == software

    res = _resolve(submodule=str(tmp_path / "missing.py"))
    assert res.source == "none" and res.command is None
    assert "not installed" in res.reason


def test_missing_preference_falls_through_and_is_mentioned(tmp_path):
    res = _resolve(str(tmp_path / "nope"), package=True)
    assert res.source == "package"
    res = _resolve(str(tmp_path / "nope"))
    assert res.source == "none"
    assert "Preferences" in res.reason and "not found" in res.reason


def test_launch_appends_the_folder_and_detaches():
    calls = []

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return "proc"

    res = LauncherResolution(["viewer", "-x"], "path", "ok", env={"A": "1"}, cwd="/soft")
    assert launch_ndviewer_light(res, "/data/acq", popen=fake_popen) == "proc"
    args, kw = calls[0]
    assert args == ["viewer", "-x", "/data/acq"]
    assert kw["cwd"] == "/soft"
    assert kw["env"]["A"] == "1" and "PATH" in {k.upper() for k in kw["env"]}
    assert kw["close_fds"] is True
    assert kw["stdin"] == kw["stdout"] == kw["stderr"] == subprocess.DEVNULL
    if sys.platform == "win32":
        assert kw["creationflags"] & subprocess.DETACHED_PROCESS
        assert kw["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        assert kw["start_new_session"] is True

    calls.clear()
    launch_ndviewer_light(LauncherResolution(["v"], "path", "ok"), "/d", popen=fake_popen)
    assert calls[0][1]["env"] is None


def test_launch_without_a_command_raises_the_reason():
    res = LauncherResolution(None, "none", "not installed")
    with pytest.raises(RuntimeError, match="not installed"):
        launch_ndviewer_light(res, "/d", popen=lambda *a, **k: None)


def test_def_flags_default():
    assert control._def.NDVIEWER_LIGHT_COMMAND == "" and control._def.RECORD_SNAPS_IN_HISTORY is False
