# QMetaObject.invokeMethod Error Investigation

## Issue

Error message during TCP-initiated acquisitions:
```
QMetaObject.invokeMethod failed to invoke set_acquisition_running_state on WellplateMultiPointWidget
```

## Location

`control/microscope_control_server.py:996-1008`

## Observation

The method IS successfully called (confirmed by debug log inside the slot), but `invokeMethod` returns `False`.

**Timeline from logs:**
```
13:41:48.691 - WellplateMultiPointWidget - DEBUG - set_acquisition_running_state: is_running=True, nz=2, delta_z_um=1.5
13:41:48.695 - MicroscopeControlServer - ERROR - QMetaObject.invokeMethod failed to invoke...
```

The slot executes 4ms BEFORE the error is logged, proving the method is being called.

## Root Cause

PyQt type system quirk. When using `Q_ARG(int, yaml_data.nz)`, if `yaml_data.nz` is not exactly a Python `int` (e.g., could be `numpy.int64` or similar), Qt's type matching fails but the method still executes through Python's binding layer.

## Impact

**Low** - The acquisition completes successfully despite the error. This is a cosmetic/logging issue only.

## Proposed Fix

Explicitly cast values to Python native types:

```python
success = QMetaObject.invokeMethod(
    widget,
    "set_acquisition_running_state",
    Qt.BlockingQueuedConnection,
    Q_ARG(bool, bool(is_running)),
    Q_ARG(int, int(yaml_data.nz)),
    Q_ARG(float, float(yaml_data.delta_z_um)),
)
```

## Files to Modify

- `control/microscope_control_server.py` (line ~1000)

## Testing

Run a TCP-initiated acquisition and verify no error is logged:
```bash
python scripts/run_acquisition.py --yaml <path> --simulation --wait --verbose
grep "QMetaObject.invokeMethod failed" /Users/hongquan/Library/Logs/squid/main_hcs.log
```
