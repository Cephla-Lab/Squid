"""The startup reporter state machine (control/startup_progress.py). No Qt involved."""

import pytest

import control._def
import squid.config
from control.startup_progress import (
    NULL_REPORTER,
    StartupAborted,
    StartupCoreDeviceError,
    StartupFailed,
    StartupReporter,
    StepState,
    declare_expected_steps,
    wait_until,
)


class RecordingReporter(StartupReporter):
    """Keeps every state a row went through, in order."""

    def __init__(self):
        super().__init__()
        self.history = []

    def _on_changed(self, result):
        self.history.append((result.key, result.state))


def states_of(reporter, key):
    return [state for k, state in reporter.history if k == key]


class TestStep:
    def test_a_clean_step_goes_pending_connecting_ready(self):
        reporter = RecordingReporter()
        reporter.declare("camera", "Main camera", core=True)
        with reporter.step("camera", core=True):
            assert reporter.get("camera").state == StepState.CONNECTING
        assert states_of(reporter, "camera") == [StepState.PENDING, StepState.CONNECTING, StepState.READY]
        assert reporter.get("camera").finished and not reporter.get("camera").failed
        assert reporter.failures == []

    def test_an_undeclared_step_gets_a_row(self):
        reporter = StartupReporter()
        with reporter.step("nl5", label="NL5"):
            pass
        assert [(r.key, r.label, r.state) for r in reporter.results] == [("nl5", "NL5", StepState.READY)]

    def test_a_warning_set_by_the_body_survives_the_step(self):
        reporter = StartupReporter()
        with reporter.step("prep_filter_wheel") as progress:
            progress.warning("homing failed")
        row = reporter.get("prep_filter_wheel")
        assert (row.state, row.detail) == (StepState.WARNING, "homing failed")
        assert reporter.failures == []  # a warning is not a failure: startup carries on

    def test_detail_is_kept_but_warmup_chatter_is_dropped_on_ready(self):
        reporter = StartupReporter()
        with reporter.step("a") as progress:
            progress.detail("started")
        with reporter.step("b") as progress:
            progress.on_warmup_retry("ERR=System in Warmup State", 12.0)
            assert reporter.get("b").state == StepState.WARMING_UP
            assert reporter.get("b").detail == "ERR=System in Warmup State (12s)"
        assert (reporter.get("a").state, reporter.get("a").detail) == (StepState.READY, "started")
        assert (reporter.get("b").state, reporter.get("b").detail) == (StepState.READY, "")

    def test_a_failing_independent_step_is_recorded_and_the_sweep_continues(self):
        reporter = StartupReporter()
        with reporter.step("cellx", label="CellX"):
            raise RuntimeError("no port")
        with reporter.step("camera", core=True):
            pass
        row = reporter.get("cellx")
        assert row.state == StepState.FAILED
        assert row.detail == "RuntimeError: no port"
        assert isinstance(row.exception, RuntimeError)
        assert [r.key for r in reporter.failures] == ["cellx"]
        assert reporter.get("camera").state == StepState.READY

    def test_a_failing_core_step_raises_with_the_cause(self):
        reporter = StartupReporter()
        cause = ValueError("bad firmware")
        with pytest.raises(StartupCoreDeviceError) as excinfo:
            with reporter.step("microcontroller", core=True, label="Microcontroller"):
                raise cause
        assert excinfo.value.cause is cause
        assert excinfo.value.step_key == "microcontroller"
        assert "Microcontroller failed: bad firmware" in str(excinfo.value)
        assert reporter.get("microcontroller").state == StepState.FAILED

    def test_core_declared_up_front_wins_over_the_call_site(self):
        reporter = StartupReporter()
        reporter.declare("filter_wheel", "Emission filter wheel", core=True)
        with pytest.raises(StartupCoreDeviceError):
            with reporter.step("filter_wheel"):
                raise RuntimeError("x")

    def test_not_found_set_by_the_body_is_not_overwritten_by_the_failure(self):
        reporter = StartupReporter()
        with reporter.step("spinning_disk") as progress:
            progress.not_found("no serial port")
            raise RuntimeError("gone")
        row = reporter.get("spinning_disk")
        assert (row.state, row.detail) == (StepState.NOT_FOUND, "no serial port")
        assert row.failed

    def test_startup_failed_carries_the_checklist(self):
        reporter = StartupReporter()
        with reporter.step("a", label="Device A"):
            raise RuntimeError("x")
        with reporter.step("b", label="Device B"):
            pass
        error = StartupFailed(reporter.results)
        assert [r.key for r in error.failures] == ["a"]
        assert str(error) == "Initialization failed: Device A"


