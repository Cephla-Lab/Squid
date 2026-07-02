import pytest

import control.microscope
from squid_service.faults import FaultCategory, FaultError
from squid_service.models import (
    AcquireRequest,
    AutofocusCorrectRequest,
    ExposureRequest,
    IntensityRequest,
    MoveRequest,
)
from squid_service.service import SquidCoreService
from squid_service.state import InstrumentState


@pytest.fixture(scope="module")
def sim_scope():
    scope = control.microscope.Microscope.build_from_global_config(True)
    yield scope
    scope.close()


@pytest.fixture()
def service(sim_scope):
    return SquidCoreService(microscope=sim_scope, simulation=True)


def test_status_shape(service):
    s = service.status()
    assert s["state"] == "INITIALIZED"
    assert s["current_job_id"] is None
    assert s["latest_fault"] is None
    assert "session_id" in s and "server_time" in s


def test_heartbeat(service):
    h = service.heartbeat()
    assert h["alive"] is True
    assert isinstance(h["monotonic_ns"], int)
    assert h["state"] == "INITIALIZED"


def test_capabilities(service):
    caps = service.capabilities()
    assert caps["simulation"] is True
    assert isinstance(caps["objectives"], list) and caps["objectives"]
    assert {"name", "magnification", "na"} <= set(caps["objectives"][0].keys())
    assert "x_range_mm" in caps["stage"]
    assert isinstance(caps["channels"], list)


def test_capabilities_includes_version_keys(service):
    # URS API-DESC-002: capabilities() must also surface the two version keys.
    caps = service.capabilities()
    assert caps["software_version"]
    assert caps["firmware_version"]


def test_version(service):
    v = service.version()
    assert v["api_version"] == "v1"
    assert v["software_version"]
    assert v["firmware_version"]


def test_move_absolute_and_position(service, sim_scope):
    limits = sim_scope.stage.get_config()
    x = limits.X_AXIS.MAX_POSITION / 2
    y = limits.Y_AXIS.MAX_POSITION / 2
    result = service.move(MoveRequest(mode="absolute", x=x, y=y))
    pos = service.get_position()
    assert pos["x_mm"] == pytest.approx(x, abs=0.01)
    assert result["position"]["y_mm"] == pytest.approx(y, abs=0.01)


def test_move_relative(service):
    before = service.get_position()
    service.move(MoveRequest(mode="relative", x=0.1))
    after = service.get_position()
    assert after["x_mm"] == pytest.approx(before["x_mm"] + 0.1, abs=0.01)


def test_move_out_of_limits_faults(service, sim_scope):
    max_x = sim_scope.stage.get_config().X_AXIS.MAX_POSITION
    with pytest.raises(FaultError) as exc:
        service.move(MoveRequest(mode="absolute", x=max_x + 10))
    assert exc.value.fault.category == FaultCategory.INVALID_PARAM


def test_move_rejected_while_busy(service):
    service._state.transition(InstrumentState.ACQUIRING)
    try:
        with pytest.raises(FaultError) as exc:
            service.move(MoveRequest(mode="absolute", x=1.0))
        assert exc.value.fault.category == FaultCategory.PROTOCOL
        assert exc.value.fault.detail["current_state"] == "ACQUIRING"
    finally:
        service._state.transition(InstrumentState.PROCESSING)
        service._state.transition(InstrumentState.INITIALIZED)


def test_channels_and_selection(service, sim_scope):
    channels = service.list_channels()["channels"]
    assert channels, "simulated scope should expose channels"
    name = channels[0]["name"]
    result = service.select_channel(name)
    assert result["channel"] == name
    with pytest.raises(FaultError) as exc:
        service.select_channel("No Such Channel")
    assert exc.value.fault.category == FaultCategory.CONFIG


def test_exposure_and_intensity(service):
    channels = service.list_channels()["channels"]
    name = channels[0]["name"]
    assert service.set_exposure(ExposureRequest(exposure_ms=42.0, channel=name))["exposure_ms"] == 42.0
    assert service.set_intensity(IntensityRequest(channel=name, intensity=55.0))["intensity"] == 55.0


def test_objectives(service, sim_scope):
    objs = service.get_objectives()
    assert objs["current"] in objs["objectives"]
    service.set_objective(objs["current"])  # no-op set succeeds
    with pytest.raises(FaultError) as exc:
        service.set_objective("nonexistent-objective")
    assert exc.value.fault.category == FaultCategory.CONFIG


