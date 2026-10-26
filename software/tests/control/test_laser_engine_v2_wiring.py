import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import control._def
from control.laser_engine_v2 import (
    EngineLine,
    EngineOptions,
    LaserEngineV2,
    LaserEngineV2LightSource,
    build_from_config,
)
from control.laser_engine_v2_settings import save_idle_off_560_min, save_power_560_mw, save_shutter_mode_560
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
    # as the Laser Engine tab left them
    assert save_power_560_mw(600) and save_idle_off_560_min(45) and save_shutter_mode_560("always_open")
    scope = control.microscope.Microscope.build_from_global_config(simulated=True)
    try:
        engine = scope.addons.squid_laser_engine
        assert isinstance(engine, LaserEngineV2)
        assert engine.options == EngineOptions(  # from the cache
            source_idle_off_min=45, source_power_mw=600, shutter_mode="always_open"
        )
        assert engine.shutter_mode == "always_open"
        ic = scope.illumination_controller
        assert isinstance(ic.light_source, LaserEngineV2LightSource)
        assert ic.intensity_control_mode == IntensityControlMode.Software
        assert ic.shutter_control_mode == ShutterControlMode.TTL
        assert engine._ttl_map() == ic.channel_mappings_TTL  # one wavelength map for intensity and exposure
        assert ic.light_source.mechanical_shutter(ic.channel_mappings_software[561]).open_time_s == 0.001
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


def test_the_acquisition_uses_its_channels_and_the_contrast_autofocus_channel():
    from types import SimpleNamespace

    from control.core.multi_point_worker import MultiPointWorker

    def channel(wavelength):
        return SimpleNamespace(get_illumination_wavelength=lambda ill_config: wavelength)

    worker = MultiPointWorker.__new__(MultiPointWorker)  # only the fields the computation reads
    worker.microscope = SimpleNamespace(config_repo=SimpleNamespace(get_illumination_config=lambda: object()))
    worker.selected_configurations = [channel(488), channel(None)]  # None: an LED matrix channel
    worker.liveController = SimpleNamespace(get_channel_by_name=lambda objective, name: channel(561))
    worker.objectiveStore = SimpleNamespace(current_objective="10x")
    worker.do_autofocus, worker.do_reflection_af = True, False  # contrast autofocus
    assert worker._compute_illumination_wavelengths() == [488, 561]
    worker.do_reflection_af = True  # laser autofocus: no autofocus channel is imaged
    assert worker._compute_illumination_wavelengths() == [488]
    worker.do_autofocus, worker.do_reflection_af = False, False
    assert worker._compute_illumination_wavelengths() == [488]


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
    from control.lighting import _DEFAULT_CHANNEL_MAPPINGS_TTL

    scope = SimpleNamespace(
        addons=SimpleNamespace(squid_laser_engine=engine, sci_microscopy_led_array=None),
        illumination_controller=_engine_controller(engine, dict(_DEFAULT_CHANNEL_MAPPINGS_TTL)),
        low_level_drivers=SimpleNamespace(microcontroller=micro),
    )
    live = LiveController(scope, camera)
    live._get_illumination_wavelength = lambda: wavelength[0]
    live._is_led_matrix = lambda: False
    live.trigger_mode = TriggerMode.HARDWARE
    live._start_triggerred_acquisition = lambda: None  # frames are not needed: use is declared, not per frame
    return live


def test_live_view_counts_as_560_use_through_the_live_controller(monkeypatch):
    from control.laser_engine_v2_link import EngineLink

    monkeypatch.setattr(EngineLink, "RESYNC_WAIT_S", 0.0)
    engine, fake, source = _ready_df_engine()
    try:
        live = _live_controller(engine, [560])
        live.start_live()
        engine.source_idle_off_s = 60
        engine._source_last_use -= 61  # live has run past the idle-off
        engine.poll_once()
        engine.source_step()
        assert source.enabled  # live is use
        live.stop_live()
        engine._source_last_use -= 61
        engine.poll_once()
        engine.source_step()
        assert not source.enabled  # live stopped: the idle-off counts again
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


