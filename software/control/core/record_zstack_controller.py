"""Parameters, pure helpers and the controller for the "Time at XY" acquisition tab.

Per FOV the worker establishes a z reference (focus map / laser AF / contrast
AF / current stage z), then runs, in order:

  1. an initial z-stack over every channel with ``zi`` ticked,
  2. one timelapse recording per channel with ``r`` ticked (table order, each
     with its own fps/duration), at the subset of the stack planes chosen by
     ``RecordSliceSelection`` (Bottom / Mid / Top presets plus explicit planes),
  3. a final z-stack over every channel with ``zf`` ticked.

One ``ZStackPlan`` (mode + geometry) defines the planes for all three phases.
Everything here is Qt-free so the widget's ``validate()`` and the confirm
dialog can share the exact geometry the worker uses.
"""

import math
import os
import threading
import time
from dataclasses import dataclass, field
from threading import Event, Thread
from typing import Dict, List, Optional, Sequence, Tuple

import yaml

import squid.logging
from control.core.acquisition_setup import compute_pixel_size_um, start_run_state_writer
from control.core.camera_geometry import CameraGeometry, describe as describe_geometry, is_mono_format
from control.core.zstack_modes import (
    Z_MODE_FROM_BOTTOM,
    Z_MODE_RELATIVE,
    Z_MODE_SET_RANGE,
    Z_MODES,
    relative_stack_Nz,
)
from control.models.acquisition_config import AcquisitionChannel
from control.utils import sanitize_path_component, serialize_for_yaml as _serialize_for_yaml

log = squid.logging.get_logger("RecordZStackController")

# Names of the two z-stack output trees under the experiment directory.
ZSTACK_PHASE_INITIAL = "initial"
ZSTACK_PHASE_FINAL = "final"


def zstack_subdir(phase: str) -> str:
    """Experiment-relative directory holding the z-stack store for *phase*."""
    return f"zstack_{phase}"


# ---------------------------------------------------------------------------- data model


@dataclass
class ChannelPlan:
    """One row of the channel table: a channel plus what to do with it at every FOV."""

    channel: AcquisitionChannel
    fps: float = 10.0
    duration_s: float = 1.0
    zi: bool = False  # include in the initial z-stack
    r: bool = False  # record a timelapse with this channel
    zf: bool = False  # include in the final z-stack
    # Binning / centred crop / pixel format this row pins; unset fields inherit the camera.
    geometry: CameraGeometry = field(default_factory=CameraGeometry)
    # Rate this row's recording will actually be made at: min(fps, what the camera can
    # deliver at the row's exposure in its current binning / ROI).  Resolved by
    # RecordZStackController.run_acquisition BEFORE the run starts (so every FOV and
    # timepoint uses one known rate and acquisition.yaml records it).  None for a row
    # that pins its own camera settings: the camera has to be in that geometry to
    # answer, so the worker resolves it the first time the row records.
    effective_fps: Optional[float] = None

    @property
    def name(self) -> str:
        return self.channel.name

    @property
    def active(self) -> bool:
        return bool(self.zi or self.r or self.zf)

    def to_yaml_dict(self) -> dict:
        # The channel stays nested: AcquisitionChannel forbids extra keys, so a
        # flat row would have to be split apart again on load.
        d = {
            "channel": _serialize_for_yaml(self.channel),
            "fps": self.fps,
            "duration_s": self.duration_s,
            "zi": bool(self.zi),
            "r": bool(self.r),
            "zf": bool(self.zf),
        }
        if self.r and self.effective_fps is not None:
            # Informational (a run's snapshot only): the rate the recording was made at.
            d["effective_fps"] = self.effective_fps
        camera = self.geometry.to_yaml_dict()
        if camera:  # omitted when every field inherits, so old files stay identical
            d["camera"] = camera
        return d

    @classmethod
    def from_yaml_dict(cls, d: dict) -> "ChannelPlan":
        return cls(
            channel=AcquisitionChannel.model_validate(d["channel"]),
            fps=float(d.get("fps", 10.0)),
            duration_s=float(d.get("duration_s", 1.0)),
            zi=bool(d.get("zi", False)),
            r=bool(d.get("r", False)),
            zf=bool(d.get("zf", False)),
            geometry=CameraGeometry.from_yaml_dict(d.get("camera")),
        )


@dataclass
class ZStackPlan:
    """Z-stack geometry; one plan governs both the Zi and Zf stacks.

    mode        one of control.core.zstack_modes.Z_MODES
    dz_um       plane spacing (all modes)
    nz          plane count -- From Bottom only (derived for the other modes)
    z_range_um  total range centred on the reference plane -- Relative only
    z_min_mm /  absolute stage z limits -- Set Range only
    z_max_mm
    """

    mode: str = Z_MODE_FROM_BOTTOM
    dz_um: float = 1.0
    nz: int = 1
    z_range_um: float = 4.0
    z_min_mm: float = 0.0
    z_max_mm: float = 0.0


SLICE_ROLES = ("bottom", "mid", "top")


