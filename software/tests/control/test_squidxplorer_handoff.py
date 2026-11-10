"""Low RAM no longer blocks a run: the mosaic view is traded away for that run and the finished
run is handed to SquidXplorer. Covers the launcher's command construction, the start-time decision on
both multipoint tabs, and the GUI's enter / restore bookkeeping."""

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import control._def
import control.squidxplorer_launcher as launcher
import control.widgets
from control.gui_hcs import HighContentScreeningGui

# ---- launcher ----


@pytest.fixture
def no_squidxplorer_config(monkeypatch):
    monkeypatch.setattr(control._def, "SQUIDXPLORER_PYTHON", "")
    monkeypatch.setattr(control._def, "SQUIDXPLORER_GUI_LOCK_DIR", "")
    monkeypatch.setattr(launcher.shutil, "which", lambda name: None)


def test_configured_python_runs_the_viewer_module_with_the_folder_as_one_argument(no_squidxplorer_config, monkeypatch):
    monkeypatch.setattr(control._def, "SQUIDXPLORER_PYTHON", f"  {sys.executable} ")
    run_dir = "/data/my runs/plate 1_2026-01-01"

    assert launcher.resolve_squidxplorer_command(run_dir) == [sys.executable, "-m", "squidmip._viewer", run_dir]


def test_falls_back_to_the_script_on_path(no_squidxplorer_config, monkeypatch):
    monkeypatch.setattr(launcher.shutil, "which", lambda name: f"/usr/local/bin/{name}")

    assert launcher.resolve_squidxplorer_command("/data/run") == ["/usr/local/bin/squidmip-view", "/data/run"]


def test_a_configured_python_that_is_not_a_file_falls_back_to_path(no_squidxplorer_config, monkeypatch, tmp_path):
    monkeypatch.setattr(control._def, "SQUIDXPLORER_PYTHON", str(tmp_path / "no_such_python"))
    assert launcher.resolve_squidxplorer_command("/data/run") is None

    monkeypatch.setattr(launcher.shutil, "which", lambda name: "/opt/bin/squidmip-view")
    assert launcher.resolve_squidxplorer_command("/data/run") == ["/opt/bin/squidmip-view", "/data/run"]


def test_nothing_installed_resolves_to_none_and_launch_raises(no_squidxplorer_config):
    assert launcher.resolve_squidxplorer_command("/data/run") is None
    with pytest.raises(FileNotFoundError):
        launcher.launch_squidxplorer("/data/run")


def test_lock_dir_is_passed_to_the_viewer_through_its_environment(no_squidxplorer_config, monkeypatch):
    assert launcher.squidxplorer_environment() is None  # inherit ours unchanged

    monkeypatch.setattr(control._def, "SQUIDXPLORER_GUI_LOCK_DIR", " /tmp/squid_handoff_lock ")
    env = launcher.squidxplorer_environment()
    assert env["SQUIDMIP_GUI_LOCK_DIR"] == "/tmp/squid_handoff_lock"
    assert env["PATH"] == os.environ["PATH"]


def test_launch_starts_the_resolved_command_without_a_shell(no_squidxplorer_config, monkeypatch):
    monkeypatch.setattr(launcher.shutil, "which", lambda name: "/opt/bin/squidmip-view")
    monkeypatch.setattr(control._def, "SQUIDXPLORER_GUI_LOCK_DIR", "/tmp/squid_handoff_lock")
    calls = []

    def fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(pid=1)

    monkeypatch.setattr(launcher.subprocess, "Popen", fake_popen)

    process, stderr_path = launcher.launch_squidxplorer("/data/my runs/run 1")
    try:
        command, kwargs = calls[0]
        assert command == ["/opt/bin/squidmip-view", "/data/my runs/run 1"]
        assert kwargs["shell"] is False
        assert kwargs["env"]["SQUIDMIP_GUI_LOCK_DIR"] == "/tmp/squid_handoff_lock"
        assert os.path.isfile(stderr_path)
        assert launcher.read_launch_error(stderr_path) == ""
    finally:
        os.unlink(stderr_path)