def test_live_view_holds_the_560_shutter_open_only_on_the_560_channel(monkeypatch):
    from control.laser_engine_v2_link import EngineLink

    monkeypatch.setattr(EngineLink, "RESYNC_WAIT_S", 0.0)
    engine, fake, source = _ready_df_engine()
    try:
        wavelength = [561]
        live = _live_controller(engine, wavelength)
        live.update_illumination = lambda: None
        live._stop_existing_timer = live._start_new_timer = lambda: None
        assert not fake.shut_open  # L3 READY, live off
        live.start_live()
        assert fake.shut_open
        wavelength[0] = 488
        live.set_microscope_mode(MagicMock())
        assert not fake.shut_open
        wavelength[0] = 561
        live.set_microscope_mode(MagicMock())
        assert fake.shut_open
        live.stop_live()
        assert not fake.shut_open
    finally:
        engine.close()


def test_a_560_snap_opens_the_shutter_for_its_frame_only(monkeypatch):
    from control.laser_engine_v2_link import EngineLink

    monkeypatch.setattr(EngineLink, "RESYNC_WAIT_S", 0.0)
    engine, fake, source = _ready_df_engine()
    try:
        live = _live_controller(engine, [561])
        callbacks, shutter_at_frame = [], []
        live.camera.add_frame_callback.side_effect = lambda cb: callbacks.append(cb) or len(callbacks)

        def read_frame():
            shutter_at_frame.append(fake.shut_open)
            for cb in callbacks:
                cb(object())
            return object()

        live.camera.read_frame.side_effect = read_frame
        assert live.snap()
        assert shutter_at_frame == [True] and not fake.shut_open
    finally:
        engine.close()


def test_an_acquisition_with_the_560_holds_the_shutter_open_until_it_ends(monkeypatch):
    import time

    import control.microscope
    import tests.control.test_stubs as ts
    from control.core.multi_point_worker import MultiPointWorker
    from tests.control.test_MultiPointController import TestAcquisitionTracker

    monkeypatch.setattr(control._def, "LASER_ENGINE", "v2")
    scope = control.microscope.Microscope.build_from_global_config(simulated=True)
    try:
        engine = scope.addons.squid_laser_engine
        fake = engine.sim_engine
        assert engine.wait_until_ready(["L2", "L3"], timeout_s=30)
        seen = []
        acquire = MultiPointWorker.acquire_camera_image

        def recording(self, config, *args, **kwargs):
            seen.append((config.name, fake.shut_open))
            return acquire(self, config, *args, **kwargs)

        monkeypatch.setattr(MultiPointWorker, "acquire_camera_image", recording)
        tracker = TestAcquisitionTracker()
        mpc = ts.get_test_multi_point_controller(microscope=scope, callbacks=tracker.get_callbacks())
        names = [ch.name for ch in mpc.liveController.get_channels(scope.objective_store.current_objective)]
        ch488, ch561 = next(n for n in names if "488" in n), next(n for n in names if "561" in n)
        cfg = mpc.stage.get_config()
        mpc.scanCoordinates.clear_regions()
        mpc.scanCoordinates.add_single_fov_region(
            "single",
            center_x=cfg.X_AXIS.MIN_POSITION + 1.0,
            center_y=cfg.Y_AXIS.MIN_POSITION + 1.0,
            center_z=cfg.Z_AXIS.MIN_POSITION + 1.0,
        )

        def run(channels):
            seen.clear()
            tracker.started_event.clear()
            tracker.finished_event.clear()
            mpc.set_selected_configurations(channels)
            mpc.run_acquisition()
            assert tracker.started_event.wait(10) and tracker.finished_event.wait(60)
            deadline = time.monotonic() + 2.0
            while fake.shut_open and time.monotonic() < deadline:
                time.sleep(0.01)

        run([ch488, ch561])
        assert seen == [(ch488, True), (ch561, True)]  # open for the whole run, across its channels
        assert not fake.shut_open  # closed once it ended
        run([ch488])
        assert seen == [(ch488, False)]  # an acquisition without the 560 leaves it closed
    finally:
        scope.close()


