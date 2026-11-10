"""Per-channel camera geometry (binning, centred crop, pixel format) for the Time at XY tab.

Binning, ROI and pixel format are camera-wide state, not part of an
``AcquisitionChannel``. A Time at XY row can pin its own values here. Any field
left as ``None`` inherits the camera's setting at the start of the run, so a
row that pins nothing behaves exactly as before.

Channels whose resolved geometry differs produce frames of different shape,
dtype and pixel size. They are therefore acquired one after another and saved
to separate stores, never combined.
"""

from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

import squid.logging
from squid.config import CameraPixelFormat

from control.utils import clamp_roi

log = squid.logging.get_logger("camera_geometry")

# ROI width/height/offset grid used by the Camera tab (widgets.CameraSettingsWidget).
ROI_INTERVAL = 8

# Fallback list when the driver can't enumerate its formats (Toupcam), matching the Camera tab.
FALLBACK_PIXEL_FORMATS = ("MONO8", "MONO12", "MONO14", "MONO16", "BAYER_RG8", "BAYER_RG12")


def available_pixel_format_names(camera) -> Sequence[str]:
    """Pixel format names to offer for *camera* (same list the Camera tab shows)."""
    try:
        return [pf.name for pf in camera.get_available_pixel_formats()]
    except (NotImplementedError, AttributeError):
        return list(FALLBACK_PIXEL_FORMATS)


def binning_option_values(camera) -> Sequence[int]:
    """Square binning factors *camera* supports, ascending."""
    try:
        options = camera.get_binning_options()
    except (NotImplementedError, AttributeError):
        return [1]
    return sorted({int(bx) for bx, by in options if int(bx) == int(by)}) or [1]


def is_mono_format(name: str) -> bool:
    return name.upper().startswith("MONO")


@dataclass(frozen=True)
class CameraGeometry:
    """What one row pins. ``None`` means inherit the run-start camera setting.

    ``crop_w``/``crop_h`` are output-image pixels at the row's binning (the unit
    of the Camera tab's Width/Height boxes). They are set together or not at all.
    The crop is always centred on the sensor.
    """

    binning: Optional[int] = None
    crop_w: Optional[int] = None
    crop_h: Optional[int] = None
    pixel_format: Optional[str] = None

    @property
    def is_inherit(self) -> bool:
        return self.binning is None and self.crop_w is None and self.pixel_format is None

    def to_yaml_dict(self) -> dict:
        d = {}
        if self.binning is not None:
            d["binning"] = int(self.binning)
        if self.crop_w is not None and self.crop_h is not None:
            d["crop_width"] = int(self.crop_w)
            d["crop_height"] = int(self.crop_h)
        if self.pixel_format is not None:
            d["pixel_format"] = str(self.pixel_format)
        return d

    @classmethod
    def from_yaml_dict(cls, d: Optional[dict]) -> "CameraGeometry":
        if not d:
            return cls()
        binning = d.get("binning")
        crop_w, crop_h = d.get("crop_width"), d.get("crop_height")
        if crop_w is None or crop_h is None:
            crop_w = crop_h = None
        pixel_format = d.get("pixel_format")
        return cls(
            binning=int(binning) if binning is not None else None,
            crop_w=int(crop_w) if crop_w is not None else None,
            crop_h=int(crop_h) if crop_h is not None else None,
            pixel_format=str(pixel_format).upper() if pixel_format else None,
        )


def short_label(geometry: CameraGeometry) -> str:
    """Compact table-cell text, e.g. ``2×2 1024² M8``; ``—`` when the row inherits everything."""
    if geometry.is_inherit:
        return "—"
    parts = []
    if geometry.binning is not None:
        parts.append(f"{geometry.binning}×{geometry.binning}")
    if geometry.crop_w is not None:
        square = geometry.crop_w == geometry.crop_h
        parts.append(f"{geometry.crop_w}²" if square else f"{geometry.crop_w}×{geometry.crop_h}")
    if geometry.pixel_format is not None:
        fmt = geometry.pixel_format
        parts.append("M" + fmt[4:] if fmt.startswith("MONO") else fmt)
    return " ".join(parts)


