import pytest

from control.core.record_zstack_controller import frame_count, zstack_plane_count, zstack_offsets_um


def test_frame_count():
    assert frame_count(10.0, 30.0) == 300
    assert frame_count(7.5, 2.0) == 15


def test_zstack_plane_count_and_offsets():
    assert zstack_plane_count(-3.0, 3.0, 1.0) == 7
    assert zstack_offsets_um(-3.0, 3.0, 1.0) == [-3.0, -2.0, -1.0, 0.0, 1.0, 2.0, 3.0]
    assert zstack_plane_count(0.0, 5.0, 2.0) == 3  # 0,2,4
    assert zstack_offsets_um(0.0, 5.0, 2.0) == [0.0, 2.0, 4.0]


def test_zstack_plane_count_validation():
    with pytest.raises(ValueError):
        zstack_plane_count(3.0, -3.0, 1.0)  # z_max < z_min
    with pytest.raises(ValueError):
        zstack_plane_count(0.0, 3.0, 0.0)  # step == 0
    with pytest.raises(ValueError):
        zstack_plane_count(0.0, 3.0, -1.0)  # step < 0