def test_after_a_restart_the_shutter_is_open_when_the_acquisition_sees_l3_ready(monkeypatch):
    from control.core.multi_point_worker import MultiPointWorker
    from control.laser_engine_v2_link import EngineLink

    monkeypatch.setattr(EngineLink, "RESYNC_WAIT_S", 0.0)
    engine, fake, source = _ready_df_engine()
    try:
        engine.use("acquisition", [EngineLine(3, 561)])  # what MultiPointWorker.run() holds for the time point
        source.enabled = False  # off on its own (or idled off): the next use restarts it behind the closed shutter
        engine.source_step()
        engine.wake_up("L3")
        for _ in range(5):
            engine.source_step()  # ready again before the next engine poll
        time.sleep(engine.SHUTTER_OPEN_TIME_S)  # the step that read it ready opened the shutter: its open time passes
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


# ---- every imaging path declares its use: it wakes the line, keeps the 560 on, opens its shutter (and waits for it) ----


def test_tcp_illumination_holds_the_current_channel_and_follows_set_channel_with_live_off(monkeypatch):
    from control.laser_engine_v2_link import EngineLink
    from control.microscope_control_server import MicroscopeControlServer

    monkeypatch.setattr(EngineLink, "RESYNC_WAIT_S", 0.0)
    engine, fake, source = _ready_df_engine()
    try:
        wavelength = [488]
        live = _live_controller(engine, wavelength)
        live.update_illumination = lambda: None
        server = SimpleNamespace(microscope=SimpleNamespace(live_controller=live))  # what the two handlers read
        MicroscopeControlServer._cmd_turn_on_illumination(server)
        assert not fake.shut_open  # on 488
        wavelength[0] = 561
        live.set_microscope_mode(MagicMock())  # TCP set_channel, live off
        assert fake.shut_open
        MicroscopeControlServer._cmd_turn_off_illumination(server)
        assert not fake.shut_open
    finally:
        engine.close()


def _record_shutter_at(engine, fake):
    """A stand-in for an imaging step: records whether the shutter was open (and settled) when it ran."""
    seen = []

    def step(*args, **kwargs):
        seen.append((fake.shut_open, engine.is_ready_to_image(["L3"])))

    return seen, step


def _idled_off(engine, source):
    engine.source_idle_off_s = 60
    engine._source_last_use -= 61
    engine.poll_once()
    engine.source_step()
    assert not source.enabled


def _source_thread(engine, stop) -> None:
    """The engine's source thread until `stop` is set, so a blocking wait sees the 560 come back up."""
    import threading

    def run():
        while not stop.is_set():
            engine.source_step()
            time.sleep(0.002)

    threading.Thread(target=run, daemon=True).start()


@pytest.mark.parametrize("path", ["acquire_image", "autofocus", "tracking"])
def test_scripted_paths_wake_the_560_after_its_idle_off_and_image_only_once_it_is_ready(monkeypatch, path):
    """Before this, after an idle-off nothing woke the 560 on these paths and they imaged with it off."""
    import threading

    import control.microscope
    from control._def import TriggerMode
    from control.core.auto_focus_worker import AutofocusWorker
    from control.core.core import TrackingWorker
    from control.laser_engine_v2_link import EngineLink

    monkeypatch.setattr(EngineLink, "RESYNC_WAIT_S", 0.0)
    engine, fake, source = _ready_df_engine()
    stop = threading.Event()
    try:
        live = _live_controller(engine, [561])
        live.trigger_mode = TriggerMode.SOFTWARE
        _idled_off(engine, source)
        seen, step = _record_shutter_at(engine, fake)
        _source_thread(engine, stop)  # stopped by `stop` in the finally
        if path == "acquire_image":
            camera = MagicMock()
            camera.read_frame.side_effect = lambda: step() or object()
            scope = SimpleNamespace(live_controller=live, camera=camera, _wait_for_microcontroller=lambda: None)
            control.microscope.Microscope.acquire_image(scope)
        elif path == "autofocus":
            worker = SimpleNamespace(
                liveController=live,
                _keep_running=threading.Event(),
                run_autofocus=step,
                _finished_fn=lambda: None,
                _log=MagicMock(),
            )
            worker._keep_running.set()
            AutofocusWorker.run(worker)
        else:
            ill_config = object()
            channel = SimpleNamespace(get_illumination_wavelength=lambda c: 561)
            live._get_illumination_config = lambda: ill_config
            worker = SimpleNamespace(
                liveController=live,
                selected_configurations=[channel],
                trackingController=SimpleNamespace(flag_stop_tracking_requested=False),
                _run_tracking=step,
                finished=MagicMock(),
                _log=MagicMock(),
            )
            TrackingWorker.run(worker)
        assert seen == [(True, True)]  # imaged once the 560 was back, behind an open, settled shutter
        assert source.enabled and not fake.shut_open  # done: still on (for the idle-off), shutter closed
    finally:
        stop.set()
        engine.close()