class TestAbort:
    def test_abort_stops_the_next_step_before_it_runs(self):
        reporter = StartupReporter()
        reporter.declare("camera", "Main camera")
        with reporter.step("stage_xy"):
            reporter.request_abort()  # e.g. clicked while a blocking step runs
        ran = []
        with pytest.raises(StartupAborted) as excinfo:
            with reporter.step("camera"):
                ran.append(True)
        assert ran == []
        assert excinfo.value.step_key == "camera"
        assert "Main camera" in str(excinfo.value)
        assert reporter.get("camera").state == StepState.PENDING

    def test_an_abort_raised_inside_a_step_passes_through_even_when_not_core(self):
        reporter = StartupReporter()
        with pytest.raises(StartupAborted):
            with reporter.step("light_source"):
                reporter.request_abort()
                reporter.raise_if_aborted()
        assert not reporter.get("light_source").failed

    def test_cancel_fn_follows_the_abort_flag(self):
        reporter = StartupReporter()
        cancel = reporter.cancel_fn()
        assert cancel() is False
        reporter.request_abort()
        assert cancel() is True

    def test_non_interruptible_is_recorded_on_the_row(self):
        reporter = StartupReporter()
        with reporter.step("prep_home_xyz", core=True, interruptible=False):
            pass
        with reporter.step("camera"):
            pass
        assert reporter.get("prep_home_xyz").interruptible is False
        assert reporter.get("camera").interruptible is True


class TestOpenedRegistry:
    def test_close_all_runs_newest_first_and_survives_a_failing_closer(self):
        reporter = StartupReporter()
        closed = []

        def broken():
            raise OSError("port gone")

        reporter.register_opened("microcontroller", lambda: closed.append("microcontroller"))
        reporter.register_opened("broken", broken)
        reporter.register_opened("camera", lambda: closed.append("camera"))

        errors = reporter.close_all()

        assert closed == ["camera", "microcontroller"]
        assert errors == ["broken: port gone"]
        assert reporter.opened == []
        assert reporter.close_all() == []  # nothing is closed twice


class TestNullReporter:
    def test_exceptions_pass_through_unchanged_core_or_not(self):
        for core in (False, True):
            with pytest.raises(RuntimeError, match="no port"):
                with NULL_REPORTER.step("cellx", core=core):
                    raise RuntimeError("no port")

    def test_it_keeps_no_state(self):
        with NULL_REPORTER.step("a") as progress:
            progress.warning("w")
            progress.detail("d")
        NULL_REPORTER.declare("b", "B")
        NULL_REPORTER.register_opened("thing", lambda: None)
        NULL_REPORTER.request_abort()
        assert NULL_REPORTER.results == []
        assert NULL_REPORTER.opened == []
        assert NULL_REPORTER.is_abort_requested() is False
        with NULL_REPORTER.step("c"):  # an abort request does not poison the shared instance
            pass


class TestWaitUntil:
    def test_true_once_the_predicate_holds(self):
        answers = iter([False, False, True])
        assert wait_until(lambda: next(answers), timeout_s=5, sleep_fn=lambda s: None) is True

    def test_false_on_cancel_and_on_timeout(self):
        assert wait_until(lambda: False, timeout_s=5, cancel_fn=lambda: True, sleep_fn=lambda s: None) is False
        assert wait_until(lambda: False, timeout_s=0.0, sleep_fn=lambda s: None) is False


