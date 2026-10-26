from unittest.mock import MagicMock

import pytest

import control._def
from control.laser_engine_v2 import (
    EngineOptions,
    LaserEngineV2,
    LaserEngineV2LightSource,
    build_from_config,
)
from control.laser_engine_v2_settings import save_idle_off_560_min, save_power_560_mw
from control.lighting import IntensityControlMode, ShutterControlMode


@pytest.fixture(autouse=True)
def _cache_in_tmp(tmp_path, monkeypatch):
    """The 560 settings file in a temporary directory: these tests never read or write Squid's cache/."""
    monkeypatch.setattr("control.laser_engine_v2_settings.DEFAULT_CACHE_PATH", tmp_path / "laser_engine_v2.yaml")


@pytest.fixture
def resolve(monkeypatch):
    """_def._resolve_laser_engine with _def's logger captured: returns (resolve(...), the logger mock)."""
    log = MagicMock()
    monkeypatch.setattr(control._def, "log", log)

    def call(laser_engine=None, laser_engine_sn=None, use_squid_laser_engine=False, squid_laser_engine_sn=None):
        return control._def._resolve_laser_engine(
            laser_engine, laser_engine_sn, use_squid_laser_engine, squid_laser_engine_sn
        )

    return call, log


def test_the_ini_has_one_engine_selector_and_one_serial_number_key():
    assert control._def.LASER_ENGINE in (None, "v1", "v2")
    for name in ("USE_LASER_ENGINE_REV1", "LASER_ENGINE_REV1_SN", "LASER_ENGINE_REV1_SOURCE_SN"):
        assert not hasattr(control._def, name)
    assert hasattr(control._def, "USE_SQUID_LASER_ENGINE") and hasattr(control._def, "SQUID_LASER_ENGINE_SN")


def test_resolver_selects_v2_or_v1_by_laser_engine(resolve):
    call, log = resolve
    assert call("v2", "123") == ("v2", "123")
    assert call("V1 ", 12345670) == ("v1", 12345670)  # normalised; an all-digit serial number reads as an int
    for blank in (None, "", "none", "None"):
        assert call(blank) == (None, None)
    assert call("v1", use_squid_laser_engine=True) == ("v1", None)  # both say v1: fine
    log.warning.assert_not_called()


def test_resolver_legacy_key_alone_selects_v1_with_one_warning(resolve):
    call, log = resolve
    assert call(use_squid_laser_engine=True, squid_laser_engine_sn="ABC") == ("v1", "ABC")
    log.warning.assert_called_once()
    assert (
        "use laser_engine = v1 (and laser_engine_sn) instead of use_squid_laser_engine (and squid_laser_engine_sn)"
        in (log.warning.call_args[0][0])
    )


def test_resolver_refuses_a_contradiction_and_an_unknown_engine(resolve):
    call, _ = resolve
    with pytest.raises(ValueError, match="contradicts laser_engine = v2"):
        call("v2", use_squid_laser_engine=True)
    for bad in ("v3", "v0", True, 2):
        with pytest.raises(ValueError, match="laser_engine = .* use v1, v2, or leave it blank"):
            call(bad)


def test_resolver_serial_number_falls_back_to_the_legacy_key_for_v1_only(resolve):
    call, _ = resolve
    assert call("v1", None, squid_laser_engine_sn="OLD") == ("v1", "OLD")
    assert call("v1", "", squid_laser_engine_sn="OLD") == ("v1", "OLD")  # a blank key is unset
    assert call("v1", "NEW", squid_laser_engine_sn="OLD") == ("v1", "NEW")
    assert call("v2", None, squid_laser_engine_sn="OLD") == ("v2", None)  # the v1 serial number never opens a v2


def test_build_from_config_without_the_560_driver_module_has_no_source(monkeypatch):
    """The 560 source driver is supplied separately: without it, line 3 reads NOT_CONFIGURED and nothing else breaks."""
    import builtins

    real_import = builtins.__import__

    def no_560_driver(name, *a, **kw):
        if name == "control.laser_engine_v2_560_driver":
            raise ImportError("not in this build")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", no_560_driver)
    engine = build_from_config(sn="X", options=EngineOptions())
    assert isinstance(engine, LaserEngineV2) and engine._source_factory is None


def test_build_from_config_uses_the_560_driver_module_when_present(monkeypatch):
    import sys
    import types

    module = types.ModuleType("control.laser_engine_v2_560_driver")
    module.open_560_source = lambda: "source"  # takes no arguments: the driver finds the source by its USB IDs
    monkeypatch.setitem(sys.modules, "control.laser_engine_v2_560_driver", module)
    engine = build_from_config(sn="X", options=EngineOptions())
    assert engine._source_factory is module.open_560_source and engine._source_factory() == "source"


