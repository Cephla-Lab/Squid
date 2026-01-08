# MERFISH Tissue Planning Workflow

## Summary

Implement a tissue planning workflow that enables:
1. Low-mag mosaic scan of slide
2. Polygon drawing around tissue sections
3. Objective switch → automatic tile recalculation at new FOV size
4. Save session (mosaic + polygons + coordinates) to directory
5. Load coordinates into fluidics_multipoint for multi-round acquisition

## File Format

```
session_name/
├── mosaic.tiff           # Downsampled multi-channel composite
├── regions.json          # Polygon vertices in stage mm
├── coordinates.csv       # FOV coordinates (compatible with fluidics load)
└── session_metadata.json # Objective, overlap, pixel size, extents
```

---

## Implementation Plan

### Phase 1: Events and Data Structures

**File: `software/src/squid/core/events.py`**

Add these events after line ~1875 (near LoadScanCoordinatesCommand):

```python
@dataclass(frozen=True)
class SaveTissuePlanningSessionCommand(Event):
    """Save current mosaic + polygons + coordinates to directory."""
    session_path: str

@dataclass(frozen=True)
class LoadTissuePlanningSessionCommand(Event):
    """Load tissue planning session from directory."""
    session_path: str

@dataclass(frozen=True)
class TissuePlanningSessionSaved(Event):
    """Notification of save result."""
    session_path: str
    success: bool
    error: Optional[str] = None

@dataclass(frozen=True)
class TissuePlanningSessionLoaded(Event):
    """Notification of load result."""
    session_path: str
    success: bool
    total_regions: int = 0
    total_fovs: int = 0
    mosaic_loaded: bool = False
    error: Optional[str] = None
```

---

### Phase 2: IO Module

**New file: `software/src/squid/backend/io/tissue_session_io.py`**

Handles serialization of session data:

```python
"""Tissue planning session save/load."""
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import json
import csv
import numpy as np
import tifffile

@dataclass
class PolygonRegion:
    region_id: str
    vertices_mm: List[Tuple[float, float]]

@dataclass
class TissueSessionData:
    mosaic_pixel_size_mm: float
    mosaic_extents: Tuple[float, float, float, float]  # min_y, max_y, min_x, max_x
    mosaic_top_left: Tuple[float, float]  # y_mm, x_mm
    polygon_regions: List[PolygonRegion]
    planning_objective: str
    acquisition_objective: Optional[str]
    fov_width_mm: float
    fov_height_mm: float
    overlap_percent: float

def save_session(
    session_path: Path,
    data: TissueSessionData,
    mosaics: Dict[str, np.ndarray],
    coordinates: Dict[str, List[Tuple[float, float, float]]]
) -> None:
    """Save complete session to directory."""
    session_path.mkdir(parents=True, exist_ok=True)

    # 1. Save mosaic as multi-page TIFF
    _save_mosaic(session_path / "mosaic.tiff", mosaics, data)

    # 2. Save polygon regions as JSON
    _save_regions(session_path / "regions.json", data.polygon_regions)

    # 3. Save coordinates as CSV (fluidics-compatible)
    _save_coordinates(session_path / "coordinates.csv", coordinates)

    # 4. Save session metadata
    _save_metadata(session_path / "session_metadata.json", data)

def load_session(session_path: Path) -> Tuple[TissueSessionData, Dict[str, np.ndarray], Dict]:
    """Load session from directory."""
    metadata = _load_metadata(session_path / "session_metadata.json")
    regions = _load_regions(session_path / "regions.json")
    mosaics = _load_mosaic(session_path / "mosaic.tiff")
    coordinates = _load_coordinates(session_path / "coordinates.csv")

    data = TissueSessionData(
        mosaic_pixel_size_mm=metadata["mosaic_pixel_size_mm"],
        mosaic_extents=tuple(metadata["mosaic_extents"]),
        mosaic_top_left=tuple(metadata["mosaic_top_left"]),
        polygon_regions=regions,
        planning_objective=metadata["planning_objective"],
        acquisition_objective=metadata.get("acquisition_objective"),
        fov_width_mm=metadata["fov_width_mm"],
        fov_height_mm=metadata["fov_height_mm"],
        overlap_percent=metadata["overlap_percent"],
    )
    return data, mosaics, coordinates
```