def test_acquire_image(service, tmp_path):
    save_path = str(tmp_path / "img.tiff")
    result = service.acquire(AcquireRequest(save_path=save_path))
    assert result["acquired"] is True
    assert result["saved_to"]
    assert result["shape"]


def test_state_changed_events_published(service):
    q = service.events.subscribe()
    service._state.transition(InstrumentState.ACQUIRING)
    service._state.transition(InstrumentState.PROCESSING)
    service._state.transition(InstrumentState.INITIALIZED)
    kinds = [q.get_nowait().event for _ in range(3)]
    assert kinds == ["state_changed", "state_changed", "state_changed"]
    service.events.unsubscribe(q)


def test_reset_noop_from_initialized(service):
    r = service.reset()
    assert r["no_op"] is True and r["state"] == "INITIALIZED"


def test_initialize_noop_from_initialized(service):
    r = service.initialize()
    assert r["no_op"] is True and r["state"] == "INITIALIZED"


# --- URS delta (LA-WC-0001) tests ---


def test_initialize_with_home_false_from_initialized_is_noop(service):
    # URS API-LIFE-002: initialize(home=False) from INITIALIZED is a no-op and
    # skips subsystem probes entirely.
    r = service.initialize(home=False)
    assert r["no_op"] is True
    assert r["state"] == "INITIALIZED"
    assert r["verified_components"] == []
    assert r["home_performed"] is False


def test_initialize_with_home_true_is_not_a_noop(service):
    # Even from INITIALIZED, home=True forces real work (probes + homing).
    r = service.initialize(home=True)
    assert r["no_op"] is False
    assert r["state"] == "INITIALIZED"
    assert r["verified_components"] == ["stage", "camera", "mcu"]
    assert r["home_performed"] is True


def test_autofocus_status_shape(service):
    # URS API-AF-001: autofocus_status() always returns these four keys with a
    # valid readiness enum value.
    status = service.autofocus_status()
    assert {"available", "initialized", "reference_set", "readiness"} <= set(status.keys())
    assert status["readiness"] in {"OK", "NO_HARDWARE", "NOT_INITIALIZED", "NO_REFERENCE"}


def test_autofocus_store_reference_no_hardware_faults(service):
    # URS API-AF-002: the default simulated scope has SUPPORT_LASER_AUTOFOCUS=False,
    # so no focus camera is configured. autofocus_store_reference() must guard on
    # hardware presence before touching the (nonexistent) controller.
    with pytest.raises(FaultError) as exc:
        service.autofocus_store_reference()
    assert exc.value.fault.category in (FaultCategory.CONFIG, FaultCategory.AUTOFOCUS)


def test_autofocus_correct_no_hardware_faults(service):
    # URS API-AF-003: same guard applies to autofocus_correct().
    with pytest.raises(FaultError) as exc:
        service.autofocus_correct(AutofocusCorrectRequest())
    assert exc.value.fault.category in (FaultCategory.CONFIG, FaultCategory.AUTOFOCUS)


def test_initialize_probe_failure_faults_and_enters_error_state(service, sim_scope, monkeypatch):
    """Verify that probe failures transition the instrument to ERROR state and raise HARDWARE_FAULT."""

    def boom():
        raise RuntimeError("stage communication lost")

    monkeypatch.setattr(sim_scope.stage, "get_pos", boom)
    with pytest.raises(FaultError) as exc:
        service.initialize(home=True)  # home=True forces probes even from INITIALIZED
    assert exc.value.fault.category == FaultCategory.HARDWARE_FAULT
    assert exc.value.fault.code == 5001  # HARDWARE_FAULT_GENERIC
    assert exc.value.fault.component == "stage"
    assert service.state == InstrumentState.ERROR

    # Recover so later tests (or monkeypatch cleanup) see INITIALIZED
    monkeypatch.undo()
    result = service.initialize(home=True)
    assert result["state"] == "INITIALIZED"


def test_acquire_camera_failure_is_recoverable_transient_fault(service, sim_scope, monkeypatch):
    """Verify that camera acquisition failures produce recoverable HARDWARE_TRANSIENT faults."""

    def boom(*args, **kwargs):
        raise RuntimeError("frame timeout")

    monkeypatch.setattr(sim_scope, "acquire_image", boom)
    with pytest.raises(FaultError) as exc:
        service.acquire(AcquireRequest())
    fault = exc.value.fault
    assert fault.category == FaultCategory.HARDWARE_TRANSIENT
    assert fault.code == 4001  # HARDWARE_TRANSIENT_TIMEOUT
    assert fault.recoverable is True
    assert fault.scheduler_action.value == "RETRY"
    assert fault.component == "camera"
