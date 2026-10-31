# Zmotion controller library

Vendor library for the Zmotion ZMC motion controller, loaded with ctypes by `control/zmotion_spike.py`
(`_find_library` / `_load_library`).

| File | What | Version | SHA256 |
|---|---|---|---|
| `linux/x64/libzmotion.so` | Linux x86-64 shared library, from Zmotion's "Linux C++ (64-bit)" SDK package | no version string in the binary; package file dated 2022-09-26, built with GCC 4.8.4 (Ubuntu 14.04), ELF BuildID `f919f37701975efd7ec91e32440470a39df31cbc` | `51645957ed04ce29d842d920eb1e127fac3c06fe8ebca4d1510b68e4ad336c4a` |
| `windows/x64/zmotion.dll` | Windows x64 DLL, from the instrument vendor's package (the one used on the bench PC) | FileVersion 2.0.5.24 ("zmcdll") | `2dd98fb580f56c911eacf2839b1f81c635e1127537150278f1be529215f4776e` |
| `zmotion.h` | the vendor's C header from the Linux SDK package, for reference only (CRLF, comments in Chinese) | same package as the `.so` | `98e5c3becc12e52fc2a9c88caafaa9507d56c5917dc2b348ba72112ecc4227eb` |

Only the 64-bit builds are carried. The SDK's optional helper layer (`zmcaux.h` / `zmcaux.cpp`) is not
needed: the loader calls the base `ZMC_*` functions directly.

## Linux requirements

- `bool ZMC_LinuxLibInit();` must be called once, before any other library call. It returns
  **true on success** (the library returns `ZSecure_Init() == 0`). The loader does this once per process
  and raises if it returns false or is missing.
- glibc >= 2.17 (the highest symbol version the library needs is `GLIBC_2.17`, `clock_gettime`). Its only
  `NEEDED` entries are `libc.so.6` and `libm.so.6`. `pthread_create` / `pthread_join` / `pthread_cancel`
  are unversioned references: on glibc >= 2.34 they are in libc, and on older glibc the Python
  interpreter has already loaded libpthread, so they resolve inside Python either way.
- On Linux `__stdcall` is `#define`d empty in `zmotion.h`, so the functions are plain C calls (`ctypes.CDLL`).
  On Windows x64 there is only one calling convention, so `WinDLL` is equivalent.

## Overriding the path

`ZMOTION_LIB=/path/to/libzmotion.so` (or `zmotion.dll`) takes precedence over the files here. Without it
the loader tries this folder, then `libzmotion.so` / `zmotion.dll` next to `main_hcs.py`.

`tests/control/test_zmotion_library.py` loads the file for the running platform (Linux x86-64 or
Windows x64; skipped elsewhere), checks every function the loader binds is exported, and on Linux runs
`ZMC_LinuxLibInit`. It never opens a connection.
