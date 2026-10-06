import pytest

import control._def
from control.laser_engine_rev1 import (
    EngineOptions,
    LaserEngineRev1,
    LaserEngineRev1LightSource,
    build_from_config,
    options_from_def,
)
from control.lighting import IntensityControlMode, ShutterControlMode


def test_flags_are_mutually_exclusive():
    with pytest.raises(ValueError, match="USE_SQUID_LASER_ENGINE and USE_LASER_ENGINE_REV1"):
        control._def._validate_laser_engine_flags(True, True)
    control._def._validate_laser_engine_flags(False, True)


def test_build_from_config_without_the_vendor_module_has_no_source(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_l3_driver(name, *a, **kw):
        if name == "control.laser_engine_rev1_l3_driver":
            raise ImportError("not in this build")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", no_l3_driver)
    engine = build_from_config(sn="X", source_sn=None, options=EngineOptions())
    assert isinstance(engine, LaserEngineRev1) and engine._source_factory is None


def test_options_come_from_the_ini_flags(monkeypatch):
    for name in ("AOM_IN_PATH", "SHUTTER_WITH_AOM", "AOM_ATTENUATION"):  # the no-AOM options are gone (2026-10-06)
        assert not hasattr(control._def, f"LASER_ENGINE_REV1_{name}")
    monkeypatch.setattr(control._def, "LASER_ENGINE_REV1_SOURCE_IDLE_OFF_MIN", 0)
    monkeypatch.setattr(control._def, "LASER_ENGINE_REV1_SOURCE_POWER_MW", 600)  # as the .ini reader gives it
    assert options_from_def() == EngineOptions(source_idle_off_min=0, source_power_mw=600.0)
    monkeypatch.setattr(control._def, "LASER_ENGINE_REV1_SOURCE_POWER_MW", "lots")
    with pytest.raises(ValueError):
        options_from_def()  # a typo in the .ini fails at startup, not silently


def test_simulated_microscope_uses_the_rev1_engine(monkeypatch):
    import control.microscope

    monkeypatch.setattr(control._def, "USE_LASER_ENGINE_REV1", True)
    monkeypatch.setattr(control._def, "USE_SQUID_LASER_ENGINE", False)
    scope = control.microscope.Microscope.build_from_global_config(simulated=True)
    try:
        engine = scope.addons.squid_laser_engine
        assert isinstance(engine, LaserEngineRev1)
        ic = scope.illumination_controller
        assert isinstance(ic.light_source, LaserEngineRev1LightSource)
        assert ic.intensity_control_mode == IntensityControlMode.Software
        assert ic.shutter_control_mode == ShutterControlMode.TTL
        assert engine._ttl_map() == ic.channel_mappings_TTL  # one wavelength map for intensity and exposure (ruling 5)
        assert (
            "TEC1:OUT 1" in engine.sim_engine.sent and "ARM" in engine.sim_engine.sent
        )  # prepare_for_use -> on_startup
        assert engine.bringup_state in ("running", "done")
    finally:
        scope.close()


def test_multipoint_notes_engine_use_every_fov():
    from unittest.mock import MagicMock

    from control.core.multi_point_worker import MultiPointWorker

    worker = MultiPointWorker.__new__(MultiPointWorker)  # only the two fields the hook reads
    worker._laser_engine, worker._laser_channels_needed = MagicMock(), ["L3"]
    worker._note_laser_engine_use()
    worker._laser_engine.note_use.assert_called_once_with(["L3"])
    worker._laser_engine = object()  # the 2024/25 engine has no note_use
    worker._note_laser_engine_use()  # must not raise


def test_old_engine_has_on_startup():
    from control.squid_laser_engine import SquidLaserEngine_Simulation

    SquidLaserEngine_Simulation(query_interval_s=0.01).on_startup()  # = wake_up_all(); must not raise