def slice_role_indices(nz: int) -> Dict[str, int]:
    """Plane index of each preset role for an ``nz``-plane stack (they coincide when nz < 3)."""
    nz = max(1, int(nz))
    return {"bottom": 0, "mid": (nz - 1) // 2, "top": nz - 1}


def slice_label(index: int, nz: int) -> str:
    """Human label for stack plane ``index``: Bottom / Mid / Top, else its distance from Mid in dz."""
    roles = slice_role_indices(nz)
    if index == roles["bottom"]:
        return "Bottom"
    if index == roles["top"]:
        return "Top"
    if index == roles["mid"]:
        return "Mid"
    delta = index - roles["mid"]
    magnitude = "dz" if abs(delta) == 1 else f"{abs(delta)}·dz"
    return f"Mid{'+' if delta > 0 else '−'}{magnitude}"


@dataclass
class RecordSliceSelection:
    """Which planes of the shared stack the recordings visit.

    ``roles`` are the Bottom / Mid / Top presets (kept by name so they follow
    the plane count); ``indices`` are explicit extra plane indices picked in
    the Select… dialog (dropped when they fall outside the current stack).
    """

    roles: List[str] = field(default_factory=lambda: list(SLICE_ROLES))
    indices: List[int] = field(default_factory=list)

    def resolve(self, nz: int) -> List[int]:
        """Sorted, deduplicated plane indices for an ``nz``-plane stack."""
        nz = max(1, int(nz))
        role_index = slice_role_indices(nz)
        picked = {role_index[r] for r in self.roles if r in role_index}
        picked.update(int(i) for i in self.indices if 0 <= int(i) < nz)
        return sorted(picked)

    def to_yaml_dict(self) -> dict:
        return {"roles": [r for r in SLICE_ROLES if r in self.roles], "indices": sorted(int(i) for i in self.indices)}

    @classmethod
    def from_yaml_dict(cls, d: Optional[dict]) -> "RecordSliceSelection":
        if not d:
            return cls()
        roles = [r for r in (d.get("roles") or []) if r in SLICE_ROLES]
        indices = [int(i) for i in (d.get("indices") or [])]
        return cls(roles=roles, indices=indices)


@dataclass
class RecordZStackAcquisitionParameters:
    base_path: str
    experiment_id: str
    Nt: int = 1
    dt_s: float = 0.0
    # Z reference per FOV: laser AF wins over contrast AF (as on the multipoint
    # tabs); the focus map, when used, pre-positions z before either runs.
    use_laser_af: bool = False
    use_contrast_af: bool = False
    use_focus_map: bool = False
    focus_map: Optional[object] = None  # fitted control.core.core.FocusMap; not serialized
    region_laser_af_offsets: Dict[str, float] = field(default_factory=dict)
    # Channel table, in row order.
    channels: List[ChannelPlan] = field(default_factory=list)
    # Shared z geometry for the initial stack, the recordings and the final stack.
    zstack: ZStackPlan = field(default_factory=ZStackPlan)
    # Which of those planes the recordings visit.
    record_slices: RecordSliceSelection = field(default_factory=RecordSliceSelection)
    # XY / well-selection state (needed to save a full reusable settings snapshot)
    xy_mode: str = "Select Wells"
    scan_size_mm: float = 0.1
    overlap_percent: float = 10.0

    @property
    def zi_channels(self) -> List[AcquisitionChannel]:
        return [p.channel for p in self.channels if p.zi]

    @property
    def r_plans(self) -> List[ChannelPlan]:
        return [p for p in self.channels if p.r]

    @property
    def zf_channels(self) -> List[AcquisitionChannel]:
        return [p.channel for p in self.channels if p.zf]

    @property
    def active_channels(self) -> List[AcquisitionChannel]:
        return [p.channel for p in self.channels if p.active]


# ---------------------------------------------------------------------------- geometry


def frame_count(fps: float, duration_s: float) -> int:
    return int(round(fps * duration_s))


def resolve_effective_fps(camera, requested_fps: float, exposure_time_ms: float) -> float:
    """The rate a recording at *exposure_time_ms* will actually run at: *requested_fps*
    bounded by ``camera.get_max_frame_rate(exposure_time_ms)`` (readout / exposure /
    ROI-limited free-run maximum).  A camera without the query, or one that fails it,
    leaves the request unchanged.
    """
    try:
        achievable = float(camera.get_max_frame_rate(exposure_time_ms))
    except Exception:
        return float(requested_fps)
    if not achievable > 0:
        return float(requested_fps)
    return float(min(requested_fps, achievable))


def set_range_plane_count(z_min_um: float, z_max_um: float, dz_um: float) -> int:
    """Plane count for an absolute z_min..z_max range at dz -- the multipoint tabs' rule.

    ``ceil((max-min)/dz) + 1`` with an epsilon so an exact multiple (6.0/1.0)
    does not tip over into an extra plane through float error.
    """
    if dz_um <= 0:
        raise ValueError("require dz > 0")
    if z_max_um < z_min_um:
        raise ValueError("require z_max >= z_min")
    return max(1, int(math.ceil((z_max_um - z_min_um) / dz_um - 1e-9)) + 1)


def zstack_plane_count_for_plan(plan: ZStackPlan) -> int:
    """Number of planes the plan visits.  Independent of the FOV's z reference."""
    if plan.mode == Z_MODE_FROM_BOTTOM:
        if plan.nz < 1:
            raise ValueError("require Nz >= 1")
        return int(plan.nz)
    if plan.mode == Z_MODE_RELATIVE:
        return relative_stack_Nz(plan.z_range_um, plan.dz_um)
    if plan.mode == Z_MODE_SET_RANGE:
        return set_range_plane_count(plan.z_min_mm * 1000.0, plan.z_max_mm * 1000.0, plan.dz_um)
    raise ValueError(f"unknown z mode {plan.mode!r}")


def zstack_offsets_for_mode(plan: ZStackPlan, z_ref_mm: float) -> List[float]:
    """Plane offsets (µm) RELATIVE to ``z_ref_mm``, ascending.

    From Bottom  0, dz, ..., (nz-1)*dz -- the stack starts at the reference plane.
    Relative     centred on the reference plane; nz is forced odd by relative_stack_Nz
                 so the middle plane is the reference: (i - (nz-1)//2) * dz.
    Set Range    absolute z_min..z_max in dz steps, expressed relative to z_ref so the
                 worker's single move-to-offset path serves every mode.

    ``len(result) == zstack_plane_count_for_plan(plan)`` for every mode.
    """
    nz = zstack_plane_count_for_plan(plan)
    if plan.dz_um <= 0 and nz > 1:
        raise ValueError("require dz > 0")
    if plan.mode == Z_MODE_FROM_BOTTOM:
        base = 0.0
    elif plan.mode == Z_MODE_RELATIVE:
        base = -((nz - 1) // 2) * plan.dz_um
    else:
        base = (plan.z_min_mm - z_ref_mm) * 1000.0
    return [round(base + i * plan.dz_um, 6) for i in range(nz)]


def recording_offsets_for_plan(
    plan: ZStackPlan, selection: RecordSliceSelection, z_ref_mm: float
) -> List[Tuple[int, float]]:
    """``(plane_index, offset_um)`` of the recorded planes, ascending.

    Built on ``zstack_offsets_for_mode`` so a recording can never visit a plane
    the stacks would not.
    """
    offsets = zstack_offsets_for_mode(plan, z_ref_mm)
    return [(i, offsets[i]) for i in selection.resolve(len(offsets))]


def recording_dirnames(names: Sequence[str]) -> List[str]:
    """Filesystem-safe, unique, order-preserving directory names for the R rows.

    Later duplicates (after sanitizing) get a ``_{index}`` suffix so two channels
    that only differ in punctuation never share an output directory.
    """
    out: List[str] = []
    seen: Dict[str, int] = {}
    for idx, name in enumerate(names):
        base = sanitize_path_component(name)
        if base in seen:
            candidate = f"{base}_{idx}"
            while candidate in seen:
                candidate = f"{candidate}_{idx}"
            base = candidate
        seen[base] = idx
        out.append(base)
    return out


# ---------------------------------------------------------------------------- validation / summary


def validate_record_zstack_params(
    *,
    base_path: str,
    selected_well_count: int,
    channels: Sequence[ChannelPlan],
    zstack: ZStackPlan,
    record_slices: RecordSliceSelection,
    use_laser_af: bool,
    use_contrast_af: bool,
    laser_af_has_reference: bool,
    use_focus_map: bool = False,
    focus_map_ready: bool = False,
) -> Optional[str]:
    """Return an error string describing the first validation failure, or None if valid.

    Pure business rules for a Time-at-XY acquisition, kept beside the parameter
    dataclass and geometry helpers they validate (the widget's ``validate()``
    delegates here; no Qt required).
    """
    if not base_path:
        return "Base path must be set before starting acquisition."
    if selected_well_count < 1:
        return "At least one well must be selected."
    active = [p for p in channels if p.active]
    if not active:
        return "Add at least one channel and tick Zi, R, or Zf."
    for p in active:
        # A 0 ms exposure is rejected by the camera mid-run, after the stage has already moved.
        if p.channel.exposure_time <= 0:
            return f"{p.name}: exposure must be greater than 0 ms."
    r_plans = [p for p in channels if p.r]
    for p in r_plans:
        if p.fps <= 0:
            return f"{p.name}: recording FPS must be greater than 0."
        if p.duration_s <= 0:
            return f"{p.name}: recording duration must be greater than 0."
        if frame_count(p.fps, p.duration_s) < 1:
            return (
                f"{p.name}: fps×duration yields 0 frames (fps={p.fps}, duration={p.duration_s}s). "
                "Increase one or both."
            )
        if p.geometry.pixel_format is not None and not is_mono_format(p.geometry.pixel_format):
            return f"{p.name}: recordings need a mono pixel format, not {p.geometry.pixel_format}."
    # The z planes serve every phase (stacks and recordings alike).
    if zstack.mode not in Z_MODES:
        return f"Z planes: unknown mode {zstack.mode!r}."
    if zstack.dz_um <= 0:
        return "Z planes: dz must be greater than 0."
    if zstack.mode == Z_MODE_FROM_BOTTOM and zstack.nz < 1:
        return "Z planes: Nz must be at least 1."
    if zstack.mode == Z_MODE_RELATIVE and zstack.z_range_um <= 0:
        return "Z planes: range must be greater than 0."
    if zstack.mode == Z_MODE_SET_RANGE:
        if zstack.z_max_mm <= zstack.z_min_mm:
            return "Z planes: Z-max must be greater than Z-min."
        if use_laser_af or use_contrast_af:
            return "Set Range z mode is incompatible with autofocus."
    if r_plans and not record_slices.resolve(zstack_plane_count_for_plan(zstack)):
        return "Pick at least one plane to record (Bottom / Mid / Top / Select…)."
    if use_laser_af and not laser_af_has_reference:
        return "Laser AF is enabled but no reference position has been captured."
    if use_focus_map and not focus_map_ready:
        return "Use Focus Map is ticked but no focus surface is fitted (add focus points on the Focus Map tab)."
    return None


def contrast_af_applies(params: "RecordZStackAcquisitionParameters") -> bool:
    """True when contrast AF would actually run per FOV: ticked, laser AF not
    taking precedence, and the stack is a single plane or centred (Relative) —
    the same rule as MultiPointWorker.perform_autofocus."""
    if not params.use_contrast_af or params.use_laser_af:
        return False
    return zstack_plane_count_for_plan(params.zstack) == 1 or params.zstack.mode == Z_MODE_RELATIVE


def describe_focus_source(params: "RecordZStackAcquisitionParameters") -> str:
    """One line naming where each FOV's z reference comes from."""
    parts: List[str] = []
    if params.use_focus_map:
        parts.append("focus map z")
    if params.use_laser_af:
        parts.append("Laser AF")
    elif params.use_contrast_af:
        if contrast_af_applies(params):
            parts.append("Contrast AF")
        else:
            parts.append("Contrast AF (skipped: only runs with Nz = 1 or Relative)")
    if not parts:
        parts.append("stage z at each position")
    return "Focus: " + " → ".join(parts)


def _format_offsets(offsets: Sequence[float]) -> str:
    if len(offsets) == 1:
        return f"{offsets[0]:+.1f} µm"
    return f"{offsets[0]:+.1f} … {offsets[-1]:+.1f} µm"


def describe_per_fov_plan(params: RecordZStackAcquisitionParameters) -> str:
    """Human-readable per-FOV summary for the confirm dialog.

    Derived from the same helpers the worker uses, so the confirmed plan can
    never diverge from what is acquired.  Phases with no channels are omitted.
    """
    lines: List[str] = [describe_focus_source(params)]
    plan = params.zstack
    nz = zstack_plane_count_for_plan(plan)

    def stack_line(label: str, rows: List[ChannelPlan]) -> str:
        if plan.mode == Z_MODE_SET_RANGE:
            where = f"{plan.z_min_mm:.4f} … {plan.z_max_mm:.4f} mm abs"
        else:
            where = f"{_format_offsets(zstack_offsets_for_mode(plan, 0.0))} rel. reference"
        line = f"{label}: {len(rows)} ch × {nz} plane{'s' if nz != 1 else ''} ({where})"
        geometries = list(dict.fromkeys(p.geometry for p in rows))  # table order
        if len(geometries) > 1:
            line += (
                f"\n    {len(geometries)} camera settings: acquired one after another into separate stores "
                "(not overlaid)"
            )
            for g in geometries:
                names = ", ".join(p.name for p in rows if p.geometry == g)
                line += f"\n    {names}: {describe_geometry(g)}"
        elif not rows[0].geometry.is_inherit:
            line += f"\n    {describe_geometry(rows[0].geometry)}"
        return line

    if params.zi_channels:
        lines.append(stack_line("Zi", [p for p in params.channels if p.zi]))
    r_plans = params.r_plans
    if r_plans:
        planes = recording_offsets_for_plan(
            plan, params.record_slices, plan.z_min_mm if plan.mode == Z_MODE_SET_RANGE else 0.0
        )
        n_rec = len(planes)
        per_fov_s = n_rec * sum(p.duration_s for p in r_plans)
        if plan.mode == Z_MODE_SET_RANGE:
            where = ", ".join(f"{slice_label(i, nz)} {plan.z_min_mm + off / 1000.0:.4f} mm" for i, off in planes)
        else:
            where = ", ".join(f"{slice_label(i, nz)} {off:+.1f} µm" for i, off in planes)
        recs = ", ".join(
            f"{p.name} {p.fps:g} fps × {p.duration_s:g} s"
            + ("" if p.geometry.is_inherit else f" [{describe_geometry(p.geometry)}]")
            for p in r_plans
        )
        lines.append(
            f"R: {len(r_plans)} recording{'s' if len(r_plans) != 1 else ''} × {n_rec} of {nz} "
            f"plane{'s' if nz != 1 else ''} ({where}): {recs} — {per_fov_s:.1f} s/FOV"
        )
    if params.zf_channels:
        lines.append(stack_line("Zf", [p for p in params.channels if p.zf]))
    return "\n".join(lines)


# ---------------------------------------------------------------------------- frame-rate test


def frame_rate_stats(
    timestamps: Sequence[float], frame_ids: Sequence[int], duration_s: float
) -> Tuple[float, float, float, int]:
    """(mean_fps, min_fps, max_interval_s, id_gaps) for a free-run sample.

    mean_fps spans first-to-last frame (or the whole duration when only one
    frame arrived); min_fps is the rate implied by the longest gap between
    consecutive frames; id_gaps counts frame ids the camera skipped.
    """
    n = len(timestamps)
    if n < 2:
        mean = (1.0 / duration_s) if (n == 1 and duration_s > 0) else 0.0
        return mean, 0.0, 0.0, 0
    intervals = [b - a for a, b in zip(timestamps[:-1], timestamps[1:])]
    max_interval = max(intervals)
    span = timestamps[-1] - timestamps[0]
    mean = (n - 1) / span if span > 0 else 0.0
    min_fps = (1.0 / max_interval) if max_interval > 0 else 0.0
    gaps = 0
    for a, b in zip(frame_ids[:-1], frame_ids[1:]):
        if b > a + 1:
            gaps += b - a - 1
    return mean, min_fps, max_interval, gaps


@dataclass
class FrameRateTestResult:
    """Outcome of a free-run frame-rate test for one channel's settings."""

    channel_name: str
    exposure_ms: float = 0.0
    gain: float = 0.0
    requested_fps: float = 0.0  # the rate the camera said it would run at
    duration_s: float = 0.0
    frame_count: int = 0
    mean_fps: float = 0.0
    min_fps: float = 0.0
    max_interval_s: float = 0.0
    id_gaps: int = 0
    error: Optional[str] = None

    def summary(self) -> str:
        if self.error:
            return f"failed: {self.error}"
        if self.frame_count == 0:
            return f"no frames in {self.duration_s:.1f} s"
        gaps = f", {self.id_gaps} gap{'s' if self.id_gaps != 1 else ''}" if self.id_gaps else ""
        return f"{self.mean_fps:.1f} fps (min {self.min_fps:.1f}{gaps} over {self.duration_s:.1f} s)"

    def detail(self) -> str:
        return (
            f"{self.channel_name}: exposure {self.exposure_ms:g} ms, gain {self.gain:g}\n"
            f"camera offered {self.requested_fps:.2f} fps; {self.frame_count} frames in {self.duration_s:.1f} s\n"
            f"mean {self.mean_fps:.2f} fps, longest gap {self.max_interval_s * 1000:.0f} ms, "
            f"{self.id_gaps} skipped frame id(s)"
        )


# ---------------------------------------------------------------------------- snapshot


def _build_objective_info(objective_store, camera) -> dict:
    """Build the informational `objective:` YAML section.

    Mirrors the dict multi_point_controller.py builds before calling
    _save_acquisition_yaml, adapted to tolerate camera=None (the record widget's
    Save-button path may not always have a live camera reference).
    """
    current_objective = objective_store.current_objective
    objective_dict = objective_store.objectives_dict.get(current_objective, {})

    camera_binning = list(camera.get_binning()) if camera is not None else None
    # compute_pixel_size_um tolerates camera=None (returns None on any failure).
    pixel_size_um = compute_pixel_size_um(objective_store, camera)

    return {
        "name": current_objective,
        "magnification": objective_dict.get("magnification"),
        "pixel_size_um": pixel_size_um,
        "camera_binning": camera_binning,
    }


def _row_yaml(plan: ChannelPlan, objective_info: Optional[dict]) -> dict:
    """A channel row for acquisition.yaml; a row that pins binning also records its pixel size."""
    d = plan.to_yaml_dict()
    binning = plan.geometry.binning
    info = objective_info or {}
    live_binning = (info.get("camera_binning") or [None])[0]
    if binning is not None and info.get("pixel_size_um") and live_binning:
        d["camera"]["pixel_size_um"] = info["pixel_size_um"] / live_binning * binning
    return d


def _save_record_zstack_yaml(
    params: "RecordZStackAcquisitionParameters",
    yaml_path: str,
    scan_coordinates=None,
    objective_info: dict = None,
) -> None:
    """Save full Time-at-XY acquisition settings to *yaml_path*.

    Mirrors multi_point_controller.py's _save_acquisition_yaml for the
    wellplate/flexible widgets, but kept separate: this widget's shape (a
    per-channel Zi/R/Zf table with per-row fps/duration, shared recording
    planes, and a z mode block) doesn't fit the shared builder without
    polluting it with fields the other two widgets have no reason to know about.
    """
    z = params.zstack
    yaml_dict = {
        "acquisition": {
            "experiment_id": params.experiment_id,
            "start_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "widget_type": "record_zstack",
            "xy_mode": params.xy_mode,
        },
        "objective": objective_info or {},
        "time_series": {
            "nt": params.Nt,
            "delta_t_s": params.dt_s,
        },
        "autofocus": {
            "laser_af": params.use_laser_af,
            "contrast_af": params.use_contrast_af,
            "use_focus_map": params.use_focus_map,
        },
        "channels": [_row_yaml(p, objective_info) for p in params.channels],
        "record_slices": params.record_slices.to_yaml_dict(),
        "z_stack": {
            "mode": z.mode,
            "dz_um": z.dz_um,
            "nz": z.nz,
            "z_range_um": z.z_range_um,
            "z_min_mm": z.z_min_mm,
            "z_max_mm": z.z_max_mm,
        },
    }

    if params.xy_mode == "Select Wells":
        region_centers = scan_coordinates.region_centers if scan_coordinates is not None else {}
        region_shapes = scan_coordinates.region_shapes if scan_coordinates is not None else {}
        yaml_dict["wellplate_scan"] = {
            "scan_size_mm": params.scan_size_mm,
            "overlap_percent": params.overlap_percent,
            "regions": [
                {"name": name, "center_mm": _serialize_for_yaml(center), "shape": region_shapes.get(name)}
                for name, center in region_centers.items()
            ],
        }
    elif params.xy_mode == "Load Coordinates":
        # Explicit positions (e.g. smart-acquisition targets): keep every FOV, with its z when it has
        # one, so the file can be loaded straight back into Load Coordinates mode.
        region_fovs = scan_coordinates.region_fov_coordinates if scan_coordinates is not None else {}
        region_centers = scan_coordinates.region_centers if scan_coordinates is not None else {}
        yaml_dict["wellplate_scan"] = {
            "regions": [
                {
                    "name": name,
                    "center_mm": _serialize_for_yaml(region_centers.get(name, fovs[0] if fovs else None)),
                    "shape": "Manual",
                    "fovs": [[float(v) for v in fov] for fov in fovs],
                }
                for name, fovs in region_fovs.items()
            ],
        }

    # Let OSError/yaml.YAMLError propagate: both real call sites already handle
    # failures appropriately one level up -- run_acquisition()'s snapshot call site
    # wraps this in try/except Exception: log.exception(...) so a failed settings
    # snapshot never aborts a real acquisition, and the Save Settings button
    # handler (_on_save_settings_clicked) wraps this in try/except Exception:
    # QMessageBox.warning(...) so the user is told the save failed. Swallowing the
    # error here made that button's warning dialog unreachable.
    with open(yaml_path, "w", encoding="utf-8") as f:
        f.write(f"# Time at XY Acquisition Parameters - {params.experiment_id}\n\n")
        yaml.dump(yaml_dict, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


# ---------------------------------------------------------------------------- controller


class RecordZStackController:
    """Controller that spawns RecordZStackWorker on a daemon thread.

    Constructor arguments mirror MultiPointController's signature where the concept
    overlaps. Call run_acquisition(params) with a fully-built
    RecordZStackAcquisitionParameters object (as the widget does via build_parameters()).
    """

    def __init__(
        self,
        microscope,
        live_controller,
        laser_autofocus_controller,
        objective_store,
        scan_coordinates,
        callbacks,
        display_frame_fn=None,
        display_fps: float = 0.0,
        autofocus_controller=None,
    ):
        self._microscope = microscope
        self._live_controller = live_controller
        self._laser_af = laser_autofocus_controller
        self._autofocus_controller = autofocus_controller
        self._objective_store = objective_store
        self._scan_coordinates = scan_coordinates
        self._callbacks = callbacks
        self._display_frame_fn = display_frame_fn
        self._display_fps = display_fps

        self._abort_event: Event = Event()
        self._thread: Optional[Thread] = None
        self._fps_test_thread: Optional[Thread] = None
        self._worker = None
        self._last_experiment_path: Optional[str] = None

    @property
    def last_experiment_path(self) -> Optional[str]:
        """Experiment folder of the most recently started acquisition (None before the first)."""
        return self._last_experiment_path

    @property
    def last_end_reason(self) -> Optional[str]:
        """How the most recent acquisition ended ("completed", "user_abort", "error", ...).

        None while it is still running or before the first one. For unattended callers (the
        Workflow Runner), which must not analyse or build on a run that failed.
        """
        if self._worker is None or self.acquisition_in_progress():
            return None
        return getattr(self._worker, "end_reason", None)

    # ---------------------------------------------------------------------- acquisition

    def acquisition_in_progress(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def request_abort(self) -> None:
        """Signal the running worker to stop after the current FOV."""
        self._abort_event.set()

    def _resolve_recording_rates(self, params: RecordZStackAcquisitionParameters) -> None:
        """Fix every R row's recording rate now, before anything starts.

        The camera's free-run maximum at the row's exposure bounds the request, and
        every FOV / timepoint, the dataset size, time_increment_s and acquisition.yaml
        all use that one number.  A row that pins its own binning / crop / pixel format
        is left to the worker: get_max_frame_rate answers for the camera's CURRENT
        geometry, which is not the one the row will record in.
        """
        for plan in params.r_plans:
            if not plan.geometry.is_inherit:
                plan.effective_fps = None
                continue
            exposure_ms = plan.channel.camera_settings.exposure_time_ms
            plan.effective_fps = resolve_effective_fps(self._microscope.camera, plan.fps, exposure_ms)
            if plan.effective_fps < plan.fps * (1 - 1e-6):
                log.warning(
                    f"{plan.name}: camera cannot deliver {plan.fps:g} fps at {exposure_ms:g} ms exposure "
                    f"(max {plan.effective_fps:.2f}); recording at {plan.effective_fps:.2f} fps"
                )
            else:
                log.info(f"{plan.name}: recording at {plan.effective_fps:g} fps")

    def run_acquisition(self, params: RecordZStackAcquisitionParameters) -> None:
        """Spawn the worker thread.

        *params* must be a fully-built RecordZStackAcquisitionParameters (as produced
        by the widget's build_parameters() method).
        """
        from control.core.acquisition_setup import create_experiment_dir
        from control.core.record_zstack_worker import RecordZStackWorker

        if not any(p.active for p in params.channels):
            # The widget's validate() already refuses this; reject programmatic
            # callers too so the worker never starts an acquisition that does nothing.
            raise ValueError("no channel has Zi, R, or Zf ticked")
        self._resolve_recording_rates(params)

        # Resolve and create a timestamped unique output directory.
        resolved_id, experiment_dir = create_experiment_dir(params.base_path, params.experiment_id)
        params.experiment_id = resolved_id
        self._last_experiment_path = experiment_dir
        self._worker = None

        # Snapshot the acquisition settings into the experiment directory
        # (mirrors MultiPointController.start_new_experiment) so the run is
        # reproducible/auditable: acquisition_channels.yaml records objective +
        # every channel used by any phase.
        try:
            # Rows are unique by name in the table, but dedupe defensively (first
            # row wins): channels are identified by name, so the snapshot must
            # not list one twice with different settings.
            channels = []
            seen_names = set()
            for ch in params.active_channels:
                if ch.name not in seen_names:
                    seen_names.add(ch.name)
                    channels.append(ch)
            self._microscope.config_repo.save_acquisition_output(
                output_dir=experiment_dir,
                objective=self._objective_store.current_objective,
                channels=channels,
                confocal_mode=self._live_controller.is_confocal_mode(),
            )
        except Exception:
            log.exception("Failed to save acquisition settings snapshot to the experiment directory")

        # Full reusable settings snapshot (superset of acquisition_channels.yaml above,
        # written alongside it — not a replacement; see design doc's "Snapshot files" decision).
        try:
            objective_info = _build_objective_info(self._objective_store, self._microscope.camera)
            _save_record_zstack_yaml(
                params,
                os.path.join(experiment_dir, "acquisition.yaml"),
                self._scan_coordinates,
                objective_info,
            )
        except Exception:
            log.exception("Failed to save full record_zstack acquisition.yaml snapshot")

        # Collect scan coordinates: {region_id: [(x_mm, y_mm[, z_mm]), ...]}
        scan_region_fov_coords = {}
        if self._scan_coordinates is not None:
            scan_region_fov_coords = {
                region_id: list(coords) for region_id, coords in self._scan_coordinates.region_fov_coordinates.items()
            }
        if params.use_focus_map and params.focus_map is not None:
            scan_region_fov_coords = self._apply_focus_map(params.focus_map, scan_region_fov_coords)

        # Clear abort event for this run (thread-safe: Event.clear() is atomic).
        # The small window between clear() and the worker thread starting is safe
        # under the single-acquisition-at-a-time assumption enforced by the widget
        # (toggle_acquisition checks acquisition_in_progress() before calling here).
        self._abort_event.clear()

        # Acquisition watchdog breadcrumb, as MultiPointController writes it.  channels x z are the
        # z-stack images per FOV, initial and final stacks together (a recording is a stream, not
        # counted as images).
        stack_channels = len(params.zi_channels) + len(params.zf_channels)
        z = zstack_plane_count_for_plan(params.zstack) if stack_channels else 0
        run_state_writer = start_run_state_writer(
            experiment_id=params.experiment_id,
            output_path=experiment_dir,
            timepoints=params.Nt,
            region_fov_coords=scan_region_fov_coords,
            channels=stack_channels,
            z=z,
        )
        try:
            worker = RecordZStackWorker(
                scope=self._microscope,
                live_controller=self._live_controller,
                laser_auto_focus_controller=self._laser_af,
                objective_store=self._objective_store,
                params=params,
                callbacks=self._callbacks,
                abort_requested_fn=lambda: self._abort_event.is_set(),
                request_abort_fn=self.request_abort,
                scan_region_fov_coords=scan_region_fov_coords,
                display_frame_fn=self._display_frame_fn,
                display_fps=self._display_fps,
                run_state_writer=run_state_writer,
                auto_focus_controller=self._autofocus_controller,
            )
        except Exception:
            # No worker will end the breadcrumb; close it so the watchdog does not read a hang.
            run_state_writer.end("error", None)
            raise

        self._worker = worker
        self._thread = Thread(target=worker.run, name="RecordZStack-acquisition", daemon=True)
        self._thread.start()

    def _apply_focus_map(
        self, focus_map, scan_region_fov_coords: Dict[object, List[Tuple]]
    ) -> Dict[object, List[Tuple]]:
        """Return the FOV coordinates with z interpolated from the fitted focus map.

        Mirrors MultiPointController.run_acquisition's focus-surface loop: only this
        acquisition's private copy of the coordinates is rewritten (the GUI's
        ScanCoordinates keeps the user-configured ones), and the worker's
        ``_move_xy`` then moves to that z before the reference is
        established.  A region the map does not know (e.g. the tab's "current"
        single-position region with a per-region fit) falls back to the global
        surface; any other failure keeps that FOV's original coordinate.
        """
        log.info("Using focus surface for Z interpolation")
        out: Dict[object, List[Tuple]] = {}
        for region_id, coords in scan_region_fov_coords.items():
            new_coords = []
            for i, coord in enumerate(coords):
                x, y = coord[0], coord[1]
                try:
                    try:
                        z = focus_map.interpolate(x, y, region_id)
                    except ValueError:
                        z = focus_map.interpolate(x, y)
                except Exception as exc:
                    log.warning(f"focus map: no z for region {region_id!r} fov {i}: {exc}; keeping its coordinate")
                    new_coords.append(tuple(coord))
                    continue
                new_coords.append((x, y, float(z)))
            out[region_id] = new_coords
        return out

    def join(self, timeout: Optional[float] = None) -> None:
        """Wait for the acquisition thread to finish (useful in tests and scripts)."""
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    # ---------------------------------------------------------------------- frame-rate test

    def frame_rate_test_in_progress(self) -> bool:
        return self._fps_test_thread is not None and self._fps_test_thread.is_alive()

    def run_frame_rate_test(
        self,
        channel: AcquisitionChannel,
        duration_s: float,
        on_finished,
        geometry: Optional[CameraGeometry] = None,
    ) -> None:
        """Measure the camera's maximum free-run rate for *channel*'s exposure/gain.

        Runs on a daemon thread: stops live view, applies the channel (illumination
        stays OFF) and the row's camera *geometry* (binning / crop / pixel format;
        unset fields keep the camera's), streams CONTINUOUS at the highest rate the
        camera accepts for ``duration_s``, then restores the previous camera/live
        state and calls ``on_finished(FrameRateTestResult)`` from that thread (pass
        a Qt signal's ``emit`` to hop back to the GUI thread).
        """
        if self.acquisition_in_progress():
            raise RuntimeError("An acquisition is running; the frame-rate test must wait for it to finish.")
        if self.frame_rate_test_in_progress():
            raise RuntimeError("A frame-rate test is already running.")
        self._fps_test_thread = Thread(
            target=self._frame_rate_test_thread,
            args=(channel, float(duration_s), on_finished, geometry or CameraGeometry()),
            name="RecordZStack-fps-test",
            daemon=True,
        )
        self._fps_test_thread.start()

    def _frame_rate_test_thread(
        self, channel: AcquisitionChannel, duration_s: float, on_finished, geometry: CameraGeometry
    ) -> None:
        from control.core import camera_geometry
        from control.core.streaming_capture import ContinuousFrameSource
        from squid.abc import CameraAcquisitionMode

        live = self._live_controller
        camera = self._microscope.camera
        result = FrameRateTestResult(
            channel_name=channel.name,
            exposure_ms=float(channel.exposure_time),
            gain=float(channel.analog_gain),
            duration_s=duration_s,
        )
        samples: List[Tuple[float, int]] = []
        lock = threading.Lock()

        def on_frame(camera_frame) -> None:
            with lock:
                samples.append((time.perf_counter(), int(getattr(camera_frame, "frame_id", len(samples)))))

        was_live = bool(live.is_live)
        prev_config = live.currentConfiguration
        prev_mode = None
        try:
            prev_mode = camera.get_acquisition_mode()
        except Exception:
            pass
        source = None
        snapshot = None
        try:
            if was_live:
                live.stop_live()
            if not geometry.is_inherit:
                snapshot = camera_geometry.snapshot_camera(camera)
                camera_geometry.apply_geometry(camera, camera_geometry.resolve(geometry, snapshot), snapshot)
            live.set_microscope_mode(channel)  # exposure/gain; illumination is not energized while not live
            try:
                live.turn_off_illumination()
            except Exception:
                log.debug("turn_off_illumination before the frame-rate test failed", exc_info=True)

            # Ask for an absurd rate: the camera clamps to the maximum its current
            # exposure/ROI/binning allow.  That maximum is only readable once the
            # stream runs; ContinuousFrameSource applies the hint there and returns
            # what the camera reported for it (the same readback a recording's
            # paced-or-not decision is made from).
            source = ContinuousFrameSource(camera, fps=10000.0)
            offered = source.start(on_frame)
            result.requested_fps = float(offered) if isinstance(offered, (int, float)) and offered > 0 else 0.0
            deadline = time.monotonic() + duration_s
            while time.monotonic() < deadline and not self._abort_event.is_set():
                time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
        except Exception as exc:
            log.exception("frame-rate test failed")
            result.error = str(exc) or exc.__class__.__name__
        finally:
            if source is not None:
                try:
                    source.stop()
                except Exception:
                    log.exception("failed to stop the frame-rate test stream")
            try:
                camera.set_acquisition_mode(
                    prev_mode
                    if prev_mode is not None and prev_mode != CameraAcquisitionMode.CONTINUOUS
                    else CameraAcquisitionMode.SOFTWARE_TRIGGER
                )
            except Exception:
                log.exception("failed to restore the camera acquisition mode after the frame-rate test")
            if snapshot is not None:
                try:
                    camera_geometry.restore_snapshot(camera, snapshot)
                except Exception:
                    log.exception("failed to restore the camera binning/crop/format after the frame-rate test")
            if prev_config is not None:
                try:
                    live.set_microscope_mode(prev_config)
                except Exception:
                    log.exception("failed to restore the channel after the frame-rate test")
            if was_live:
                try:
                    live.start_live()
                except Exception:
                    log.exception("failed to restart live view after the frame-rate test")

            with lock:
                stamps = [t for t, _ in samples]
                ids = [i for _, i in samples]
            result.frame_count = len(stamps)
            result.mean_fps, result.min_fps, result.max_interval_s, result.id_gaps = frame_rate_stats(
                stamps, ids, duration_s
            )
            log.info(f"frame-rate test: {result.detail().replace(chr(10), ' | ')}")
            try:
                on_finished(result)
            except Exception:
                log.exception("frame-rate test on_finished callback failed")

    # ---------------------------------------------------------------------- cleanup

    def close(self, timeout_s: float = 5.0) -> None:
        """Abort any running acquisition or frame-rate test."""
        if self.frame_rate_test_in_progress():
            self._abort_event.set()
            self._fps_test_thread.join(timeout=timeout_s)

        if self.acquisition_in_progress():
            self._abort_event.set()
            if self._thread is not None:
                self._thread.join(timeout=timeout_s)
                if self._thread.is_alive():
                    log.warning(f"RecordZStack acquisition thread did not stop within {timeout_s}s")

        self._thread = None