def test_the_560_settings_are_not_machine_ini_keys():
    for name in ("SOURCE_POWER_MW", "SOURCE_IDLE_OFF_MIN", "AOM_IN_PATH", "SHUTTER_WITH_AOM", "AOM_ATTENUATION"):
        assert not hasattr(control._def, f"LASER_ENGINE_REV1_{name}") and not hasattr(
            control._def, f"LASER_ENGINE_V2_{name}"
        )


def test_simulated_microscope_uses_the_v2_engine(monkeypatch):
    import control.microscope

    monkeypatch.setattr(control._def, "LASER_ENGINE", "v2")
    assert save_power_560_mw(600) and save_idle_off_560_min(45)  # as the Laser Engine tab left them
    scope = control.microscope.Microscope.build_from_global_config(simulated=True)
    try:
        engine = scope.addons.squid_laser_engine
        assert isinstance(engine, LaserEngineV2)
        assert engine.options == EngineOptions(source_idle_off_min=45, source_power_mw=600)  # from the cache
        ic = scope.illumination_controller
        assert isinstance(ic.light_source, LaserEngineV2LightSource)
        assert ic.intensity_control_mode == IntensityControlMode.Software
        assert ic.shutter_control_mode == ShutterControlMode.TTL
        assert engine._ttl_map() == ic.channel_mappings_TTL  # one wavelength map for intensity and exposure
        assert engine.in_use_provider == scope.live_controller.illumination_wavelengths_in_use  # live counts as use
        assert (
            "TEC1:OUT 1" in engine.sim_engine.sent and "ARM" in engine.sim_engine.sent
        )  # prepare_for_use -> on_startup
        assert engine.bringup_state in ("running", "done")
    finally:
        scope.close()


def test_simulated_microscope_uses_the_v1_engine_for_laser_engine_v1(monkeypatch):
    import control.microscope
    from control.squid_laser_engine import SquidLaserEngine_Simulation

    monkeypatch.setattr(control._def, "LASER_ENGINE", "v1")
    scope = control.microscope.Microscope.build_from_global_config(simulated=True)
    try:
        assert isinstance(scope.addons.squid_laser_engine, SquidLaserEngine_Simulation)
        assert not isinstance(scope.illumination_controller.light_source, LaserEngineV2LightSource)
    finally:
        scope.close()


def test_multipoint_notes_engine_use_every_fov():
    from unittest.mock import MagicMock

    from control.core.multi_point_worker import MultiPointWorker

    worker = MultiPointWorker.__new__(MultiPointWorker)  # only the two fields the hook reads
    worker._laser_engine, worker._laser_channels_needed = MagicMock(), ["L3"]
    worker._note_laser_engine_use()
    worker._laser_engine.note_use.assert_called_once_with(["L3"])
    worker._laser_engine = object()  # the v1 engine has no note_use
    worker._note_laser_engine_use()  # must not raise


def test_old_engine_has_on_startup():
    from control.squid_laser_engine import SquidLaserEngine_Simulation

    SquidLaserEngine_Simulation(query_interval_s=0.01).on_startup()  # = wake_up_all(); must not raise


def _ready_df_engine():
    from control.laser_engine_v2_link import EngineLink
    from control.laser_engine_v2_sim import FakeEngine, FakeSource
    from control.laser_engine_v2_status import LineState

    fake, source = FakeEngine(tok_delay_polls=0), FakeSource()
    engine = LaserEngineV2(link_factory=lambda: EngineLink(fake), source_factory=lambda: source, query_interval_s=0.001)
    engine.open()
    engine.wake_up("L3")
    for _ in range(5):
        engine.source_step()
    assert engine.poll_once().channels["L3"].state == LineState.READY
    return engine, fake, source


def _live_controller(engine, wavelength):
    """A LiveController on `engine` whose channel illuminates with wavelength[0] (a list, so a test can switch it)."""
    from types import SimpleNamespace

    from control._def import TriggerMode
    from control.core.live_controller import LiveController

    micro, camera = MagicMock(), MagicMock()
    micro.is_busy.return_value = False
    camera.get_ready_for_trigger.return_value = True
    scope = SimpleNamespace(
        addons=SimpleNamespace(squid_laser_engine=engine, sci_microscopy_led_array=None),
        illumination_controller=MagicMock(),
        low_level_drivers=SimpleNamespace(microcontroller=micro),
    )
    live = LiveController(scope, camera)
    live._get_illumination_wavelength = lambda: wavelength[0]
    live._is_led_matrix = lambda: False
    live.trigger_mode = TriggerMode.HARDWARE
    live._start_triggerred_acquisition = lambda: None  # frames are not needed: use is polled, not per frame
    engine.in_use_provider = live.illumination_wavelengths_in_use  # what Microscope wires
    return live