def describe(geometry: CameraGeometry) -> str:
    """Readable description for tooltips and the confirm dialog."""
    binning = f"{geometry.binning}×{geometry.binning}" if geometry.binning is not None else "current"
    crop = f"{geometry.crop_w}×{geometry.crop_h} centred" if geometry.crop_w is not None else "current"
    fmt = geometry.pixel_format or "current"
    return f"binning {binning}, crop {crop}, format {fmt}"


def sensor_size_at(camera, binning: int) -> Tuple[int, int]:
    """Full-sensor output size (px) at *binning*, from the size at the camera's current binning."""
    width, height = camera.get_resolution()
    current = int(camera.get_binning()[0])
    return int(width * current / binning), int(height * current / binning)


def validate_rows(plans, camera) -> Optional[str]:
    """Error text if an active row pins a geometry *camera* can't deliver, else None."""
    if camera is None:
        return None
    bins = binning_option_values(camera)
    formats = available_pixel_format_names(camera)
    for plan in plans:
        g = plan.geometry
        if not plan.active or g.is_inherit:
            continue
        if g.binning is not None and g.binning not in bins:
            return f"{plan.name}: the camera does not offer {g.binning}×{g.binning} binning."
        if g.pixel_format is not None and g.pixel_format not in formats:
            return f"{plan.name}: the camera does not offer pixel format {g.pixel_format}."
        if g.crop_w is not None:
            binning = g.binning if g.binning is not None else int(camera.get_binning()[0])
            sensor_w, sensor_h = sensor_size_at(camera, binning)
            if g.crop_w > sensor_w or g.crop_h > sensor_h:
                return (
                    f"{plan.name}: crop {g.crop_w}×{g.crop_h} is larger than the sensor "
                    f"at {binning}×{binning} binning ({sensor_w}×{sensor_h})."
                )
    return None


@dataclass(frozen=True)
class CameraSnapshot:
    """The camera's geometry at the start of a run; inherited fields resolve against it."""

    binning: Tuple[int, int]
    roi: Tuple[int, int, int, int]
    pixel_format: Optional[str]


@dataclass(frozen=True)
class ResolvedGeometry:
    """A fully specified geometry. Hashable, so it keys z-stack groups and frame-shape probes.

    ``crop_w``/``crop_h`` of ``None`` means "keep the snapshot ROI": only possible when
    the row inherits both crop and binning, so the ROI the user set is still valid.
    """

    binning: int
    crop_w: Optional[int]
    crop_h: Optional[int]
    pixel_format: Optional[str]

    def dir_name(self) -> str:
        """Directory label for a z-stack group, e.g. ``bin2_1024x1024_mono8``."""
        crop = f"{self.crop_w}x{self.crop_h}" if self.crop_w is not None else "roi"
        fmt = (self.pixel_format or "fmt").lower()
        return f"bin{self.binning}_{crop}_{fmt}"

    def to_yaml_dict(self) -> dict:
        return {
            "binning": self.binning,
            "crop_width": self.crop_w,
            "crop_height": self.crop_h,
            "pixel_format": self.pixel_format,
        }


def resolve(geometry: CameraGeometry, snapshot: CameraSnapshot) -> ResolvedGeometry:
    binning = geometry.binning if geometry.binning is not None else int(snapshot.binning[0])
    pixel_format = geometry.pixel_format or snapshot.pixel_format
    if geometry.crop_w is not None:
        crop_w, crop_h = geometry.crop_w, geometry.crop_h
    elif binning == int(snapshot.binning[0]):
        crop_w = crop_h = None
    else:
        # Binning changes but the crop is inherited: scale the user's ROI so the
        # field of view stays the same.
        scale = int(snapshot.binning[0]) / binning
        crop_w = int(snapshot.roi[2] * scale)
        crop_h = int(snapshot.roi[3] * scale)
    return ResolvedGeometry(binning=binning, crop_w=crop_w, crop_h=crop_h, pixel_format=pixel_format)


def snapshot_camera(camera) -> CameraSnapshot:
    fmt = None
    try:
        pf = camera.get_pixel_format()
        fmt = pf.name if pf is not None else None
    except Exception:
        log.exception("Could not read the camera pixel format")
    return CameraSnapshot(
        binning=tuple(int(b) for b in camera.get_binning()),
        roi=tuple(int(v) for v in camera.get_region_of_interest()),
        pixel_format=fmt,
    )


