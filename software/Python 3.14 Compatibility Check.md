# Python 3.14 Compatibility -- Squid Dependencies

## Supported (confirmed)
| Package | Notes |
|---------|-------|
| numpy | Wheels for 3.14 since v2.3+ |
| scipy | Supported |
| pandas | Supported |
| pydantic (v2) | Supported -- but **pydantic v1** (used by old napari) is **NOT** |
| Pillow | Supported |
| matplotlib | Supported |
| PyYAML | Supported |
| filelock | Supported |
| platformdirs | Supported |
| scikit-image | Wheels for 3.14 since v0.26 |
| napari (latest) | Supported -- requires PyQt6, not PyQt5 |
| opencv-python-headless | Uses stable ABI (cp37-abi3), works |
| opencv-contrib-python-headless | Same stable ABI, works |
| torch (PyTorch) | Supported since ~2.13 |
| dask | Active releases in 2026 |
| zarr | Supported |
| tifffile | Supported |
| imageio | Supported |
| pyqtgraph | Pure Python, likely works |
| qtpy | Pure Python abstraction layer, works |

## Blocked / Incompatible
| Package | Issue |
|---------|-------|
| **PyQt5** | **No Python 3.14 support -- this is the main blocker** |
| **pydantic v1** | Explicitly incompatible with 3.14 (the `__slots__` error) |

## Uncertain / Likely OK but unconfirmed
| Package | Notes |
|---------|-------|
| pyserial | Pure Python, last release 2020 -- probably works but untested |
| pyvisa | Likely works |
| hidapi | Cython extension -- no 3.14 wheels confirmed |
| GitPython | Marked as NOT supporting 3.14 on pyreadiness.org |
| aicsimageio | Unclear, niche package |
| basicpy | Unclear |
| ome-zarr | Unclear |
| pydantic-xml | Likely works if pydantic v2 is used |
| pyreadline3 | Windows-specific, unclear |
| qtconsole | Depends on Qt backend |
| lxml / lxml_html_clean | C extension -- needs checking |
| mcp | Unclear |
| Hardware drivers (PySpin, pyAndorSDK3, ids_peak, pyvcam, pm16) | Vendor-specific, unlikely to have 3.14 builds yet |

## Bottom Line

The **two hard blockers** are **PyQt5** and **pydantic v1** (via old napari). Migrating to Python 3.14 would require:
1. Switching from PyQt5 to PyQt6 (non-trivial API changes)
2. Upgrading napari to a version that uses pydantic v2
3. Verifying all hardware vendor SDKs have 3.14 builds

**Recommendation: Stay on Python 3.10** for production use. It's the sweet spot where everything works.

## Sources
- [Python 3.14 Readiness](https://pyreadiness.org/3.14/)
- [NumPy 2.3.0 Release Notes](https://numpy.org/devdocs/release/2.3.0-notes.html)
- [PyQt5 on PyPI](https://pypi.org/project/PyQt5/)
- [napari Installation](https://napari.org/stable/getting_started/installation.html)
- [PyTorch torch.compile Python 3.14 support](https://dev-discuss.pytorch.org/t/torch-compile-support-for-python-3-14-completed/3276)
- [Pydantic v1 Python 3.14 issue](https://github.com/pydantic/pydantic/issues/12618)
- [Anaconda Python 3.14 overview](https://www.anaconda.com/blog/python-3-14-what-data-scientists-developers-need-know)
