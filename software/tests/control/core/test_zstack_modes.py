import pytest

from control.core.zstack_modes import (
    Z_MODE_FROM_BOTTOM,
    Z_MODE_RELATIVE,
    Z_MODE_SET_RANGE,
    Z_MODES,
    relative_stack_Nz,
)


def test_z_modes_are_the_three_gui_labels_in_order():
    assert Z_MODES == [Z_MODE_FROM_BOTTOM, Z_MODE_SET_RANGE, Z_MODE_RELATIVE]
    assert Z_MODES == ["From Bottom", "Set Range", "Relative"]


@pytest.mark.parametrize(
    "z_range_um, dz_um, expected",
    [
        (20.0, 5.0, 5),  # exact fit: 2 steps each side of the focus plane
        (20.0, 4.0, 7),  # 2.5 half-steps rounds up to 3
        (6.0, 1.5, 5),
        (0.1, 1.5, 3),  # never fewer than one slice each side
        (10.0, 3.0, 5),
    ],
)
def test_relative_stack_nz(z_range_um, dz_um, expected):
    assert relative_stack_Nz(z_range_um, dz_um) == expected


@pytest.mark.parametrize("z_range_um", [0.1, 1.0, 7.3, 20.0, 150.0])
@pytest.mark.parametrize("dz_um", [0.3, 1.5, 4.969, 10.0])
def test_relative_stack_nz_is_odd_and_covers_the_range(z_range_um, dz_um):
    nz = relative_stack_Nz(z_range_um, dz_um)
    assert nz >= 3 and nz % 2 == 1
    # Covered range (Nz - 1) * dz falls short of the request by at most the documented 0.1 * dz.
    assert (nz - 1) * dz_um >= z_range_um - 0.1 * dz_um - 1e-9


def test_relative_stack_nz_absorbs_the_microstep_snap_of_dz():
    # set_deltaZ snaps a nominal 5 um step to whole microsteps (4.969 um); a plain ceil() would
    # turn the requested 20 um / 5 um stack from 5 slices into 7.
    assert relative_stack_Nz(20.0, 4.969) == 5


@pytest.mark.parametrize("dz_um", [0.0, -1.0])
def test_relative_stack_nz_with_no_step_is_a_single_slice(dz_um):
    assert relative_stack_Nz(20.0, dz_um) == 1


def test_z_mode_maps_to_the_backend_stacking_config():
    import control._def
    from control.widgets import _YAML_Z_CONFIG_TO_Z_MODE, z_mode_to_stacking_index

    stacking = {mode: control._def.Z_STACKING_CONFIG_MAP[z_mode_to_stacking_index(mode)] for mode in Z_MODES}
    assert stacking == {
        Z_MODE_FROM_BOTTOM: "FROM BOTTOM",
        Z_MODE_SET_RANGE: "FROM BOTTOM",
        Z_MODE_RELATIVE: "FROM CENTER",
    }
    # Relative round-trips through acquisition.yaml as "FROM CENTER".
    assert _YAML_Z_CONFIG_TO_Z_MODE["FROM CENTER"] == Z_MODE_RELATIVE
    assert _YAML_Z_CONFIG_TO_Z_MODE["FROM BOTTOM"] == Z_MODE_FROM_BOTTOM