def image_size_px(camera) -> Optional[Tuple[int, int]]:
    """Width x height of the frames *camera* delivers now, or None when it cannot be read.

    The hardware ROI (the Camera tab's crop, at the current binning) is applied
    first, then the centred software crop of ``get_crop_size()``, which only ever
    makes the frame smaller (control.utils.crop_image).
    """
    try:
        _x, _y, width, height = (int(v) for v in camera.get_region_of_interest())
    except Exception:
        log.exception("Could not read the camera ROI")
        return None
    try:
        crop_w, crop_h = camera.get_crop_size()
    except Exception:
        crop_w = crop_h = None
    if crop_w:
        width = min(width, int(crop_w))
    if crop_h:
        height = min(height, int(crop_h))
    return width, height


def centred_roi(sensor_w: int, sensor_h: int, crop_w: int, crop_h: int) -> Tuple[int, int, int, int]:
    """A crop of about ``crop_w × crop_h``, centred and snapped to the Camera tab's 8-px grid."""
    return clamp_roi(
        (sensor_w - crop_w) / 2,
        (sensor_h - crop_h) / 2,
        crop_w,
        crop_h,
        sensor_w,
        sensor_h,
        x_interval=ROI_INTERVAL,
        y_interval=ROI_INTERVAL,
        min_width=ROI_INTERVAL * 2,
        min_height=ROI_INTERVAL * 2,
    )


def apply_geometry(camera, resolved: ResolvedGeometry, snapshot: CameraSnapshot) -> None:
    """Set pixel format, then binning, then the centred crop. Call with streaming stopped.

    Order matters: binning resets the sensor size the ROI is measured against.
    """
    if resolved.pixel_format is not None:
        current = camera.get_pixel_format()
        if current is None or current.name != resolved.pixel_format:
            camera.set_pixel_format(CameraPixelFormat.from_string(resolved.pixel_format))
    if tuple(camera.get_binning()) != (resolved.binning, resolved.binning):
        camera.set_binning(resolved.binning, resolved.binning)
    if resolved.crop_w is None:
        roi = snapshot.roi
    else:
        sensor_w, sensor_h = camera.get_resolution()
        roi = centred_roi(sensor_w, sensor_h, resolved.crop_w, resolved.crop_h)
    if tuple(camera.get_region_of_interest()) != tuple(roi):
        camera.set_region_of_interest(*roi)
    readback = tuple(camera.get_region_of_interest())
    if readback != tuple(roi):
        # Some drivers (Toupcam) log and swallow a rejected ROI; say so here too.
        log.warning(f"Camera ROI is {readback} after requesting {roi} for {resolved}")
    log.info(f"Camera geometry: binning {resolved.binning}, ROI {readback}, format {resolved.pixel_format}")


def restore_snapshot(camera, snapshot: CameraSnapshot) -> None:
    """Put the run-start pixel format, binning and ROI back, in that order."""
    if snapshot.pixel_format is not None:
        current = camera.get_pixel_format()
        if current is None or current.name != snapshot.pixel_format:
            camera.set_pixel_format(CameraPixelFormat.from_string(snapshot.pixel_format))
    if tuple(camera.get_binning()) != tuple(snapshot.binning):
        camera.set_binning(*snapshot.binning)
    if tuple(camera.get_region_of_interest()) != tuple(snapshot.roi):
        camera.set_region_of_interest(*snapshot.roi)


def pixel_size_for(objective_store, camera, binning: int) -> Optional[float]:
    """Sample-plane pixel size (µm) at *binning*, without touching the camera state.

    Scales the driver's own binned pixel size (what compute_pixel_size_um uses) by
    the binning ratio, so it agrees with every other pixel size in the app.
    """
    try:
        factor = objective_store.get_pixel_size_factor()
        binned_um = camera.get_pixel_size_binned_um()
        if factor is None or binned_um is None:
            return None
        current_bin = int(camera.get_binning()[0])
        return float(factor) * float(binned_um) / current_bin * binning
    except Exception:
        log.exception("Could not compute the pixel size")
        return None
