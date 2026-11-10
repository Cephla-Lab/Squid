"""Write detected targets back out as a Squid acquisition.

The output is a normal flexible-type ``acquisition.yaml`` plus a sibling
``coordinates.csv`` -- no new format. Two different consumers read it and they
read *different fields*, so both must be written consistently:

* The GUI (``FlexibleMultiPointWidget._load_positions``) reads only
  ``center_mm`` and expands it by the NX/NY spinboxes, which it sets from
  ``flexible_scan.nx``/``ny`` first. Emitting ``nx: 1, ny: 1`` is therefore
  load-bearing: without it each target silently becomes an NX x NY grid.
* The headless path (``acquisition_settings.apply_acquisition_settings``)
  prefers the explicit per-position ``fovs`` list and forces NX=NY=1 anyway.

Channels, z-stack and autofocus come from a high-magnification *template*
acquisition.yaml, not from the low-mag survey.
"""

import copy
import csv
import math
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import yaml

import squid.logging

from .targets import Target

_log = squid.logging.get_logger(__name__)

#: Region ids beginning with this are parsed as manual regions by
#: ScanCoordinates.sort_coordinates, which does int(key[6:]) and raises.
_MANUAL_PREFIX = "manual"
_WELL_RE = re.compile(r"^[A-Za-z]{1,2}\d{1,3}$")


class TargetValidationError(ValueError):
    pass


#: Largest inter-objective offset accepted, in mm. Real parcentric/parfocal
#: mismatches are tens to hundreds of microns; anything past these bounds is
#: almost certainly a units mistake (microns typed into a millimetre field).
MAX_XY_OFFSET_MM = 5.0
MAX_Z_OFFSET_MM = 2.0


#: Millimetre decimals kept when writing coordinates. 1e-6 mm is a nanometre --
#: far finer than any stage resolves, and it keeps float noise like
#: 3.0029999999995827 out of the emitted files.
_MM_DECIMALS = 6


def _round_mm(values):
    return [round(float(v), _MM_DECIMALS) for v in values]


def sanitize_region_name(name: str) -> str:
    """Make a region id safe for ScanCoordinates and for filename parsing.

    Three separate traps: names starting with 'manual' crash sort_coordinates;
    names shaped like a well id ('A1') get silently reordered into plate order;
    and underscores break the stitcher's ``file.split('_', 3)`` filename parser.
    """
    clean = re.sub(r"[^A-Za-z0-9]+", "-", str(name)).strip("-") or "target"
    if clean.lower().startswith(_MANUAL_PREFIX):
        clean = "t-" + clean
    if _WELL_RE.match(clean):
        clean = "t-" + clean
    return clean


def _software_pos_limit() -> Optional[Any]:
    try:
        from control._def import SOFTWARE_POS_LIMIT

        return SOFTWARE_POS_LIMIT
    except Exception:  # pragma: no cover - offline use without machine config
        return None


def filter_to_stage_limits(targets: Sequence[Target]) -> Tuple[List[Target], List[Target]]:
    """Split targets into (inside, outside) the software stage limits.

    ``ScanCoordinates.add_region_from_fovs`` raises on the *first* out-of-limit
    FOV, which would discard the whole region. A lattice fit can legitimately
    extrapolate a node past the edge of the plate, so drop those here and report
    them rather than letting the load fail.
    """
    limits = _software_pos_limit()
    if limits is None:
        return list(targets), []

    inside, outside = [], []
    for t in targets:
        ok = limits.X_NEGATIVE <= t.x_mm <= limits.X_POSITIVE and limits.Y_NEGATIVE <= t.y_mm <= limits.Y_POSITIVE
        if ok and t.z_mm is not None:
            ok = limits.Z_NEGATIVE <= t.z_mm <= limits.Z_POSITIVE
        (inside if ok else outside).append(t)
    return inside, outside


def _normalize_z(targets: Sequence[Target]) -> List[Target]:
    """Z is all-or-nothing per region downstream; make it all-or-nothing here.

    ``add_region_from_fovs`` computes ``has_z = all(len(fov) > 2 ...)`` and
    ``read_coordinates_csv`` applies the same rule, so a single target missing z
    silently drops z for every other target. Decide explicitly instead.
    """
    if not targets:
        return []
    if all(t.z_mm is not None for t in targets):
        return list(targets)
    if any(t.z_mm is not None for t in targets):
        _log.warning("Some targets lack z; dropping z from all of them so the region stays homogeneous.")
    return [
        Target(
            x_mm=t.x_mm,
            y_mm=t.y_mm,
            z_mm=None,
            label=t.label,
            score=t.score,
            source_region=t.source_region,
            source_fov=t.source_fov,
            measured=t.measured,
            metrics=t.metrics,
        )
        for t in targets
    ]


def _finite(value) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise TargetValidationError(f"Offset component {value!r} is not a finite number")
    return number


