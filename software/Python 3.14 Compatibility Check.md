# Python 3.14 Compatibility -- Squid Dependencies

## Supported (confirmed)
| Package | Notes |
|---------|-------|
| numpy | Wheels for 3.14 since v2.3+ |
| scipy | Supported |
| pandas | Supported |
| pydantic (v2) | Supported. (pydantic v1 is not, but nothing in our stack needs it: napari 0.5.x already ran on pydantic 2.x) |
| Pillow | Supported |
| matplotlib | Supported |
| PyYAML | Supported |
| filelock | Supported |
| platformdirs | Supported |
| scikit-image | Wheels for 3.14 since v0.26 |
| napari (latest) | Supported -- ships both `[pyqt5]` and `[pyqt6]` extras (0.7.x and 0.9.x); we use PyQt6 |
| opencv-python-headless | Uses stable ABI (cp37-abi3), works |
| opencv-contrib-python-headless | Same stable ABI, works |
| torch (PyTorch) | Supported since ~2.13 |
| dask | Active releases in 2026 |
| zarr | Supported |
| tifffile | Supported |
| imageio | Supported |
| pyqtgraph | Pure Python, likely works |
| qtpy | Pure Python abstraction layer, works |
| PyQt5 | Installs on 3.14: PyQt5 5.15.11 wheels are `cp38-abi3` and PyQt5-sip 12.19.0 ships cp314 wheels. Not a blocker -- we moved to PyQt6 for other reasons |

## Blocked / Incompatible
None confirmed in the pip stack. Earlier drafts listed PyQt5 and pydantic v1 here; both were wrong
(see the Supported table). The likely blockers are the old pins pulled in by unmaintained packages:
aicsimageio 4.14 pins `tifffile<2023.3.15`, `zarr<2.16`, `lxml<5`; basicpy pins `scipy<1.13`
(peng-lab/BaSiCPy#173). Whether those old releases have 3.14 wheels has not been checked.

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

No confirmed hard blocker in the pip stack. The PyQt6 + napari 0.7 migration in this PR removes the
Qt-side uncertainty; what remains before a 3.14 move is:
1. Confirming 3.14 wheels (or dropping) the pinned-back packages aicsimageio and basicpy pull in
2. Verifying all hardware vendor SDKs have 3.14 builds

**Recommendation: Stay on Python 3.10** for production use. It's the sweet spot where everything works.

## Sources
- [Python 3.14 Readiness](https://pyreadiness.org/3.14/)
- [NumPy 2.3.0 Release Notes](https://numpy.org/devdocs/release/2.3.0-notes.html)
- [PyQt5 on PyPI](https://pypi.org/project/PyQt5/)
- [napari Installation](https://napari.org/stable/getting_started/installation.html)
- [PyTorch torch.compile Python 3.14 support](https://dev-discuss.pytorch.org/t/torch-compile-support-for-python-3-14-completed/3276)
- [Pydantic v1 Python 3.14 issue](https://github.com/pydantic/pydantic/issues/12618)
- [Anaconda Python 3.14 overview](https://www.anaconda.com/blog/python-3-14-what-data-scientists-developers-need-know)
