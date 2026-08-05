import pytest

# The DCAM SDK shared library is loaded at import time (control/dcamapi4.py).
# Skip the whole module where it is not installed (e.g. CI without the SDK).
try:
    from control.camera_hamamatsu import HamamatsuCamera
except OSError as e:  # libdcamapi.so / dcamapi.dll not present
    pytest.skip(f"DCAM SDK not available: {e}", allow_module_level=True)


def test_normalize_sensor_mode_name():
    assert HamamatsuCamera._normalize_sensor_mode_name("ULTRA QUIET") == "ultra_quiet"
    assert HamamatsuCamera._normalize_sensor_mode_name(" Standard ") == "standard"
    assert HamamatsuCamera._normalize_sensor_mode_name("FAST") == "fast"