def test_live_view_counts_as_560_use_through_the_live_controller(monkeypatch):
    from control.laser_engine_v2_link import EngineLink

    monkeypatch.setattr(EngineLink, "RESYNC_WAIT_S", 0.0)
    engine, fake, source = _ready_df_engine()
    try:
        live = _live_controller(engine, [560])
        assert live.illumination_wavelengths_in_use() == []
        live.start_live()
        assert live.illumination_wavelengths_in_use() == [560]
        engine.source_idle_off_s = 60
        engine._source_last_use -= 61  # live has run past the idle-off
        engine.poll_once()
        engine.source_step()
        assert source.enabled  # live is use
        live.stop_live()
        assert live.illumination_wavelengths_in_use() == []
    finally:
        engine.close()


@pytest.mark.parametrize("switched_off_yet", [True, False])  # False: before the source thread runs the switch-off
@pytest.mark.parametrize("entry", ["channel switch", "live start"])
def test_going_live_on_the_560_after_its_idle_off_wakes_it(monkeypatch, entry, switched_off_yet):
    """The 560 idled off while live was on another channel (or off). Going live on the 560 at its unchanged intensity
    reaches no light source (the intensity is cached), so the switch or the start itself wakes line 3 - also when it
    comes before the source thread has run the switch-off, while the source still reads ready."""
    from control.laser_engine_v2_link import EngineLink
    from control.laser_engine_v2_status import LineState

    monkeypatch.setattr(EngineLink, "RESYNC_WAIT_S", 0.0)
    engine, fake, source = _ready_df_engine()
    try:
        wavelength = [488]
        live = _live_controller(engine, wavelength)
        live.update_illumination = lambda: None  # an unchanged intensity: nothing reaches the engine
        live._stop_existing_timer = live._start_new_timer = lambda: None
        if entry == "channel switch":
            live.start_live()
        engine.source_idle_off_s = 60
        engine._source_last_use -= 61  # past the 560's idle-off
        engine.poll_once()  # the idle-off: the switch-off is queued
        if switched_off_yet:
            engine.source_step()
        wavelength[0] = 560
        if entry == "channel switch":
            live.set_microscope_mode(MagicMock())  # back to the 560 channel, live still on
        else:
            live.start_live()
        for _ in range(5):
            engine.source_step()
            engine.poll_once()
        assert source.enabled and engine.poll_once().channels["L3"].state == LineState.READY
    finally:
        engine.close()


def test_after_a_restart_the_shutter_is_open_when_the_acquisition_sees_l3_ready(monkeypatch):
    from control.core.multi_point_worker import MultiPointWorker
    from control.laser_engine_v2_link import EngineLink

    monkeypatch.setattr(EngineLink, "RESYNC_WAIT_S", 0.0)
    engine, fake, source = _ready_df_engine()
    try:
        source.enabled = False  # off on its own (or idled off): the next use restarts it behind the closed shutter
        engine.source_step()
        engine.wake_up("L3")
        for _ in range(5):
            engine.source_step()  # ready again before the next engine poll
        worker = MultiPointWorker.__new__(MultiPointWorker)  # only the fields the gate reads
        worker._laser_engine, worker._laser_channels_needed = engine, ["L3"]
        worker.callbacks, worker.abort_requested_fn = MagicMock(), lambda: False
        worker._wait_for_laser_engine()
        worker.callbacks.signal_laser_engine_waiting.assert_not_called()  # READY at once...
        assert fake.shut_open  # ...with the shutter already open: no dark frames
    finally:
        engine.close()


def test_a_power_change_holds_the_acquisition_until_the_source_reads_it(monkeypatch):
    """The worker's first look is the engine's cached status, with no poll since the power change: it must not let the
    acquisition start at the old power."""
    from control.core.multi_point_worker import MultiPointWorker
    from control.laser_engine_v2_link import EngineLink

    monkeypatch.setattr(EngineLink, "RESYNC_WAIT_S", 0.0)
    engine, fake, source = _ready_df_engine()
    try:
        worker = MultiPointWorker.__new__(MultiPointWorker)  # only the fields the gate reads
        worker._laser_engine, worker._laser_channels_needed = engine, ["L3"]
        worker.callbacks, worker.abort_requested_fn = MagicMock(), lambda: False
        engine.READY_TIMEOUT_S = 0.05  # no source thread here: the laser never reads 600 mW
        engine.set_source_power_mw(600.0)
        with pytest.raises(RuntimeError, match="did not reach ready state"):
            worker._wait_for_laser_engine()
        worker.callbacks.signal_laser_engine_waiting.assert_called_once_with(["L3"])
        engine.source_step()  # the laser reads 600 mW
        worker.callbacks.reset_mock()
        worker._wait_for_laser_engine()
        worker.callbacks.signal_laser_engine_waiting.assert_not_called()  # ready at the new power: no wait
    finally:
        engine.close()


