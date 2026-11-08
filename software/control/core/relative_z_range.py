"""Pure helpers for a Z-range set relative to the laser-AF reference plane (Qt-free)."""

import math
from typing import List, Optional, Sequence, Tuple

RelativeRange = Tuple[float, float]

TRAVEL_EPS_UM = 1e-6  # travel ends are inclusive with this tolerance


def nz_for_range(z_min_um: float, z_max_um: float, dz_um: float) -> int:
    """Number of planes for a range: ceil((max - min) / dz) + 1, as the widget's update_Nz.

    The quotient is rounded to 9 places before ceil so 0.6 / 0.2 = 3.0000000000000004 does not add a plane.
    Returns 1 when dz is not finite and positive or the range is empty (a display fallback only).
    """
    if not (math.isfinite(dz_um) and dz_um > 0) or z_max_um <= z_min_um:
        return 1
    return int(math.ceil(round((z_max_um - z_min_um) / dz_um, 9))) + 1


def relative_range_problem(
    rng: RelativeRange, dz_um: float, travel_span_um: float, z_stacking_config: str
) -> Optional[str]:
    """Why a relative Z-range cannot run, or None. Checks config, dz, range order, then stack length vs travel."""
    if z_stacking_config != "FROM BOTTOM":
        return "A relative Z-range runs from Z-min up (From Bottom): change the z-stack config, or clear the range."
    if not (math.isfinite(dz_um) and dz_um > 0):
        return f"The z step must be a positive number of µm, got {dz_um}: set dz."
    z_min_um, z_max_um = rng
    if not (math.isfinite(z_min_um) and math.isfinite(z_max_um) and z_min_um <= z_max_um):
        return (
            f"The Z-range must run from Z-min up to Z-max, got {z_min_um:.1f} to {z_max_um:.1f} µm: "
            "set the range again."
        )
    nz = nz_for_range(z_min_um, z_max_um, dz_um)
    span_um = (nz - 1) * dz_um
    if span_um > travel_span_um:
        return (
            f"The z-stack spans {span_um:.1f} µm, longer than the travel ({travel_span_um:.0f} µm): "
            "reduce the range or raise dz."
        )
    return None


def frame_targets(
    anchor_um: float,
    z_min_rel_um: float,
    nz: int,
    dz_um: float,
    channel_offsets_um: Sequence[float],
    travel_um: Tuple[float, float],
) -> List[List[Optional[float]]]:
    """Absolute piezo target per [channel][k], or None when it falls outside travel (ends inclusive, within TRAVEL_EPS_UM).

    Target = anchor + z_min_rel + k * dz + channel offset; eligibility is decided after the offset.
    """
    low_um, high_um = travel_um
    targets: List[List[Optional[float]]] = []
    for offset_um in channel_offsets_um:
        row: List[Optional[float]] = []
        for k in range(nz):
            z_um = anchor_um + z_min_rel_um + k * dz_um + offset_um
            row.append(z_um if low_um - TRAVEL_EPS_UM <= z_um <= high_um + TRAVEL_EPS_UM else None)
        targets.append(row)
    return targets