def validate_offsets(
    xy_offset_mm: Optional[Sequence[float]] = None, z_offset_mm: Optional[float] = None
) -> Tuple[Optional[Tuple[float, float]], Optional[float]]:
    """Normalise offsets to (dx, dy) / dz floats, raising TargetValidationError when out of bounds."""
    xy = None
    if xy_offset_mm is not None:
        try:
            dx, dy = (_finite(v) for v in xy_offset_mm)
        except (TypeError, ValueError) as e:
            raise TargetValidationError(f"xy_offset_mm must be [dx, dy] in mm, got {xy_offset_mm!r}") from e
        if abs(dx) > MAX_XY_OFFSET_MM or abs(dy) > MAX_XY_OFFSET_MM:
            raise TargetValidationError(
                f"xy_offset_mm {xy_offset_mm!r} exceeds +/-{MAX_XY_OFFSET_MM} mm; offsets are in millimetres"
            )
        xy = (dx, dy)
    dz = None
    if z_offset_mm is not None:
        dz = _finite(z_offset_mm)
        if abs(dz) > MAX_Z_OFFSET_MM:
            raise TargetValidationError(
                f"z_offset_mm {z_offset_mm!r} exceeds +/-{MAX_Z_OFFSET_MM} mm; offsets are in millimetres"
            )
    return xy, dz


def apply_offsets(
    targets: Sequence[Target],
    xy_offset_mm: Optional[Sequence[float]] = None,
    z_offset_mm: Optional[float] = None,
) -> List[Target]:
    """Shift targets by the offset between the survey and the follow-up objective.

    Convention: ``target_high = target_low + offset``. To measure it, centre one
    feature under the survey objective and note the stage XY(Z), switch
    objectives, re-centre and note again; the offset is the second minus the
    first. Z is shifted only for targets that carry a z (``--carry-z``); a z
    offset without any z to apply it to is reported once, not silently ignored.
    """
    xy, dz = validate_offsets(xy_offset_mm, z_offset_mm)
    if xy is None and dz is None:
        return list(targets)
    if dz is not None and targets and not any(t.z_mm is not None for t in targets):
        _log.warning("z_offset_mm given but no target carries a z (use carry_z); the z offset has no effect.")
    out = []
    for t in targets:
        metrics = dict(t.metrics)
        x, y, z = t.x_mm, t.y_mm, t.z_mm
        if xy is not None:
            x, y = x + xy[0], y + xy[1]
            metrics["xy_offset_mm"] = [xy[0], xy[1]]
        if dz is not None and z is not None:
            z = z + dz
            metrics["z_offset_mm"] = dz
        out.append(
            Target(
                x_mm=x,
                y_mm=y,
                z_mm=z,
                label=t.label,
                score=t.score,
                source_region=t.source_region,
                source_fov=t.source_fov,
                measured=t.measured,
                metrics=metrics,
            )
        )
    return out


def label_targets(targets: Sequence[Target], prefix: str = "smart") -> List[Target]:
    """Give every target a unique, downstream-safe region id."""
    out, seen = [], {}
    for i, t in enumerate(targets):
        base = sanitize_region_name(f"{prefix}-{t.source_region or 'fov'}-{t.label or i}")
        n = seen.get(base, 0)
        seen[base] = n + 1
        out.append(
            Target(
                x_mm=t.x_mm,
                y_mm=t.y_mm,
                z_mm=t.z_mm,
                label=base if n == 0 else f"{base}-{n}",
                score=t.score,
                source_region=t.source_region,
                source_fov=t.source_fov,
                measured=t.measured,
                metrics=t.metrics,
            )
        )
    return out


