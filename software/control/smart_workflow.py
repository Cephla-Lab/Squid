"""Build the smart-ring recipe workflow from what an operator supplies.

The recipe: survey the wells selected in the survey acquisition.yaml at low
magnification, find the structures in the survey, switch objective, and image
every structure with the follow-up acquisition (for the 4DCell rings: a Time
at XY time-lapse). It is an ordinary Workflow Runner workflow, so the result
can be saved, edited step by step, and re-run. No Qt in here; the wizard dialog
is a thin skin over :func:`build_smart_ring_workflow`.
"""

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import yaml

import squid.logging
from control.workflow_runner import (
    LAST_TARGETS_PLACEHOLDER,
    SequenceItem,
    SequenceType,
    Workflow,
    parse_well_list,
)

_log = squid.logging.get_logger(__name__)

#: The wellplate XY mode whose regions are whole wells.
SELECT_WELLS = "Select Wells"


@dataclass
class SmartRingRecipe:
    """The operator's inputs. Offsets follow ``target_high = target_low + offset``.

    The wells to survey are the ones selected in ``survey_yaml`` (see :func:`survey_wells`).
    """

    survey_yaml: str
    objective_low: str
    template_yaml: str
    objective_high: str
    xy_offset_mm: Optional[List[float]] = None
    z_offset_mm: Optional[float] = None
    analyzer: str = "ring_grid"
    analyzer_params: Dict[str, Any] = field(default_factory=dict)
    carry_z: bool = True
    output_dir: Optional[str] = None
    require_laser_af_reference: bool = True
    overlay: bool = True
    #: Base saving folder of the follow-up (Image targets) step; None = that tab's own folder.
    recording_dir: Optional[str] = None


def _read_yaml(path: str) -> Optional[dict]:
    try:
        if os.path.isdir(path):
            path = os.path.join(path, "acquisition.yaml")
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _widget_type(data: Optional[dict]) -> Optional[str]:
    if not data:
        return None
    return str((data.get("acquisition") or {}).get("widget_type") or "").lower() or None


def _objective_name(data: Optional[dict]) -> Optional[str]:
    if not data:
        return None
    return (data.get("objective") or {}).get("name")


def _has_binning(data: Optional[dict]) -> bool:
    return bool(data) and (data.get("objective") or {}).get("camera_binning") is not None


def _binning(data: Optional[dict]) -> Optional[Tuple[int, int]]:
    """The ``objective.camera_binning`` a file was saved at, or None when absent/malformed."""
    value = ((data or {}).get("objective") or {}).get("camera_binning")
    try:
        x, y = (int(v) for v in value)
    except (TypeError, ValueError):
        return None
    return x, y


def _binning_label(binning: Sequence[int]) -> str:
    return "x".join(str(int(v)) for v in binning)


def _image_size(data: Optional[dict]) -> Optional[Tuple[int, int]]:
    """The ``objective.image_size_px`` (frame size after the camera crop) a file was saved at."""
    value = ((data or {}).get("objective") or {}).get("image_size_px")
    try:
        width, height = (int(v) for v in value)
    except (TypeError, ValueError):
        return None
    return (width, height) if width > 0 and height > 0 else None


def _pixel_size_um(data: Optional[dict]) -> Optional[float]:
    try:
        value = float(((data or {}).get("objective") or {}).get("pixel_size_um"))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _lattice_span_mm(recipe: "SmartRingRecipe") -> Optional[Tuple[float, float, int, int]]:
    """Width, height (mm), rows, cols of one ring lattice, which ring_grid needs whole in every image."""
    if recipe.analyzer != "ring_grid":
        return None
    try:
        from control.core.smart_acquisition import analyzers

        params = dict(analyzers.defaults_for("ring_grid"))
    except Exception:
        return None
    params.update(recipe.analyzer_params or {})
    try:
        rows, cols = int(params["grid_rows"]), int(params["grid_cols"])
        pitch_um, diameter_um = float(params["grid_pitch_um"]), float(params["ring_diameter_um"])
    except (KeyError, TypeError, ValueError):
        return None
    return ((cols - 1) * pitch_um + diameter_um) / 1000.0, ((rows - 1) * pitch_um + diameter_um) / 1000.0, rows, cols


def _autofocus(data: Optional[dict]) -> dict:
    return dict((data or {}).get("autofocus") or {})


