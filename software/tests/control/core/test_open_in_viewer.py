"""The Qt-free side of the Open in Viewer button: the enable rule, the launch preflight and the launch itself."""

import os
import sys

import pytest

from control.core.open_in_viewer import (
    SUBMODULE_HINT,
    VIEWER_PACKAGES,
    RunSnapshot,
    check_viewer,
    finished_run_dir,
    launch_viewer,
    software_dir,
    viewer_launch_command,
)


@pytest.mark.parametrize("reason", ["completed", "completed_with_errors", "user_abort", "error"])
def test_finished_run_dir_for_each_end_reason(reason, tmp_path):
    (tmp_path / "run").mkdir()
    assert finished_run_dir(RunSnapshot(str(tmp_path / "run"), False), reason) == str(tmp_path / "run")


def test_finished_run_dir_failed_to_start_is_none(tmp_path):
    (tmp_path / "run").mkdir()
    assert finished_run_dir(RunSnapshot(str(tmp_path / "run"), False), "failed_to_start") is None


def test_finished_run_dir_skip_saving_is_none(tmp_path):
    (tmp_path / "run").mkdir()
    assert finished_run_dir(RunSnapshot(str(tmp_path / "run"), True), "completed") is None


def test_finished_run_dir_missing_folder_is_none(tmp_path):
    assert finished_run_dir(RunSnapshot(str(tmp_path / "run"), False), "completed") is None


def test_finished_run_dir_without_a_snapshot_is_none(tmp_path):
    assert finished_run_dir(None, "completed") is None
    assert finished_run_dir(RunSnapshot(None, False), "completed") is None


def test_check_viewer_ok(tmp_path):
    p = check_viewer(str(tmp_path), find_spec=lambda n: object(), isfile=lambda f: True)
    assert p.ok and p.problem() == ""
    assert p.entry_point == str(tmp_path / "control" / "ndviewer_light" / "ndviewer_light" / "__main__.py")


def test_check_viewer_reports_missing_packages(tmp_path):
    p = check_viewer(
        str(tmp_path), find_spec=lambda n: None if n in ("vispy", "superqt") else object(), isfile=lambda f: True
    )
    assert p.missing_packages == ("vispy", "superqt")
    assert "pip install vispy superqt" in p.problem()


def test_check_viewer_reports_missing_entry_point(tmp_path):
    p = check_viewer(str(tmp_path), find_spec=lambda n: object(), isfile=lambda f: False)
    assert not p.ok
    assert SUBMODULE_HINT in p.problem() and p.entry_point in p.problem()


def test_check_viewer_never_imports_dotted_modules(tmp_path):
    seen = []
    check_viewer(str(tmp_path), find_spec=lambda n: seen.append(n) or object(), isfile=lambda f: True)
    assert seen == list(VIEWER_PACKAGES)
    assert all("." not in n for n in seen)


def test_viewer_launch_command():
    assert viewer_launch_command("/data/run") == [
        sys.executable,
        "-m",
        "control.ndviewer_light.ndviewer_light",
        "/data/run",
    ]


def test_launch_viewer_uses_popen_with_cwd_in_its_own_session(tmp_path):
    # start_new_session: the viewer must outlive Squid's terminal (SIGHUP goes to Squid's session)
    calls = []

    def popen(args, **kw):
        calls.append((args, kw))
        return "proc"

    assert launch_viewer("/data/run", str(tmp_path), popen=popen) == "proc"
    assert calls == [(viewer_launch_command("/data/run"), {"cwd": str(tmp_path), "start_new_session": True})]


def test_launch_viewer_does_not_wait(tmp_path):
    class NeverWaited:
        def wait(self, *a, **k):
            raise AssertionError("waited")

        def communicate(self, *a, **k):
            raise AssertionError("waited")

    launch_viewer("/data/run", str(tmp_path), popen=lambda *a, **k: NeverWaited())


def test_launch_viewer_propagates_popen_errors(tmp_path):
    def popen(*a, **k):
        raise OSError("no exec")

    with pytest.raises(OSError):
        launch_viewer("/data/run", str(tmp_path), popen=popen)


def test_software_dir_contains_the_control_package():
    assert os.path.isdir(os.path.join(software_dir(), "control"))


def test_check_viewer_pins_ndv_in_the_install_hint(tmp_path):
    # The bundled viewer needs ndv>=0.4,<0.5; an unpinned hint would install a version it cannot use
    p = check_viewer(str(tmp_path), find_spec=lambda n: None if n == "ndv" else object(), isfile=lambda f: True)
    assert 'pip install "ndv>=0.4,<0.5"' in p.problem()
