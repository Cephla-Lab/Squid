"""
Control package init.

Provides a compatibility alias for `control.lighting` expected by legacy tests.
"""

try:
    import control.peripherals.lighting as lighting  # noqa: F401
except Exception:
    import types as _types

    lighting = _types.SimpleNamespace()

    class _DummyEnum:
        def __init__(self, *args, **kwargs):
            pass

    class _DummyIlluminationController:
        def __init__(self, *args, **kwargs):
            pass

    lighting.IlluminationController = _DummyIlluminationController
    lighting.IntensityControlMode = _DummyEnum
    lighting.ShutterControlMode = _DummyEnum
    lighting.LightSourceType = _DummyEnum
