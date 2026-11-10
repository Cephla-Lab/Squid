"""One call from survey folder to runnable acquisition: analyse, overlay, emit.

This is the in-process form of ``scripts/analyze_to_targets.py`` for callers
that already live inside Squid (the Workflow Runner's *analyze* step). No Qt,
no hardware; it composes :mod:`pipeline` and :mod:`emit` and turns the two
ways an analysis can come up empty into one exception the caller can gate on.
"""

import logging
import os
from typing import Any, Callable, Dict, Optional, Sequence

from . import emit, pipeline

_log = logging.getLogger(__name__)


def analyze_and_emit(
    acq_dir: str,
    analyzer: str,
    params: Optional[Dict[str, Any]],
    template_path: str,
    out_dir: Optional[str] = None,
    *,
    carry_z: bool = False,
    sweep_z: bool = False,
    interpolate_z: bool = True,
    xy_offset_mm: Optional[Sequence[float]] = None,
    z_offset_mm: Optional[float] = None,
    overlay: bool = True,
    prefix: str = "smart",
    channel: Optional[str] = None,
    require_complete: bool = True,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    log: Optional[logging.Logger] = None,
) -> Dict[str, Any]:
    """Analyse ``acq_dir`` and write the follow-up acquisition into ``out_dir``.

    Returns :func:`emit.write_targets`'s summary (whose ``targets`` are the
    ones written: offset, filtered, labelled) extended with the raw
    ``analysis_targets``, ``fovs``, ``errors`` and ``overlays``. Raises
    :class:`emit.TargetValidationError` when the analysis finds nothing (or
    nothing inside the stage limits) so an unattended caller stops instead of
    running an empty acquisition. Overlay failures are logged, never fatal:
    they are for humans, the coordinates are the deliverable.
    """
    log = log or _log
    if not acq_dir or not os.path.isdir(acq_dir):
        raise FileNotFoundError(f"Survey acquisition folder not found: {acq_dir!r}")
    if not template_path or not os.path.exists(template_path):
        raise FileNotFoundError(f"Template acquisition.yaml not found: {template_path!r}")
    out_dir = out_dir or os.path.join(acq_dir, "smart_targets")

    result = pipeline.run_analysis(
        acq_dir,
        analyzer,
        params=params or {},
        channel=channel,
        carry_z=carry_z,
        sweep_z=sweep_z,
        interpolate_z=interpolate_z,
        require_complete=require_complete,
        progress_cb=progress_cb,
    )
    for message in result.errors:
        log.warning(f"analysis: {message}")
    log.info(f"{len(result.fovs)} FOV(s) -> {len(result.targets)} target(s) ({result.measured_count} measured)")

    overlays = []
    if overlay and result.targets:
        try:
            overlays = pipeline.save_overlays(
                acq_dir,
                result,
                os.path.join(out_dir, "overlays"),
                channel=channel,
                require_complete=require_complete,
            )
        except Exception as e:  # overlays are diagnostics; never fail the step on them
            log.warning(f"Could not write overlays: {e}")

    if not result.targets:
        raise emit.TargetValidationError(f"Analysis found no targets in {acq_dir}")

    summary = emit.write_targets(
        result.targets,
        template_path,
        out_dir,
        prefix=prefix,
        xy_offset_mm=xy_offset_mm,
        z_offset_mm=z_offset_mm,
        source_acquisition=acq_dir,
    )
    summary["analysis_targets"] = result.targets
    summary["fovs"] = result.fovs
    summary["errors"] = list(result.errors)
    summary["overlays"] = overlays
    return summary
