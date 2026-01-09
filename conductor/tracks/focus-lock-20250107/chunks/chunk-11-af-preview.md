# Chunk 11: AF Camera Preview (Optional)

## Goal

Add live AF camera preview to the focus lock widget showing the spot position, using a dedicated stream handler that maintains backend/frontend separation.

## Complexity Note

This chunk adds a custom stream handler stack which is clean but relatively heavy for an optional preview feature. Consider:
- **Deferring** this entire chunk if preview is not critical
- **Reusing** existing `QtStreamHandler` pattern if it can be adapted
- The architecture is correct (backend/frontend separation) but adds new abstractions

If preview is truly optional for initial release, implement Chunks 1-10 first and add this later.

## Dependencies

- Chunk 3 (UI Widget)
- Chunk 6 (Controller)

## Files to Create

| File | Purpose |
|------|---------|
| `software/src/squid/backend/io/focus_lock_stream_handler.py` | Backend stream handler (no Qt) |
| `software/src/squid/ui/qt_focus_lock_stream_handler.py` | Qt wrapper for UI |

## Files to Modify

| File | Changes |
|------|---------|
| `software/src/squid/backend/controllers/autofocus/continuous_focus_lock.py` | Push frames to stream handler |
| `software/src/squid/ui/widgets/hardware/focus_lock_status.py` | Add preview display |

## Architecture

**Key Principle**: Maintain strict backend/frontend separation. No Qt code in backend.

```
Controller (backend)                    UI (frontend)
─────────────────────                   ─────────────────
                                        qt_handler = QtFocusLockStreamHandler()
                                              │
controller.set_preview_handler(───────► qt_handler.handler)
                                              │
controller._push_preview_frame()              │
       │                                      │
       ▼                                      │
handler.push_frame(FocusLockFrame)            │
       │                                      │
       └──────────────────────────────► preview_frame.emit()
                                              │
                                              ▼
                                        widget._on_preview()
```

## Deliverables

### Backend: FocusLockFrame Dataclass

```python
# software/src/squid/backend/io/focus_lock_stream_handler.py

from dataclasses import dataclass
from typing import Callable, Optional
import numpy as np


@dataclass
class FocusLockFrame:
    """Frame data with focus lock metadata for preview display."""
    image: np.ndarray          # Cropped spot image (e.g., 64x64)
    spot_x: float              # Spot X position within crop (for crosshair)
    spot_y: float              # Spot Y position within crop
    correlation: float         # Cross-correlation quality (0-1)
    z_error_um: float          # Current Z error
    timestamp: float           # Frame timestamp
```

### Backend: FocusLockStreamHandler

```python
@dataclass
class FocusLockStreamFunctions:
    """Callbacks for focus lock stream - set by UI layer."""
    on_preview_frame: Callable[[FocusLockFrame], None] = lambda f: None


class FocusLockStreamHandler:
    """Stream handler for focus lock preview frames.

    Backend component - NO Qt dependencies.
    UI layer wraps this and provides Qt-specific callbacks.

    Same pattern as StreamHandler/QtStreamHandler for camera frames.
    """

    def __init__(self, functions: Optional[FocusLockStreamFunctions] = None):
        self._fns = functions or FocusLockStreamFunctions()
        self._enabled = False

    def set_functions(self, functions: FocusLockStreamFunctions) -> None:
        """Set callbacks (called by UI layer during setup)."""
        self._fns = functions

    def set_enabled(self, enabled: bool) -> None:
        """Enable/disable preview streaming."""
        self._enabled = enabled

    def push_frame(self, frame: FocusLockFrame) -> None:
        """Push a frame to the stream (called by focus lock controller)."""
        if self._enabled:
            self._fns.on_preview_frame(frame)
```

### Frontend: QtFocusLockStreamHandler

```python
# software/src/squid/ui/qt_focus_lock_stream_handler.py

from typing import Optional
from qtpy.QtCore import QObject, Signal

from squid.backend.io.focus_lock_stream_handler import (
    FocusLockFrame,
    FocusLockStreamHandler,
    FocusLockStreamFunctions,
)


class QtFocusLockStreamHandler(QObject):
    """Qt wrapper for focus lock preview stream.

    Bridges backend callbacks to Qt signals for thread-safe UI updates.
    """

    preview_frame = Signal(object)  # Emits FocusLockFrame

    def __init__(self, handler: Optional[FocusLockStreamHandler] = None, parent=None):
        super().__init__(parent)
        self._handler = handler or FocusLockStreamHandler()
        self._handler.set_functions(FocusLockStreamFunctions(
            on_preview_frame=self._on_frame,
        ))

    def _on_frame(self, frame: FocusLockFrame) -> None:
        """Bridge callback to Qt signal (thread-safe crossing)."""
        self.preview_frame.emit(frame)

    @property
    def handler(self) -> FocusLockStreamHandler:
        """Get backend handler for controller to use."""
        return self._handler

    def set_enabled(self, enabled: bool) -> None:
        """Enable/disable preview streaming."""
        self._handler.set_enabled(enabled)
```