def test_a_failed_start_leaves_no_log_file_behind(no_squidxplorer_config, monkeypatch):
    monkeypatch.setattr(launcher.shutil, "which", lambda name: "/opt/bin/squidmip-view")
    created = []
    real_mkstemp = launcher.tempfile.mkstemp

    def recording_mkstemp(**kwargs):
        fd, path = real_mkstemp(**kwargs)
        created.append(path)
        return fd, path

    def failing_popen(command, **kwargs):
        raise OSError("cannot execute")

    monkeypatch.setattr(launcher.tempfile, "mkstemp", recording_mkstemp)
    monkeypatch.setattr(launcher.subprocess, "Popen", failing_popen)

    with pytest.raises(OSError):
        launcher.launch_squidxplorer("/data/run")
    assert created and not os.path.exists(created[0])


def test_read_launch_error_never_raises(tmp_path):
    assert launcher.read_launch_error(str(tmp_path / "gone.log")) == ""
    log = tmp_path / "err.log"
    log.write_text("x" * 50 + "GuiAlreadyOpen\n")
    assert launcher.read_launch_error(str(log), max_chars=14) == "GuiAlreadyOpen"


# ---- start of a run on the two multipoint tabs ----


def _tab_fake():
    fake = MagicMock()
    fake.base_path_is_set = True
    fake.performance_mode = False
    fake.location_list = [object()]  # Flexible: a location is already in the list
    fake.multipointController.acquisition_in_progress.return_value = False
    fake.multipointController.Nt = 1
    fake.multipointController.deltat = 0
    fake.configure_controller_from_ui.return_value = None
    fake.checkbox_skipSaving.isChecked.return_value = True  # no disk space check
    fake._probe_note = ""
    return fake


@pytest.mark.parametrize(
    "widget_class", [control.widgets.FlexibleMultiPointWidget, control.widgets.WellplateMultiPointWidget]
)
@pytest.mark.parametrize("interactive", [True, False])
def test_low_ram_trades_the_mosaic_away_instead_of_refusing_the_run(widget_class, interactive, monkeypatch):
    dialogs = []
    monkeypatch.setattr(control.widgets, "mosaic_ram_exceeds_available", lambda *a, **kw: True)
    monkeypatch.setattr(control.widgets, "resource_allocation_dialog", lambda parent=None: dialogs.append(parent))
    fake = _tab_fake()

    widget_class.toggle_acquisition(fake, True, interactive=interactive)

    # An unattended start never opens a dialog, and still disables the mosaic.
    assert len(dialogs) == (1 if interactive else 0)
    fake.signal_request_mosaic_disable.emit.assert_called_once_with(interactive)
    fake.multipointController.run_acquisition.assert_called_once()
    fake._refuse_start.assert_not_called()


@pytest.mark.parametrize(
    "widget_class", [control.widgets.FlexibleMultiPointWidget, control.widgets.WellplateMultiPointWidget]
)
def test_enough_ram_starts_the_run_with_the_mosaic(widget_class, monkeypatch):
    monkeypatch.setattr(control.widgets, "mosaic_ram_exceeds_available", lambda *a, **kw: False)
    monkeypatch.setattr(
        control.widgets, "resource_allocation_dialog", lambda parent=None: pytest.fail("no dialog expected")
    )
    fake = _tab_fake()

    widget_class.toggle_acquisition(fake, True)

    fake.signal_request_mosaic_disable.emit.assert_not_called()
    fake.multipointController.run_acquisition.assert_called_once()


# ---- GUI bookkeeping around the run ----


def _gui_fake(performance_mode=False, last_end_reason="completed", run_dir=None):
    fake = SimpleNamespace(
        performance_mode=performance_mode,
        _mosaic_disabled_for_run=False,
        _performance_mode_forced=False,
        _resource_limited_run_interactive=True,
        _squidxplorer_prompt_suppressed=False,
        _last_acquisition_save_target=run_dir,
        multipointController=SimpleNamespace(last_end_reason=last_end_reason),
        log=MagicMock(),
        offered=[],
        mode_changes=[],
    )

    def set_performance_mode(enabled):
        fake.mode_changes.append(enabled)
        fake.performance_mode = enabled

    fake.setPerformanceMode = set_performance_mode
    fake._offer_squidxplorer_after_run = lambda run_dir, completed: fake.offered.append((run_dir, completed))
    return fake


