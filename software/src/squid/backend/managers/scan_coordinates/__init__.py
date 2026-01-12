"""Scan coordinates package for managing multi-point acquisitions.

This package provides:
- ScanCoordinates: Main coordinator class for scan region management
- geometry: Pure geometry functions (point_in_polygon, point_in_circle, etc.)
- grid: Pure grid generation functions (coming soon)
- wellplate: Wellplate coordinate helpers (coming soon)

Re-exports for backwards compatibility:
    from squid.backend.managers.scan_coordinates import ScanCoordinates
"""

from squid.backend.managers.scan_coordinates.scan_coordinates import (
    AddScanCoordinateRegion,
    ClearedScanCoordinates,
    FovCenter,
    RemovedScanCoordinateRegion,
    ScanCoordinates,
    ScanCoordinatesSiLA2,
    ScanCoordinatesUpdate,
)

__all__ = [
    "ScanCoordinates",
    "ScanCoordinatesSiLA2",
    "ScanCoordinatesUpdate",
    "AddScanCoordinateRegion",
    "ClearedScanCoordinates",
    "RemovedScanCoordinateRegion",
    "FovCenter",
]