### Frame Source

**Important**: `_last_frame` must be populated somewhere. Options:

1. **From LaserAutofocusController**: `self._laser_af.image` stores the raw frame (line 778 in laser_auto_focus_controller.py)
2. **Store in controller**: Focus lock controller stores frame from each measurement

**Recommended**: Use `self._laser_af.image` which already exists:

```python
def _crop_around_spot(self, result: LaserAFResult, size: int = 64) -> np.ndarray:
    """Crop region around detected spot for preview."""
    # Use frame stored by laser AF controller during measurement
    frame = self._laser_af.image
    if frame is None:
        return np.zeros((size, size), dtype=np.uint8)
    # ... rest of cropping logic
```

### Controller Integration

```python
# In continuous_focus_lock.py

class ContinuousFocusLockController:
    def __init__(self, ...):
        ...
        self._preview_handler: Optional[FocusLockStreamHandler] = None

    def set_preview_handler(self, handler: FocusLockStreamHandler) -> None:
        """Set optional preview stream handler (called by UI layer)."""
        self._preview_handler = handler

    def _control_loop(self) -> None:
        ...
        while self._running:
            result = self._laser_af.measure_displacement_continuous()

            # Push preview frame if handler is set and enabled
            if self._preview_handler is not None:
                frame, spot_x_local, spot_y_local = self._crop_around_spot(result)
                self._preview_handler.push_frame(FocusLockFrame(
                    image=frame.copy(),  # Copy to ensure buffer lifetime
                    spot_x=spot_x_local, # Spot position WITHIN the crop (not hardcoded!)
                    spot_y=spot_y_local,
                    correlation=result.correlation or 0.0,
                    z_error_um=result.displacement_um - self._target_um,
                    timestamp=result.timestamp,
                ))
            ...

    def _crop_around_spot(self, result: LaserAFResult, size: int = 64) -> tuple[np.ndarray, float, float]:
        """Crop region around detected spot for preview.

        Returns:
            (cropped_image, spot_x_local, spot_y_local)
            where local coordinates are the spot position WITHIN the crop.
        """
        # Use frame stored by laser AF controller during measurement
        frame = self._laser_af.image
        if frame is None or result.spot_x_px is None or result.spot_y_px is None:
            return np.zeros((size, size), dtype=np.uint8), size / 2, size / 2

        cx, cy = int(result.spot_x_px), int(result.spot_y_px)
        half = size // 2

        # Handle edge cases
        h, w = frame.shape[:2]
        x1 = max(0, cx - half)
        y1 = max(0, cy - half)
        x2 = min(w, cx + half)
        y2 = min(h, cy + half)

        crop = frame[y1:y2, x1:x2]

        # Compute local spot coordinates within the crop
        # (account for edge clipping)
        spot_x_local = result.spot_x_px - x1
        spot_y_local = result.spot_y_px - y1

        # Pad if needed (edge of frame)
        if crop.shape != (size, size):
            padded = np.zeros((size, size), dtype=crop.dtype)
            # Place crop in padded array, adjusting local coords if needed
            pad_x = (size - crop.shape[1]) // 2
            pad_y = (size - crop.shape[0]) // 2
            padded[pad_y:pad_y + crop.shape[0], pad_x:pad_x + crop.shape[1]] = crop
            spot_x_local += pad_x
            spot_y_local += pad_y
            return padded, spot_x_local, spot_y_local

        return crop, spot_x_local, spot_y_local
```

### Widget Preview Display