@pytest.fixture
def immediate_single_shot(monkeypatch):
    monkeypatch.setattr(control.gui_hcs.QTimer, "singleShot", staticmethod(lambda ms, fn: fn()))


def test_forced_performance_mode_is_restored_and_the_run_is_offered(immediate_single_shot):
    fake = _gui_fake(run_dir="/data/run")

    HighContentScreeningGui.enterPerformanceModeForRun(fake)
    assert fake.performance_mode is True and fake._performance_mode_forced is True

    HighContentScreeningGui._on_resource_limited_run_finished(fake)
    assert fake.mode_changes == [True, False]
    assert fake.offered == [("/data/run", True)]
    assert fake._mosaic_disabled_for_run is False


def test_performance_mode_the_operator_turned_on_is_left_on(immediate_single_shot):
    fake = _gui_fake(performance_mode=True)

    HighContentScreeningGui.enterPerformanceModeForRun(fake)
    HighContentScreeningGui._on_resource_limited_run_finished(fake)

    assert fake.performance_mode is True
    assert fake.mode_changes == [True]  # only the (no-op) request to enter it


def test_unattended_run_restores_performance_mode_but_offers_nothing(immediate_single_shot):
    fake = _gui_fake(run_dir="/data/run")

    HighContentScreeningGui.enterPerformanceModeForRun(fake, False)
    HighContentScreeningGui._on_resource_limited_run_finished(fake)

    assert fake.performance_mode is False
    assert fake.offered == []
    # The flag does not leak into the next (interactive) resource-limited run.
    assert fake._resource_limited_run_interactive is True


def test_performance_mode_is_restored_before_the_finished_signal_returns(monkeypatch):
    # An unattended workflow starts its next acquisition right after acquisition_finished; its RAM
    # check must not see the mode this run forced. Only the prompt is deferred.
    deferred = []
    monkeypatch.setattr(control.gui_hcs.QTimer, "singleShot", staticmethod(lambda ms, fn: deferred.append(fn)))
    fake = _gui_fake(run_dir="/data/run")

    HighContentScreeningGui.enterPerformanceModeForRun(fake)
    HighContentScreeningGui._on_resource_limited_run_finished(fake)

    assert fake.performance_mode is False
    assert fake.offered == [] and len(deferred) == 1


@pytest.mark.parametrize("end_reason", ["user_abort", "error", "failed_to_start", None])
def test_a_run_that_did_not_complete_is_not_reported_as_complete(immediate_single_shot, end_reason):
    fake = _gui_fake(last_end_reason=end_reason, run_dir="/data/run")

    HighContentScreeningGui.enterPerformanceModeForRun(fake)
    HighContentScreeningGui._on_resource_limited_run_finished(fake)

    assert fake.offered == [("/data/run", False)]
    assert fake.performance_mode is False


def test_an_ordinary_run_finishing_touches_nothing(immediate_single_shot):
    fake = _gui_fake()
    HighContentScreeningGui._on_resource_limited_run_finished(fake)
    assert fake.mode_changes == [] and fake.offered == []


def test_no_prompt_for_an_incomplete_run_a_suppressed_prompt_or_a_missing_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(control.gui_hcs, "QMessageBox", MagicMock(side_effect=AssertionError("no dialog expected")))
    fake = SimpleNamespace(_squidxplorer_prompt_suppressed=False, log=MagicMock(), _launch_squidxplorer=MagicMock())

    HighContentScreeningGui._offer_squidxplorer_after_run(fake, str(tmp_path), False)
    HighContentScreeningGui._offer_squidxplorer_after_run(fake, str(tmp_path / "missing"), True)
    HighContentScreeningGui._offer_squidxplorer_after_run(fake, None, True)
    fake._squidxplorer_prompt_suppressed = True
    HighContentScreeningGui._offer_squidxplorer_after_run(fake, str(tmp_path), True)

    fake._launch_squidxplorer.assert_not_called()
