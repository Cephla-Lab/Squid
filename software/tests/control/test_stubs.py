"""
Compatibility shim for legacy test imports.

Re-exports the actual stubs from tests.unit.control.test_stubs so both
`tests.control.test_stubs` and the original path work.
"""

from tests.unit.control.test_stubs import *  # noqa: F401,F403