Key implementation details:
- `_save_mosaic`: Use tifffile to write multi-page TIFF with channel names in metadata
- `_save_coordinates`: CSV with columns `region,x (mm),y (mm),z (mm)` - matches existing fluidics load format
- `_load_coordinates`: Parse to `Dict[str, List[Tuple]]` for `LoadScanCoordinatesCommand`

---

### Phase 3: Session Manager

**New file: `software/src/squid/backend/managers/tissue_planning_session.py`**

Manages session state and coordinates recalculation:

```python
class TissuePlanningSession:
    """Manages tissue planning session state."""

    def __init__(
        self,
        event_bus: EventBus,
        scan_coordinates: ScanCoordinates,
        objective_store: ObjectiveStore,
        camera: AbstractCamera,
    ):
        self._event_bus = event_bus
        self._scan_coordinates = scan_coordinates
        self._objective_store = objective_store
        self._camera = camera

        self._polygon_regions: List[PolygonRegion] = []
        self._overlap_percent: float = 10.0
        self._mosaic_state: Optional[MosaicState] = None

        # Subscribe to events
        event_bus.subscribe(SaveTissuePlanningSessionCommand, self._on_save)
        event_bus.subscribe(LoadTissuePlanningSessionCommand, self._on_load)
        event_bus.subscribe(ManualShapesChanged, self._on_shapes_changed)
        event_bus.subscribe(ObjectiveChanged, self._on_objective_changed)

    def set_mosaic_state(self, mosaics, extents, pixel_size_mm, top_left, channels):
        """Called by UI to provide mosaic data for saving."""
        self._mosaic_state = MosaicState(mosaics, extents, pixel_size_mm, top_left, channels)

    def _on_shapes_changed(self, event: ManualShapesChanged):
        """Store polygon definitions when user draws shapes."""
        self._polygon_regions = [
            PolygonRegion(f"tissue_{i}", list(shape))
            for i, shape in enumerate(event.shapes_mm)
        ]

    def _on_objective_changed(self, event: ObjectiveChanged):
        """Recalculate tiles when objective changes."""
        if self._polygon_regions:
            self._recalculate_tiles()

    def _recalculate_tiles(self):
        """Regenerate tile grids for all polygons using current objective FOV."""
        self._scan_coordinates.clear_regions()

        for region in self._polygon_regions:
            coords = np.array(region.vertices_mm)
            tiles = self._scan_coordinates.get_points_for_manual_region(
                coords, self._overlap_percent
            )
            # Add to scan coordinates as region
            # ... (use existing add_region or direct assignment)

    def _on_save(self, cmd: SaveTissuePlanningSessionCommand):
        """Save session to directory."""
        try:
            path = Path(cmd.session_path)

            # Get current coordinates from scan_coordinates
            coordinates = self._scan_coordinates.region_fov_coordinates

            # Build session data
            data = TissueSessionData(
                mosaic_pixel_size_mm=self._mosaic_state.pixel_size_mm,
                # ... fill other fields
            )

            save_session(path, data, self._mosaic_state.mosaics, coordinates)

            self._event_bus.publish(TissuePlanningSessionSaved(
                session_path=str(path), success=True
            ))
        except Exception as e:
            self._event_bus.publish(TissuePlanningSessionSaved(
                session_path=str(path), success=False, error=str(e)
            ))

    def _on_load(self, cmd: LoadTissuePlanningSessionCommand):
        """Load session from directory."""
        # Load data, restore polygons, publish LoadScanCoordinatesCommand
        # Publish TissuePlanningSessionLoaded
```

---

### Phase 4: UI Integration

**File: `software/src/squid/ui/widgets/display/napari_mosaic.py`**

Add Save/Load buttons after Clear button (around line 374):

```python
# In __init__, after clear_button:
self.save_session_button = QPushButton("Save Planning Session")
self.save_session_button.clicked.connect(self._on_save_session_clicked)
_layout.addWidget(self.save_session_button)

self.load_session_button = QPushButton("Load Planning Session")
self.load_session_button.clicked.connect(self._on_load_session_clicked)
_layout.addWidget(self.load_session_button)
```

Add methods:

```python
def _on_save_session_clicked(self):
    """Open dialog and trigger save."""
    from qtpy.QtWidgets import QFileDialog
    folder = QFileDialog.getSaveFileName(
        self, "Save Planning Session", "", "Session Directory"
    )[0]
    if folder:
        # Push mosaic state to manager before save
        self._push_mosaic_state_to_manager()
        self._event_bus.publish(SaveTissuePlanningSessionCommand(session_path=folder))

def _on_load_session_clicked(self):
    """Open dialog and trigger load."""
    from qtpy.QtWidgets import QFileDialog
    folder = QFileDialog.getExistingDirectory(self, "Load Planning Session")
    if folder:
        self._event_bus.publish(LoadTissuePlanningSessionCommand(session_path=folder))

def _push_mosaic_state_to_manager(self):
    """Send current mosaic state to TissuePlanningSession manager."""
    # Access compositor's worker to get mosaic arrays
    worker = self._compositor._worker
    with worker._lock:
        mosaics = {ch: arr.copy() for ch, arr in worker._mosaics.items()}
        extents = dict(worker._extents)
        top_left = dict(worker._top_left)
        pixel_size = worker._pixel_size_mm

    # Publish or call manager directly (depending on wiring)

def restore_mosaic_from_session(self, mosaics: Dict[str, np.ndarray], data: TissueSessionData):
    """Restore napari layers from loaded session."""
    # Clear existing layers, recreate from loaded mosaics
```

**File: `software/src/squid/ui/widgets/acquisition/fluidics_multipoint.py`**

The existing `load_coordinates()` method already handles CSV loading. Add convenience method:

```python
def load_from_planning_session(self, session_path: str):
    """Load coordinates from tissue planning session."""
    coords_file = os.path.join(session_path, "coordinates.csv")
    if os.path.exists(coords_file):
        # Use existing load logic
        self._load_coordinates_from_file(coords_file)
```

---

### Phase 5: Wiring

**File: `software/src/squid/application.py` (or equivalent DI location)**

Instantiate and wire `TissuePlanningSession`:

```python
tissue_planning = TissuePlanningSession(
    event_bus=event_bus,
    scan_coordinates=scan_coordinates,
    objective_store=objective_store,
    camera=camera,
)
```

---

## Files to Modify

| File | Changes |
|------|---------|
| `core/events.py` | Add 4 new events |
| `backend/io/tissue_session_io.py` | **NEW** - save/load functions |
| `backend/managers/tissue_planning_session.py` | **NEW** - session manager |
| `backend/managers/__init__.py` | Export new manager |
| `backend/io/__init__.py` | Export new IO functions |
| `ui/widgets/display/napari_mosaic.py` | Add Save/Load buttons, mosaic state methods |
| `application.py` | Wire TissuePlanningSession |

---

## Verification

1. **Unit tests** for `tissue_session_io.py`:
   - Test save/load round-trip with sample data
   - Test CSV format matches fluidics expectations

2. **Integration test**:
   - Create mock mosaic + polygons
   - Save session
   - Load session into fresh instance
   - Verify coordinates match

3. **Manual end-to-end test**:
   ```
   1. Run with --simulation
   2. Start low-mag mosaic acquisition (a few tiles)
   3. Press 'D', draw polygon around region
   4. Click "Save Planning Session" → select folder
   5. Verify folder contains mosaic.tiff, regions.json, coordinates.csv, session_metadata.json
   6. Clear mosaic, click "Load Planning Session" → select folder
   7. Verify mosaic restored, coordinates visible in acquisition widget
   8. Switch objective → verify tile count updates for new FOV size
   9. Load coordinates.csv into fluidics_multipoint → verify regions appear
   ```

---

## Implementation Order

1. **Events** - Add new event types (quick, unblocks everything)
2. **IO module** - Implement save/load (can test independently)
3. **Session manager** - Core logic, subscribe to events
4. **UI buttons** - Simple additions to napari_mosaic
5. **Wiring** - Connect in application.py
6. **Testing** - Unit + integration + manual

---

## Notes

- Polygon coordinates stored in **stage mm** - objective-independent, so they remain valid when switching objectives
- Tile recalculation uses existing `ScanCoordinates.get_points_for_manual_region()` which already handles FOV size from current objective
- CSV format matches existing fluidics load format for seamless integration
- Mosaic saved at viewer resolution (downsampled) per user preference