def _z_mode(data: Optional[dict]) -> Optional[str]:
    return ((data or {}).get("z_stack") or {}).get("mode")


def _wells_from(data: dict) -> List[str]:
    xy_mode = (data.get("acquisition") or {}).get("xy_mode") or SELECT_WELLS
    if xy_mode != SELECT_WELLS:
        raise ValueError(f"The survey acquisition.yaml must use {SELECT_WELLS} (got {xy_mode!r})")
    regions = (data.get("wellplate_scan") or {}).get("regions") or []
    wells = parse_well_list([r.get("name", "") if isinstance(r, dict) else r for r in regions])
    if not wells:
        raise ValueError(
            "The survey acquisition.yaml selects no wells: select them on the Wellplate tab's plate map "
            "and use Save Settings…"
        )
    return wells


def survey_wells(path: str) -> List[str]:
    """The wells selected in the survey acquisition.yaml (its ``wellplate_scan.regions``), in order.

    Raises ValueError, with a message for the operator, when the file is unreadable,
    not a wellplate acquisition, not in Select Wells mode, or selects no wells.
    """
    data = _read_yaml(path) if path else None
    if data is None:
        raise ValueError(f"Survey acquisition.yaml not readable: {path!r}")
    if _widget_type(data) != "wellplate":
        raise ValueError(f"The survey acquisition.yaml must be a wellplate acquisition (got {_widget_type(data)!r})")
    return _wells_from(data)


def validate_recipe(recipe: SmartRingRecipe) -> List[str]:
    """Errors that make the recipe unrunnable. Warnings (see :func:`recipe_warnings`) are separate."""
    errors = []
    if not recipe.objective_low or not recipe.objective_high:
        errors.append("Both objectives must be given")
    elif recipe.objective_low == recipe.objective_high:
        errors.append("The survey and follow-up objectives are the same; nothing to switch")

    survey = _read_yaml(recipe.survey_yaml) if recipe.survey_yaml else None
    if survey is None:
        errors.append(f"Survey acquisition.yaml not readable: {recipe.survey_yaml!r}")
    elif _widget_type(survey) != "wellplate":
        errors.append(f"The survey acquisition.yaml must be a wellplate acquisition (got {_widget_type(survey)!r})")
    else:
        try:
            _wells_from(survey)
        except ValueError as e:
            errors.append(str(e))

    template = _read_yaml(recipe.template_yaml) if recipe.template_yaml else None
    if template is None:
        errors.append(f"Follow-up template acquisition.yaml not readable: {recipe.template_yaml!r}")
    elif _widget_type(template) not in ("record_zstack", "flexible", "wellplate"):
        errors.append(f"Unsupported template widget_type {_widget_type(template)!r}")

    try:
        from control.core.smart_acquisition import analyzers
        from control.core.smart_acquisition.emit import validate_offsets

        if recipe.analyzer not in analyzers.available():
            errors.append(f"Unknown analyzer {recipe.analyzer!r}")
        else:
            try:
                analyzers.create(recipe.analyzer, recipe.analyzer_params or {})
            except Exception as e:
                errors.append(f"Analyzer parameters: {e}")
        validate_offsets(recipe.xy_offset_mm, recipe.z_offset_mm)
    except ImportError as e:
        errors.append(f"Smart acquisition unavailable: {e}")
    except ValueError as e:
        errors.append(str(e))
    return errors


