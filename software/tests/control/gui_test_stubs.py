"""
Compatibility shim for legacy test imports.

Re-exports GUI-related stubs from tests.integration.control.gui_test_stubs
so `tests.control.gui_test_stubs` continues to work.
"""

from tests.integration.control.gui_test_stubs import *  # noqa: F401,F403

# Provide a minimal alias for the legacy control.lighting import used in tests.
import squid.backend.drivers.lighting as lighting  # type: ignore
import types as _types

# Insert into control namespace if missing for test compatibility
try:
    import control as _control
except ModuleNotFoundError:
    import sys as _sys

    _control = _types.ModuleType("control")
    _sys.modules["control"] = _control

if not hasattr(_control, "lighting"):
    _control.lighting = lighting  # type: ignore[attr-defined]
