"""Z-stack modes shared by the acquisition tabs and the Qt-free controllers.

The three modes map onto the backend's ``Z_STACKING_CONFIG_MAP`` indices
(see ``control.widgets.z_mode_to_stacking_index``):

  From Bottom -- stack starts at the focus plane and runs upward (the historical behavior)
  Set Range   -- absolute Z-min/Z-max; incompatible with laser AF, which is disabled
  Relative    -- stack is CENTRED on the focus plane, so the laser AF / contrast AF /
                 focus map / stored-point reference can sit mid-sample

Kept out of ``control.widgets`` so the record/z-stack controller and worker can
use the same labels and the same odd-Nz rule without importing Qt.
"""

import math

Z_MODE_FROM_BOTTOM = "From Bottom"
Z_MODE_SET_RANGE = "Set Range"
Z_MODE_RELATIVE = "Relative"
Z_MODES = [Z_MODE_FROM_BOTTOM, Z_MODE_SET_RANGE, Z_MODE_RELATIVE]


# Slack allowed when rounding the half-stack up, in units of half-steps.  set_deltaZ snaps
# dz to a whole Z microstep (~0.094 um), so a nominal 5.000 um becomes 4.969 and a plain
# ceil() of range/(2*dz) tips just past the integer -- turning a requested 20 um / 5 um
# stack from 5 slices into 7.  Absorbing that costs at most 0.1 * dz of coverage.
_RELATIVE_STACK_RATIO_TOLERANCE = 0.05


def relative_stack_Nz(z_range_um: float, dz_um: float) -> int:
    """Number of slices covering z_range_um at dz_um, forced ODD.

    An odd Nz puts exactly one slice on the focus plane and makes the stack symmetric
    about it, which is what MultiPointWorker's round((NZ - 1) / 2) offset assumes.  The
    count is rounded up, so the range actually covered -- (Nz - 1) * dz -- normally
    exceeds the requested range, by up to one step.  The tolerance below means it can also
    fall short, but never by more than 0.1 * dz.
    """
    if dz_um <= 0:
        return 1
    half_steps = z_range_um / (2 * dz_um) - _RELATIVE_STACK_RATIO_TOLERANCE
    return 2 * max(1, math.ceil(half_steps)) + 1
