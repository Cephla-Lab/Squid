"""Unit tests for the named acquisition-method registry (URS API-METH-001..005).

Pure Python: no hardware, no Microscope. Uses tmp_path for the methods dir.
"""

import pytest
import yaml

from squid_service.faults import FaultCategory, FaultError
from squid_service.methods import MethodRegistry


def _valid_config(channel="BF LED matrix full"):
    return {
        "acquisition": {"widget_type": "wellplate"},
        "sample": {"wellplate_format": "96 well plate"},
        "z_stack": {"nz": 1, "delta_z_mm": 0.001},
        "time_series": {"nt": 1, "delta_t_s": 0.0},
        "channels": [{"name": channel}],
        "autofocus": {"contrast_af": False, "laser_af": False},
        "wellplate_scan": {
            "scan_size_mm": 0.5,
            "overlap_percent": 10,
            "regions": [{"name": "A1", "center_mm": [14.3, 11.36, 0.5], "shape": "Square"}],
        },
    }


def test_save_list_get_roundtrip(tmp_path):
    reg = MethodRegistry(tmp_path)
    reg.save("scan_a", _valid_config(), overwrite=False)

    listed = reg.list()
    assert len(listed) == 1
    summary = listed[0]
    assert summary["name"] == "scan_a"
    assert summary["widget_type"] == "wellplate"
    assert summary["nz"] == 1
    assert summary["nt"] == 1
    assert summary["wellplate_format"] == "96 well plate"

    got = reg.get("scan_a")
    assert got["name"] == "scan_a"
    assert got["config"]["acquisition"]["widget_type"] == "wellplate"


def test_save_existing_without_overwrite_faults(tmp_path):
    reg = MethodRegistry(tmp_path)
    reg.save("dup", _valid_config(), overwrite=False)
    with pytest.raises(FaultError) as exc:
        reg.save("dup", _valid_config(), overwrite=False)
    assert exc.value.fault.category == FaultCategory.INVALID_PARAM


def test_overwrite_missing_faults_unknown(tmp_path):
    reg = MethodRegistry(tmp_path)
    with pytest.raises(FaultError) as exc:
        reg.save("ghost", _valid_config(), overwrite=True)
    assert exc.value.fault.category == FaultCategory.PROTOCOL
    assert exc.value.fault.code == 1001  # PROTOCOL_UNKNOWN_RESOURCE


def test_update_existing_with_overwrite(tmp_path):
    reg = MethodRegistry(tmp_path)
    reg.save("m", _valid_config(), overwrite=False)
    updated = _valid_config()
    updated["time_series"]["nt"] = 3
    reg.save("m", updated, overwrite=True)
    assert reg.get("m")["config"]["time_series"]["nt"] == 3


def test_delete_unknown_faults(tmp_path):
    reg = MethodRegistry(tmp_path)
    with pytest.raises(FaultError) as exc:
        reg.delete("nope")
    assert exc.value.fault.category == FaultCategory.PROTOCOL
    assert exc.value.fault.code == 1001


def test_delete_roundtrip(tmp_path):
    reg = MethodRegistry(tmp_path)
    reg.save("gone", _valid_config(), overwrite=False)
    assert reg.exists("gone")
    reg.delete("gone")
    assert not reg.exists("gone")


@pytest.mark.parametrize("bad_name", ["../evil", "", "a/b", "with space", ".hidden"])
def test_invalid_name_faults(tmp_path, bad_name):
    reg = MethodRegistry(tmp_path)
    with pytest.raises(FaultError) as exc:
        reg.path_for(bad_name)
    assert exc.value.fault.category == FaultCategory.INVALID_PARAM
    assert reg.exists(bad_name) is False


def test_save_invalid_name_faults(tmp_path):
    reg = MethodRegistry(tmp_path)
    with pytest.raises(FaultError) as exc:
        reg.save("../evil", _valid_config(), overwrite=False)
    assert exc.value.fault.category == FaultCategory.INVALID_PARAM


def test_save_invalid_config_faults(tmp_path):
    reg = MethodRegistry(tmp_path)
    bad = {"acquisition": {"widget_type": "not_a_real_type"}}
    with pytest.raises(FaultError) as exc:
        reg.save("bad_cfg", bad, overwrite=False)
    assert exc.value.fault.category == FaultCategory.INVALID_PARAM


def test_list_carries_error_for_unparseable(tmp_path):
    reg = MethodRegistry(tmp_path)
    reg.save("good", _valid_config(), overwrite=False)
    (tmp_path / "broken.yaml").write_text("{ this is not: valid: yaml :::")

    listed = {s["name"]: s for s in reg.list()}
    assert "good" in listed and "error" not in listed["good"]
    assert "broken" in listed and "error" in listed["broken"]


def test_list_empty_when_dir_missing(tmp_path):
    reg = MethodRegistry(tmp_path / "does_not_exist")
    assert reg.list() == []


def test_get_unknown_faults(tmp_path):
    reg = MethodRegistry(tmp_path)
    with pytest.raises(FaultError) as exc:
        reg.get("missing")
    assert exc.value.fault.code == 1001


def test_saved_file_is_yaml(tmp_path):
    reg = MethodRegistry(tmp_path)
    reg.save("y", _valid_config(), overwrite=False)
    path = tmp_path / "y.yaml"
    assert path.exists()
    loaded = yaml.safe_load(path.read_text())
    assert loaded["acquisition"]["widget_type"] == "wellplate"
