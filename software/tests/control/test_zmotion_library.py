"""Hardware-free checks of the vendored Zmotion controller libraries.

Loads the vendored library for the running platform with ctypes, checks that every function the
loader in control/zmotion_spike.py binds is exported, and (Linux) runs the vendor's required
ZMC_LinuxLibInit. No connection is ever opened. The module does not import control.zmotion_spike
(which pulls in control._def): the bound names are read from its source.
"""

import ast
import ctypes
import pathlib
import platform
import sys

import pytest

SOFTWARE_DIR = pathlib.Path(__file__).resolve().parents[2]
ZMOTION_DIR = SOFTWARE_DIR / "drivers and libraries" / "zmotion"
LINUX_LIB = ZMOTION_DIR / "linux" / "x64" / "libzmotion.so"
WINDOWS_LIB = ZMOTION_DIR / "windows" / "x64" / "zmotion.dll"
LOADER_SOURCE = SOFTWARE_DIR / "control" / "zmotion_spike.py"

IS_64BIT_PYTHON = ctypes.sizeof(ctypes.c_void_p) == 8
IS_LINUX_X86_64 = sys.platform.startswith("linux") and platform.machine() == "x86_64" and IS_64BIT_PYTHON
IS_WINDOWS_X64 = sys.platform == "win32" and IS_64BIT_PYTHON


def _bound_symbols():
    """The keys of _PROTOTYPES in control/zmotion_spike.py, read without importing it."""
    tree = ast.parse(LOADER_SOURCE.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_PROTOTYPES" for t in node.targets
        ):
            names = [ast.literal_eval(k) for k in node.value.keys]
            assert "ZMC_OpenEth" in names and "ZMC_DirectCommand" in names, names
            return names
    raise AssertionError(f"_PROTOTYPES not found in {LOADER_SOURCE}")


@pytest.mark.skipif(not IS_LINUX_X86_64, reason="vendored libzmotion.so is a Linux x86-64 build")
def test_linux_library_loads_exports_bound_symbols_and_initialises():
    assert LINUX_LIB.is_file(), f"missing {LINUX_LIB}"
    lib = ctypes.CDLL(str(LINUX_LIB))
    missing = [name for name in _bound_symbols() + ["ZMC_LinuxLibInit"] if not hasattr(lib, name)]
    assert not missing, f"{LINUX_LIB} does not export {missing}"

    # zmotion.h: `bool ZMC_LinuxLibInit();` returns true on success (it is ZSecure_Init() == 0).
    init = lib.ZMC_LinuxLibInit
    init.argtypes = []
    init.restype = ctypes.c_bool
    assert init() is True


@pytest.mark.skipif(not IS_WINDOWS_X64, reason="vendored zmotion.dll is a Windows x64 build")
def test_windows_library_loads_and_exports_bound_symbols():
    assert WINDOWS_LIB.is_file(), f"missing {WINDOWS_LIB}"
    lib = ctypes.WinDLL(str(WINDOWS_LIB))
    missing = [name for name in _bound_symbols() if not hasattr(lib, name)]
    assert not missing, f"{WINDOWS_LIB} does not export {missing}"