class TestDeclareExpectedSteps:
    @pytest.fixture
    def bare_machine(self, monkeypatch):
        """A machine with none of the optional devices."""
        for name in (
            "USE_PRIOR_STAGE",
            "USE_PI_FOCUS_STAGE",
            "USE_Z_LIFT_STAGE",
            "ENABLE_SPINNING_DISK_CONFOCAL",
            "ENABLE_NL5",
            "ENABLE_CELLX",
            "USE_XERYON",
            "USE_OBJECTIVE_TURRET",
            "SUPPORT_LASER_AUTOFOCUS",
            "RUN_FLUIDICS",
            "HAS_OBJECTIVE_PIEZO",
            "SUPPORT_SCIMICROSCOPY_LED_ARRAY",
            "USE_LDI_SERIAL_CONTROL",
            "USE_CELESTA_ETHERNET_CONTROL",
            "USE_ANDOR_LASER_CONTROL",
            "USE_VORTRAN_LASER_CONTROL",
        ):
            monkeypatch.setattr(control._def, name, False)
        monkeypatch.setattr(control._def, "LASER_ENGINE", None)
        monkeypatch.setattr(squid.config, "get_filter_wheel_config", lambda: None)
        return monkeypatch

    @staticmethod
    def declared(**kwargs):
        reporter = StartupReporter()
        declare_expected_steps(reporter, **kwargs)
        return reporter

    def test_a_bare_machine_declares_only_what_every_machine_has(self, bare_machine):
        reporter = self.declared()
        assert [r.key for r in reporter.results] == [
            "imports",
            "microcontroller",
            "stage_xy",
            "camera",
            "illumination_controller",
            "config_profiles",
            "prep_watchdog",
            "prep_camera",
            "prep_home_xyz",
            "gui_objects",
            "gui_hardware",
            "gui_widgets",
            "gui_layout",
            "gui_connections",
            "gui_position",
        ]
        assert all(r.state == StepState.PENDING for r in reporter.results)
        assert reporter.get("microcontroller").core and reporter.get("camera").core
        assert not reporter.get("gui_position").core
        assert reporter.get("prep_home_xyz").interruptible is False

    def test_skip_init_drops_the_homing_rows(self, bare_machine):
        bare_machine.setattr(control._def, "HAS_OBJECTIVE_PIEZO", True)
        keys = [r.key for r in self.declared(skip_init=True).results]
        assert "prep_home_xyz" not in keys and "prep_piezo" not in keys
        assert "piezo" in keys
        assert "prep_piezo" in [r.key for r in self.declared().results]

    def test_devices_added_on_this_branch_get_rows(self, bare_machine):
        bare_machine.setattr(control._def, "USE_PI_FOCUS_STAGE", True)
        bare_machine.setattr(control._def, "USE_Z_LIFT_STAGE", True)
        bare_machine.setattr(control._def, "Z_LIFT_FOCUS_WHEEL_DRIVES_PI", True)
        bare_machine.setattr(control._def, "RUN_FLUIDICS", True)
        bare_machine.setattr(control._def, "LASER_ENGINE", "v2")
        bare_machine.setattr(control._def, "USE_OBJECTIVE_TURRET", True)
        bare_machine.setattr(control._def, "USE_VORTRAN_LASER_CONTROL", True)
        reporter = self.declared()
        keys = [r.key for r in reporter.results]
        for key in (
            "stage_z",
            "fluidics",
            "laser_engine",
            "prep_laser_engine",
            "prep_focus_wheel",
            "objective_changer",
            "prep_objective",
            "light_source",
        ):
            assert key in keys
        assert reporter.get("stage_z").core and reporter.get("stage_z").interruptible is False
        assert reporter.get("light_source").label == "Vortran VersaLase"
        # Order follows the build: stage, addons, camera, light source, controller, then the prepare steps.
        assert keys.index("stage_z") < keys.index("fluidics") < keys.index("camera") < keys.index("light_source")
        assert keys.index("light_source") < keys.index("illumination_controller") < keys.index("prep_laser_engine")
        assert keys.index("prep_home_xyz") < keys.index("prep_focus_wheel") < keys.index("prep_objective")

    def test_simulation_has_no_external_light_source_row_and_everything_is_interruptible(self, bare_machine):
        bare_machine.setattr(control._def, "USE_LDI_SERIAL_CONTROL", True)
        assert self.declared().get("light_source").label == "LDI laser engine"
        simulated = self.declared(simulated=True)
        assert simulated.get("light_source") is None
        assert all(r.interruptible for r in simulated.results)
