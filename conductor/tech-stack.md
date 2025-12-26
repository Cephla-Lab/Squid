# Tech Stack - Squid

## Programming Language
- **Python:** 3.9+ (Primary software language)
- **C++/Arduino:** For Teensy-based microcontroller firmware

## Frontend/GUI Framework
- **PyQt5:** Main GUI framework (using `qtpy` for abstraction)
- **pyqtgraph:** High-performance data and image visualization
- **napari:** Advanced multi-dimensional image viewer and analysis platform

## Hardware Communication
- **pyserial:** For communication with microcontrollers and other serial devices
- **pyvisa:** For controlling VISA-compatible instruments
- **hidapi:** For interacting with USB HID devices

## Data Processing & Analysis
- **numpy:** Fundamental package for scientific computing
- **scipy:** Library for scientific and technical computing
- **pandas:** Data manipulation and analysis
- **scikit-image:** Image processing for Python
- **opencv:** Open Source Computer Vision Library (headless version)
- **dask_image:** Distributed image processing

## Image File Formats
- **tifffile:** For reading and writing TIFF files
- **ome_zarr:** Support for OME-NGFF (Zarr) format
- **aicsimageio:** Consistent API for various biological image formats

## Build & Tooling
- **setuptools:** Project packaging and distribution
- **pytest:** Testing framework (with `pytest-qt` and `pytest-xvfb`)
- **black:** Code formatting
- **pydantic_xml:** XML serialization/deserialization for configurations
