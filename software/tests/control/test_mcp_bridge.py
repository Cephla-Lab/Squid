import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient  # noqa: F401  (ensures fastapi present)

import control.microscope
import mcp_microscope_server as bridge
from squid_service.config import ServiceConfig
from squid_service.rest.app import create_app
from squid_service.service import SquidCoreService


@pytest.fixture(scope="module")
def sim_scope():
    scope = control.microscope.Microscope.build_from_global_config(True)
    yield scope
    scope.close()


@pytest.fixture()
def asgi_client(sim_scope):
    service = SquidCoreService(microscope=sim_scope, simulation=True)
    app = create_app(service, ServiceConfig())
    transport = httpx.ASGITransport(app=app)
    return bridge.make_client(transport=transport)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_tool_list_is_static_and_curated():
    tools = bridge.tool_definitions()
    names = {t.name for t in tools}
    assert "microscope_ping" in names
    assert "microscope_move_to" in names
    assert "microscope_run_acquisition_from_yaml" in names
    assert "microscope_python_exec" in names
    move = next(t for t in tools if t.name == "microscope_move_to")
    assert "x_mm" in move.inputSchema["properties"]  # legacy arg names preserved


def test_dispatch_ping_and_position(asgi_client):
    result = _run(bridge.dispatch(asgi_client, "microscope_ping", {}))
    assert result["alive"] is True
    pos = _run(bridge.dispatch(asgi_client, "microscope_get_position", {}))
    assert set(pos) == {"x_mm", "y_mm", "z_mm"}


def test_dispatch_move_maps_legacy_args(asgi_client):
    result = _run(bridge.dispatch(asgi_client, "microscope_move_to", {"x_mm": 10.0, "y_mm": 10.0}))
    assert result["position"]["x_mm"] == pytest.approx(10.0, abs=0.01)


def test_dispatch_surfaces_canonical_fault(asgi_client):
    result = _run(bridge.dispatch(asgi_client, "microscope_set_channel", {"channel_name": "No Such"}))
    assert result["error"]["category"] == "CONFIG"
    assert result["error"]["code"] == 3001


def test_unknown_tool_rejected(asgi_client):
    with pytest.raises(ValueError):
        _run(bridge.dispatch(asgi_client, "microscope_nonexistent", {}))


# ---- URS delta (API-COMPAT-002, binding, added 2026-07-02) -----------------
#
# Legacy TCP-era tool names not covered above, mapped onto the new REST API,
# plus four brand-new tools. `microscope_set_display_plate_view` is
# intentionally NOT present: the underlying `control._def.DISPLAY_PLATE_VIEW`
# flag no longer exists on master (plate view was unified into the mosaic
# view / UnifiedMosaicWidget, governed solely by `display_mosaic_view`), so
# there is nothing left for that tool to control.


def test_tool_list_includes_urs_delta_and_skips_display_plate_view():
    tools = bridge.tool_definitions()
    names = {t.name for t in tools}
    for name in (
        "microscope_run_acquisition",
        "microscope_set_performance_mode",
        "microscope_get_performance_mode",
        "microscope_get_view_settings",
        "microscope_set_view_settings",
        "microscope_set_save_downsampled_images",
        "microscope_set_display_mosaic_view",
        "microscope_get_methods",
        "microscope_run_method",
        "microscope_autofocus_status",
        "microscope_store_af_reference",
    ):
        assert name in names, name
    assert "microscope_set_display_plate_view" not in names


def test_tool_list_includes_acquire_laser_af_image():
    # URS API-COMPAT-002 follow-up: the legacy TCP `_cmd_acquire_laser_af_image`
    # command was dropped when the bridge was rewritten; it must be restored
    # with its original argument names.
    tools = bridge.tool_definitions()
    tool = next(t for t in tools if t.name == "microscope_acquire_laser_af_image")
    assert set(tool.inputSchema["properties"]) == {"save_path", "use_last_frame"}


