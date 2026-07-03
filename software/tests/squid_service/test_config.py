import pytest
from pydantic import ValidationError

from squid_service.config import ServiceConfig
from squid_service.models import AcquisitionRequest, ExposureRequest, MoveRequest


def test_defaults_are_loopback_no_auth():
    cfg = ServiceConfig()
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 8060
    assert cfg.auth_enabled is False


def test_non_loopback_requires_auth_token():
    with pytest.raises(ValidationError):
        ServiceConfig(host="0.0.0.0")
    with pytest.raises(ValidationError):
        ServiceConfig(host="0.0.0.0", auth_enabled=True, auth_token="")
    cfg = ServiceConfig(host="0.0.0.0", auth_enabled=True, auth_token="s3cret")
    assert cfg.auth_enabled


def test_from_def_reads_globals(monkeypatch):
    import control._def

    monkeypatch.setattr(control._def, "CORE_SERVICE_HOST", "127.0.0.1", raising=False)
    monkeypatch.setattr(control._def, "CORE_SERVICE_PORT", 5099, raising=False)
    monkeypatch.setattr(control._def, "CORE_SERVICE_AUTH_ENABLED", False, raising=False)
    monkeypatch.setattr(control._def, "CORE_SERVICE_AUTH_TOKEN", "", raising=False)
    cfg = ServiceConfig.from_def()
    assert cfg.port == 5099


def test_move_request_rejects_extras():
    with pytest.raises(ValidationError):
        MoveRequest(x_mm=1.0)  # old TCP field name must NOT validate silently
    req = MoveRequest(mode="relative", x=1.5)
    assert req.block_until_complete is True


def test_exposure_bounds():
    with pytest.raises(ValidationError):
        ExposureRequest(exposure_ms=0)
    with pytest.raises(ValidationError):
        ExposureRequest(exposure_ms=20000)


def test_acquisition_request_shape():
    req = AcquisitionRequest(yaml_path="/tmp/a.yaml", overrides={"wells": "A1:B2"})
    assert req.overrides.wells == "A1:B2"
    assert req.overrides.output_path is None
    assert req.overrides.sample_format is None


def test_acquisition_request_requires_exactly_one_source():
    with pytest.raises(ValidationError):
        AcquisitionRequest()  # none of method/yaml_path/grid
    with pytest.raises(ValidationError):
        AcquisitionRequest(method="m1", yaml_path="/tmp/a.yaml")  # two sources
    req = AcquisitionRequest(method="spheroid_4ch_20x", autofocus={"reflection": True})
    assert req.method == "spheroid_4ch_20x"
    assert req.autofocus.reflection is True and req.autofocus.contrast is None


def test_grid_spec_validation():
    req = AcquisitionRequest(grid={"wells": "A1:B2", "channels": ["BF LED matrix full"]})
    assert req.grid.nx == 2 and req.grid.wellplate_format == "96 well plate"
    with pytest.raises(ValidationError):
        AcquisitionRequest(grid={"wells": "A1", "channels": []})  # empty channels