def _engine_controller(engine, ttl_map):
    """An IlluminationController on `engine`, its port map read from `ttl_map` (wavelength -> port code) on every
    call, so a test can remap a wavelength by editing the dict."""
    from types import SimpleNamespace

    from control.lighting import IlluminationController, LightSourceType

    controller = IlluminationController(
        MagicMock(),
        IntensityControlMode.Software,
        ShutterControlMode.TTL,
        LightSourceType.CephlaLaserEngineV2,
        engine.light_source,
    )
    controller.config_repo = MagicMock()
    illumination_config = controller.config_repo.get_illumination_config.return_value
    illumination_config.channels = [SimpleNamespace(wavelength_nm=w) for w in ttl_map]
    illumination_config.get_source_code.side_effect = lambda channel: ttl_map[channel.wavelength_nm]
    engine.ttl_map_provider = lambda: controller.channel_mappings_TTL
    return controller


def test_a_port_remap_at_the_same_intensity_moves_the_set_point_to_the_new_line(monkeypatch):
    from control._def import ILLUMINATION_CODE
    from control.laser_engine_v2_link import EngineLink

    monkeypatch.setattr(EngineLink, "RESYNC_WAIT_S", 0.0)
    engine, fake, _ = _ready_df_engine()
    try:
        fake.tok = [True] * 5
        ttl_map = {488: ILLUMINATION_CODE.ILLUMINATION_D2}
        controller = _engine_controller(engine, ttl_map)
        controller.set_intensity(488, 40)
        assert fake.lines[1]["target"] > 0
        ttl_map[488] = ILLUMINATION_CODE.ILLUMINATION_D4  # the operator remaps 488 nm to D4 (engine line 4)
        controller.set_intensity(488, 40)  # same intensity
        assert controller.microcontroller.set_illumination.call_args.args[0] == ILLUMINATION_CODE.ILLUMINATION_D4
        assert fake.lines[3]["target"] > 0  # line 4 got the set-point too
    finally:
        engine.close()


def _line2_sets(fake, since):
    return [c for c in fake.sent[since:] if c.startswith("LINE2:SET")]


def test_a_wavelength_gets_its_set_point_back_after_another_wavelength_used_its_line(monkeypatch):
    """470 and 488 nm on one port drive one engine line: switching back to 488 at its unchanged % must send it again,
    as the line holds 470's set-point."""
    from control._def import ILLUMINATION_CODE
    from control.laser_engine_v2_link import EngineLink

    monkeypatch.setattr(EngineLink, "RESYNC_WAIT_S", 0.0)
    engine, fake, _ = _ready_df_engine()
    try:
        fake.tok = [True] * 5
        controller = _engine_controller(
            engine, {470: ILLUMINATION_CODE.ILLUMINATION_D2, 488: ILLUMINATION_CODE.ILLUMINATION_D2}
        )
        ceiling = fake.lines[1]["max"]
        controller.set_intensity(488, 50)
        controller.set_intensity(470, 20)
        mark = len(fake.sent)
        controller.set_intensity(488, 50)
        assert _line2_sets(fake, mark) == ["LINE2:SET %.4f" % (0.5 * ceiling)]
        assert fake.lines[1]["target"] == pytest.approx(0.5 * ceiling, abs=1e-4)
    finally:
        engine.close()


def test_an_unchanged_percent_of_the_wavelength_a_line_holds_is_not_sent_again(monkeypatch):
    """The other side of the test above: each set-point waits out the line's ramp, so one already held is not resent."""
    from control._def import ILLUMINATION_CODE
    from control.laser_engine_v2_link import EngineLink

    monkeypatch.setattr(EngineLink, "RESYNC_WAIT_S", 0.0)
    engine, fake, _ = _ready_df_engine()
    try:
        fake.tok = [True] * 5
        controller = _engine_controller(
            engine, {470: ILLUMINATION_CODE.ILLUMINATION_D2, 488: ILLUMINATION_CODE.ILLUMINATION_D2}
        )
        controller.set_intensity(470, 20)
        controller.set_intensity(488, 50)
        mark = len(fake.sent)
        controller.set_intensity(488, 50)
        assert _line2_sets(fake, mark) == []
    finally:
        engine.close()