def test_autofocus_cancelled_while_the_560_comes_up_does_not_focus(monkeypatch):
    import threading

    from control.core.auto_focus_worker import AutofocusWorker
    from control.laser_engine_v2_link import EngineLink

    monkeypatch.setattr(EngineLink, "RESYNC_WAIT_S", 0.0)
    engine, fake, source = _ready_df_engine()
    try:
        live = _live_controller(engine, [561])
        _idled_off(engine, source)  # no source thread: the 560 never comes back
        ran, finished = [], []
        worker = SimpleNamespace(
            liveController=live,
            _keep_running=threading.Event(),  # not set: the operator stopped autofocus
            run_autofocus=lambda: ran.append(1),
            _finished_fn=lambda: finished.append(1),
            _log=MagicMock(),
        )
        AutofocusWorker.run(worker)
        assert ran == [] and finished == [1] and not fake.shut_open
    finally:
        engine.close()


def test_tracking_with_the_560_key_off_reports_and_finishes(monkeypatch):
    from control.core.core import TrackingWorker
    from control.laser_engine_v2_link import EngineLink

    monkeypatch.setattr(EngineLink, "RESYNC_WAIT_S", 0.0)
    engine, fake, source = _ready_df_engine()
    try:
        live = _live_controller(engine, [561])
        source.needs_key = True  # the operator must cycle the key: a wait cannot bring it back
        engine.source_step()
        channel = SimpleNamespace(get_illumination_wavelength=lambda c: 561)
        live._get_illumination_config = lambda: object()
        tracked = []
        worker = SimpleNamespace(
            liveController=live,
            selected_configurations=[channel],
            trackingController=SimpleNamespace(flag_stop_tracking_requested=False),
            _run_tracking=lambda: tracked.append(1),
            finished=MagicMock(),
            _log=MagicMock(),
        )
        TrackingWorker.run(worker)
        assert tracked == [] and worker.finished.emit.called and not fake.shut_open
    finally:
        engine.close()