def load_template(template_path: str) -> Dict[str, Any]:
    if os.path.isdir(template_path):
        template_path = os.path.join(template_path, "acquisition.yaml")
    with open(template_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Template {template_path} is not a YAML mapping")
    return data


def template_widget_type(template: Dict[str, Any]) -> str:
    """The widget type an emitted acquisition will run in: the template's, or flexible."""
    widget_type = str((template.get("acquisition") or {}).get("widget_type") or "flexible").lower()
    return "record_zstack" if widget_type == "record_zstack" else "flexible"


def _prepare_common(template: Dict[str, Any], widget_type: str) -> Dict[str, Any]:
    out = copy.deepcopy(template)

    acq = dict(out.get("acquisition") or {})
    acq["widget_type"] = widget_type
    acq["xy_mode"] = "Load Coordinates"
    acq.pop("experiment_id", None)
    acq.pop("start_time", None)
    out["acquisition"] = acq

    # Drop the identity checks that would reject this file. validate_hardware
    # compares objective name and camera binning against what is mounted *now*;
    # surveying at 4x and imaging at 20x is guaranteed to mismatch, and both
    # checks are skipped when the field is absent.
    objective = dict(out.get("objective") or {})
    for key in ("name", "camera_binning"):
        objective.pop(key, None)
    out["objective"] = objective
    return out


def build_acquisition_dict(
    targets: Sequence[Target], template: Dict[str, Any], notes: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Build the acquisition mapping for ``targets`` in the template's widget type.

    * A wellplate/flexible template becomes a *flexible* acquisition with one
      position per target (``nx = ny = 1``, see the module docstring).
    * A ``record_zstack`` template (the Record + Z-Stack tab) keeps its recording
      and z-stack settings, focus setting and time series, and
      lists the targets as explicit ``wellplate_scan.regions`` for that tab's
      "Load Coordinates" mode. Each region carries both ``center_mm`` and a
      one-element ``fovs`` list so the per-target z (when carried) reaches the
      worker, which moves z per FOV.

      When every target carries a z, a template that ticks *Use Focus Map* has
      that flag cleared: the carried z (survey z + parfocal offset) already
      plays the focus map's role, an unattended run has no fitted surface to
      start from, and the map would overwrite the carried z. The reason is
      appended to ``notes`` when a list is given.
    """
    widget_type = template_widget_type(template)
    out = _prepare_common(template, widget_type)

    if widget_type == "record_zstack":
        out.pop("flexible_scan", None)
        scan = dict(out.get("wellplate_scan") or {})
        scan["regions"] = [
            {"name": t.label, "center_mm": t.as_fov(), "shape": "Manual", "fovs": [t.as_fov()]} for t in targets
        ]
        out["wellplate_scan"] = scan
        autofocus = dict(out.get("autofocus") or {})
        targets_carry_z = bool(targets) and all(t.z_mm is not None for t in targets)
        if autofocus.get("use_focus_map") and targets_carry_z:
            autofocus["use_focus_map"] = False
            out["autofocus"] = autofocus
            message = (
                "template ticks Use Focus Map; cleared because every target carries its own z "
                "(survey z + dz), which takes the focus map's place"
            )
            _log.warning(message)
            if notes is not None:
                notes.append(message)
        return out

    out.pop("wellplate_scan", None)
    flexible = dict(out.get("flexible_scan") or {})
    flexible["nx"] = 1
    flexible["ny"] = 1
    flexible["positions"] = [{"name": t.label, "center_mm": t.as_fov(), "fovs": [t.as_fov()]} for t in targets]
    out["flexible_scan"] = flexible
    return out


def coordinate_rows(targets: Sequence[Target]) -> Tuple[List[str], List[List[Any]]]:
    has_z = bool(targets) and all(t.z_mm is not None for t in targets)
    header = ["region", "x (mm)", "y (mm)"] + (["z (mm)"] if has_z else [])
    rows = []
    for t in targets:
        row = [t.label] + _round_mm([t.x_mm, t.y_mm])
        if has_z:
            row.extend(_round_mm([t.z_mm]))
        rows.append(row)
    return header, rows


def write_targets(
    targets: Sequence[Target],
    template_path: str,
    out_dir: str,
    prefix: str = "smart",
    xy_offset_mm: Optional[Sequence[float]] = None,
    z_offset_mm: Optional[float] = None,
    source_acquisition: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate, label and write ``acquisition.yaml`` + ``coordinates.csv``.

    Offsets (see :func:`apply_offsets`) are applied *before* the stage-limit
    filter, so a target the offset pushes off the plate is dropped and counted
    rather than written. Returns a summary dict describing what was written
    and what was dropped.
    """
    if not targets:
        raise TargetValidationError("No targets to write.")

    template = load_template(template_path)
    xy, dz = validate_offsets(xy_offset_mm, z_offset_mm)
    shifted = apply_offsets(targets, xy_offset_mm=xy, z_offset_mm=dz)
    normalized = _normalize_z(shifted)
    inside, outside = filter_to_stage_limits(normalized)
    if not inside:
        raise TargetValidationError(
            f"All {len(targets)} target(s) fall outside the software stage limits; nothing to write."
        )
    if outside:
        _log.warning(f"Dropped {len(outside)} target(s) outside the software stage limits.")

    labelled = label_targets(inside, prefix=prefix)

    os.makedirs(out_dir, exist_ok=True)
    yaml_path = os.path.join(out_dir, "acquisition.yaml")
    csv_path = os.path.join(out_dir, "coordinates.csv")

    notes: List[str] = []
    acquisition = build_acquisition_dict(labelled, template, notes=notes)
    # Provenance: how these coordinates came to be. Unknown top-level keys are
    # ignored by every acquisition.yaml reader, so this is safe to carry along.
    acquisition["smart"] = {
        "source_acquisition": source_acquisition,
        "template": os.path.abspath(template_path),
        "prefix": prefix,
        "xy_offset_mm": list(xy) if xy is not None else None,
        "z_offset_mm": dz,
        "targets": len(labelled),
        "inferred": sum(1 for t in labelled if not t.measured),
    }
    with open(yaml_path, "w", encoding="utf-8") as f:
        yaml.dump(
            acquisition,
            f,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )

    header, rows = coordinate_rows(labelled)
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)

    return {
        "acquisition_yaml": yaml_path,
        "coordinates_csv": csv_path,
        "written": len(labelled),
        "dropped_out_of_limits": len(outside),
        "inferred": sum(1 for t in labelled if not t.measured),
        "has_z": "z (mm)" in header,
        "widget_type": template_widget_type(template),
        "xy_offset_mm": list(xy) if xy is not None else None,
        "z_offset_mm": dz,
        # Template settings changed on the way out (e.g. Use Focus Map cleared), for the log.
        "notes": notes,
        # The targets exactly as written: offset, filtered to the stage limits, labelled.
        "targets": labelled,
    }