```python
# In focus_lock_status.py

class FocusLockStatusWidget(QWidget):
    def __init__(self, ui_event_bus: UIEventBus, parent=None):
        super().__init__(parent)
        ...
        # AF preview (optional, connected later)
        self._preview_label = QLabel()
        self._preview_label.setFixedSize(64, 64)
        self._preview_label.setScaledContents(True)
        self._preview_enabled = True  # Can be disabled via config

        # Qt stream handler (created by widget, passed to app wiring)
        self._qt_preview_handler: Optional[QtFocusLockStreamHandler] = None

    def setup_preview(self) -> Optional[QtFocusLockStreamHandler]:
        """Create and connect preview handler. Returns handler for controller.

        Returns None if preview is disabled, allowing graceful degradation.
        """
        if not self._preview_enabled:
            return None

        self._qt_preview_handler = QtFocusLockStreamHandler()
        self._qt_preview_handler.preview_frame.connect(self._on_preview_frame)
        return self._qt_preview_handler

    def set_preview_enabled(self, enabled: bool) -> None:
        """Enable/disable preview display."""
        self._preview_enabled = enabled
        self._preview_label.setVisible(enabled)
        if self._qt_preview_handler:
            self._qt_preview_handler.set_enabled(enabled)

    def _on_preview_frame(self, frame: FocusLockFrame) -> None:
        """Update AF camera preview from FocusLockFrame."""
        image = frame.image

        # Normalize to uint8 if needed
        if image.dtype != np.uint8:
            image = (image / max(image.max(), 1) * 255).astype(np.uint8)

        # Convert to QImage (use tobytes() to ensure buffer lifetime)
        height, width = image.shape[:2]
        if len(image.shape) == 2:
            qimage = QImage(image.tobytes(), width, height, width, QImage.Format_Grayscale8)
        else:
            qimage = QImage(image.tobytes(), width, height, width * 3, QImage.Format_RGB888)

        pixmap = QPixmap.fromImage(qimage)

        # Draw crosshair at spot position
        painter = QPainter(pixmap)
        painter.setPen(QPen(Qt.red, 1))
        cx, cy = int(frame.spot_x), int(frame.spot_y)
        painter.drawLine(cx - 5, cy, cx + 5, cy)
        painter.drawLine(cx, cy - 5, cx, cy + 5)
        painter.end()

        self._preview_label.setPixmap(pixmap)
```

### Application Wiring

```python
# In main_window.py or wherever widget/controller are connected
# (See Chunk 8 for full wiring details)

def _wire_focus_lock_preview(
    controller: ContinuousFocusLockController,
    widget: FocusLockStatusWidget,
) -> None:
    """Wire preview stream from controller to widget."""
    if controller is None or widget is None:
        return  # Graceful degradation

    qt_handler = widget.setup_preview()
    if qt_handler is None:
        # Preview disabled in widget config
        return

    controller.set_preview_handler(qt_handler.handler)
    qt_handler.set_enabled(True)
```

**Important**: Always guard for `None` - both controller/widget may be absent, and `setup_preview()` may return `None` if preview is disabled.

## Testing

```bash
cd software
python main_hcs.py --simulation
# Verify preview updates in widget
```

## Completion Checklist

### Backend Stream Handler
- [ ] Create `FocusLockFrame` dataclass with all metadata fields
- [ ] Create `FocusLockStreamFunctions` callback dataclass
- [ ] Create `FocusLockStreamHandler` class (NO Qt dependencies)
- [ ] Implement `set_functions()` for UI layer to provide callbacks
- [ ] Implement `set_enabled()` to enable/disable streaming
- [ ] Implement `push_frame()` called by controller

### Frontend Qt Wrapper
- [ ] Create `QtFocusLockStreamHandler` inheriting from QObject
- [ ] Define `preview_frame = Signal(object)`
- [ ] Wrap backend handler and provide callback
- [ ] Bridge callback to Qt signal emission
- [ ] Expose `handler` property for controller access

### Controller Integration
- [ ] Add `_preview_handler: Optional[FocusLockStreamHandler]`
- [ ] Add `set_preview_handler()` method
- [ ] Implement `_crop_around_spot()` returning `(image, spot_x_local, spot_y_local)`
- [ ] Compute local spot coordinates within crop (NOT hardcoded center!)
- [ ] Handle edge padding when spot is near frame edge
- [ ] Push frames in control loop (when handler set and enabled)
- [ ] Copy frame data before pushing (buffer lifetime)

### Widget Integration
- [ ] Add preview QLabel to layout
- [ ] Implement `setup_preview()` returning handler
- [ ] Connect to `preview_frame` signal
- [ ] Implement `_on_preview_frame()` slot
- [ ] Convert numpy to QImage safely (use tobytes())
- [ ] Draw crosshair at spot position
- [ ] Only show preview in expanded view

### Buffer Safety
- [ ] Use `frame.copy()` in controller before pushing
- [ ] Use `image.tobytes()` in widget for QImage

### Architecture Verification
- [ ] NO Qt imports in backend stream handler
- [ ] NO EventBus for frame data
- [ ] Clean separation: backend pushes, frontend displays
- [ ] Same pattern as StreamHandler/QtStreamHandler

### Testing
- [ ] Manual test: Preview updates during lock
- [ ] Manual test: Crosshair tracks spot
- [ ] Manual test: Collapsed view hides preview
- [ ] Verify no performance degradation

### Verification
- [ ] Preview visible in widget
- [ ] Updates at ~10 Hz
- [ ] Crosshair correctly positioned
- [ ] No EventBus flooding
- [ ] Backend has no Qt dependencies