def test_an_acquisition_releases_the_560_between_time_points(monkeypatch):
    """Per time point: the shutter closes and the idle-off counts during the wait for the next one."""
    import control.microscope
    import tests.control.test_stubs as ts
    from control.core.multi_point_worker import MultiPointWorker
    from tests.control.test_MultiPointController import TestAcquisitionTracker

    monkeypatch.setattr(control._def, "LASER_ENGINE", "v2")
    scope = control.microscope.Microscope.build_from_global_config(simulated=True)
    try:
        engine = scope.addons.squid_laser_engine
        fake = engine.sim_engine
        assert engine.wait_until_ready(["L3"], timeout_s=30)
        during, between, inside = [], [], [False]
        single = MultiPointWorker.run_single_time_point
        sleep = MultiPointWorker._sleep

        def recording_point(self):
            during.append((fake.shut_open, engine._line3_held()))
            inside[0] = True
            try:
                return single(self)
            finally:
                inside[0] = False

        def recording_sleep(self, sec):  # also called inside a time point: only the wait between them counts here
            if not inside[0]:
                between.append((fake.shut_open, engine._line3_held()))
            return sleep(self, sec)

        monkeypatch.setattr(MultiPointWorker, "run_single_time_point", recording_point)
        monkeypatch.setattr(MultiPointWorker, "_sleep", recording_sleep)
        tracker = TestAcquisitionTracker()
        mpc = ts.get_test_multi_point_controller(microscope=scope, callbacks=tracker.get_callbacks())
        names = [ch.name for ch in mpc.liveController.get_channels(scope.objective_store.current_objective)]
        cfg = mpc.stage.get_config()
        mpc.scanCoordinates.clear_regions()
        mpc.scanCoordinates.add_single_fov_region(
            "single",
            center_x=cfg.X_AXIS.MIN_POSITION + 1.0,
            center_y=cfg.Y_AXIS.MIN_POSITION + 1.0,
            center_z=cfg.Z_AXIS.MIN_POSITION + 1.0,
        )
        mpc.set_selected_configurations([next(n for n in names if "561" in n)])
        mpc.set_Nt(2)
        mpc.set_deltat(5.0)  # longer than a simulated time point: a late one is skipped, not waited for
        mpc.run_acquisition()
        assert tracker.started_event.wait(10) and tracker.finished_event.wait(60)
        assert during == [(True, True), (True, True)]  # each time point: held, the shutter open
        assert between and all(state == (False, False) for state in between)  # the wait: released, closed
    finally:
        scope.close()


def test_the_live_controllers_own_channel_lookup_reaches_line_3(monkeypatch):
    """Through the live controller's real channel lookup (the tests above stub the wavelength)."""
    import control.microscope

    monkeypatch.setattr(control._def, "LASER_ENGINE", "v2")
    scope = control.microscope.Microscope.build_from_global_config(simulated=True)
    try:
        engine = scope.addons.squid_laser_engine
        fake = engine.sim_engine
        assert engine.wait_until_ready(["L3"], timeout_s=30)
        live = scope.live_controller
        objective = scope.objective_store.current_objective
        live.set_microscope_mode(live.get_channel_by_name(objective, "Fluorescence 561 nm Ex"))
        with live.use_current_channel("snap"):
            assert fake.shut_open
        assert not fake.shut_open
        live.set_microscope_mode(live.get_channel_by_name(objective, "Fluorescence 488 nm Ex"))
        with live.use_current_channel("snap"):
            assert not fake.shut_open
    finally:
        scope.close()


def test_two_tcp_clients_turning_illumination_on_at_once_leave_no_orphan_hold(monkeypatch):
    """The control server runs each client on its own thread: two overlapping turn_on_illumination calls must leave one
    hold, which turn_off_illumination releases (an orphan would hold the shutter open and the 560 on)."""
    import threading

    from control.laser_engine_v2_link import EngineLink

    monkeypatch.setattr(EngineLink, "RESYNC_WAIT_S", 0.0)
    engine, fake, source = _ready_df_engine()
    try:
        live = _live_controller(engine, [561])
        captured, resume = threading.Event(), threading.Event()
        original = live.use_current_channel

        def slow_use(name):
            hold = original(name)
            if threading.current_thread().name == "first":
                captured.set()  # the first client has made its hold, not yet recorded it
                assert resume.wait(5)
            return hold

        live.use_current_channel = slow_use
        first = threading.Thread(target=live.hold_current_channel, args=("tcp",), name="first")
        second = threading.Thread(target=live.hold_current_channel, args=("tcp",), name="second")
        first.start()
        assert captured.wait(5)
        second.start()
        second.join(0.3)  # the second client, meanwhile
        resume.set()
        first.join(5)
        second.join(5)
        assert not first.is_alive() and not second.is_alive()
        assert len(engine._holds) == 1 and fake.shut_open
        live.release_current_channel("tcp")
        assert not engine._holds and not fake.shut_open
    finally:
        engine.close()