def recipe_warnings(
    recipe: SmartRingRecipe,
    live_binning: Optional[Sequence[int]] = None,
    live_image_size: Optional[Sequence[int]] = None,
) -> List[str]:
    """Things worth a look before running unattended, but not fatal.

    ``live_binning`` and ``live_image_size`` are the camera's binning and frame size
    (after the Camera tab's crop) now, when known. The survey step refuses a survey
    file saved at another binning, but runs at whatever crop the camera has, so both
    are compared with the survey file; the 20x rows' own binning and crop are free
    to differ. The survey field of view is also checked against one ring lattice.
    """
    warnings = []
    survey = _read_yaml(recipe.survey_yaml) if recipe.survey_yaml else None
    template = _read_yaml(recipe.template_yaml) if recipe.template_yaml else None
    name = _objective_name(survey)
    if name and recipe.objective_low and name != recipe.objective_low:
        warnings.append(
            f"The survey acquisition.yaml was recorded with objective '{name}' but the recipe surveys with "
            f"'{recipe.objective_low}'; the file will be refused as a hardware mismatch"
        )
    survey_binning = _binning(survey)
    if survey_binning is not None:
        saved = _binning_label(survey_binning)
        if live_binning is None:
            warnings.append(
                f"The survey acquisition.yaml was saved at camera binning {saved}; the Camera tab must be at "
                f"{saved} when the workflow runs, or the survey step is refused"
            )
        elif tuple(int(v) for v in live_binning) != survey_binning:
            warnings.append(
                f"The survey acquisition.yaml was saved at camera binning {saved} but the camera is at "
                f"{_binning_label(live_binning)} now; the survey step will be refused unless the Camera tab is set "
                f"to {saved} before the workflow runs (or save the survey file again at the binning you want)"
            )
    warnings.extend(_crop_warnings(recipe, survey, survey_binning, live_binning, live_image_size))
    if _has_binning(template):
        warnings.append(
            "The follow-up template records a camera binning; that global value is ignored and the live binning "
            "applies, except to channel rows that set their own camera binning"
        )
    if recipe.z_offset_mm and not recipe.carry_z:
        warnings.append("A z offset has no effect unless the survey z is carried (carry_z)")
    if recipe.xy_offset_mm is None:
        warnings.append("No XY offset between the objectives; only right if they are parcentric to within a field")
    if _widget_type(template) == "record_zstack":
        # A Time at XY template: the settings below are applied unattended at every ring.
        autofocus = _autofocus(template)
        if autofocus.get("use_focus_map"):
            if recipe.carry_z:
                warnings.append(
                    "The follow-up template ticks Use Focus Map; the emitted run clears it because each ring "
                    "carries its own z (survey z + dz)"
                )
            else:
                warnings.append(
                    "The follow-up template ticks Use Focus Map; without carry_z the run refuses to start unless "
                    "a focus surface is fitted in the Focus Map tab"
                )
        if _z_mode(template) == "Set Range":
            warnings.append(
                "The follow-up template uses Set Range z planes (absolute Z-min/Z-max, autofocus off); every ring "
                "gets the same planes. Use From Bottom or Relative with Laser AF for the rings"
            )
        if not autofocus.get("laser_af"):
            warnings.append(
                "The follow-up template has Laser AF unticked; rings are imaged at the carried z (survey z + dz) only"
            )
    return warnings


def _crop_warnings(recipe, survey, survey_binning, live_binning, live_image_size) -> List[str]:
    warnings = []
    # Pixel counts only compare at the same binning; a binning mismatch is reported above.
    same_binning = (
        survey_binning is None or live_binning is None or tuple(int(v) for v in live_binning) == survey_binning
    )
    live = tuple(int(v) for v in live_image_size) if live_image_size else None
    saved = _image_size(survey)
    if same_binning and live and saved and live != saved:
        warnings.append(
            f"The survey acquisition.yaml was saved with {saved[0]}x{saved[1]} px images but the camera crop gives "
            f"{live[0]}x{live[1]} px now; the survey runs at the current crop, with its tiles recomputed from the "
            f"current field of view (or set the Camera tab crop back, or save the survey file again)"
        )

    # ring_grid looks for the whole lattice in each image: check the crop the survey will run at.
    size = live if (live and same_binning) else saved
    pixel_um = _pixel_size_um(survey)
    span = _lattice_span_mm(recipe)
    if size and pixel_um and span:
        fov_w, fov_h = size[0] * pixel_um / 1000.0, size[1] * pixel_um / 1000.0
        need_w, need_h, rows, cols = span
        if fov_w < need_w or fov_h < need_h:
            warnings.append(
                f"At {size[0]}x{size[1]} px ({pixel_um:.2f} um/px) the survey field of view is "
                f"{fov_w:.2f} x {fov_h:.2f} mm, smaller than one {rows}x{cols} ring lattice "
                f"({need_w:.2f} x {need_h:.2f} mm). ring_grid looks for the whole lattice in each image, so "
                f"Find targets will likely find no rings; widen the Camera tab crop before the workflow runs"
            )
    return warnings