def test_dispatch_run_acquisition_maps_legacy_grid_args(asgi_client, sim_scope):
    # asgi_client's service has no MultiPointController/ScanCoordinates attached
    # (see the fixture above), so a body that's well-formed enough to pass
    # AcquisitionRequest/GridSpec validation reaches the service layer and
    # fails there with CONFIG_CAPABILITY_MISSING -- NOT a PROTOCOL schema
    # violation. That distinguishes "legacy args mapped into a valid GridSpec"
    # from "mapped into garbage that 422s at the FastAPI boundary".
    objective = sim_scope.objective_store.current_objective
    channel = sim_scope.live_controller.get_channels(objective)[0].name
    result = _run(
        bridge.dispatch(
            asgi_client,
            "microscope_run_acquisition",
            {
                "wells": "A1",
                "channels": [channel],
                "nx": 1,
                "ny": 1,
                "experiment_id": "grid_test",
                "base_path": "/tmp",
            },
        )
    )
    assert result["error"]["category"] == "CONFIG"
    assert result["error"]["code"] == 3003


def test_dispatch_run_method_maps_legacy_args(asgi_client):
    result = _run(
        bridge.dispatch(
            asgi_client,
            "microscope_run_method",
            {"method": "some_method", "wells": "A1", "base_path": "/tmp", "operator": "tester"},
        )
    )
    assert result["error"]["category"] == "CONFIG"
    assert result["error"]["code"] == 3003


def test_dispatch_performance_mode_headless(asgi_client):
    got = _run(bridge.dispatch(asgi_client, "microscope_get_performance_mode", {}))
    assert got["performance_mode"] is None  # headless service -> no GUI attached

    result = _run(bridge.dispatch(asgi_client, "microscope_set_performance_mode", {"enabled": True}))
    assert result["error"]["category"] == "CONFIG"
    assert result["error"]["code"] == 3003


def test_dispatch_view_settings_roundtrip(asgi_client):
    import control._def as _def

    original_wells = _def.SAVE_DOWNSAMPLED_WELL_IMAGES
    original_mosaic = _def.USE_NAPARI_FOR_MOSAIC_DISPLAY
    try:
        got = _run(bridge.dispatch(asgi_client, "microscope_get_view_settings", {}))
        assert "save_downsampled_well_images" in got
        assert "display_mosaic_view" in got

        flipped_wells = _run(
            bridge.dispatch(asgi_client, "microscope_set_save_downsampled_images", {"enabled": not original_wells})
        )
        assert flipped_wells["save_downsampled_well_images"] == (not original_wells)

        flipped_mosaic = _run(
            bridge.dispatch(asgi_client, "microscope_set_display_mosaic_view", {"enabled": not original_mosaic})
        )
        assert flipped_mosaic["display_mosaic_view"] == (not original_mosaic)

        restored = _run(
            bridge.dispatch(
                asgi_client,
                "microscope_set_view_settings",
                {"save_downsampled_well_images": original_wells, "display_mosaic_view": original_mosaic},
            )
        )
        assert restored["save_downsampled_well_images"] == original_wells
        assert restored["display_mosaic_view"] == original_mosaic
    finally:
        _def.SAVE_DOWNSAMPLED_WELL_IMAGES = original_wells
        _def.USE_NAPARI_FOR_MOSAIC_DISPLAY = original_mosaic


def test_dispatch_get_methods_without_registry(asgi_client):
    result = _run(bridge.dispatch(asgi_client, "microscope_get_methods", {}))
    assert result["error"]["category"] == "CONFIG"
    assert result["error"]["code"] == 3003


def test_dispatch_autofocus_status_and_store_reference(asgi_client):
    status = _run(bridge.dispatch(asgi_client, "microscope_autofocus_status", {}))
    assert set(status) >= {"available", "initialized", "reference_set", "readiness"}

    # The default simulated scope has no reflection-AF hardware, so this must
    # surface a canonical fault rather than crash.
    result = _run(bridge.dispatch(asgi_client, "microscope_store_af_reference", {}))
    assert result["error"]["category"] in ("CONFIG", "AUTOFOCUS")


def test_dispatch_acquire_laser_af_image_not_ready(asgi_client):
    # Same sim scope as test_dispatch_autofocus_status_and_store_reference:
    # AF hardware is configured but no frame has been streamed, so the
    # default use_last_frame=True request must surface a canonical fault
    # (AUTOFOCUS_NOT_READY here, or CONFIG_CAPABILITY_MISSING on a config
    # with no AF hardware at all) rather than being dropped/unmapped.
    result = _run(bridge.dispatch(asgi_client, "microscope_acquire_laser_af_image", {}))
    assert result["error"]["category"] in ("CONFIG", "AUTOFOCUS")