def build_smart_ring_workflow(recipe: SmartRingRecipe) -> Workflow:
    """The five-step workflow. Raises ValueError when :func:`validate_recipe` finds errors."""
    errors = validate_recipe(recipe)
    if errors:
        raise ValueError("; ".join(errors))
    xy = [float(v) for v in recipe.xy_offset_mm] if recipe.xy_offset_mm is not None else None
    dz = float(recipe.z_offset_mm) if recipe.z_offset_mm is not None else None
    sequences = [
        SequenceItem(
            name=f"Objective {recipe.objective_low}",
            sequence_type=SequenceType.OBJECTIVE,
            objective=recipe.objective_low,
            require_laser_af_reference=recipe.require_laser_af_reference,
        ),
        SequenceItem(
            name=f"Survey {recipe.objective_low}",
            sequence_type=SequenceType.ACQUISITION,
            # No wells override: the survey file's own selected wells are surveyed.
            config_path=recipe.survey_yaml,
        ),
        SequenceItem(
            name="Find targets",
            sequence_type=SequenceType.ANALYZE,
            analyzer=recipe.analyzer,
            analyzer_params=dict(recipe.analyzer_params or {}) or None,
            template_path=recipe.template_yaml,
            output_dir=recipe.output_dir or None,
            carry_z=bool(recipe.carry_z),
            xy_offset_mm=xy,
            z_offset_mm=dz,
            overlay=bool(recipe.overlay),
        ),
        SequenceItem(
            name=f"Objective {recipe.objective_high}",
            sequence_type=SequenceType.OBJECTIVE,
            objective=recipe.objective_high,
            require_laser_af_reference=recipe.require_laser_af_reference,
        ),
        SequenceItem(
            name=f"Image targets {recipe.objective_high}",
            sequence_type=SequenceType.ACQUISITION,
            config_path=LAST_TARGETS_PLACEHOLDER,
            save_dir=(recipe.recording_dir or "").strip() or None,
        ),
    ]
    return Workflow(sequences=sequences, num_cycles=1, stop_on_failure=True)


# ---------------------------------------------------------------- remembered wizard inputs
#: Per-checkout memory of the wizard's inputs, like the other cache/ files. Best
#: effort: a missing or unreadable file just means nothing is remembered.
WIZARD_CACHE = os.path.join("cache", "smart_ring_wizard.yaml")


def _read_wizard_cache() -> dict:
    try:
        with open(WIZARD_CACHE, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, yaml.YAMLError):
        return {}


def _update_wizard_cache(key: str, value: Any) -> None:
    data = _read_wizard_cache()
    data[key] = value
    try:
        os.makedirs(os.path.dirname(WIZARD_CACHE) or ".", exist_ok=True)
        with open(WIZARD_CACHE, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=False)
    except OSError:
        _log.debug("Could not save the smart-ring wizard cache", exc_info=True)


def _pair_key(objective_low: str, objective_high: str) -> str:
    return f"{objective_low}->{objective_high}"


def load_objective_offset(objective_low: str, objective_high: str) -> Optional[Tuple[float, float, float]]:
    """The (dx, dy, dz) mm last used from *objective_low* to *objective_high*, or None."""
    entry = (_read_wizard_cache().get("objective_offsets") or {}).get(_pair_key(objective_low, objective_high))
    try:
        return float(entry["dx_mm"]), float(entry["dy_mm"]), float(entry["dz_mm"])
    except (KeyError, TypeError, ValueError):
        return None


def save_objective_offset(objective_low: str, objective_high: str, dx_mm: float, dy_mm: float, dz_mm: float) -> None:
    """Remember the offset between this objective pair for the next wizard."""
    offsets = dict(_read_wizard_cache().get("objective_offsets") or {})
    offsets[_pair_key(objective_low, objective_high)] = {
        "dx_mm": float(dx_mm),
        "dy_mm": float(dy_mm),
        "dz_mm": float(dz_mm),
    }
    _update_wizard_cache("objective_offsets", offsets)


def load_recording_folder() -> Optional[str]:
    """The recording folder the wizard last used, or None."""
    value = _read_wizard_cache().get("recording_folder")
    return str(value) if value else None


def save_recording_folder(path: Optional[str]) -> None:
    """Remember the recording folder for the next wizard; an empty one (the tab's own folder) is remembered too."""
    _update_wizard_cache("recording_folder", (path or "").strip() or None)
